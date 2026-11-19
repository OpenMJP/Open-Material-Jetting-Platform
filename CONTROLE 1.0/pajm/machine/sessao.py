"""
Sessao e transporte do protocolo PAJM v1.1, lado do PC.

Implementa as camadas 4 e 5 do PROTOCOL.md: janela Go-Back-N com ACK
cumulativo e retransmissao, creditos de swath e de fila, abertura de sessao
com HELLO, heartbeat por PING e deteccao de perda de enlace.

DESENHO: UMA FILA DE SAIDA E UM BOMBEAMENTO

Quem chama empilha comandos com os metodos de alto nivel, que apenas
ENFILEIRAM. Todo o trabalho de tempo (transmitir dentro da janela, respeitar
creditos, retransmitir por RTO, mandar PING, vigiar o timeout) acontece em
`bombear()`, chamado periodicamente.

Isso tira da interface qualquer responsabilidade de controle de fluxo, e deixa
o conjunto testavel com um relogio falso: os testes avancam o tempo em vez de
esperar, entao verificam RTO e timeout em milissegundos de execucao.

O ACK significa "recebido e tratado", nao "executado" (secao 4.2). Quem quer
saber se o movimento terminou olha o STATUS, nao o ACK.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from . import protocolo as pv


class Evento(Enum):
    HELLO_ACK = auto()
    STATUS = auto()
    ERRO = auto()
    PARAMETRO = auto()
    LOG = auto()
    ENLACE_PERDIDO = auto()
    NACK = auto()


@dataclass
class Aviso:
    tipo: Evento
    dado: object = None


class EnlacePerdido(RuntimeError):
    pass


class VersaoIncompativel(RuntimeError):
    pass


@dataclass
class _EmVoo:
    seq: int
    op: int
    quadro: bytes
    enviado_em: float
    consome_swath: bool
    consome_fila: bool


#: Quais opcodes consomem cada recurso (secao 4.3).
CONSOME_FILA = frozenset({
    pv.Op.HOME, pv.Op.HEAD_POWER, pv.Op.MOVE, pv.Op.DWELL, pv.Op.SPIT,
    pv.Op.JOG, pv.Op.MOTORS_OFF, pv.Op.JOB_END, pv.Op.SWATH_END,
})
CONSOME_SWATH = frozenset({pv.Op.SWATH_BEGIN})


class Sessao:
    def __init__(self, transporte, relogio: Callable[[], float] | None = None):
        self._t = transporte
        self._agora = relogio or time.perf_counter
        self._dec = pv.Decodificador()

        self.hello_ack: pv.HelloAck | None = None
        self.status: pv.Status | None = None
        self.aberta = False
        self.motivo_do_fim: str | None = None

        self._proximo_seq = 1
        self._em_voo: list[_EmVoo] = []
        self._fila: list[tuple[int, bytes]] = []
        self._ultimo_envio = 0.0
        self._ultima_recepcao = 0.0
        self._estouros_seguidos = 0
        self._ultima_retransmissao = 0.0

        # Creditos informados pelo ESP; None antes do primeiro ACK ou STATUS.
        self._swath_free: int | None = None
        self._queue_free: int | None = None

        self._prioritario: tuple[int, set[int], float, float] | None = None

        # CANCEL pedido desde o ultimo JOB_BEGIN. Quem esta enviando um job
        # (envio.enviar_unidades) para de mandar passadas ao ver isto, venha
        # o pedido de onde vier: do proprio painel ou da barra superior.
        self.cancelamento_pedido = False

    # ---- unidades --------------------------------------------------------

    def passos_por_mm(self, eixo: int) -> float:
        if self.hello_ack is None:
            raise RuntimeError("sem HELLO_ACK ainda; nao da para converter")
        return self.hello_ack.passos_por_mm(eixo)

    def para_passos(self, eixo: int, mm: float) -> int:
        return int(round(mm * self.passos_por_mm(eixo)))

    def para_mm(self, eixo: int, passos: int) -> float:
        return passos / self.passos_por_mm(eixo)

    # ---- abertura de sessao ----------------------------------------------

    def abrir(self) -> pv.HelloAck:
        """
        Handshake do HELLO (secao 5.1).

        Manda um 0x00 antes do HELLO para o receptor do ESP fechar qualquer
        quadro pela metade: sem ele, o lixo que a ROM cospe na UART durante o
        boot gruda no HELLO e o derruba.
        """
        for _ in range(pv.HELLO_TRIES):
            self._t.escrever(
                bytes([pv.DELIMITADOR])
                + pv.Quadro(pv.Op.HELLO, 0, pv.p_hello()).no_fio())
            limite = self._agora() + pv.HELLO_RETRY_S
            while self._agora() < limite:
                for q in self._dec.alimentar(self._t.ler()):
                    if q.tipo == pv.Op.HELLO_ACK:
                        return self._aceitar_hello(q)
                time.sleep(0.001) if self._agora is time.perf_counter else None
        raise EnlacePerdido(
            f"o ESP nao respondeu ao HELLO em {pv.HELLO_TRIES} tentativas")

    def _aceitar_hello(self, q: pv.Quadro) -> pv.HelloAck:
        ha = pv.HelloAck.de_bytes(q.payload)
        if ha.proto_major != pv.PROTO_MAJOR:
            raise VersaoIncompativel(
                f"o firmware fala o protocolo {ha.proto_major}.{ha.proto_minor} "
                f"e este software fala {pv.PROTO_MAJOR}.{pv.PROTO_MINOR}. "
                "Versao maior diferente e incompativel.")
        self.hello_ack = ha
        self.aberta = True
        self.motivo_do_fim = None
        # O ESP zera o transporte ao receber HELLO; o PC faz o mesmo.
        self._proximo_seq = 1
        self._em_voo.clear()
        self._fila.clear()
        self._swath_free = ha.swath_slots
        self._queue_free = ha.queue_slots
        self._estouros_seguidos = 0
        agora = self._agora()
        self._ultima_recepcao = agora
        self._ultimo_envio = agora
        return ha

    def fechar(self) -> None:
        """
        Nao ha mensagem de encerramento (secao 5.2): fechar a porta leva o ESP
        ao LINK_TIMEOUT, e com job ativo isso cancela o job, que e o
        comportamento desejado.
        """
        self.aberta = False
        try:
            self._t.fechar()
        except Exception:
            pass

    # ---- creditos --------------------------------------------------------

    @property
    def swath_disponivel(self) -> int:
        if self._swath_free is None:
            return 0
        gastos = sum(1 for e in self._em_voo if e.consome_swath)
        return max(0, self._swath_free - gastos)

    @property
    def fila_disponivel(self) -> int:
        if self._queue_free is None:
            return 0
        gastos = sum(1 for e in self._em_voo if e.consome_fila)
        return max(0, self._queue_free - gastos)

    def _pode_enviar(self, op: int) -> bool:
        if len(self._em_voo) >= pv.WINDOW:
            return False
        if op in CONSOME_SWATH and self.swath_disponivel <= 0:
            return False
        if op in CONSOME_FILA and self.fila_disponivel <= 0:
            return False
        return True

    # ---- envio -----------------------------------------------------------

    def enfileirar(self, op: int, payload: bytes = b"") -> None:
        """Empilha um quadro sequenciado. O envio acontece em `bombear`."""
        esperado = pv.TAMANHOS.get(op)
        if esperado is not None and len(payload) != esperado:
            raise ValueError(
                f"{pv.Op(op).name} espera {esperado} bytes de payload, "
                f"recebeu {len(payload)}")
        self._fila.append((int(op), payload))

    def enviar_prioritario(self, op: int) -> None:
        """
        ESTOP e CANCEL (secao 4.1 e 4.4): seq 0, fora da janela, sem ACK.

        Vao na frente de tudo que esta enfileirado e sao repetidos a cada
        PRIORITY_REPEAT ate o STATUS mostrar o estado esperado, por no maximo
        um segundo. Nao existe confirmacao para eles.
        """
        if op == pv.Op.ESTOP:
            esperados = {int(pv.Estado.FAULT)}
        elif op == pv.Op.CANCEL:
            esperados = {int(pv.Estado.STOPPING), int(pv.Estado.IDLE)}
        else:
            raise ValueError(f"{op} nao e um quadro prioritario")
        agora = self._agora()
        self._escrever(pv.Quadro(op, 0).no_fio())
        self._prioritario = (int(op), esperados, agora, agora)

    def _escrever(self, dados: bytes) -> None:
        self._t.escrever(dados)
        self._ultimo_envio = self._agora()

    # ---- bombeamento -----------------------------------------------------

    def bombear(self) -> list[Aviso]:
        """
        Le o que chegou, reage, e transmite o que couber.

        Chame periodicamente. Devolve os avisos que surgiram nesta passada.
        """
        avisos: list[Aviso] = []
        if not self.aberta:
            return avisos

        agora = self._agora()

        # 1. recepcao
        dados = self._t.ler()
        if dados:
            for q in self._dec.alimentar(dados):
                self._ultima_recepcao = agora
                self._tratar(q, avisos)

        # 2. prioritario pendente
        self._repetir_prioritario(agora)

        # 3. retransmissao por RTO
        if self._em_voo:
            mais_antigo = self._em_voo[0]
            if agora - mais_antigo.enviado_em >= pv.RTO_S:
                self._estouros_seguidos += 1
                if self._estouros_seguidos > pv.MAX_RETRIES:
                    return self._perder(
                        avisos,
                        f"{pv.MAX_RETRIES} retransmissoes sem resposta "
                        f"a partir do seq {mais_antigo.seq}")
                self._retransmitir(agora)

        # 4. perda de enlace por silencio
        if agora - self._ultima_recepcao >= pv.LINK_TIMEOUT_S:
            return self._perder(
                avisos,
                f"nenhum quadro valido recebido em {pv.LINK_TIMEOUT_S:.0f} s")

        # 5. transmitir o que couber na janela e nos creditos
        while self._fila and self._pode_enviar(self._fila[0][0]):
            op, payload = self._fila.pop(0)
            seq = self._proximo_seq
            self._proximo_seq = (self._proximo_seq + 1) & 0xFF
            quadro = pv.Quadro(op, seq, payload).no_fio()
            self._em_voo.append(_EmVoo(
                seq=seq, op=op, quadro=quadro, enviado_em=agora,
                consome_swath=op in CONSOME_SWATH,
                consome_fila=op in CONSOME_FILA))
            self._escrever(quadro)

        # 6. heartbeat. Vale tambem com quadros parados na fila do PC: uma
        # swath esperando slot espera a anterior imprimir, por segundos, e o
        # ESP encerra a sessao com 1 s sem receber nada. O PING nao consome
        # credito e e permitido ate entre SWATH_BEGIN e SWATH_END (secao 7.3).
        if (len(self._em_voo) < pv.WINDOW
                and agora - self._ultimo_envio >= pv.PING_PERIOD_S):
            self._escrever(pv.Quadro(pv.Op.PING, self._proximo_seq).no_fio())
            self._em_voo.append(_EmVoo(
                seq=self._proximo_seq, op=int(pv.Op.PING),
                quadro=pv.Quadro(pv.Op.PING, self._proximo_seq).no_fio(),
                enviado_em=agora, consome_swath=False, consome_fila=False))
            self._proximo_seq = (self._proximo_seq + 1) & 0xFF

        return avisos

    def _perder(self, avisos: list[Aviso], motivo: str) -> list[Aviso]:
        self.aberta = False
        self.motivo_do_fim = motivo
        avisos.append(Aviso(Evento.ENLACE_PERDIDO, motivo))
        return avisos

    def _retransmitir(self, agora: float, a_partir_de: int | None = None):
        """Go-Back-N: reenvia tudo em voo a partir do ponto indicado."""
        inicio = 0
        if a_partir_de is not None:
            for i, e in enumerate(self._em_voo):
                if e.seq == a_partir_de:
                    inicio = i
                    break
            else:
                return
        for e in self._em_voo[inicio:]:
            e.enviado_em = agora
            self._escrever(e.quadro)
        self._ultima_retransmissao = agora

    def _repetir_prioritario(self, agora: float) -> None:
        if self._prioritario is None:
            return
        op, esperados, inicio, ultimo = self._prioritario
        if self.status is not None and self.status.state in esperados:
            self._prioritario = None
            return
        if agora - inicio >= pv.PRIORITY_MAX_S:
            self._prioritario = None
            return
        if agora - ultimo >= pv.PRIORITY_REPEAT_S:
            self._escrever(pv.Quadro(op, 0).no_fio())
            self._prioritario = (op, esperados, inicio, agora)

    # ---- tratamento dos quadros recebidos --------------------------------

    def _tratar(self, q: pv.Quadro, avisos: list[Aviso]) -> None:
        if q.tipo == pv.Op.ACK:
            a = pv.Ack.de_bytes(q.payload)
            self._confirmar(a)
        elif q.tipo == pv.Op.NACK:
            n = pv.Nack.de_bytes(q.payload)
            avisos.append(Aviso(Evento.NACK, n))
            agora = self._agora()
            if agora - self._ultima_retransmissao >= pv.RTO_S:
                self._retransmitir(agora, a_partir_de=n.expected_seq)
        elif q.tipo == pv.Op.STATUS:
            self.status = pv.Status.de_bytes(q.payload)
            self._swath_free = self.status.swath_free
            self._queue_free = self.status.queue_free
            avisos.append(Aviso(Evento.STATUS, self.status))
        elif q.tipo == pv.Op.ERR:
            avisos.append(Aviso(Evento.ERRO, pv.Err.de_bytes(q.payload)))
        elif q.tipo == pv.Op.PARAM_VALUE:
            avisos.append(
                Aviso(Evento.PARAMETRO, pv.ParamValue.de_bytes(q.payload)))
        elif q.tipo == pv.Op.LOG:
            avisos.append(
                Aviso(Evento.LOG, q.payload.decode("ascii", "replace")))
        elif q.tipo == pv.Op.HELLO_ACK:
            # O ESP reiniciou e respondeu a um HELLO que nao mandamos agora.
            avisos.append(
                Aviso(Evento.HELLO_ACK, pv.HelloAck.de_bytes(q.payload)))

    def _confirmar(self, a: pv.Ack) -> None:
        """ACK cumulativo: libera tudo ate `ack_seq` (secao 4.2)."""
        for i, e in enumerate(self._em_voo):
            if e.seq == a.ack_seq:
                del self._em_voo[:i + 1]
                self._estouros_seguidos = 0
                break
        self._swath_free = a.swath_free
        self._queue_free = a.queue_free

    # ---- comandos de alto nivel ------------------------------------------

    def ping(self) -> None:
        self.enfileirar(pv.Op.PING)

    def limpar_falha(self) -> None:
        self.enfileirar(pv.Op.CLEAR_FAULT)

    def parar(self) -> None:
        self._fila.clear()
        self.enviar_prioritario(pv.Op.ESTOP)

    def cancelar(self) -> None:
        # O que ainda nao saiu do PC e do trabalho cancelado: mandar depois
        # do CANCEL so produz recusas (SWATH_SEQUENCE para cada bloco de
        # dados). O seq e atribuido no envio, entao descartar nao abre buraco
        # na janela.
        self._fila.clear()
        self.cancelamento_pedido = True
        self.enviar_prioritario(pv.Op.CANCEL)

    def referenciar(self, eixos: str = "xyz") -> None:
        m = sum(b for e, b in (("x", 1), ("y", 2), ("z", 4))
                if e in eixos.lower())
        self.enfileirar(pv.Op.HOME, pv.p_home(m))

    def cabecote(self, ligado: bool) -> None:
        self.enfileirar(pv.Op.HEAD_POWER, pv.p_head_power(ligado))

    def mover_mm(self, eixo: int, pos_mm: float, vel_mm_s: float) -> None:
        self.enfileirar(pv.Op.MOVE, pv.p_move(
            eixo, self.para_passos(eixo, pos_mm),
            self.para_passos(eixo, vel_mm_s)))

    def jog_mm(self, eixo: int, delta_mm: float, vel_mm_s: float) -> None:
        self.enfileirar(pv.Op.JOG, pv.p_jog(
            eixo, self.para_passos(eixo, delta_mm),
            self.para_passos(eixo, vel_mm_s)))

    def esperar(self, ms: int) -> None:
        self.enfileirar(pv.Op.DWELL, pv.p_dwell(ms))

    def disparar_parado(self, n: int, periodo_us: int, padrao: bytes) -> None:
        self.enfileirar(pv.Op.SPIT, pv.p_spit(n, periodo_us, padrao))

    def iniciar_job(self, job_id: int, total_swaths: int = 0) -> None:
        self.cancelamento_pedido = False
        self.enfileirar(pv.Op.JOB_BEGIN, pv.p_job_begin(job_id, total_swaths))

    def terminar_job(self) -> None:
        self.enfileirar(pv.Op.JOB_END)

    def definir_parametro(self, param_id: int, valor: int) -> None:
        self.enfileirar(pv.Op.SET_PARAM, pv.p_set_param(param_id, valor))

    def ler_parametro(self, param_id: int) -> None:
        self.enfileirar(pv.Op.GET_PARAM, pv.p_get_param(param_id))

    def salvar_parametros(self) -> None:
        self.enfileirar(pv.Op.SAVE_PARAMS)

    @property
    def tem_motors_off(self) -> bool:
        """MOTORS_OFF existe a partir do protocolo 1.3."""
        return self.hello_ack is not None and self.hello_ack.proto_minor >= 3

    def desligar_motores(self) -> None:
        """Solta os eixos para mover a mao. Todos perdem a referencia."""
        if not self.tem_motors_off:
            raise VersaoIncompativel(
                "desligar os motores exige o firmware com protocolo 1.3")
        self.enfileirar(pv.Op.MOTORS_OFF)

    def desligar_para_fechar(self, timeout_s: float = 1.0) -> bool:
        """
        Cabecote e motores desligados antes de fechar a porta.

        So em IDLE: em JOB quem desliga e o proprio ESP, que cancela o job ao
        perder o enlace (secao 5.2), e em FAULT os drivers ja estao
        desligados. Espera os comandos serem confirmados, no maximo
        `timeout_s`. Devolve True se tudo foi confirmado. Um firmware 1.4
        desliga sozinho de qualquer jeito, um segundo depois.
        """
        if self.status is None or self.status.state != int(pv.Estado.IDLE):
            return False
        self.cabecote(False)
        if self.tem_motors_off:
            self.desligar_motores()
        limite = self._agora() + timeout_s
        while self.pendentes and self._agora() < limite:
            for aviso in self.bombear():
                if aviso.tipo == Evento.ENLACE_PERDIDO:
                    return False
            if self._agora is time.perf_counter:
                time.sleep(0.002)
        return not self.pendentes

    def obter_parametros(self, ids, timeout_s: float = 2.0) -> dict[int, int]:
        """
        Le varios parametros e espera as respostas.

        Bloqueia, ao contrario do resto da classe. E de proposito: isto e
        chamado uma vez, antes de montar um job, para saber a aceleracao e a
        margem de recuo que o firmware esta usando. Esses valores decidem se a
        peca cabe, e chutar um padrao seria inventar numero de hardware.
        """
        pedidos = [int(i) for i in ids]
        for i in pedidos:
            self.ler_parametro(i)
        achados: dict[int, int] = {}
        limite = self._agora() + timeout_s
        while len(achados) < len(pedidos) and self._agora() < limite:
            for aviso in self.bombear():
                if aviso.tipo == Evento.PARAMETRO:
                    achados[int(aviso.dado.param_id)] = int(aviso.dado.value)
                elif aviso.tipo == Evento.ENLACE_PERDIDO:
                    raise EnlacePerdido(str(aviso.dado))
            if self._agora is time.perf_counter:
                time.sleep(0.002)
        faltando = [i for i in pedidos if i not in achados]
        if faltando:
            raise TimeoutError(
                "a maquina nao devolveu os parametros "
                + ", ".join(f"0x{i:04X}" for i in faltando))
        return achados

    def enviar_swath(self, swath_id: int, y_passos: int, x_start_passos: int,
                     pitch: int, colunas: bytes, fire_delay_us: int,
                     vel_passos_s: int, dir_menos_x: bool = False,
                     pho_cba: bool = False) -> int:
        """
        Enfileira uma swath inteira: BEGIN, os blocos de DATA e o END.

        Entre BEGIN e END so podem ir SWATH_DATA da mesma swath, PING e
        prioritarios (secao 7.3), e como a fila e servida em ordem, enfileirar
        os tres juntos garante isso.

        Devolve o numero de colunas.
        """
        if len(colunas) % pv.BYTES_POR_COLUNA:
            raise ValueError("os dados tem que ser multiplos de 16 bytes")
        n_cols = len(colunas) // pv.BYTES_POR_COLUNA
        maximo = self.hello_ack.max_cols if self.hello_ack else pv.MAX_COLS
        if not 1 <= n_cols <= maximo:
            raise ValueError(f"{n_cols} colunas fora de 1..{maximo}")

        self.enfileirar(pv.Op.SWATH_BEGIN, pv.p_swath_begin(
            swath_id, dir_menos_x, pho_cba, pitch, y_passos, x_start_passos,
            n_cols, fire_delay_us, vel_passos_s))

        passo = pv.COLUNAS_POR_SWATH_DATA * pv.BYTES_POR_COLUNA
        for i in range(0, len(colunas), passo):
            bloco = colunas[i:i + passo]
            self._fila.append((
                int(pv.Op.SWATH_DATA),
                pv.p_swath_data(swath_id, i // pv.BYTES_POR_COLUNA, bloco)))

        self.enfileirar(pv.Op.SWATH_END,
                        pv.p_swath_end(swath_id, pv.crc32(colunas)))
        return n_cols

    # ---- conveniencia ----------------------------------------------------

    @property
    def ocioso(self) -> bool:
        """Nada enfileirado, nada em voo, e o ESP diz estar em IDLE."""
        return (not self._fila and not self._em_voo
                and self.status is not None
                and self.status.state == pv.Estado.IDLE)

    @property
    def repetindo_prioritario(self) -> bool:
        """Um ESTOP ou CANCEL ainda esta sendo repetido (secao 4.4)."""
        return self._prioritario is not None

    @property
    def pendentes(self) -> int:
        return len(self._fila) + len(self._em_voo)
