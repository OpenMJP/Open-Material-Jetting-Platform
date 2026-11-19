"""
Fatiamento: malha 3D -> bitmap de cada camada.

ESTRATEGIA
Nao stitchamos contornos. Para cada camada pegamos os segmentos brutos da
interseccao triangulo-plano e preenchemos por linha de varredura com a regra
par-impar. Consequencias:

  - nao precisa identificar quais contornos sao furos: par-impar resolve;
  - tolera malha nao estanque e nao manifold, degradando em vez de quebrar;
  - cada linha de varredura E um bico do cabecote, entao a grade do raster
    ja nasce alinhada com o hardware.

GRADE
  passo em X = 25.4 / dpi_x   (define a frequencia de disparo)
  passo em Y = 25.4 / dpi_y   (imposto pelo passo efetivo dos bicos)

O pixel NAO e quadrado. Toda conversao mm <-> pixel passa por GradeRaster,
que e o unico lugar do programa que conhece esses dois fatores.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# --------------------------------------------------------------------------
# Grade
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class GradeRaster:
    """Mapeia milimetros para colunas e linhas do bitmap."""
    x0_mm: float
    y0_mm: float
    dpi_x: float
    dpi_y: float
    n_colunas: int
    n_linhas: int

    @property
    def passo_x_mm(self) -> float:
        return 25.4 / self.dpi_x

    @property
    def passo_y_mm(self) -> float:
        return 25.4 / self.dpi_y

    @property
    def largura_mm(self) -> float:
        return self.n_colunas * self.passo_x_mm

    @property
    def altura_mm(self) -> float:
        return self.n_linhas * self.passo_y_mm

    @classmethod
    def para_area(cls, x_min, y_min, x_max, y_max, dpi_x, dpi_y,
                  margem_colunas: int = 0) -> "GradeRaster":
        px = 25.4 / dpi_x
        py = 25.4 / dpi_y
        n_col = int(np.ceil((x_max - x_min) / px)) + 1 + margem_colunas
        n_lin = int(np.ceil((y_max - y_min) / py)) + 1
        return cls(float(x_min), float(y_min), float(dpi_x), float(dpi_y),
                   int(n_col), int(n_lin))


# --------------------------------------------------------------------------
# Alturas de camada
# --------------------------------------------------------------------------

def alturas_camadas(z_min: float, z_max: float,
                    espessura_mm: float) -> np.ndarray:
    """
    Planos de corte. Cortamos no MEIO de cada camada, nao na base: uma camada
    que vai de z ate z+e e representada pela seccao em z + e/2. Cortar na base
    faz superficies horizontais caírem exatamente sobre o plano de corte, que e
    o caso numericamente instavel.
    """
    if espessura_mm <= 0:
        raise ValueError("Espessura de camada deve ser positiva.")
    altura = z_max - z_min
    if altura <= 0:
        return np.empty(0)
    n = int(np.ceil(altura / espessura_mm))
    return z_min + (np.arange(n) + 0.5) * espessura_mm


# --------------------------------------------------------------------------
# Interseccao triangulo-plano
# --------------------------------------------------------------------------

def segmentos_da_camada(vertices: np.ndarray, faces: np.ndarray,
                        z: float) -> np.ndarray:
    """
    Devolve (M, 4) com [x1, y1, x2, y2] dos segmentos da seccao em z.

    O plano e deslocado por um epsilon quando coincide com a cota de algum
    vertice. Com isso nenhum vertice fica exatamente sobre o plano, cada
    triangulo cortado gera exatamente 2 cruzamentos, e o par-impar fecha.
    """
    tris = vertices[faces]                       # (F, 3, 3)
    zs = tris[:, :, 2]

    # Afasta o plano de qualquer vertice coincidente.
    escala = float(np.ptp(vertices[:, 2])) or 1.0
    eps = escala * 1e-9
    for _ in range(8):
        if not np.any(np.abs(zs - z) < eps):
            break
        z += eps * 4.0

    abaixo = zs < z
    n_abaixo = abaixo.sum(axis=1)
    cortados = (n_abaixo == 1) | (n_abaixo == 2)
    if not np.any(cortados):
        return np.empty((0, 4))

    tris = tris[cortados]
    abaixo = abaixo[cortados]

    xs_int = np.empty((len(tris), 3))
    ys_int = np.empty((len(tris), 3))
    cruza = np.empty((len(tris), 3), dtype=bool)

    for k, (i, j) in enumerate(((0, 1), (1, 2), (2, 0))):
        pi, pj = tris[:, i, :], tris[:, j, :]
        cruza[:, k] = abaixo[:, i] != abaixo[:, j]
        dz = pj[:, 2] - pi[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(dz != 0.0, (z - pi[:, 2]) / dz, 0.0)
        xs_int[:, k] = pi[:, 0] + t * (pj[:, 0] - pi[:, 0])
        ys_int[:, k] = pi[:, 1] + t * (pj[:, 1] - pi[:, 1])

    # As duas arestas que cruzam, por triangulo.
    ordem = np.argsort(~cruza, axis=1, kind="stable")[:, :2]
    xs2 = np.take_along_axis(xs_int, ordem, axis=1)
    ys2 = np.take_along_axis(ys_int, ordem, axis=1)

    return np.column_stack([xs2[:, 0], ys2[:, 0], xs2[:, 1], ys2[:, 1]])


# --------------------------------------------------------------------------
# Rasterizacao por linha de varredura
# --------------------------------------------------------------------------

def rasterizar(segmentos: np.ndarray, grade: GradeRaster,
               deslocamento_colunas: np.ndarray | None = None) -> np.ndarray:
    """
    Segmentos -> bitmap booleano (n_linhas, n_colunas).

    deslocamento_colunas: vetor de tamanho n_linhas com o deslocamento em
    colunas a aplicar em cada linha. E por aqui que entra a compensacao do
    cisalhamento causado pelo cabecote montado em angulo.
    """
    bitmap = np.zeros((grade.n_linhas, grade.n_colunas), dtype=bool)
    if len(segmentos) == 0:
        return bitmap

    x1, y1, x2, y2 = segmentos.T
    py, px = grade.passo_y_mm, grade.passo_x_mm

    y_min = np.minimum(y1, y2)
    y_max = np.maximum(y1, y2)

    # Linhas de varredura cruzadas por cada segmento, intervalo [min, max).
    j0 = np.ceil((y_min - grade.y0_mm) / py).astype(np.int64)
    j1 = np.ceil((y_max - grade.y0_mm) / py).astype(np.int64)
    np.clip(j0, 0, grade.n_linhas, out=j0)
    np.clip(j1, 0, grade.n_linhas, out=j1)

    contagem = j1 - j0
    np.clip(contagem, 0, None, out=contagem)
    total = int(contagem.sum())
    if total == 0:
        return bitmap

    seg = np.repeat(np.arange(len(segmentos)), contagem)
    inicio_por_seg = np.repeat(np.cumsum(contagem) - contagem, contagem)
    desloc = np.arange(total) - inicio_por_seg
    j = j0[seg] + desloc

    y_linha = grade.y0_mm + j * py
    dy = (y2 - y1)[seg]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(dy != 0.0, (y_linha - y1[seg]) / dy, 0.0)
    xc = x1[seg] + t * (x2 - x1)[seg]

    # Ordena por linha e depois por X: os cruzamentos ficam aos pares.
    ordem = np.lexsort((xc, j))
    j = j[ordem]
    xc = xc[ordem]

    # Numero impar de cruzamentos numa linha so acontece com malha aberta
    # cortada exatamente ali. Descartamos o ultimo cruzamento da linha, e nao
    # a linha inteira: assim perde-se um vao, nao metade da peca.
    por_linha = np.bincount(j, minlength=grade.n_linhas)
    impares = np.flatnonzero(por_linha & 1)
    if len(impares):
        ultimo_da_linha = np.cumsum(por_linha) - 1
        manter = np.ones(len(j), dtype=bool)
        manter[ultimo_da_linha[impares]] = False
        j = j[manter]
        xc = xc[manter]
        if len(j) == 0:
            return bitmap

    x_ini, x_fim = xc[0::2], xc[1::2]
    linhas = j[0::2]

    c0 = np.ceil((x_ini - grade.x0_mm) / px).astype(np.int64)
    c1 = np.ceil((x_fim - grade.x0_mm) / px).astype(np.int64)

    if deslocamento_colunas is not None:
        d = deslocamento_colunas[linhas]
        c0 = c0 + d
        c1 = c1 + d

    np.clip(c0, 0, grade.n_colunas, out=c0)
    np.clip(c1, 0, grade.n_colunas, out=c1)

    validos = c1 > c0
    linhas, c0, c1 = linhas[validos], c0[validos], c1[validos]
    if len(linhas) == 0:
        return bitmap

    # Preenchimento por array de diferencas: O(spans + pixels).
    dif = np.zeros((grade.n_linhas, grade.n_colunas + 1), dtype=np.int32)
    np.add.at(dif, (linhas, c0), 1)
    np.add.at(dif, (linhas, c1), -1)
    return np.cumsum(dif, axis=1)[:, :grade.n_colunas] > 0


# --------------------------------------------------------------------------
# Compensacao do cabecote inclinado
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CompensacaoBicos:
    """Deslocamento em X de cada bico, e o quanto sobrou sem compensar."""
    colunas: np.ndarray           # (n_bicos,) inteiro, sempre >= 0
    residuo_colunas: float        # maior sobra, em fracao de coluna
    cisalhamento_mm: float        # contribuicao do angulo
    fase_mm: float                # contribuicao das 3 fases
    dpi_x: float

    @property
    def residuo_um(self) -> float:
        """Sobra do arredondamento em micrometros."""
        return self.residuo_colunas * (25.4 / self.dpi_x) * 1000.0


def compensar_bicos(cab, dpi_x: float,
                    velocidade_mm_s: float) -> CompensacaoBicos:
    """
    Deslocamento em X de cada bico, somando as DUAS causas antes de arredondar.

    1. INCLINACAO DO CABECOTE  [DS]
       O bico i fica adiantado em i * passo * cos(angulo). Com os angulos de
       projeto do fabricante isso da 5.5 mm (variante 80 pL) a 14.9 mm
       (variante 40 pL) entre o primeiro e o ultimo bico. E o termo dominante.

    2. DISPARO EM TRES FASES  [GO tabela 6.8 e 6.10]
       Parede compartilhada impede disparar canais adjacentes juntos, entao
       os bicos vao em 3 ciclos A-B-C separados por um atraso fixo de 60 us.
       Como o carro anda, o ciclo B cai v*60us adiante do A, e o C, 2*v*60us.
       E o que o manual chama de "saw tooth effect" quando nao se compensa.

    Somar antes de arredondar importa: o termo 1 e grande e o termo 2 e
    pequeno; arredondar separado jogaria o termo 2 fora quase sempre.

    O residuo devolvido e a maior sobra depois do arredondamento. Se ficar
    proximo de 0.5, a grade em X esta no limite do que da para representar
    com colunas inteiras -- sinal de que a velocidade ou o DPI estao altos
    demais para a compensacao ser fiel.
    """
    px = 25.4 / dpi_x
    n = cab.n_bicos

    avanco = cab.avanco_x_por_bico_mm
    if avanco is None:
        raise ValueError(
            "Sem o angulo do cabecote nao da para compensar o cisalhamento. "
            "Defina a variante ou meca o angulo de montagem.")

    idx = np.arange(n)
    desloc_cis_mm = idx * avanco
    fase = idx % cab.n_fases
    desloc_fase_mm = fase * velocidade_mm_s * cab.atraso_ciclo_s

    total_mm = desloc_cis_mm + desloc_fase_mm
    em_colunas = total_mm / px
    inteiro = np.rint(em_colunas).astype(np.int64)
    residuo = float(np.abs(em_colunas - inteiro).max())

    return CompensacaoBicos(
        colunas=inteiro - inteiro.min(),
        residuo_colunas=residuo,
        cisalhamento_mm=float(desloc_cis_mm.max()),
        fase_mm=float(desloc_fase_mm.max()),
        dpi_x=float(dpi_x),
    )
