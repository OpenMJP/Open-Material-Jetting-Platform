"""
Imagens 2D como fonte de jateamento, ao lado do fatiamento de STL.

POR QUE ISTO CABE AQUI
Todo o caminho depois do fatiamento ja e bitmap: faixas de 128 bicos,
compensacao do cabecote inclinado, 16 bytes por posicao, protocolo. Uma
imagem ja E um bitmap. Ela so precisa entrar na grade da maquina, e dai para
baixo nada muda.

Isso tambem aproxima o software do que o TCC descreve: "processamento de
imagens, unindo dados de jateamento com posicionamento dos motores", e o
requisito de "permitir que o usuario determine os padroes que serao jateados".

A RESTRICAO QUE GOVERNA TUDO
O Xaar 128 e BINARIO: cada bico ejeta uma gota ou nao ejeta, sem escala de
cinza. Entao uma imagem com tons continuos nao pode ser jateada como e. Ela
precisa virar pontos, e e o metodo de meio-tom que decide se o resultado
parece a imagem ou parece uma mancha.

Tres metodos, com usos diferentes:

  limiar   - acima do limiar nao jateia, abaixo jateia. Exato e instantaneo.
             E o certo para desenho de linha, logotipo, texto e padroes de
             teste, que ja sao preto e branco.
  bayer    - matriz ordenada 8x8. Da tons com um padrao regular, nao forma
             grumos, e e instantaneo. Costuma funcionar bem em jato de tinta.
  difusao  - Floyd-Steinberg. A melhor aparencia para fotografia, mas e
             serial por natureza e leva alguns segundos numa imagem grande.

A GRADE NAO E QUADRADA
O passo em Y vem do cabecote (70,6 um a 31 graus) e o passo em X vem do
micropasso. Os dois raramente coincidem, entao a imagem e reamostrada com
fatores diferentes em cada eixo. Quem pede 50 mm de largura recebe 50 mm,
nao 50 mm distorcidos.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .fatiador import GradeRaster

#: Extensoes que o Qt decodifica sem nenhuma dependencia extra.
FORMATOS_IMAGEM = (
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif",
    ".webp", ".pbm", ".pgm", ".ppm", ".xbm", ".xpm", ".svg",
)

try:
    import pymupdf as _pdf
    PDF_DISPONIVEL = True
except Exception:          # pragma: no cover
    try:
        import fitz as _pdf
        PDF_DISPONIVEL = True
    except Exception:
        _pdf = None
        PDF_DISPONIVEL = False


class ImagemInvalida(RuntimeError):
    pass


@dataclass
class ImagemCarregada:
    """Imagem em tons de cinza, 0 = escuro, 1 = claro."""
    cinza: np.ndarray            # (h, w) float32 em 0..1
    nome: str
    dpi_origem: tuple[float, float] | None = None
    paginas: int = 1
    #: True so quando o tamanho fisico e confiavel, isto e, veio de um PDF.
    tamanho_confiavel: bool = False

    @property
    def altura_px(self) -> int:
        return int(self.cinza.shape[0])

    @property
    def largura_px(self) -> int:
        return int(self.cinza.shape[1])

    def tamanho_natural_mm(self) -> tuple[float, float] | None:
        """
        Tamanho SUGERIDO pelo DPI que o arquivo declara. Nunca e verdade.

        Um PNG nao sabe que tamanho tem. O campo de DPI existe, mas a maioria
        dos programas grava um valor padrao sem significado: o proprio Qt
        escreve 100 dpi em todo PNG que salva. Entao 1000 pixels tanto podem
        ser 10 mm quanto 1 m, e aceitar o metadado como medida imprimiria no
        tamanho errado em silencio.

        O PDF e diferente: a pagina tem tamanho fisico de verdade, em pontos,
        e ai o valor vale.

        Quem decide o tamanho em mm e o usuario. Isto so preenche o campo.
        """
        if not self.dpi_origem:
            return None
        dx, dy = self.dpi_origem
        if dx <= 0 or dy <= 0:
            return None
        return (self.largura_px * 25.4 / dx, self.altura_px * 25.4 / dy)


def formatos_suportados() -> list[str]:
    f = list(FORMATOS_IMAGEM)
    if PDF_DISPONIVEL:
        f.append(".pdf")
    return f


def filtro_de_arquivo() -> str:
    """String de filtro para a caixa de abrir arquivo."""
    imgs = " ".join(f"*{e}" for e in FORMATOS_IMAGEM)
    partes = [f"Imagens ({imgs})"]
    if PDF_DISPONIVEL:
        partes.insert(0, f"Imagens e PDF ({imgs} *.pdf)")
        partes.append("PDF (*.pdf)")
    partes.append("Todos os arquivos (*)")
    return ";;".join(partes)


# --------------------------------------------------------------------------
# Carregamento
# --------------------------------------------------------------------------

def carregar(caminho, pagina: int = 0,
             dpi_pdf: float = 600.0) -> ImagemCarregada:
    """
    Le a imagem e devolve em tons de cinza.

    O Qt e usado para decodificar e, mais adiante, para reamostrar. Ele nao
    exige janela nem QApplication para isso, entao o modulo continua
    testavel sem tela.
    """
    p = Path(caminho)
    if not p.exists():
        raise ImagemInvalida(f"arquivo nao encontrado: {p}")
    if p.suffix.lower() == ".pdf":
        return _carregar_pdf(p, pagina, dpi_pdf)
    return _carregar_qt(p)


def _carregar_qt(p: Path) -> ImagemCarregada:
    from PySide6.QtGui import QImage

    img = QImage(str(p))
    if img.isNull():
        raise ImagemInvalida(
            f"nao consegui decodificar {p.name}. Formatos aceitos: "
            + ", ".join(FORMATOS_IMAGEM))

    dpi = None
    # O Qt guarda em pontos por metro; zero significa "nao declarado".
    if img.dotsPerMeterX() > 0 and img.dotsPerMeterY() > 0:
        dpi = (img.dotsPerMeterX() * 0.0254, img.dotsPerMeterY() * 0.0254)

    return ImagemCarregada(cinza=_qimage_para_cinza(img), nome=p.name,
                           dpi_origem=dpi)


def _carregar_pdf(p: Path, pagina: int, dpi: float) -> ImagemCarregada:
    if not PDF_DISPONIVEL:
        raise ImagemInvalida(
            "PDF precisa do pymupdf. Rode: pip install pymupdf")
    doc = _pdf.open(str(p))
    try:
        if not 0 <= pagina < doc.page_count:
            raise ImagemInvalida(
                f"{p.name} tem {doc.page_count} pagina(s); pedida a "
                f"{pagina + 1}")
        pg = doc[pagina]
        # PDF e vetorial: a resolucao de rasterizacao e escolha nossa, e
        # convem ser maior que a da maquina para o meio-tom ter o que amostrar.
        pm = pg.get_pixmap(dpi=int(dpi), colorspace=_pdf.csGRAY, alpha=False)
        arr = np.frombuffer(pm.samples, dtype=np.uint8)
        arr = arr.reshape(pm.height, pm.stride)[:, :pm.width]
        cinza = arr.astype(np.float32) / 255.0
        return ImagemCarregada(cinza=cinza, nome=f"{p.name} (pag. {pagina+1})",
                               dpi_origem=(dpi, dpi),
                               paginas=doc.page_count,
                               tamanho_confiavel=True)
    finally:
        doc.close()


def _qimage_para_cinza(img) -> np.ndarray:
    from PySide6.QtGui import QImage

    # Fundo BRANCO onde houver transparencia: o que e transparente nao deve
    # virar gota. Sem isso, um PNG com fundo alfa viraria um retangulo cheio.
    if img.hasAlphaChannel():
        fundo = QImage(img.size(), QImage.Format.Format_RGB32)
        fundo.fill(0xFFFFFFFF)
        from PySide6.QtGui import QPainter
        pintor = QPainter(fundo)
        pintor.drawImage(0, 0, img)
        pintor.end()
        img = fundo

    img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    h, w = img.height(), img.width()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.sizeInBytes())
    arr = arr.reshape(h, img.bytesPerLine())[:, :w]
    return (arr.astype(np.float32) / 255.0).copy()


# --------------------------------------------------------------------------
# Preparacao para a maquina
# --------------------------------------------------------------------------

@dataclass
class Opcoes:
    largura_mm: float
    altura_mm: float
    modo: str = "limiar"            # limiar | bayer | difusao
    limiar: float = 0.5
    inverter: bool = False
    espelhar_x: bool = False
    espelhar_y: bool = False
    x0_mm: float = 10.0
    y0_mm: float = 10.0
    z_mm: float = 0.0


MODOS = ("limiar", "bayer", "difusao")

#: Matriz de Bayer 8x8, normalizada em 0..1.
_BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
], dtype=np.float32) / 64.0


def mascara_de_opacidade(forma: tuple[int, int], opacidade: float,
                         ordem: int = 0, linha0: int = 0) -> np.ndarray:
    """
    Mascara para jatear so uma fracao das gotas, em Bayer 8x8.

    Imprimir algo por cima de outra coisa com 100% das gotas nao mostra a
    sobreposicao: o preto fica preto. Com uma fracao, onde duas impressoes
    se sobrepoem fica mais escuro.

    `ordem` escolhe QUAIS gotas: a impressao k fica com os limiares em
    [k*opacidade, (k+1)*opacidade), modulo 1. Impressoes consecutivas pegam
    gotas complementares, e n delas com opacidade 1/n cobrem tudo
    exatamente; com a mesma mascara para todas, as gotas cairiam umas sobre
    as outras e nada escureceria. `linha0` desloca a linha da matriz, para
    passadas em Y diferentes seguirem a grade do substrato.
    """
    if not 0.0 < opacidade <= 1.0:
        raise ValueError("a opacidade vai de 0 (exclusive) a 1")
    n_lin, n_col = forma
    lado = _BAYER8.shape[0]
    t = _BAYER8[np.ix_((np.arange(n_lin) + linha0) % lado,
                       np.arange(n_col) % lado)] + 0.5 / 64.0
    inicio = (ordem * opacidade) % 1.0
    return ((t - inicio) % 1.0) < opacidade


def contorno(bitmap: np.ndarray, largura_x: int, largura_y: int) -> np.ndarray:
    """
    A borda de uma regiao, com `largura_x` colunas e `largura_y` linhas de
    espessura (o pixel nao e quadrado, entao as duas vem separadas).

    E o que sobra da regiao depois de uma erosao retangular: um pixel fica
    na borda se algum vizinho dentro do retangulo esta fora da regiao. A
    borda da imagem conta como fora.
    """
    b = np.asarray(bitmap, dtype=bool)
    nucleo = b.copy()
    for _ in range(max(0, largura_x)):
        e = np.zeros_like(nucleo)
        e[:, 1:-1] = nucleo[:, 1:-1] & nucleo[:, :-2] & nucleo[:, 2:]
        nucleo = e
    for _ in range(max(0, largura_y)):
        e = np.zeros_like(nucleo)
        e[1:-1, :] = nucleo[1:-1, :] & nucleo[:-2, :] & nucleo[2:, :]
        nucleo = e
    return b & ~nucleo


def mascara_de_opacidade_por_coluna(forma: tuple[int, int], opacidade: float,
                                    ordem: int = 0) -> np.ndarray:
    """
    Como `mascara_de_opacidade`, mas a escolha depende so da coluna.

    Para impressoes que se sobrepoem com um deslocamento em Y DESCONHECIDO
    (o alinhamento entre faixas): com Bayer 2D, um deslocamento de algumas
    linhas pode fazer as duas pegarem as mesmas colunas e a sobreposicao nao
    escurecer. Por coluna, as gotas de ordens diferentes sao complementares
    para qualquer deslocamento em Y. Os limiares seguem a sequencia de bits
    invertidos (64 niveis espalhados), para a fracao sair uniforme.
    """
    if not 0.0 < opacidade <= 1.0:
        raise ValueError("a opacidade vai de 0 (exclusive) a 1")
    n_lin, n_col = forma
    c = np.arange(n_col) % 64
    inv = np.zeros_like(c)
    for b in range(6):
        inv |= ((c >> b) & 1) << (5 - b)
    t = inv / 64.0 + 0.5 / 64.0
    inicio = (ordem * opacidade) % 1.0
    linha = ((t - inicio) % 1.0) < opacidade
    return np.broadcast_to(linha, (n_lin, n_col)).copy()


def _reamostrar(cinza: np.ndarray, n_colunas: int,
                n_linhas: int) -> np.ndarray:
    """
    Reamostra para a grade da maquina, que tem passos diferentes em X e Y.

    Usa o escalonador do Qt, que trata bem reducao e ampliacao e aceita
    fatores diferentes por eixo. Reimplementar isso em numpy daria mais
    codigo e pior resultado.
    """
    from PySide6.QtGui import QImage

    h, w = cinza.shape
    if (w, h) == (n_colunas, n_linhas):
        return cinza
    buf = np.ascontiguousarray(
        np.clip(cinza * 255.0, 0, 255).astype(np.uint8))
    img = QImage(buf.data, w, h, w, QImage.Format.Format_Grayscale8)
    from PySide6.QtCore import Qt
    img = img.scaled(
        n_colunas, n_linhas,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation)
    saida = img.convertToFormat(QImage.Format.Format_Grayscale8)
    arr = np.frombuffer(saida.constBits(), dtype=np.uint8,
                        count=saida.sizeInBytes())
    arr = arr.reshape(saida.height(), saida.bytesPerLine())[:, :n_colunas]
    return arr.astype(np.float32) / 255.0


def _difusao_erro(cinza: np.ndarray, limiar: float) -> np.ndarray:
    """
    Floyd-Steinberg. O erro de cada pixel e empurrado para os vizinhos ainda
    nao decididos, o que preserva a densidade media e evita as faixas que o
    limiar puro produz.

    E serial por natureza: o pixel seguinte depende do anterior. Por isso e
    o modo mais lento dos tres. O laco usa listas do Python, e nao indices
    do numpy: cada acesso a um elemento de array custa varias vezes mais, e
    assim o laco fica ~5x mais rapido, com o mesmo resultado.
    """
    h, w = cinza.shape
    saida = np.zeros((h, w), dtype=bool)
    k7, k3, k5, k1 = 7.0 / 16.0, 3.0 / 16.0, 5.0 / 16.0, 1.0 / 16.0
    prox = cinza[0].astype(np.float64).tolist()
    for y in range(h):
        linha = prox
        prox = (cinza[y + 1].astype(np.float64).tolist() if y + 1 < h
                else [0.0] * w)
        lin_saida = [False] * w
        erro = 0.0                      # erro do pixel a esquerda
        for x in range(w):
            antigo = linha[x] + erro * k7
            if antigo < limiar:
                lin_saida[x] = True
                erro = antigo
            else:
                erro = antigo - 1.0
            if x:
                prox[x - 1] += erro * k3
            prox[x] += erro * k5
            if x + 1 < w:
                prox[x + 1] += erro * k1
        saida[y] = lin_saida
        time.sleep(0)                   # cede o GIL: a interface roda junto
    return saida


def para_bitmap(img: ImagemCarregada, dpi_x: float, dpi_y: float,
                opcoes: Opcoes) -> tuple[np.ndarray, GradeRaster]:
    """
    Imagem -> bitmap na grade da maquina, pronto para virar swaths.

    True significa jatear. Por convencao, o ESCURO da imagem vira gota: e o
    que faz um desenho preto no branco sair desenhado, e nao em negativo.
    """
    if opcoes.modo not in MODOS:
        raise ValueError(f"modo {opcoes.modo!r} desconhecido; use {MODOS}")
    if opcoes.largura_mm <= 0 or opcoes.altura_mm <= 0:
        raise ValueError("a largura e a altura em mm tem que ser positivas")

    n_colunas = max(1, int(round(opcoes.largura_mm * dpi_x / 25.4)))
    n_linhas = max(1, int(round(opcoes.altura_mm * dpi_y / 25.4)))

    cinza = _reamostrar(img.cinza, n_colunas, n_linhas)
    # O topo do arquivo vai para o Y MAIOR: a linha 0 do bitmap e o Y menor
    # (lado do endstop), e quem olha a mesa ve o Y menor embaixo. Sem isto a
    # imagem saia espelhada em Y (conferido na maquina em 03/11/2026).
    cinza = cinza[::-1, :]
    if opcoes.inverter:
        cinza = 1.0 - cinza
    if opcoes.espelhar_x:
        cinza = cinza[:, ::-1]
    if opcoes.espelhar_y:
        cinza = cinza[::-1, :]

    if opcoes.modo == "limiar":
        bitmap = cinza < opcoes.limiar
    elif opcoes.modo == "bayer":
        lado = _BAYER8.shape[0]
        matriz = np.tile(
            _BAYER8,
            (n_linhas // lado + 1, n_colunas // lado + 1))[:n_linhas, :n_colunas]
        # Centra a matriz no limiar pedido, para o controle continuar valendo.
        # O meio degrau (1/128) faz o preto puro jatear em todos os pontos:
        # sem ele, a celula de valor 0 da matriz nunca dispara.
        bitmap = cinza < (matriz - 0.5 + opcoes.limiar + 0.5 / 64)
    else:
        bitmap = _difusao_erro(cinza, opcoes.limiar)

    grade = GradeRaster(opcoes.x0_mm, opcoes.y0_mm, dpi_x, dpi_y,
                        n_colunas, n_linhas)
    return np.ascontiguousarray(bitmap), grade


#: Resolucao em que o padrao de teste e desenhado (20 px/mm). Bem acima da
#: grade da maquina, para a reamostragem nao borrar as bordas.
PX_POR_MM_PADRAO = 20


def padrao_de_teste(largura_mm: float = 60.0,
                    altura_mm: float = 9.0) -> ImagemCarregada:
    """
    Faixa de teste sintetica, para a primeira impressao de verdade.

    Cinco blocos ao longo de X, separados por 1 mm em branco:

    1. cheio: todos os bicos em todas as colunas (falha de bico vira risco);
    2. barras verticais de 0,5 mm a cada 1 mm (grade de disparo, pitch);
    3. barras horizontais de 0,5 mm a cada 1 mm (ordem dos bicos em Y);
    4. xadrez de 1 mm (as duas coisas ao mesmo tempo);
    5. gradiente de branco a preto (meio-tom; use Bayer ou difusao).

    Com a altura de uma faixa (128 bicos), sai numa swath so. Passa pelo
    mesmo caminho de uma imagem carregada, de proposito.
    """
    px = PX_POR_MM_PADRAO
    w = max(5, int(round(largura_mm * px)))
    h = max(1, int(round(altura_mm * px)))
    cinza = np.ones((h, w), dtype=np.float32)
    xs = np.arange(w)[None, :] / px          # mm
    ys = np.arange(h)[:, None] / px

    vao = 1.0
    bloco = max(0.5, (largura_mm - 4 * vao) / 5)
    for i in range(5):
        x0 = i * (bloco + vao)
        dentro = (xs >= x0) & (xs < x0 + bloco)
        if i == 0:
            preto = np.ones_like(ys + xs, dtype=bool)
        elif i == 1:
            preto = np.broadcast_to(((xs - x0) % 1.0) < 0.5, (h, w))
        elif i == 2:
            preto = np.broadcast_to((ys % 1.0) < 0.5, (h, w))
        elif i == 3:
            preto = (((xs - x0) // 1.0 + ys // 1.0) % 2) == 0
        else:
            rampa = np.clip(1.0 - (xs - x0) / bloco, 0.0, 1.0)
            cinza = np.where(dentro, np.broadcast_to(rampa, (h, w)), cinza)
            continue
        cinza = np.where(dentro & preto, 0.0, cinza).astype(np.float32)

    dpi = px * 25.4
    return ImagemCarregada(cinza=np.ascontiguousarray(cinza),
                           nome="padrao de teste", dpi_origem=(dpi, dpi),
                           tamanho_confiavel=True)


def estimar_cobertura(bitmap: np.ndarray) -> float:
    """Fracao da area que recebe gota. Util para prever consumo de tinta."""
    return float(bitmap.sum()) / max(1, bitmap.size)
