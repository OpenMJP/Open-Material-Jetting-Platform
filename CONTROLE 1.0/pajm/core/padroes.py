"""
Padroes de teste e calibracao.

Sao bitmaps SINTETICOS que percorrem exatamente o mesmo caminho de uma peca
fatiada (faixas, compensacao, codificacao, 16 bytes por posicao W). Isso e
proposital: um padrao que exercita um caminho diferente nao valida o caminho
que vai imprimir de verdade.

Cada gerador devolve, alem do bitmap, as DIMENSOES NOMINAIS do que foi
desenhado. O plano de validacao do TCC preve escanear o impresso e comparar
com o original, e isso so funciona se o original vier com medidas declaradas.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .fatiador import GradeRaster
from .imagem import mascara_de_opacidade_por_coluna


@dataclass
class Passada:
    """
    Uma passada do cabecote, com o Y em que ela e jateada.

    O bitmap tem exatamente n_bicos linhas, porque A LINHA DO BITMAP E O BICO:
    a linha i e jateada pelo bico i, sempre. Nao da para "reorganizar" linhas
    para afastar o desenho; o que afasta e mover o eixo Y entre passadas.
    """
    grupo: int
    bitmap: np.ndarray            # (n_bicos, n_colunas)
    y_mm: float
    bicos: list[int]
    x_mm: float = 0.0


@dataclass
class Padrao:
    nome: str
    bitmap: np.ndarray            # como fica no substrato, para a previa
    grade: GradeRaster
    descricao: str
    referencia: dict = field(default_factory=dict)
    legenda: list[str] = field(default_factory=list)
    passadas: list[Passada] = field(default_factory=list)

    @property
    def largura_mm(self) -> float:
        return self.grade.largura_mm

    @property
    def altura_mm(self) -> float:
        return self.grade.altura_mm

    @property
    def n_gotas(self) -> int:
        return int(self.bitmap.sum())

    def texto_referencia(self) -> str:
        linhas = [f"Padrao: {self.nome}", self.descricao, "",
                  f"dimensoes: {self.largura_mm:.2f} x {self.altura_mm:.2f} mm",
                  f"grade: {self.grade.n_linhas} linhas x "
                  f"{self.grade.n_colunas} colunas "
                  f"({self.grade.dpi_x:.0f} x {self.grade.dpi_y:.0f} dpi)",
                  f"gotas: {self.n_gotas}", ""]
        for k, v in self.referencia.items():
            linhas.append(f"{k}: {v}")
        if self.legenda:
            linhas.append("")
            linhas.extend(self.legenda)
        return "\n".join(linhas)


def _grade(n_colunas: int, n_linhas: int, dpi_x: float,
           dpi_y: float) -> GradeRaster:
    return GradeRaster(0.0, 0.0, dpi_x, dpi_y, n_colunas, n_linhas)


# --------------------------------------------------------------------------
# 1. Verificacao de bicos
# --------------------------------------------------------------------------

def teste_de_bicos(n_bicos: int, dpi_x: float, dpi_y: float,
                   segmentos: int = 8,
                   comprimento_segmento_mm: float = 2.0) -> Padrao:
    """
    Escada de linhas, como a verificacao de bicos das impressoras de mesa:
    linha interrompida = bico entupido, e a posicao da falha diz qual.

    Tudo numa passada so. A linha do bitmap E o bico, entao nao da para
    desenhar uma linha horizontal "de verdade" com varios bicos: cada bico
    jateia so a sua propria linha. O truque das impressoras e montar cada
    linha com segmentos de bicos vizinhos, um depois do outro em X:

        linha L (0-based) = bicos L*s + 1 ate L*s + s   (s = segmentos)
        o segmento k da linha L e o bico L*s + k + 1

    Como bicos vizinhos estao a um passo (~70 um) um do outro em Y, cada
    linha desce um degrau por segmento e sai levemente inclinada, igual a
    figura de referencia. Linhas vizinhas ficam s passos afastadas (~0,56 mm
    com s = 8), o bastante para contar. Um bico entupido vira uma lacuna do
    tamanho de um segmento na linha dele.
    """
    if n_bicos < 1 or segmentos < 1 or n_bicos % segmentos:
        raise ValueError(
            f"segmentos tem que dividir o numero de bicos ({n_bicos})")

    passo_x = 25.4 / dpi_x
    passo_y = 25.4 / dpi_y
    larg = max(1, int(round(comprimento_segmento_mm / passo_x)))
    n_linhas = n_bicos // segmentos

    bitmap = np.zeros((n_bicos, segmentos * larg), dtype=bool)
    for b in range(n_bicos):
        k = b % segmentos
        bitmap[b, k * larg:(k + 1) * larg] = True

    return Padrao(
        nome="Verificacao de bicos",
        bitmap=bitmap,
        grade=_grade(segmentos * larg, n_bicos, dpi_x, dpi_y),
        descricao=(
            "Escada de linhas numa passada so. Cada linha e feita de "
            f"{segmentos} segmentos, um por bico. Linha interrompida indica "
            "bico entupido ou com jato irregular."),
        referencia={
            "bicos": n_bicos,
            "linhas": n_linhas,
            "segmentos por linha": segmentos,
            "comprimento do segmento (mm)": round(larg * passo_x, 3),
            "distancia entre linhas (mm)": round(segmentos * passo_y, 3),
            # O que medir para conferir a resolucao: o espacamento das gotas
            # ao longo de cada linha (em X) e entre bicos vizinhos (em Y)
            "resolucao em X (dpi)": round(dpi_x, 1),
            "espacamento entre gotas em X (um)": round(passo_x * 1000, 2),
            "gotas por segmento": larg,
            "resolucao em Y, passo entre bicos (dpi)": round(dpi_y, 1),
        },
        legenda=[
            "A primeira linha e a do lado do bico 1 (Y menor, perto do "
            "endstop do Y); na previa e no impresso, a de baixo.",
            f"Segmento k (1 a {segmentos}, da esquerda para a direita) da "
            f"linha L (1 a {n_linhas}) = bico {segmentos}*(L-1) + k.",
        ])


# --------------------------------------------------------------------------
# 2. Alinhamento / espacamento de linhas
# --------------------------------------------------------------------------

def teste_de_alinhamento(dpi_x: float, dpi_y: float,
                         n_opcoes: int = 7,
                         passo_correcao_um: float = 20.0,
                         bloco_largura_mm: float = 12.0,
                         bloco_altura_mm: float = 2.5,
                         espaco_mm: float = 2.5,
                         eixo: str = "x") -> Padrao:
    """
    Sequencia numerada de PARES de blocos, cada par com um deslocamento
    diferente entre as duas metades. Quem calibra escolhe o par
    que nao mostra nem sobreposicao nem lacuna, e esse numero vira a correcao.

    E o mesmo principio dos padroes de alinhamento de impressora de mesa, que
    foi a referencia pedida.

    `eixo`:
      "x" varre o deslocamento ao longo da varredura. Serve para alinhar ida
          e volta na impressao bidirecional.
    O Y fica com `teste_de_alinhamento_y`: aqui os dois blocos saem na
    mesma passada, e em Y isso so trocaria de bico, sem medir nada.

    O deslocamento da opcao i (1-based, centrada) e:
        (i - (n_opcoes+1)/2) * passo_correcao_um
    entao a opcao do meio e sempre o deslocamento zero.
    """
    if eixo != "x":
        raise ValueError("o alinhamento em Y e teste_de_alinhamento_y: o "
                         "deslocamento tem de vir do eixo, entre passadas")
    if n_opcoes < 3 or n_opcoes % 2 == 0:
        raise ValueError("n_opcoes tem que ser impar e >= 3, para haver um "
                         "meio com deslocamento zero")

    px = 25.4 / dpi_x
    py = 25.4 / dpi_y
    larg = max(1, int(round(bloco_largura_mm / px)))
    alt = max(1, int(round(bloco_altura_mm / py)))
    esp = max(1, int(round(espaco_mm / py)))

    centro = (n_opcoes + 1) / 2.0
    desloc_um = [(i + 1 - centro) * passo_correcao_um for i in range(n_opcoes)]
    desloc_px = [int(round(d / 1000.0 / (px if eixo == "x" else py)))
                 for d in desloc_um]

    folga = max(abs(d) for d in desloc_px) + 2
    n_colunas = larg + 2 * folga
    altura = n_opcoes * (2 * alt + esp)

    bitmap = np.zeros((altura, n_colunas), dtype=bool)
    legenda = []

    for i in range(n_opcoes):
        topo = i * (2 * alt + esp)
        d = desloc_px[i]
        # metade de cima: referencia, sempre na mesma posicao
        bitmap[topo:topo + alt, folga:folga + larg] = True
        # metade de baixo: deslocada
        if eixo == "x":
            c0 = folga + d
            bitmap[topo + alt:topo + 2 * alt, c0:c0 + larg] = True
        else:
            l0 = topo + alt + d
            l0 = max(0, min(l0, altura - alt))
            bitmap[l0:l0 + alt, folga:folga + larg] = True
        legenda.append(
            f"opcao {i + 1}: {desloc_um[i]:+.0f} um "
            f"({d:+d} {'colunas' if eixo == 'x' else 'linhas'})")

    return Padrao(
        nome=f"Alinhamento em {eixo.upper()}",
        bitmap=bitmap,
        grade=_grade(n_colunas, altura, dpi_x, dpi_y),
        descricao=(
            "Pares de blocos com deslocamento crescente. Escolha o par sem "
            "sobreposicao nem lacuna entre as duas metades; o "
            "numero dele e a correcao a gravar no perfil."),
        referencia={
            "eixo varrido": eixo,
            "opcoes": n_opcoes,
            "passo entre opcoes (um)": passo_correcao_um,
            "opcao de deslocamento zero": int(centro),
            "bloco (mm)": f"{bloco_largura_mm} x {bloco_altura_mm}",
            "deslocamentos (um)": [round(d, 1) for d in desloc_um],
        },
        legenda=legenda)


def correcao_escolhida(opcao: int, n_opcoes: int,
                       passo_correcao_um: float) -> float:
    """Converte a opcao escolhida a olho na correcao em micrometros."""
    if not 1 <= opcao <= n_opcoes:
        raise ValueError(f"opcao {opcao} fora de 1..{n_opcoes}")
    return (opcao - (n_opcoes + 1) / 2.0) * passo_correcao_um


def teste_de_alinhamento_y(n_bicos: int, dpi_x: float, dpi_y: float,
                           passo_faixa_mm: float,
                           n_opcoes: int = 7,
                           passo_correcao_um: float = 25.0,
                           bloco_largura_mm: float = 8.0,
                           espaco_mm: float = 2.0,
                           opacidade: float = 0.5) -> Padrao:
    """
    Calibra o quanto o Y avanca de uma faixa para a seguinte, para que a
    faixa nova encoste na anterior sem lacuna e sem sobreposicao.

    Toda passada usa os n_bicos bicos, como numa impressao de verdade (faixa
    de ~9 mm no XJ128). O deslocamento vem do eixo Y, entre passadas:

      passada A (Y = 0): a "faixa anterior", um bloco cheio para cada opcao,
          lado a lado em X;
      passada B_i (Y = passo_faixa + d_i): a "faixa seguinte" so da opcao i,
          um bloco cheio logo depois (em Y) do bloco i da passada A.

    A emenda fica entre o ultimo bico da passada A e o primeiro da B_i. Com
    d_i = 0 a passada B cai onde a faixa seguinte de uma impressao cairia
    hoje. A opcao em que a emenda some (sem linha branca nem linha escura)
    diz quanto somar a correcao atual (`correcao_escolhida`).

    O numero da opcao vai em i entalhes brancos no meio do bloco B_i, longe
    da emenda para nao atrapalhar a leitura dela.

    Os blocos saem com so `opacidade` das gotas (meio-tom), cada passada com
    colunas complementares da outra (`mascara_de_opacidade_por_coluna`, que
    nao depende do deslocamento em Y, a incognita do teste). Com 100% a
    sobreposicao nao aparece, porque preto sobre preto continua preto; com
    50%, ela vira uma faixa bem mais escura que o resto do bloco.
    """
    if n_opcoes < 3 or n_opcoes % 2 == 0:
        raise ValueError("n_opcoes tem que ser impar e >= 3, para haver um "
                         "meio com deslocamento zero")

    px = 25.4 / dpi_x
    py = 25.4 / dpi_y
    larg = max(1, int(round(bloco_largura_mm / px)))
    esp = max(1, int(round(espaco_mm / px)))
    n_colunas = n_opcoes * larg + (n_opcoes - 1) * esp

    # Entalhes de ~0.4 mm, encolhendo se for preciso para caber n_opcoes
    marca = min(int(round(0.4 / px)), larg // (2 * n_opcoes + 1))
    if marca < 2:
        raise ValueError(
            f"o bloco e estreito demais para numerar {n_opcoes} opcoes; "
            "aumente a largura do bloco")
    alt_marca = max(2, min(int(round(1.5 / py)), n_bicos // 3))
    l_marca = (n_bicos - alt_marca) // 2

    centro = (n_opcoes + 1) / 2.0
    desloc_um = [(i + 1 - centro) * passo_correcao_um for i in range(n_opcoes)]

    def coluna(i: int) -> int:
        return i * (larg + esp)

    a = np.zeros((n_bicos, n_colunas), dtype=bool)
    for i in range(n_opcoes):
        a[:, coluna(i):coluna(i) + larg] = True
    a &= mascara_de_opacidade_por_coluna(a.shape, opacidade, ordem=0)
    meio_tom_b = mascara_de_opacidade_por_coluna(a.shape, opacidade, ordem=1)
    passadas = [Passada(grupo=0, bitmap=a, y_mm=0.0,
                        bicos=list(range(n_bicos)))]

    for i in range(n_opcoes):
        b = np.zeros((n_bicos, n_colunas), dtype=bool)
        c0 = coluna(i)
        b[:, c0:c0 + larg] = True
        for m in range(i + 1):
            c = c0 + (2 * m + 1) * marca
            b[l_marca:l_marca + alt_marca, c:c + marca] = False
        b &= meio_tom_b
        passadas.append(Passada(
            grupo=i + 1, bitmap=b, y_mm=passo_faixa_mm + desloc_um[i] / 1000.0,
            bicos=list(range(n_bicos))))

    # Previa: como cai no substrato, com d_i arredondado para linhas
    d_px = [int(round(d / 1000.0 / py)) for d in desloc_um]
    altura = 2 * n_bicos + max(0, max(d_px))
    composto = np.zeros((altura, n_colunas), dtype=bool)
    composto[:n_bicos] = a
    for i, p in enumerate(passadas[1:]):
        c0 = coluna(i)
        for lin in range(n_bicos):
            destino = n_bicos + lin + d_px[i]
            if 0 <= destino < altura:
                composto[destino, c0:c0 + larg] |= p.bitmap[lin, c0:c0 + larg]

    return Padrao(
        nome="Alinhamento em Y (avanco entre faixas)",
        bitmap=composto,
        grade=_grade(n_colunas, altura, dpi_x, dpi_y),
        descricao=(
            f"Duas faixas inteiras ({n_bicos} bicos) por opcao: a primeira "
            "(Y menor, embaixo na previa) numa passada, a segunda numa "
            "passada separada, com o Y avancado "
            "do passo entre faixas mais o deslocamento da opcao. Escolha a "
            "opcao em que a emenda entre as duas some."),
        referencia={
            "opcoes": n_opcoes,
            "passo entre opcoes (um)": passo_correcao_um,
            "opcao de deslocamento zero": int(centro),
            "avanco entre faixas usado (mm)": round(passo_faixa_mm, 4),
            "faixa (mm)": round(n_bicos * py, 3),
            "opacidade de cada passada": f"{opacidade:.0%}",
            "bloco (mm)": f"{larg * px:.2f} x {n_bicos * py:.2f}",
            "deslocamentos (um)": [round(d, 1) for d in desloc_um],
        },
        legenda=[
            "Linha branca na emenda = o Y avancou demais (correcao "
            "negativa); linha escura = avancou de menos (positiva). Com "
            "opacidade abaixo de 100%, a escura e onde as duas faixas se "
            "sobrepoem.",
            "Linhas brancas que aparecem igual nos dois blocos sao bicos "
            "entupidos, nao a emenda.",
        ] + [f"opcao {i + 1} ({i + 1} entalhe(s)): {d:+.0f} um"
             for i, d in enumerate(desloc_um)],
        passadas=passadas)


# --------------------------------------------------------------------------
# 2b. Angulo do cabecote
# --------------------------------------------------------------------------

def angulo_da_opcao(opcao: int, n_opcoes: int, passo_deg: float,
                    angulo_base_deg: float) -> float:
    """Opcao escolhida a olho no teste visual -> angulo em graus."""
    if not 1 <= opcao <= n_opcoes:
        raise ValueError(f"opcao {opcao} fora de 1..{n_opcoes}")
    return angulo_base_deg + (opcao - (n_opcoes + 1) / 2.0) * passo_deg


def teste_de_angulo(n_bicos: int, dpi_x: float, dpi_y: float,
                    passo_faixa_mm: float, passo_bico_mm: float,
                    angulo_base_deg: float,
                    n_opcoes: int = 9, passo_deg: float = 0.5,
                    fase_mm=None,
                    n_linhas: int = 3, espacamento_mm: float = 1.0,
                    espaco_opcoes_mm: float = 3.0) -> Padrao:
    """
    Calibracao VISUAL do angulo do cabecote, no estilo dos alinhamentos.

    O angulo entra na compensacao de cisalhamento: o dado do bico i sai
    i * passo_bico * cos(angulo) adiante em X. Se o angulo usado nao e o
    real, cada bico cai um pouco fora, proporcional a i: uma linha vertical
    sai inclinada dentro da faixa e, na emenda com a faixa seguinte, o bico
    128 de uma encosta no bico 1 da outra com o erro inteiro acumulado. Isso
    vira um DEGRAU na emenda, facil de ver.

    Cada opcao e um grupo de linhas verticais compensado com um angulo
    diferente (angulo_base + k * passo_deg). A compensacao normal do envio
    continua sendo a do angulo_base; a diferenca para o angulo da opcao vai
    deslocando as linhas do bitmap, bico a bico, com o MESMO arredondamento
    que o envio faz (por isso a fase de cada bico, `fase_mm`, entra aqui).

    Duas passadas com o mesmo desenho, a segunda uma faixa adiante em Y. A opcao
    em que as linhas atravessam a emenda sem degrau e o angulo real. O
    avanco de Y nao muda nada em X, entao este teste nao depende do
    alinhamento em Y.

    A resolucao e de uma coluna no bico 128: com o XJ128 a 31 graus e ~360
    dpi isso da ~0,45 grau. Passos menores fazem opcoes vizinhas sairem
    iguais.
    """
    if n_opcoes < 3 or n_opcoes % 2 == 0:
        raise ValueError("n_opcoes tem que ser impar e >= 3, para haver um "
                         "meio igual ao angulo atual")
    centro = (n_opcoes + 1) / 2.0
    angulos = [angulo_base_deg + (i + 1 - centro) * passo_deg
               for i in range(n_opcoes)]
    if min(angulos) <= 0 or max(angulos) >= 90:
        raise ValueError("as opcoes tem que ficar entre 0 e 90 graus")

    px = 25.4 / dpi_x
    idx = np.arange(n_bicos)
    fase = (np.zeros(n_bicos) if fase_mm is None
            else np.asarray(fase_mm, dtype=float))

    def colunas(ang: float) -> np.ndarray:
        a = passo_bico_mm * math.cos(math.radians(ang))
        return np.rint((idx * a + fase) / px).astype(np.int64)

    base = colunas(angulo_base_deg)
    extra = [colunas(a) - base for a in angulos]
    margem = int(max(np.abs(e).max() for e in extra)) + 1

    ex = 2
    passo_l = max(ex + 2, int(round(espacamento_mm / px)))
    marca_passo = 2 * ex
    larg_linhas = (n_linhas - 1) * passo_l + ex
    larg_opcao = 2 * margem + larg_linhas + passo_l + n_opcoes * marca_passo
    esp = max(1, int(round(espaco_opcoes_mm / px)))
    n_col = n_opcoes * larg_opcao + (n_opcoes - 1) * esp

    # Marcas da opcao: so na passada de baixo, na metade de baixo da faixa,
    # longe da emenda
    l_marca = n_bicos // 2

    def desenhar(com_marcas: bool) -> np.ndarray:
        bm = np.zeros((n_bicos, n_col), dtype=bool)
        for k in range(n_opcoes):
            c0 = k * (larg_opcao + esp) + margem
            for i in range(n_bicos):
                d = int(extra[k][i])
                for m in range(n_linhas):
                    c = c0 + m * passo_l + d
                    bm[i, c:c + ex] = True
                if com_marcas and i >= l_marca:
                    for m in range(k + 1):
                        c = c0 + larg_linhas + passo_l + m * marca_passo + d
                        bm[i, c:c + ex] = True
        return bm

    a = desenhar(False)
    b = desenhar(True)
    passadas = [
        Passada(grupo=0, bitmap=a, y_mm=0.0, bicos=list(range(n_bicos))),
        Passada(grupo=1, bitmap=b, y_mm=passo_faixa_mm,
                bicos=list(range(n_bicos))),
    ]
    sobra_col = [int(e[-1]) for e in extra]

    return Padrao(
        nome="Angulo do cabecote (visual)",
        bitmap=np.vstack([a, b]),
        grade=_grade(n_col, 2 * n_bicos, dpi_x, dpi_y),
        descricao=(
            "Grupos de linhas verticais, cada um compensado com um angulo "
            "diferente, em duas faixas emendadas. Escolha o grupo em que as "
            "linhas atravessam a emenda sem degrau: o angulo dele e o real."),
        referencia={
            "opcoes": n_opcoes,
            "passo entre opcoes (graus)": passo_deg,
            "opcao do angulo atual": int(centro),
            "angulo atual (graus)": round(angulo_base_deg, 3),
            "angulos (graus)": [round(x, 2) for x in angulos],
            "desvio no bico 128 (colunas)": sobra_col,
        },
        legenda=[
            "Na previa, o grupo do meio sai reto: ela mostra o resultado "
            "SE o angulo atual for o real. No impresso, o reto e o certo.",
            "Degrau para um lado nas opcoes de um lado e para o outro nas "
            "do outro lado; a certa fica entre elas.",
            "Opcoes vizinhas iguais na coluna acima tem o mesmo desenho; "
            "aumente o passo.",
        ] + [f"opcao {k + 1} ({k + 1} marca(s)): {x:.2f} graus"
             for k, x in enumerate(angulos)],
        passadas=passadas)


def teste_de_angulo_distancia(n_bicos: int, dpi_x: float, dpi_y: float,
                              bico_a: int = 1, bico_b: int = 128,
                              comprimento_mm: float = 30.0,
                              n_verticais: int = 5,
                              espacamento_verticais_mm: float = 2.0) -> Padrao:
    """
    Ajuste fino do angulo, por medida (paquimetro ou scanner), numa
    passada so. O teste visual (`teste_de_angulo`) acha o angulo com
    resolucao de ~0,45 grau; este chega a ~0,1 grau.

    Dentro de uma passada o eixo Y nao anda, entao a distancia em Y entre
    duas linhas jateadas pelos bicos a e b depende so da geometria do
    cabecote [DS]:

        D = (b - a) * passo_bico * sen(angulo)

    passo_bico (137,1 um) e de fabrica; o angulo depende da peca impressa
    que segura o cabecote. Medindo D (paquimetro ou scanner) sai o angulo,
    sem misturar a calibracao do motor Y. Com a = 1 e b = 128, 1 grau muda
    D em ~0,26 mm.

    As linhas verticais a direita conferem o mesmo angulo pelo outro lado:
    o cisalhamento em X e compensado com passo_bico * cos(angulo), e se o
    angulo usado estiver errado elas saem inclinadas. Depois de aplicar o
    angulo medido, reimpressas, devem sair retas.

    Escolha a e b entre os bicos que a verificacao mostrou funcionando: uma
    linha de um bico so some se ele estiver entupido.
    """
    if not 1 <= bico_a < bico_b <= n_bicos:
        raise ValueError(f"precisa 1 <= bico A < bico B <= {n_bicos}")
    px = 25.4 / dpi_x
    py = 25.4 / dpi_y
    larg_h = max(1, int(round(comprimento_mm / px)))
    folga = max(1, int(round(3.0 / px)))
    ex = 2
    passo_v = max(ex + 1, int(round(espacamento_verticais_mm / px)))
    n_verticais = max(0, n_verticais)
    larg_v = (n_verticais - 1) * passo_v + ex if n_verticais else 0
    n_col = larg_h + (folga + larg_v if n_verticais else 0)

    la, lb = bico_a - 1, bico_b - 1
    bitmap = np.zeros((n_bicos, n_col), dtype=bool)
    bitmap[la, :larg_h] = True
    bitmap[lb, :larg_h] = True
    for k in range(n_verticais):
        c = larg_h + folga + k * passo_v
        bitmap[la:lb + 1, c:c + ex] = True

    d_nom = (bico_b - bico_a) * py
    return Padrao(
        nome="Angulo do cabecote (medida)",
        bitmap=bitmap,
        grade=_grade(n_col, n_bicos, dpi_x, dpi_y),
        descricao=(
            f"Duas linhas horizontais, dos bicos {bico_a} e {bico_b}, numa "
            "passada so. Meca a distancia entre elas, centro a centro: ela "
            "da o angulo real do cabecote. As linhas verticais a direita "
            "devem sair retas com o angulo certo."),
        referencia={
            "bicos": f"{bico_a} e {bico_b}",
            "comprimento das linhas (mm)": round(larg_h * px, 2),
            "distancia nominal (mm)": round(d_nom, 4),
            "linhas verticais": n_verticais,
        },
        legenda=[
            "Meca em varios pontos ao longo das linhas e use a media.",
            "angulo = asen(D / ((B - A) x 0,1371 mm))",
        ])


def angulo_medido(distancia_mm: float, bico_a: int, bico_b: int,
                  passo_bico_mm: float) -> float:
    """Distancia medida entre as linhas dos bicos a e b -> angulo em graus."""
    if bico_b <= bico_a:
        raise ValueError("o bico B tem que vir depois do A")
    s = distancia_mm / ((bico_b - bico_a) * passo_bico_mm)
    if not 0.0 < s <= 1.0:
        raise ValueError(
            f"{distancia_mm} mm nao cabe na geometria do cabecote: o maximo "
            f"entre esses bicos e {(bico_b - bico_a) * passo_bico_mm:.3f} mm")
    return math.degrees(math.asin(s))


# --------------------------------------------------------------------------
# 3. Grade dimensional e linhas verticais
# --------------------------------------------------------------------------

def grade_dimensional(dpi_x: float, dpi_y: float,
                      largura_mm: float = 50.0, altura_mm: float = 50.0,
                      espacamento_mm: float = 10.0,
                      espessura_mm: float = 0.3) -> Padrao:
    """
    Quadriculado com espacamento conhecido, para escanear o impresso e
    conferir a escala nos dois eixos (e o esquadro). Imprime pelo caminho
    normal, em quantas faixas a altura pedir.
    """
    if espacamento_mm <= 0 or largura_mm < espacamento_mm \
            or altura_mm < espacamento_mm:
        raise ValueError("o espacamento tem que caber na largura e na altura")
    px = 25.4 / dpi_x
    py = 25.4 / dpi_y
    ex = max(1, int(round(espessura_mm / px)))
    ey = max(1, int(round(espessura_mm / py)))
    # A ultima linha cabe inteira: nenhuma e empurrada para dentro, senao o
    # ultimo intervalo sairia mais curto que o nominal
    n_col = int(round(largura_mm / px)) + ex
    n_lin = int(round(altura_mm / py)) + ey

    bitmap = np.zeros((n_lin, n_col), dtype=bool)
    xs = np.arange(0.0, largura_mm + 1e-9, espacamento_mm)
    ys = np.arange(0.0, altura_mm + 1e-9, espacamento_mm)
    for x in xs:
        c = int(round(x / px))
        bitmap[:, c:c + ex] = True
    for y in ys:
        lin = int(round(y / py))
        bitmap[lin:lin + ey, :] = True

    return Padrao(
        nome="Grade dimensional",
        bitmap=bitmap,
        grade=_grade(n_col, n_lin, dpi_x, dpi_y),
        descricao=(
            "Quadriculado de espacamento conhecido. Escaneie e meca a "
            "distancia entre linhas nos dois eixos, longe das bordas: o erro "
            "de escala e (medido - nominal) / nominal."),
        referencia={
            "area nominal (mm)": f"{largura_mm:g} x {altura_mm:g}",
            "espacamento nominal (mm)": espacamento_mm,
            "linhas verticais": len(xs),
            "linhas horizontais": len(ys),
            "espessura (mm)": espessura_mm,
            "da 1a a ultima linha vertical (mm)": round(float(xs[-1]), 3),
            "da 1a a ultima linha horizontal (mm)": round(float(ys[-1]), 3),
        })


def linhas_verticais(n_bicos: int, dpi_x: float, dpi_y: float,
                     passo_faixa_mm: float,
                     faixas: int = 4, largura_mm: float = 40.0,
                     espacamento_mm: float = 2.0,
                     espessura_mm: float = 0.2) -> Padrao:
    """
    Linhas verticais atravessando varias faixas, para avaliar o
    deslocamento a cada avanco de Y: numa maquina certa cada linha sai reta
    e continua. Degrau numa emenda = a faixa seguinte caiu deslocada em X;
    linhas inclinadas dentro da faixa = angulo do cabecote diferente do
    usado na compensacao. Marcas curtas a esquerda apontam as emendas.

    As linhas do bitmap seguem a grade de uma faixa (n_bicos linhas por
    faixa), de modo que a marca k cai exatamente no primeiro bico da faixa k.
    """
    if faixas < 2:
        raise ValueError("sao precisas ao menos 2 faixas para haver emenda")
    px = 25.4 / dpi_x
    py = 25.4 / dpi_y
    marca = max(1, int(round(3.0 / px)))
    folga = max(1, int(round(1.0 / px)))
    n_lin = faixas * n_bicos
    ex = max(1, int(round(espessura_mm / px)))
    larg = int(round(largura_mm / px)) + ex
    n_col = marca + folga + larg

    bitmap = np.zeros((n_lin, n_col), dtype=bool)
    xs = np.arange(0.0, largura_mm + 1e-9, espacamento_mm)
    for x in xs:
        c = marca + folga + int(round(x / px))
        bitmap[:, c:c + ex] = True
    for f in range(1, faixas):
        bitmap[f * n_bicos, :marca] = True

    return Padrao(
        nome="Linhas verticais (deslocamento entre faixas)",
        bitmap=bitmap,
        grade=_grade(n_col, n_lin, dpi_x, dpi_y),
        descricao=(
            "Linhas verticais atravessando as emendas entre faixas. Cada "
            "linha deve sair reta e continua; degrau na emenda indica "
            "deslocamento em X ao avancar o Y."),
        referencia={
            "faixas": faixas,
            "emendas": faixas - 1,
            "avanco entre faixas (mm)": round(passo_faixa_mm, 4),
            "altura nominal (mm)": round(
                (faixas - 1) * passo_faixa_mm + n_bicos * py, 3),
            "linhas": len(xs),
            "espacamento (mm)": espacamento_mm,
            "espessura (mm)": espessura_mm,
        },
        legenda=["As marcas curtas a esquerda ficam no primeiro bico de "
                 "cada faixa, logo depois (em Y) de cada emenda."])


# --------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------

def padrao_para_unidades(p: Padrao, x0_mm: float, y0_mm: float,
                         z_mm: float):
    """
    Padrao -> unidades do envio, com o canto em (x0, y0) da mesa.

    Com passadas, cada uma vira uma unidade de exatamente n_bicos linhas no
    Y dela (uma swath cada). Sem passadas, o bitmap inteiro vai como uma
    unidade so, e o envio divide em faixas como faz com uma imagem.
    """
    from .envio import Unidade
    g = p.grade
    if not p.passadas:
        return [Unidade(rotulo=p.nome, bitmap=p.bitmap,
                        grade=GradeRaster(x0_mm, y0_mm, g.dpi_x, g.dpi_y,
                                          g.n_colunas, g.n_linhas),
                        z_mm=z_mm)]
    return [Unidade(rotulo=f"{p.nome} / passada {pa.grupo + 1}",
                    bitmap=pa.bitmap,
                    grade=GradeRaster(x0_mm + pa.x_mm, y0_mm + pa.y_mm,
                                      g.dpi_x, g.dpi_y,
                                      pa.bitmap.shape[1], pa.bitmap.shape[0]),
                    z_mm=z_mm)
            for pa in p.passadas]
