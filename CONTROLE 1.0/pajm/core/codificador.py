"""
Bitmap da camada -> matriz "posicao W  ->  vetor de jateamento".

Esta e a fronteira com o firmware. Dois niveis, de proposito:

  1. MATRIZ LOGICA  (n_W, n_bicos) de booleanos, sem ambiguidade nenhuma.
  2. MATRIZ EMPACOTADA  (n_W, 16) de bytes, na ordem exata em que o firmware
     deve enviar pelo SPI.

ORDEM DO FLUXO DE BITS  [GO tabela 6.11 e secao 6.2.4]

  "The first bit shifted in will be the most significant bit in the shift
   register, that is bit 63, which will drive channel 64 or 128."

  Primeiro carregamento (nSS2 ativo, chip 2): CH128, CH127, ... CH66, CH65
  Segundo  carregamento (nSS1 ativo, chip 1): CH64,  CH63,  ... CH2,  CH1

  Juntando os dois, o fluxo de 128 bits e simplesmente OS CANAIS EM ORDEM
  DECRESCENTE, de 128 ate 1, com o bit mais significativo primeiro. Os bytes
  0..7 sao o chip 2 e os bytes 8..15 sao o chip 1.

O QUE AINDA DEPENDE DE DECISAO
  Apenas o sentido fisico: qual extremidade do cabecote e o canal 1 em
  relacao ao eixo Y da maquina. Enquanto isso nao for definido, empacotar()
  se RECUSA a produzir bytes, porque errar isso nao gera erro nenhum --
  gera peca espelhada.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..profile import Cabecote


class FaltaDefinicao(RuntimeError):
    def __init__(self, faltando: list[str]):
        self.faltando = faltando
        super().__init__(
            "Nao da para empacotar o vetor de jateamento: "
            + "; ".join(faltando))


# --------------------------------------------------------------------------
# Faixas
# --------------------------------------------------------------------------

def dividir_em_faixas(bitmap: np.ndarray, n_bicos: int) -> list[np.ndarray]:
    """
    Bitmap mais alto que o cabecote precisa de varias passadas em Y.
    Cada faixa tem exatamente n_bicos linhas; a ultima e completada com
    linhas vazias.
    """
    faixas = []
    for inicio in range(0, bitmap.shape[0], n_bicos):
        faixa = bitmap[inicio:inicio + n_bicos]
        if faixa.shape[0] < n_bicos:
            faixa = np.vstack([
                faixa,
                np.zeros((n_bicos - faixa.shape[0], bitmap.shape[1]), bool)])
        faixas.append(faixa)
    return faixas


# --------------------------------------------------------------------------
# Matriz logica
# --------------------------------------------------------------------------

@dataclass
class MatrizJateamento:
    """
    logica[w, i] = True -> na posicao W = w, o bico de indice i jateia.
    i e 0-based; o canal do cabecote e i + 1.
    """
    logica: np.ndarray            # (n_W, n_bicos) bool
    dpi_x: float
    dpi_y: float

    @property
    def n_w(self) -> int:
        return int(self.logica.shape[0])

    @property
    def n_bicos(self) -> int:
        return int(self.logica.shape[1])

    @property
    def passo_w_mm(self) -> float:
        return 25.4 / self.dpi_x

    @property
    def n_posicoes_ativas(self) -> int:
        return int(np.count_nonzero(self.logica.any(axis=1)))

    @property
    def n_gotas(self) -> int:
        return int(np.count_nonzero(self.logica))

    def comprimento_mm(self) -> float:
        return self.n_w * self.passo_w_mm


def montar_matriz(faixa: np.ndarray, dpi_x: float, dpi_y: float,
                  compensacao=None) -> MatrizJateamento:
    """
    faixa: (n_bicos, n_colunas) -> matriz (n_W, n_bicos).

    E AQUI que a compensacao de cisalhamento entra, e nao no fatiamento.

    O bico i esta fisicamente adiantado em X (cabecote inclinado) e dispara na
    fase (i mod 3). Para que a gota do bico i caia na coluna certa da peca, o
    dado dele precisa ser emitido `compensacao.colunas[i]` posicoes W depois.
    A matriz resultante e mais longa que a peca exatamente por esse atraso:
    no comeco da passada so os primeiros bicos tem dado, e no fim so os
    ultimos, que e o comportamento correto de um cabecote inclinado.

    Sem compensacao, a matriz e a transposta simples da faixa.
    """
    n_bicos, n_col = faixa.shape

    if compensacao is None:
        return MatrizJateamento(np.ascontiguousarray(faixa.T), dpi_x, dpi_y)

    desloc = np.asarray(compensacao.colunas, dtype=np.int64)
    if len(desloc) != n_bicos:
        raise ValueError(
            f"a compensacao tem {len(desloc)} bicos e a faixa tem {n_bicos}")

    n_w = n_col + int(desloc.max())
    logica = np.zeros((n_w, n_bicos), dtype=bool)
    for i in range(n_bicos):
        d = int(desloc[i])
        logica[d:d + n_col, i] = faixa[i]
    return MatrizJateamento(logica, dpi_x, dpi_y)


# --------------------------------------------------------------------------
# Empacotamento
# --------------------------------------------------------------------------

def camada_como_jateada(bitmap: np.ndarray, n_bicos: int,
                        compensacao) -> np.ndarray:
    """
    Camada inteira convertida para o dado que sai ao cabecote, faixa a faixa.

    Serve so para VISUALIZACAO. E o mesmo caminho de `montar_matriz`, aplicado
    a todas as faixas da camada e remontado num bitmap unico, para a interface
    poder mostrar lado a lado o que a peca e e o que o cabecote recebe.

    O resultado sai mais largo que a camada, pelo deslocamento maximo, e
    visivelmente cisalhado. Cada bloco de `n_bicos` linhas repete o mesmo
    padrao de cisalhamento, porque cada faixa usa os mesmos 128 bicos.
    """
    faixas = dividir_em_faixas(bitmap, n_bicos)
    matrizes = [montar_matriz(f, 1.0, 1.0, compensacao) for f in faixas]
    n_w = max(m.n_w for m in matrizes)

    saida = np.zeros((len(faixas) * n_bicos, n_w), dtype=bool)
    for k, m in enumerate(matrizes):
        saida[k * n_bicos:(k + 1) * n_bicos, :m.n_w] = m.logica.T
    return saida[:bitmap.shape[0]]


def empacotar(matriz: MatrizJateamento, cab: Cabecote) -> np.ndarray:
    """
    Matriz logica -> (n_W, 16) uint8, na ordem de transmissao do SPI:
    bytes 0..7 = chip 2 (canais 128..65), bytes 8..15 = chip 1 (canais 64..1).
    """
    if cab.sentido_indice not in (1, -1):
        raise FaltaDefinicao([
            "qual extremidade do cabecote e o canal 1 em relacao ao eixo Y "
            "(cabecote.sentido_indice)"])

    log = matriz.logica
    if cab.sentido_indice == -1:
        log = log[:, ::-1]

    # coluna i = canal i+1  ->  fluxo em ordem decrescente de canal
    fluxo = log[:, ::-1]
    return np.packbits(fluxo, axis=1, bitorder="big")


def faixa_de_bytes_do_chip(cab: Cabecote, chip: int) -> tuple[int, int]:
    """
    Intervalo de bytes de cada chip no vetor empacotado.
    chip 2 -> bytes 0..7 (canais 128..65);  chip 1 -> bytes 8..15 (64..1).
    """
    n = cab.bits_por_chip // 8
    return (0, n) if chip == 2 else (n, 2 * n)


def bytes_por_posicao(cab: Cabecote) -> int:
    return cab.n_bicos // 8


# --------------------------------------------------------------------------
# Autoteste
# --------------------------------------------------------------------------

def autoteste() -> list[str]:
    """
    Confere o empacotamento contra a tabela 6.11 do manual, bit a bit.
    Existe porque erro de empacotamento nao gera excecao: gera peca errada.
    """
    falhas: list[str] = []
    cab = Cabecote(sentido_indice=1)

    def vetor(indice0: int, c: Cabecote = cab) -> np.ndarray:
        log = np.zeros((1, 128), dtype=bool)
        log[0, indice0] = True
        return empacotar(MatrizJateamento(log, 360, 360), c)[0]

    # Canal 128 = indice 127 -> primeiro bit do fluxo -> byte 0, MSB.
    b = vetor(127)
    if b.shape != (16,):
        falhas.append(f"formato esperado (16,), veio {b.shape}")
    elif b[0] != 0x80 or b.sum() != 0x80:
        falhas.append(
            f"canal 128 deveria dar byte0=0x80 e o resto zero, veio {list(b)}")

    # Canal 1 = indice 0 -> ultimo bit do fluxo -> byte 15, LSB.
    b = vetor(0)
    if b[15] != 0x01 or b.sum() != 0x01:
        falhas.append(
            f"canal 1 deveria dar byte15=0x01 e o resto zero, veio {list(b)}")

    # Fronteira entre os chips: canal 65 e o ultimo do chip 2 (byte 7, LSB)
    # e canal 64 e o primeiro do chip 1 (byte 8, MSB).
    b = vetor(64)
    if b[7] != 0x01:
        falhas.append(
            f"canal 65 deveria ser o ultimo bit do chip 2 (byte7=0x01), "
            f"veio byte7=0x{b[7]:02X}")
    b = vetor(63)
    if b[8] != 0x80:
        falhas.append(
            f"canal 64 deveria ser o primeiro bit do chip 1 (byte8=0x80), "
            f"veio byte8=0x{b[8]:02X}")

    # Sentido invertido espelha: o indice 0 passa a ser o canal 128.
    cab_inv = Cabecote(sentido_indice=-1)
    b = vetor(0, cab_inv)
    if b[0] != 0x80:
        falhas.append(
            f"com sentido invertido o indice 0 deveria virar canal 128 "
            f"(byte0=0x80), veio byte0=0x{b[0]:02X}")

    # Sem o sentido definido, tem que recusar.
    try:
        empacotar(MatrizJateamento(np.zeros((1, 128), bool), 360, 360),
                  Cabecote(sentido_indice=None))
        falhas.append("empacotou sem o sentido definido -- deveria recusar")
    except FaltaDefinicao:
        pass

    # Fases: A=1,4,7...  B=2,5,8...  C=3,6,9...  (canais, 1-based)
    if [cab.nome_da_fase(cab.fase_do_bico(i)) for i in range(6)] \
            != ["A", "B", "C", "A", "B", "C"]:
        falhas.append("agrupamento de fases nao bate com a tabela 6.8")

    return falhas
