"""
Protocolo PAJM v1.1, lado do PC.

Implementa `Firmware_Software/PROTOCOL.md` versao 1.1, que e o contrato
fechado entre o software de controle e o firmware do ESP32-S3.

Este modulo substitui `protocolo.py`, que era uma proposta escrita pelo lado
do software quando ainda nao havia documento do outro lado. O que sobreviveu
daquela proposta foi so o formato da coluna de 16 bytes, que por sorte ja
estava certo: a secao 6.1 do PROTOCOL.md descreve exatamente a mesma ordem.

ESCOPO DESTE ARQUIVO
Apenas enlace e apresentacao: enquadramento COBS, CRC, e a montagem e leitura
dos payloads. As camadas de transporte (janela Go-Back-N, ACK, retransmissao)
e de sessao (HELLO, heartbeat, timeout) ficam em `sessao.py`, porque sao
maquinas de estado com tempo e merecem arquivo proprio.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

# --------------------------------------------------------------------------
# Constantes (PROTOCOL.md secao 8.1)
# --------------------------------------------------------------------------

BAUD = 2_000_000
BAUD_ALTERNATIVO = 921_600          # fallback se 2 Mbaud nao passar no bring-up

MAX_PAYLOAD = 1028                  # SWATH_DATA com 64 colunas
MAIOR_QUADRO_NO_FIO = 1038          # COBS + delimitador
WINDOW = 8
RTO_S = 0.200
MAX_RETRIES = 5
PING_PERIOD_S = 0.200
STATUS_PERIOD_S = 0.100
LINK_TIMEOUT_S = 1.000
HELLO_RETRY_S = 0.250
HELLO_TRIES = 10
PRIORITY_REPEAT_S = 0.100
PRIORITY_MAX_S = 1.000
SWATH_SLOTS = 4
MAX_COLS = 20000
QUEUE_SLOTS = 32

PROTO_MAJOR = 1
PROTO_MINOR = 4

COLUNAS_POR_SWATH_DATA = 64         # maximo que cabe em MAX_PAYLOAD
BYTES_POR_COLUNA = 16

DELIMITADOR = 0x00


class Op(IntEnum):
    """Opcodes (secao 7.2)."""
    # PC -> ESP
    HELLO = 0x01
    PING = 0x02
    CLEAR_FAULT = 0x03
    ESTOP = 0x08
    CANCEL = 0x09
    JOB_BEGIN = 0x10
    JOB_END = 0x11
    HOME = 0x12
    HEAD_POWER = 0x13
    MOVE = 0x14
    DWELL = 0x15
    SPIT = 0x16
    SWATH_BEGIN = 0x30
    SWATH_DATA = 0x31
    SWATH_END = 0x32
    JOG = 0x40
    SET_PARAM = 0x41
    GET_PARAM = 0x42
    SAVE_PARAMS = 0x43
    MOTORS_OFF = 0x44                   # v1.3
    # ESP -> PC
    HELLO_ACK = 0x80
    ACK = 0x81
    NACK = 0x82
    STATUS = 0x83
    ERR = 0x84
    PARAM_VALUE = 0x85
    LOG = 0x86


#: Quadros tratados fora da janela, sem ACK, com seq ignorado (secao 4.1).
PRIORITARIOS = frozenset({Op.ESTOP, Op.CANCEL})

#: Mensagens de trabalho e de dados: erro nelas aborta o job (secao 7.1).
DE_TRABALHO = frozenset(
    o for o in Op if 0x10 <= int(o) <= 0x3F)


class Estado(IntEnum):
    IDLE = 0
    JOB = 1
    STOPPING = 2
    FAULT = 3


class Erro(IntEnum):
    """ERR.code (secao 7.9)."""
    BAD_STATE = 0x01
    BAD_LENGTH = 0x02
    UNKNOWN_OPCODE = 0x03
    BAD_PARAM = 0x04
    NOT_HOMED = 0x05
    OUT_OF_TRAVEL = 0x06
    FIRE_TOO_FAST = 0x07
    NO_SWATH_SLOT = 0x08
    NO_QUEUE_SLOT = 0x09
    SWATH_SEQUENCE = 0x0A
    SWATH_INCOMPLETE = 0x0B
    SWATH_CRC = 0x0C
    HEAD_OFF = 0x0D
    LINK_LOST = 0x0E
    CANCELLED = 0x0F


class Falha(IntEnum):
    """STATUS.fault (secao 7.9)."""
    ESTOP = 0x01
    HEAD_NFAULT = 0x02
    READY_TIMEOUT = 0x03
    FIRE_OVERRUN = 0x04
    ENDSTOP = 0x05
    INTERNAL = 0x06


class Param(IntEnum):
    """param_id para SET_PARAM e GET_PARAM (secao 8.2)."""
    ACCEL_X = 0x0001
    ACCEL_Y = 0x0002
    ACCEL_Z = 0x0003
    VMAX_X = 0x0011
    VMAX_Y = 0x0012
    VMAX_Z = 0x0013
    RUNUP_MARGIN = 0x0020
    CHECK_READY = 0x0030
    LOG_ENABLE = 0x0031
    MIN_FIRE_PERIOD = 0x0032


EIXO_X, EIXO_Y, EIXO_Z = 0, 1, 2


def recuo_de_aceleracao(speed_passos_s: int, accel_passos_s2: int,
                        runup_margin: int) -> int:
    """
    Passos que o eixo X precisa ANTES e DEPOIS da swath so para acelerar e
    frear (secao 7.5):

        d = ceil(speed**2 / (2 * ACCEL_X)) + RUNUP_MARGIN

    O firmware recusa a swath com OUT_OF_TRAVEL se `x_start - d` ou
    `x_end + d` cairem fora do curso. Quem posiciona a peca precisa reservar
    esse espaco: a 5000 passos/s com ACCEL de 40000, sao 353 passos, ou seja
    quase 9 mm de cada lado que a peca nao pode ocupar.

    A formula vive aqui, e nao no simulador nem no envio, para os dois usarem
    exatamente a mesma conta.
    """
    if accel_passos_s2 <= 0:
        raise ValueError("a aceleracao tem que ser positiva")
    freada = -(-(speed_passos_s * speed_passos_s) // (2 * accel_passos_s2))
    return int(freada + runup_margin)


def nome_do_erro(codigo: int) -> str:
    try:
        return Erro(codigo).name
    except ValueError:
        return f"desconhecido(0x{codigo:02X})"


def nome_da_falha(codigo: int) -> str:
    try:
        return Falha(codigo).name
    except ValueError:
        return f"desconhecida(0x{codigo:02X})"


# --------------------------------------------------------------------------
# CRC
# --------------------------------------------------------------------------

def _tabela_crc16() -> list[int]:
    tab = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        tab.append(c)
    return tab


_CRC16 = _tabela_crc16()


def crc16(dados: bytes) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, sem reflexao, sem XOR."""
    c = 0xFFFF
    for b in dados:
        c = ((c << 8) & 0xFFFF) ^ _CRC16[((c >> 8) ^ b) & 0xFF]
    return c


