"""
Porta serial para o protocolo v1.1.

O ESP fala por UART0 atraves de um CH343P a 2 Mbaud, 8N1, sem controle de
fluxo por hardware (PROTOCOL.md secao 2).

CUIDADO COM DTR E RTS
Essas linhas do CH343P vao ao circuito de auto-reset do ESP, nao a controle
de fluxo. Abrir a porta com elas ativas REINICIA O ESP no meio de um job. Por
isso elas sao desativadas ANTES de abrir, conforme a secao 5.1, e mesmo assim
a sessao tolera um reset na abertura: o HELLO e repetido ate dez vezes.

O console do ESP-IDF sai pela USB nativa, nao por esta porta. Se aparecer
texto legivel no fluxo, e sinal de que o firmware esta logando na UART0 e vai
corromper os quadros.
"""
from __future__ import annotations

from . import protocolo as pv


class SerialIndisponivel(RuntimeError):
    pass


class TransporteSerial:
    def __init__(self, porta: str, baud: int = pv.BAUD,
                 timeout_s: float = 0.0, espiao=None):
        """
        `espiao(direcao, dados)` ve todos os bytes crus que passam, com
        direcao "tx" ou "rx".

        Existe porque, quando o primeiro HELLO falha numa bancada, a unica
        pergunta que importa e: chegou alguma coisa, e o que foi? Sem ver o
        fio, a diferenca entre "o ESP esta mudo", "veio lixo de boot" e "veio
        quadro com CRC errado" e indistinguivel.
        """
        try:
            import serial
        except ImportError as e:
            raise SerialIndisponivel(
                "pyserial nao esta instalado. Rode:\n"
                "  pip install pyserial") from e

        self.porta = porta
        self.baud = baud
        self.espiao = espiao
        self.bytes_tx = 0
        self.bytes_rx = 0
        s = serial.Serial()
        s.port = porta
        s.baudrate = baud
        s.bytesize = serial.EIGHTBITS
        s.parity = serial.PARITY_NONE
        s.stopbits = serial.STOPBITS_ONE
        s.timeout = timeout_s
        s.write_timeout = 2.0
        s.rtscts = False
        s.dsrdtr = False
        s.xonxoff = False
        # ANTES de abrir: nao acionar o auto-reset (secao 5.1).
        s.dtr = False
        s.rts = False
        s.open()
        try:
            s.dtr = False
            s.rts = False
        except Exception:
            pass
        s.reset_input_buffer()
        s.reset_output_buffer()
        self._s = s

    def escrever(self, dados: bytes) -> None:
        self._s.write(dados)
        self.bytes_tx += len(dados)
        if self.espiao:
            self.espiao("tx", dados)

    def ler(self) -> bytes:
        n = self._s.in_waiting
        dados = self._s.read(n) if n else b""
        if dados:
            self.bytes_rx += len(dados)
            if self.espiao:
                self.espiao("rx", dados)
        return dados

    def fechar(self) -> None:
        try:
            self._s.close()
        except Exception:
            pass

    @staticmethod
    def portas_disponiveis() -> list[tuple[str, str]]:
        try:
            from serial.tools import list_ports
        except ImportError:
            return []
        return [(p.device, p.description) for p in list_ports.comports()]

    @staticmethod
    def disponivel() -> bool:
        try:
            import serial  # noqa: F401
            return True
        except ImportError:
            return False
