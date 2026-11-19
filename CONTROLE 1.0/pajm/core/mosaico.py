"""
Camadas lado a lado num plano so.

POR QUE ISTO EXISTE
Sem cura UV, a tinta solvente nao solidifica a ponto de aguentar outra camada
por cima. Entao, para validar que o fatiamento e a geracao de camadas estao
corretos antes de existir cura, jateia-se o que SERIA a camada 1 numa posicao,
desloca-se, jateia-se a camada 2 ao lado, e assim por diante, tudo no mesmo Z.

Nao e impressao 3D: e uma folha de contato das camadas, para conferir a olho
que a sequencia de secoes faz sentido.

ESCALA
Uma peca real tem milhares de camadas e nenhuma mesa comporta isso. Por isso
se escolhe um INTERVALO de camadas, e mesmo um intervalo pode nao caber: a
funcao divide o que sobra em chapas numeradas em vez de truncar em silencio.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .fatiador import GradeRaster


@dataclass(frozen=True)
class Celula:
    """Uma camada posicionada na chapa."""
    indice_camada: int
    z_mm: float
    linha: int
    coluna: int
    x0_mm: float
    y0_mm: float


@dataclass
class Chapa:
    """Uma mesa cheia de camadas."""
    numero: int
    bitmap: np.ndarray
    grade: GradeRaster
    celulas: list[Celula]

    @property
    def n_camadas(self) -> int:
        return len(self.celulas)


@dataclass
class PlanoMosaico:
    """O arranjo calculado, antes de montar os bitmaps."""
    indices: list[int]
    n_colunas: int
    n_linhas: int
    por_chapa: int
    n_chapas: int
    largura_celula_mm: float
    altura_celula_mm: float
    espacamento_mm: float
    mesa_x_mm: float
    mesa_y_mm: float
    margem_mm: float
    erros: list[str]
    avisos: list[str]

    @property
    def viavel(self) -> bool:
        return not self.erros


def selecionar_camadas(n_total: int, primeira: int, ultima: int,
                       passo: int = 1) -> list[int]:
    """
    Intervalo de camadas, 1-based e inclusivo nas duas pontas, como o usuario
    le na interface. Devolve indices 0-based.
    """
    primeira = max(1, min(primeira, n_total))
    ultima = max(primeira, min(ultima, n_total))
    passo = max(1, passo)
    return list(range(primeira - 1, ultima, passo))


def planejar(camadas, indices: list[int], mesa_x_mm: float | None,
             mesa_y_mm: float | None, espacamento_mm: float = 2.0,
             margem_mm: float = 10.0,
             folga_x_mm: float = 0.0) -> PlanoMosaico:
    """
    Calcula o arranjo sem montar bitmap nenhum.

    `folga_x_mm` e o espaco que precisa sobrar a direita da chapa e que nao
    pode ser ocupado por celulas: o cisalhamento do cabecote inclinado estende
    o dado de cada swath para alem da peca (14,9 mm na variante /40), e o eixo
    ainda precisa de recuo para frear. Sem reservar isso, a folha de contato
    preenche a mesa inteira e o firmware recusa a ultima coluna por falta de
    curso.
    """
    erros: list[str] = []
    avisos: list[str] = []

    if not mesa_x_mm or not mesa_y_mm:
        erros.append(
            "Sem o tamanho da mesa nao da para dispor as camadas. "
            "Defina a area util na pagina Maquina.")
    if not indices:
        erros.append("Nenhuma camada selecionada no intervalo.")

    g = camadas.grade
    lc = g.largura_mm
    ac = g.altura_mm

    n_col = n_lin = por_chapa = n_chapas = 0
    if not erros:
        util_x = mesa_x_mm - 2 * margem_mm - folga_x_mm
        util_y = mesa_y_mm - 2 * margem_mm
        passo_x = lc + espacamento_mm
        passo_y = ac + espacamento_mm

        # Cabe k celulas se k*celula + (k-1)*espacamento <= util
        n_col = int((util_x + espacamento_mm) // passo_x)
        n_lin = int((util_y + espacamento_mm) // passo_y)

        if n_col < 1 or n_lin < 1:
            erros.append(
                f"Uma camada sozinha ({lc:.1f} x {ac:.1f} mm) nao cabe na "
                f"mesa de {mesa_x_mm:.0f} x {mesa_y_mm:.0f} mm com "
                f"{margem_mm:.0f} mm de margem e {folga_x_mm:.1f} mm de folga "
                "para o cisalhamento. Reduza a peca.")
        else:
            por_chapa = n_col * n_lin
            n_chapas = int(np.ceil(len(indices) / por_chapa))
            if n_chapas > 1:
                avisos.append(
                    f"Cabem {por_chapa} camadas por chapa ({n_col} x {n_lin}). "
                    f"As {len(indices)} selecionadas ocupam {n_chapas} chapas.")
            if n_chapas > 20:
                avisos.append(
                    f"{n_chapas} chapas e muita coisa para um ensaio. "
                    "Considere aumentar o passo do intervalo.")

    return PlanoMosaico(
        indices=indices, n_colunas=n_col, n_linhas=n_lin, por_chapa=por_chapa,
        n_chapas=n_chapas, largura_celula_mm=lc, altura_celula_mm=ac,
        espacamento_mm=espacamento_mm, mesa_x_mm=mesa_x_mm or 0.0,
        mesa_y_mm=mesa_y_mm or 0.0, margem_mm=margem_mm,
        erros=erros, avisos=avisos)


def montar_chapa(camadas, plano: PlanoMosaico, numero: int) -> Chapa:
    """
    Monta o bitmap de uma chapa.

    A chapa inteira e um bitmap unico na mesma grade das camadas, entao ela
    passa pelo mesmo caminho de faixas, compensacao e codificacao que uma
    camada normal. Nada de especial precisa existir depois daqui.
    """
    if not plano.viavel:
        raise ValueError("; ".join(plano.erros))

    g = camadas.grade
    col_celula = g.n_colunas
    lin_celula = g.n_linhas
    passo_col = col_celula + int(round(plano.espacamento_mm / g.passo_x_mm))
    passo_lin = lin_celula + int(round(plano.espacamento_mm / g.passo_y_mm))

    n_col, n_lin = plano.n_colunas, plano.n_linhas
    total_col = n_col * passo_col - (passo_col - col_celula)
    total_lin = n_lin * passo_lin - (passo_lin - lin_celula)

    bitmap = np.zeros((total_lin, total_col), dtype=bool)
    celulas: list[Celula] = []

    inicio = numero * plano.por_chapa
    fatia = plano.indices[inicio:inicio + plano.por_chapa]

    for k, idx in enumerate(fatia):
        lin, col = divmod(k, n_col)
        c0 = col * passo_col
        l0 = lin * passo_lin
        bitmap[l0:l0 + lin_celula, c0:c0 + col_celula] |= camadas.bitmap(idx)
        celulas.append(Celula(
            indice_camada=idx, z_mm=float(camadas.z_mm[idx]),
            linha=lin, coluna=col,
            x0_mm=c0 * g.passo_x_mm, y0_mm=l0 * g.passo_y_mm))

    # A chapa comeca na MARGEM DA MESA, nao na origem da peca. Herdar o x0
    # do modelo punha a folha de contato onde a peca estava, inclusive em X
    # negativo, e o firmware recusa por falta de curso e de espaco para
    # acelerar antes da primeira coluna.
    grade = GradeRaster(plano.margem_mm, plano.margem_mm, g.dpi_x, g.dpi_y,
                        total_col, total_lin)
    return Chapa(numero=numero, bitmap=bitmap, grade=grade, celulas=celulas)


def relatorio(plano: PlanoMosaico, chapas: list[Chapa]) -> str:
    """
    Texto que acompanha o ensaio: qual celula e qual camada.

    O ensaio so serve se der para saber, olhando a chapa impressa, qual
    retangulo e qual camada da peca. Sem isso e so um borrao bonito.
    """
    linhas = [
        "Ensaio de camadas lado a lado",
        f"mesa: {plano.mesa_x_mm:.0f} x {plano.mesa_y_mm:.0f} mm",
        f"celula: {plano.largura_celula_mm:.2f} x "
        f"{plano.altura_celula_mm:.2f} mm, "
        f"espacamento {plano.espacamento_mm:.1f} mm",
        f"arranjo: {plano.n_colunas} colunas x {plano.n_linhas} linhas "
        f"= {plano.por_chapa} camadas por chapa",
        f"total: {len(plano.indices)} camadas em {plano.n_chapas} chapa(s)",
        "",
    ]
    for ch in chapas:
        linhas.append(f"--- chapa {ch.numero + 1} ---")
        linhas.append("  celula   camada        z (mm)      x0 (mm)   y0 (mm)")
        for c in ch.celulas:
            linhas.append(
                f"  L{c.linha + 1}C{c.coluna + 1:<4}  "
                f"{c.indice_camada + 1:<10}  {c.z_mm:8.3f}  "
                f"{c.x0_mm:10.2f}  {c.y0_mm:8.2f}")
        linhas.append("")
    return "\n".join(linhas)