def crc32(dados: bytes) -> int:
    """CRC-32 IEEE/zlib, usado no SWATH_END."""
    return zlib.crc32(dados) & 0xFFFFFFFF


# --------------------------------------------------------------------------
# COBS (secao 3.1)
# --------------------------------------------------------------------------

def cobs_codifica(dados: bytes) -> bytes:
    """
    Reescreve o quadro para nao conter nenhum 0x00, que passa a marcar
    exclusivamente o fim do quadro.

    E a alternativa ao escape byte a byte que eu tinha proposto antes. Faz o
    mesmo trabalho com overhead menor e limitado: no maximo 1 byte a cada 254,
    contra os ~2% variaveis do escape.
    """
    saida = bytearray()
    i_codigo = len(saida)
    saida.append(0)                 # reservado para o codigo do bloco
    codigo = 1
    for b in dados:
        if b != 0:
            saida.append(b)
            codigo += 1
            if codigo == 0xFF:
                saida[i_codigo] = codigo
                i_codigo = len(saida)
                saida.append(0)
                codigo = 1
        else:
            saida[i_codigo] = codigo
            i_codigo = len(saida)
            saida.append(0)
            codigo = 1
    saida[i_codigo] = codigo
    return bytes(saida)


def cobs_decodifica(dados: bytes) -> bytes:
    """Inverso de `cobs_codifica`. Levanta ValueError se o bloco for invalido."""
    saida = bytearray()
    i = 0
    n = len(dados)
    while i < n:
        codigo = dados[i]
        if codigo == 0:
            raise ValueError("byte zero dentro de um bloco COBS")
        i += 1
        fim = i + codigo - 1
        if fim > n:
            raise ValueError("bloco COBS ultrapassa o fim do quadro")
        saida += dados[i:fim]
        i = fim
        if codigo != 0xFF and i < n:
            saida.append(0)
    return bytes(saida)


