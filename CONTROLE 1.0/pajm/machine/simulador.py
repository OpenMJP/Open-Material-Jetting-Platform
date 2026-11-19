"""
Firmware simulado falando PROTOCOL.md v1.1.

Implementa o lado do ESP32-S3 no mesmo processo, falando os MESMOS BYTES que
a maquina real falaria. Nao e um stub que responde sucesso: ele mantem a
maquina de estados da secao 7.1, a janela de recepcao da secao 4.2, os
creditos da 4.3, e aplica as validacoes da 7.5.

Serve a tres coisas:
  1. desenvolver e testar a interface inteira sem a maquina;
  2. exercitar o protocolo de ponta a ponta, achando divergencia de formato
     antes de ela virar comportamento estranho na bancada;
  3. servir de referencia executavel do comportamento esperado.

O tempo e simulado: nada acontece sozinho, a execucao da fila avanca quando
`avancar()` e chamado. Assim os testes sao rapidos e deterministicos.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from . import protocolo as pv


@dataclass
class _Swath:
    swath_id: int
    dir_menos_x: bool
    pho_cba: bool
    pitch: int
    y: int
    x_start: int
    n_cols: int
    fire_delay_us: int
    speed: int
    dados: bytearray = field(default_factory=bytearray)

    @property
    def colunas_recebidas(self) -> int:
        return len(self.dados) // pv.BYTES_POR_COLUNA


@dataclass
class _Tarefa:
    op: int
    dado: object = None


class FirmwareSimulado:
    def __init__(self,
                 steps_per_m=(40_000, 40_000, 160_000),
                 travel=(8_000, 8_000, 6_000),
                 swath_slots: int = pv.SWATH_SLOTS,
                 queue_slots: int = pv.QUEUE_SLOTS,
                 vmax=(8_000, 4_000, 2_000),
                 min_fire_period_us: int = 200,
                 fw=(1, 0, 0)):
        self.steps_per_m = tuple(steps_per_m)
        self.travel = tuple(travel)
        self.swath_slots = swath_slots
        self.queue_slots = queue_slots
        self.vmax = tuple(vmax)
        self.min_fire_period_us = min_fire_period_us
        self.fw = tuple(fw)

        self.estado = pv.Estado.IDLE
        self.pos = [0, 0, 0]
        self.referenciado = [False, False, False]
        self.vpp = False
        self.job_id = 0
        self.swath_id = 0
        self.last_error = 0
        self.fault = 0

        self.parametros: dict[int, int] = {
            pv.Param.ACCEL_X: 40_000, pv.Param.ACCEL_Y: 20_000,
            pv.Param.ACCEL_Z: 10_000,
            pv.Param.VMAX_X: vmax[0], pv.Param.VMAX_Y: vmax[1],
            pv.Param.VMAX_Z: vmax[2],
            pv.Param.RUNUP_MARGIN: 40, pv.Param.CHECK_READY: 1,
            pv.Param.LOG_ENABLE: 0,
            pv.Param.MIN_FIRE_PERIOD: min_fire_period_us,
        }

        self._dec = pv.Decodificador()
        self._saida = bytearray()
        self._esperado = 1              # proximo seq esperado
        self._seq_proprio = 0
        self._nack_enviado = False

        self._slots: list[_Swath] = []  # swaths completas, na fila
        self._recebendo: _Swath | None = None
        self._fila: list[_Tarefa] = []

        # Estado PLANEJADO (secao 7.1): as validacoes olham o que a maquina
        # tera depois de executar a fila, nao o que ela e agora.
        self._plan_ref = [False, False, False]
        self._plan_vpp = False
        self._plan_pos = [0, 0, 0]

        # contadores para teste
        self.estops = 0
        self.cancels = 0
        self.gotas = 0
        self.swaths_executadas = 0
        self.motores_desligados_por_enlace = 0

    # ---- interface de bytes ---------------------------------------------

    def alimentar(self, dados: bytes) -> None:
        for q in self._dec.alimentar(dados):
            self._receber(q)

    def drenar(self) -> bytes:
        out = bytes(self._saida)
        self._saida.clear()
        return out

    # ---- envio -----------------------------------------------------------

    def _enviar(self, op: int, payload: bytes = b"") -> None:
        self._saida.extend(pv.Quadro(op, self._seq_proprio, payload).no_fio())
        self._seq_proprio = (self._seq_proprio + 1) & 0xFF

    def _ack(self, seq: int) -> None:
        self._enviar(pv.Op.ACK, struct.pack(
            "<BBBB", seq & 0xFF, self._swath_free(), self._queue_free(), 0))

    def _erro(self, seq: int, code: int, detail: int = 0) -> None:
        """ERR vai ANTES do ACK do quadro que causou o erro (secao 4.2)."""
        self.last_error = int(code)
        self._enviar(pv.Op.ERR, struct.pack("<BBHi", seq & 0xFF, int(code),
                                            0, int(detail)))

    def _swath_free(self) -> int:
        usados = len(self._slots) + (1 if self._recebendo else 0)
        return max(0, self.swath_slots - usados)

    def _queue_free(self) -> int:
        return max(0, self.queue_slots - len(self._fila))

    def enviar_status(self) -> None:
        self._enviar(pv.Op.STATUS, self.status().bytes())

    def status(self) -> pv.Status:
        flags = 0
        for i, r in enumerate(self.referenciado):
            if r:
                flags |= 1 << i
        if self.vpp:
            flags |= 0x08
        if any(t.op == pv.Op.SWATH_END for t in self._fila):
            flags |= 0x20
        return pv.Status(
            state=int(self.estado), flags=flags, job_id=self.job_id,
            swath_id=self.swath_id, swath_free=self._swath_free(),
            queue_free=self._queue_free(), pos=tuple(self.pos),
            last_error=self.last_error, fault=self.fault,
            frames_dropped=min(self._dec.frames_dropped, 0xFFFF))

    # ---- recepcao --------------------------------------------------------

    def _receber(self, q: pv.Quadro) -> None:
        # Prioritarios: fora da janela, seq ignorado, sem ACK (secao 4.1)
        if q.tipo in (pv.Op.ESTOP, pv.Op.CANCEL):
            self._prioritario(q.tipo)
            return

        # Sem sessao, so HELLO e atendido (secao 5.1)
        if q.tipo == pv.Op.HELLO:
            self._hello(q)
            return
        if self.estado is None:
            return

        # Janela Go-Back-N (secao 4.2)
        if q.seq != self._esperado:
            atras = (self._esperado - q.seq) & 0xFF
            if 1 <= atras <= pv.WINDOW:
                self._ack((self._esperado - 1) & 0xFF)   # duplicata
            elif not self._nack_enviado:
                self._enviar(pv.Op.NACK,
                             struct.pack("<BB", self._esperado, 0x01))
                self._nack_enviado = True
            return

        self._nack_enviado = False
        self._esperado = (self._esperado + 1) & 0xFF
        self._tratar(q)

    def _hello(self, q: pv.Quadro) -> None:
        if len(q.payload) != 2:
            return
        if self.estado == pv.Estado.JOB:
            self._desligar(pv.Erro.LINK_LOST)
        self._esperado = 1
        self._seq_proprio = 0
        self._nack_enviado = False
        self._enviar(pv.Op.HELLO_ACK, struct.pack(
            pv.HelloAck.FORMATO,
            pv.PROTO_MAJOR, pv.PROTO_MINOR,
            self.fw[0], self.fw[1], self.fw[2],
            pv.WINDOW, self.swath_slots, self.queue_slots,
            pv.MAX_PAYLOAD, pv.MAX_COLS,
            self.parametros[pv.Param.MIN_FIRE_PERIOD], 0,
            self.steps_per_m[0], self.steps_per_m[1], self.steps_per_m[2],
            self.travel[0], self.travel[1], self.travel[2]))

    def _prioritario(self, op: int) -> None:
        if op == pv.Op.ESTOP:
            self.estops += 1
            self.vpp = False
            self._fila.clear()
            self._slots.clear()
            self._recebendo = None
            self.referenciado = [False, False, False]
            self._plan_ref = [False, False, False]
            self._plan_vpp = False
            self.estado = pv.Estado.FAULT
            self.fault = int(pv.Falha.ESTOP)
        else:
            self.cancels += 1
            if self.estado == pv.Estado.JOB:
                self._desligar(pv.Erro.CANCELLED)
            elif self.estado == pv.Estado.IDLE:
                self._fila.clear()

    def perder_enlace(self) -> None:
        """
        LINK_TIMEOUT do lado do ESP (secao 5.3). O simulador nao tem relogio,
        entao quem testa chama isto no lugar de esperar 1 s de silencio.
        Desde a v1.4 o cabecote e os motores desligam junto.
        """
        if self.estado == pv.Estado.JOB:
            self._desligar(pv.Erro.LINK_LOST)
        elif self.estado == pv.Estado.IDLE:
            self._fila.clear()
            self.vpp = False
            self._plan_vpp = False
        if self.estado != pv.Estado.FAULT:
            self.referenciado = [False, False, False]
            self._plan_ref = [False, False, False]
            self.motores_desligados_por_enlace += 1

    def _desligar(self, motivo: int) -> None:
        """Rotina de desligamento (secao 7.7)."""
        self.vpp = False
        self._plan_vpp = False
        self._fila.clear()
        self._slots.clear()
        self._recebendo = None
        self.estado = pv.Estado.IDLE
        self.last_error = int(motivo)

    # ---- tratamento por opcode -------------------------------------------

    def _tratar(self, q: pv.Quadro) -> None:
        op, seq, p = q.tipo, q.seq, q.payload

        try:
            op = pv.Op(op)
        except ValueError:
            self._erro(seq, pv.Erro.UNKNOWN_OPCODE, q.tipo)
            self._ack(seq)
            return

        esperado = pv.TAMANHOS.get(op)
        if esperado is not None and len(p) != esperado:
            self._erro(seq, pv.Erro.BAD_LENGTH, len(p))
            self._ack(seq)
            return

        erro = self._despachar(op, seq, p)
        if erro is not None:
            code, detalhe = erro
            self._erro(seq, code, detalhe)
            # Em JOB, erro de mensagem de trabalho ou de dados aborta o job
            if self.estado == pv.Estado.JOB and op in pv.DE_TRABALHO:
                self._desligar(code)
        self._ack(seq)

    def _despachar(self, op, seq, p):
        E = pv.Erro
        manual = {pv.Op.JOG, pv.Op.SET_PARAM, pv.Op.SAVE_PARAMS,
                  pv.Op.MOTORS_OFF}

        if self.estado == pv.Estado.FAULT and op not in (
                pv.Op.CLEAR_FAULT, pv.Op.GET_PARAM, pv.Op.PING):
            return (E.BAD_STATE, int(self.estado))
        if self.estado == pv.Estado.STOPPING and op not in (
                pv.Op.GET_PARAM, pv.Op.PING):
            return (E.BAD_STATE, int(self.estado))
        if self.estado == pv.Estado.JOB and op in manual:
            return (E.BAD_STATE, int(self.estado))

        if op == pv.Op.PING:
            return None

        if op == pv.Op.CLEAR_FAULT:
            if self.estado != pv.Estado.FAULT:
                return (E.BAD_STATE, int(self.estado))
            self.estado = pv.Estado.IDLE
            self.fault = 0
            self.referenciado = [False, False, False]
            self._plan_ref = [False, False, False]
            return None

        if op == pv.Op.JOB_BEGIN:
            if self.estado != pv.Estado.IDLE:
                return (E.BAD_STATE, int(self.estado))
            self.job_id, _total = struct.unpack("<HH", p)
            self.estado = pv.Estado.JOB
            self.last_error = 0
            return None

        if op == pv.Op.JOB_END:
            if self.estado != pv.Estado.JOB:
                return (E.BAD_STATE, int(self.estado))
            return self._enfileirar(_Tarefa(pv.Op.JOB_END))

        if op == pv.Op.HOME:
            (m,) = struct.unpack("<B", p)
            if m & ~0x07:
                return (E.BAD_PARAM, 0)
            for i in range(3):
                if m & (1 << i):
                    self._plan_ref[i] = True
                    self._plan_pos[i] = 0
            return self._enfileirar(_Tarefa(pv.Op.HOME, m))

        if op == pv.Op.HEAD_POWER:
            ligar = bool(p[0])
            self._plan_vpp = ligar
            return self._enfileirar(_Tarefa(pv.Op.HEAD_POWER, ligar))

        if op == pv.Op.MOVE:
            eixo, _r1, _r2, pos, vel = struct.unpack("<BBHiI", p)
            if eixo > 2:
                return (E.BAD_PARAM, 0)
            if not self._plan_ref[eixo]:
                return (E.NOT_HOMED, 1 << eixo)
            if not 0 <= pos <= self.travel[eixo]:
                return (E.OUT_OF_TRAVEL, pos)
            if vel > self.vmax[eixo] or vel == 0:
                return (E.BAD_PARAM, 8)
            self._plan_pos[eixo] = pos
            return self._enfileirar(_Tarefa(pv.Op.MOVE, (eixo, pos)))

        if op == pv.Op.DWELL:
            return self._enfileirar(_Tarefa(pv.Op.DWELL, struct.unpack("<I", p)[0]))

        if op == pv.Op.SPIT:
            n, per, _pad = struct.unpack("<HH", p[:4])[0], struct.unpack("<HH", p[:4])[1], p[4:]
            if not self._plan_vpp:
                return (E.HEAD_OFF, 0)
            if per < self.parametros[pv.Param.MIN_FIRE_PERIOD]:
                return (E.FIRE_TOO_FAST, per)
            return self._enfileirar(_Tarefa(pv.Op.SPIT, (n, _pad)))

        if op == pv.Op.JOG:
            if self.estado != pv.Estado.IDLE:
                return (E.BAD_STATE, int(self.estado))
            eixo, _r1, _r2, delta, vel = struct.unpack("<BBHiI", p)
            if eixo > 2:
                return (E.BAD_PARAM, 0)
            if vel > self.vmax[eixo] or vel == 0:
                return (E.BAD_PARAM, 8)
            destino = self._plan_pos[eixo] + delta
            if self._plan_ref[eixo] and not 0 <= destino <= self.travel[eixo]:
                return (E.OUT_OF_TRAVEL, destino)
            self._plan_pos[eixo] = max(0, min(destino, self.travel[eixo]))
            return self._enfileirar(_Tarefa(pv.Op.JOG, (eixo, delta)))

        if op == pv.Op.MOTORS_OFF:
            if self.estado != pv.Estado.IDLE:
                return (E.BAD_STATE, int(self.estado))
            r = self._enfileirar(_Tarefa(pv.Op.MOTORS_OFF))
            if r is None:
                self._plan_ref = [False, False, False]
            return r

        if op == pv.Op.SET_PARAM:
            if self._fila:
                return (E.BAD_STATE, int(self.estado))
            pid, _r, valor = struct.unpack("<HHi", p)
            if pid not in self.parametros:
                return (E.BAD_PARAM, pid)
            if pid == pv.Param.MIN_FIRE_PERIOD and valor < 180:
                return (E.BAD_PARAM, valor)
            self.parametros[pid] = valor
            return None

        if op == pv.Op.GET_PARAM:
            (pid,) = struct.unpack("<H", p)
            if pid not in self.parametros:
                return (E.BAD_PARAM, pid)
            self._enviar(pv.Op.PARAM_VALUE,
                         struct.pack("<HHi", pid, 0, self.parametros[pid]))
            return None

        if op == pv.Op.SAVE_PARAMS:
            return None

        if op == pv.Op.SWATH_BEGIN:
            return self._swath_begin(p)

        if op == pv.Op.SWATH_DATA:
            return self._swath_data(p)

        if op == pv.Op.SWATH_END:
            return self._swath_end(p)

        return (E.UNKNOWN_OPCODE, int(op))

    def _enfileirar(self, t: _Tarefa):
        if self._queue_free() <= 0:
            return (pv.Erro.NO_QUEUE_SLOT, 0)
        self._fila.append(t)
        return None

    # ---- swaths ----------------------------------------------------------

    def _swath_begin(self, p: bytes):
        E = pv.Erro
        if self.estado != pv.Estado.JOB:
            return (E.BAD_STATE, int(self.estado))
        if self._swath_free() <= 0:
            return (E.NO_SWATH_SLOT, 0)

        sid, flags, pitch, y, x_start, n_cols, fdelay, speed = struct.unpack(
            "<HBBiiHHI", p)
        if flags & ~0x03:
            return (E.BAD_PARAM, 2)
        dir_menos = bool(flags & 0x01)
        if dir_menos:
            # v1.0 do firmware so imprime em +X (secao 7.5)
            return (E.BAD_PARAM, 2)
        if not (self._plan_ref[0] and self._plan_ref[1]):
            return (E.NOT_HOMED, 0b011)
        if not self._plan_vpp:
            return (E.HEAD_OFF, 0)
        if not 1 <= n_cols <= pv.MAX_COLS or pitch < 1:
            return (E.BAD_PARAM, 12)
        # Como no firmware: contra o parametro, que SET_PARAM pode mudar
        if speed > self.parametros[pv.Param.VMAX_X] or speed == 0:
            return (E.BAD_PARAM, 16)

        periodo_us = 10 ** 6 * pitch // max(1, speed)
        if periodo_us < self.parametros[pv.Param.MIN_FIRE_PERIOD]:
            return (E.FIRE_TOO_FAST, periodo_us)
        if fdelay >= periodo_us:
            return (E.BAD_PARAM, 14)

        s = +1 if not dir_menos else -1
        d = pv.recuo_de_aceleracao(
            speed, self.parametros[pv.Param.ACCEL_X],
            self.parametros[pv.Param.RUNUP_MARGIN])
        x_end = x_start + s * (n_cols - 1) * pitch
        for valor, limite in ((x_start - s * d, self.travel[0]),
                              (x_end + s * d, self.travel[0]),
                              (y, self.travel[1])):
            if not 0 <= valor <= limite:
                return (E.OUT_OF_TRAVEL, valor)

        self._recebendo = _Swath(sid, dir_menos, bool(flags & 0x02), pitch,
                                 y, x_start, n_cols, fdelay, speed)
        return None

    def _swath_data(self, p: bytes):
        E = pv.Erro
        if self._recebendo is None:
            return (E.SWATH_SEQUENCE, 0)
        sid, col_start = struct.unpack("<HH", p[:4])
        dados = p[4:]
        if sid != self._recebendo.swath_id:
            return (E.SWATH_SEQUENCE, self._recebendo.colunas_recebidas)
        if col_start != self._recebendo.colunas_recebidas:
            return (E.SWATH_SEQUENCE, self._recebendo.colunas_recebidas)
        if not dados or len(dados) % pv.BYTES_POR_COLUNA:
            return (E.BAD_LENGTH, len(p))
        self._recebendo.dados.extend(dados)
        return None

    def _swath_end(self, p: bytes):
        E = pv.Erro
        if self._recebendo is None:
            return (E.SWATH_SEQUENCE, 0)
        sid, _r, crc = struct.unpack("<HHI", p)
        s = self._recebendo
        if sid != s.swath_id:
            return (E.SWATH_SEQUENCE, s.colunas_recebidas)
        if s.colunas_recebidas != s.n_cols:
            self._recebendo = None
            return (E.SWATH_INCOMPLETE, s.colunas_recebidas)
        calculado = pv.crc32(bytes(s.dados))
        if calculado != crc:
            self._recebendo = None
            return (E.SWATH_CRC, calculado if calculado < 2**31
                    else calculado - 2**32)
        self._recebendo = None
        if self._queue_free() <= 0:
            return (E.NO_QUEUE_SLOT, 0)
        self._slots.append(s)
        self._fila.append(_Tarefa(pv.Op.SWATH_END, s))
        return None

    # ---- execucao da fila (tempo simulado) -------------------------------

    def avancar(self, tarefas: int = 1) -> None:
        """Executa ate `tarefas` itens da fila, como a maquina faria."""
        for _ in range(tarefas):
            if not self._fila:
                return
            t = self._fila.pop(0)
            if t.op == pv.Op.HOME:
                for i in range(3):
                    if t.dado & (1 << i):
                        self.referenciado[i] = True
                        self.pos[i] = 0
            elif t.op == pv.Op.HEAD_POWER:
                self.vpp = bool(t.dado)
            elif t.op == pv.Op.MOVE:
                eixo, pos = t.dado
                self.pos[eixo] = pos
            elif t.op == pv.Op.MOTORS_OFF:
                self.referenciado = [False, False, False]
            elif t.op == pv.Op.JOG:
                eixo, delta = t.dado
                self.pos[eixo] = max(
                    0, min(self.pos[eixo] + delta, self.travel[eixo]))
            elif t.op == pv.Op.SPIT:
                n, padrao = t.dado
                self.gotas += n * sum(bin(b).count("1") for b in padrao)
            elif t.op == pv.Op.SWATH_END:
                s: _Swath = t.dado
                self.gotas += sum(bin(b).count("1") for b in s.dados)
                self.pos[0] = s.x_start + (s.n_cols - 1) * s.pitch
                self.pos[1] = s.y
                self.swath_id = s.swath_id
                self.swaths_executadas += 1
                if s in self._slots:
                    self._slots.remove(s)
            elif t.op == pv.Op.JOB_END:
                self._desligar(0)
                return

    def executar_tudo(self, limite: int = 100_000) -> None:
        n = 0
        while self._fila and n < limite:
            self.avancar(1)
            n += 1


class TransporteSimulado:
    """Liga a `Sessao` ao firmware simulado, sem porta serial nenhuma."""

    def __init__(self, firmware: FirmwareSimulado | None = None,
                 auto_executar: bool = False):
        self.firmware = firmware or FirmwareSimulado()
        self.auto_executar = auto_executar

    def escrever(self, dados: bytes) -> None:
        self.firmware.alimentar(dados)

    def ler(self) -> bytes:
        if self.auto_executar:
            self.firmware.avancar(1)
        return self.firmware.drenar()

    def fechar(self) -> None:
        pass