# --------------------------------------------------------------------------
# Quadro
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Quadro:
    """quadro bruto: type | seq | payload | crc16  (secao 3.1)."""
    tipo: int
    seq: int
    payload: bytes = b""

    def bruto(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(
                f"payload de {len(self.payload)} bytes passa de {MAX_PAYLOAD}")
        corpo = bytes([int(self.tipo) & 0xFF, self.seq & 0xFF]) + self.payload
        return corpo + struct.pack("<H", crc16(corpo))

    def no_fio(self) -> bytes:
        return cobs_codifica(self.bruto()) + bytes([DELIMITADOR])

    @property
    def nome(self) -> str:
        try:
            return Op(self.tipo).name
        except ValueError:
            return f"0x{self.tipo:02X}"


class Decodificador:
    """
    Acumula bytes ate achar 0x00 e entrega os quadros validos.

    Descarta em silencio, contando em `frames_dropped` (secao 3.2):
      - COBS invalido
      - quadro bruto com menos de 4 bytes ou mais de MAX_PAYLOAD + 4
      - CRC que nao confere
      - acumulo acima de MAIOR_QUADRO_NO_FIO sem encontrar delimitador

    O lixo que a ROM e o bootloader do ESP cospem na UART durante o boot cai
    justamente nessas regras, que e o motivo de elas existirem.
    """

    def __init__(self):
        self._buf = bytearray()
        self.frames_dropped = 0
        self.descartando = False

    def alimentar(self, dados: bytes) -> list[Quadro]:
        saida: list[Quadro] = []
        for b in dados:
            if b == DELIMITADOR:
                bloco = bytes(self._buf)
                self._buf.clear()
                if self.descartando:
                    self.descartando = False
                    self.frames_dropped += 1
                    continue
                if not bloco:
                    continue            # 0x00 isolado: quadro vazio, ignorado
                q = self._montar(bloco)
                if q is None:
                    self.frames_dropped += 1
                else:
                    saida.append(q)
            else:
                if self.descartando:
                    continue
                self._buf.append(b)
                if len(self._buf) > MAIOR_QUADRO_NO_FIO:
                    # Sem delimitador a essa altura, nao ha quadro valido aqui.
                    self._buf.clear()
                    self.descartando = True
        return saida

    @staticmethod
    def _montar(bloco: bytes) -> Quadro | None:
        try:
            bruto = cobs_decodifica(bloco)
        except ValueError:
            return None
        if len(bruto) < 4 or len(bruto) > MAX_PAYLOAD + 4:
            return None
        corpo, recebido = bruto[:-2], struct.unpack("<H", bruto[-2:])[0]
        if crc16(corpo) != recebido:
            return None
        return Quadro(corpo[0], corpo[1], corpo[2:])


# --------------------------------------------------------------------------
# Payloads PC -> ESP (secao 7.3)
# --------------------------------------------------------------------------

def p_hello(major: int = PROTO_MAJOR, minor: int = PROTO_MINOR) -> bytes:
    return struct.pack("<BB", major, minor)


def p_job_begin(job_id: int, total_swaths: int = 0) -> bytes:
    return struct.pack("<HH", job_id, total_swaths)


def p_home(mascara_eixos: int) -> bytes:
    return struct.pack("<B", mascara_eixos & 0x07)


def p_head_power(ligado: bool) -> bytes:
    return struct.pack("<B", 1 if ligado else 0)


def p_move(eixo: int, pos_passos: int, vel_passos_s: int) -> bytes:
    return struct.pack("<BBHiI", eixo, 0, 0, int(pos_passos),
                       int(vel_passos_s))


def p_dwell(ms: int) -> bytes:
    return struct.pack("<I", int(ms))


def coluna_de_canais(canais) -> bytes:
    """
    Coluna de 16 bytes (secao 6.1) com os canais dados ligados (1 a 128).

    O bit j (7 = MSB) do byte b aciona o canal 128 - 8b - (7 - j). Serve
    para padroes fixos, como o SPIT de teste de bicos: CH128 e CH1 dao
    byte 0 = 0x80 e byte 15 = 0x01.
    """
    coluna = bytearray(BYTES_POR_COLUNA)
    for ch in canais:
        if not 1 <= ch <= 8 * BYTES_POR_COLUNA:
            raise ValueError(f"canal {ch} fora de 1 a 128")
        k = 8 * BYTES_POR_COLUNA - ch
        coluna[k // 8] |= 0x80 >> (k % 8)
    return bytes(coluna)


def p_spit(n_disparos: int, periodo_us: int, padrao: bytes) -> bytes:
    if len(padrao) != BYTES_POR_COLUNA:
        raise ValueError("o padrao do SPIT tem 16 bytes")
    return struct.pack("<HH", int(n_disparos), int(periodo_us)) + padrao


def p_swath_begin(swath_id: int, dir_menos_x: bool, pho_cba: bool,
                  pitch: int, y: int, x_start: int, n_cols: int,
                  fire_delay_us: int, speed: int) -> bytes:
    """
    SWATH_BEGIN, 20 bytes (secao 7.3).

    Note que NAO ha campo de Z: a altura da camada e comandada por MOVE entre
    as swaths. Isso difere da minha proposta antiga, que carregava Z em cada
    faixa, e e mais simples: uma camada inteira compartilha o mesmo Z.
    """
    flags = (1 if dir_menos_x else 0) | (2 if pho_cba else 0)
    return struct.pack("<HBBiiHHI", int(swath_id), flags, int(pitch),
                       int(y), int(x_start), int(n_cols),
                       int(fire_delay_us), int(speed))


def p_swath_data(swath_id: int, col_start: int, colunas: bytes) -> bytes:
    if not colunas or len(colunas) % BYTES_POR_COLUNA:
        raise ValueError("os dados tem que ser um multiplo de 16 bytes")
    n = len(colunas) // BYTES_POR_COLUNA
    if n > COLUNAS_POR_SWATH_DATA:
        raise ValueError(
            f"{n} colunas passam do maximo de {COLUNAS_POR_SWATH_DATA}")
    return struct.pack("<HH", int(swath_id), int(col_start)) + colunas


def p_swath_end(swath_id: int, crc_32: int) -> bytes:
    return struct.pack("<HHI", int(swath_id), 0, int(crc_32) & 0xFFFFFFFF)


def p_jog(eixo: int, delta_passos: int, vel_passos_s: int) -> bytes:
    return struct.pack("<BBHiI", eixo, 0, 0, int(delta_passos),
                       int(vel_passos_s))


def p_set_param(param_id: int, valor: int) -> bytes:
    return struct.pack("<HHi", int(param_id), 0, int(valor))


def p_get_param(param_id: int) -> bytes:
    return struct.pack("<H", int(param_id))


# --------------------------------------------------------------------------
# Payloads ESP -> PC (secao 7.4)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class HelloAck:
    proto_major: int
    proto_minor: int
    fw_major: int
    fw_minor: int
    fw_patch: int
    window: int
    swath_slots: int
    queue_slots: int
    max_payload: int
    max_cols: int
    min_fire_period_us: int
    steps_per_m: tuple[int, int, int]
    travel: tuple[int, int, int]

    FORMATO = "<BBBBBBBBHHHHIIIiii"

    @classmethod
    def de_bytes(cls, b: bytes) -> "HelloAck":
        v = struct.unpack(cls.FORMATO, b)
        return cls(v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7],
                   v[8], v[9], v[10],
                   (v[12], v[13], v[14]), (v[15], v[16], v[17]))

    def passos_por_mm(self, eixo: int) -> float:
        return self.steps_per_m[eixo] / 1000.0

    @property
    def versao_firmware(self) -> str:
        return f"{self.fw_major}.{self.fw_minor}.{self.fw_patch}"


TAMANHO_HELLO_ACK = struct.calcsize(HelloAck.FORMATO)


@dataclass(frozen=True)
class Ack:
    ack_seq: int
    swath_free: int
    queue_free: int

    @classmethod
    def de_bytes(cls, b: bytes) -> "Ack":
        v = struct.unpack("<BBBB", b)
        return cls(v[0], v[1], v[2])


@dataclass(frozen=True)
class Nack:
    expected_seq: int
    reason: int

    @classmethod
    def de_bytes(cls, b: bytes) -> "Nack":
        return cls(*struct.unpack("<BB", b))


@dataclass(frozen=True)
class Status:
    state: int
    flags: int
    job_id: int
    swath_id: int
    swath_free: int
    queue_free: int
    pos: tuple[int, int, int]
    last_error: int
    fault: int
    frames_dropped: int

    FORMATO = "<BBHHBBiiiBBH"

    @classmethod
    def de_bytes(cls, b: bytes) -> "Status":
        v = struct.unpack(cls.FORMATO, b)
        return cls(v[0], v[1], v[2], v[3], v[4], v[5],
                   (v[6], v[7], v[8]), v[9], v[10], v[11])

    def bytes(self) -> bytes:
        return struct.pack(self.FORMATO, self.state, self.flags, self.job_id,
                           self.swath_id, self.swath_free, self.queue_free,
                           self.pos[0], self.pos[1], self.pos[2],
                           self.last_error, self.fault, self.frames_dropped)

    # flags (secao 7.4)
    @property
    def referenciado_x(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def referenciado_y(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def referenciado_z(self) -> bool:
        return bool(self.flags & 0x04)

    @property
    def vpp_ligado(self) -> bool:
        return bool(self.flags & 0x08)

    @property
    def nfault(self) -> bool:
        return bool(self.flags & 0x10)

    @property
    def swath_em_execucao(self) -> bool:
        return bool(self.flags & 0x20)

    @property
    def nome_estado(self) -> str:
        try:
            return Estado(self.state).name
        except ValueError:
            return f"desconhecido({self.state})"


TAMANHO_STATUS = struct.calcsize(Status.FORMATO)


@dataclass(frozen=True)
class Err:
    seq: int
    code: int
    detail: int

    @classmethod
    def de_bytes(cls, b: bytes) -> "Err":
        v = struct.unpack("<BBHi", b)
        return cls(v[0], v[1], v[3])

    def __str__(self) -> str:
        return (f"{nome_do_erro(self.code)} no quadro seq={self.seq} "
                f"(detalhe {self.detail})")


@dataclass(frozen=True)
class ParamValue:
    param_id: int
    value: int

    @classmethod
    def de_bytes(cls, b: bytes) -> "ParamValue":
        v = struct.unpack("<HHi", b)
        return cls(v[0], v[2])


#: Tamanhos exatos de payload por opcode, para recusar quadro malformado.
TAMANHOS = {
    Op.HELLO: 2, Op.PING: 0, Op.CLEAR_FAULT: 0, Op.ESTOP: 0, Op.CANCEL: 0,
    Op.JOB_BEGIN: 4, Op.JOB_END: 0, Op.HOME: 1, Op.HEAD_POWER: 1,
    Op.MOVE: 12, Op.DWELL: 4, Op.SPIT: 20, Op.SWATH_BEGIN: 20,
    Op.SWATH_END: 8, Op.JOG: 12, Op.SET_PARAM: 8, Op.GET_PARAM: 2,
    Op.SAVE_PARAMS: 0, Op.MOTORS_OFF: 0,
    Op.HELLO_ACK: TAMANHO_HELLO_ACK, Op.ACK: 4, Op.NACK: 2,
    Op.STATUS: TAMANHO_STATUS, Op.ERR: 8, Op.PARAM_VALUE: 8,
}
