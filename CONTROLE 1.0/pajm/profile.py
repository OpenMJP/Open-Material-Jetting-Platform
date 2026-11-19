"""
Perfil de maquina PAJM.

REGRA DESTE ARQUIVO: nenhum valor de hardware e inventado.
Cada constante traz a FONTE no comentario. O que ainda nao foi definido fica
None e aparece na lista de pendencias, com a fonte de onde obter e o que
aquilo bloqueia.

FONTES
  [DS]  Xaar 128 datasheet, Issue 10, July 2023 ("xaar doc.pdf")
  [GO]  XJ128 / XJ128 Plus Guide to Operation, Xaar doc. D031010302 ver. A
        ("xaar manual.pdf")
  [PCB] Projeto PAJM: "PCB PAJM V2/PAJM PCB.xlsx" e esquematico pajm_V2.0
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path


# --------------------------------------------------------------------------
# Variantes do Xaar 128  [DS]
# --------------------------------------------------------------------------
# A variante e identificavel pela COR DO ISOLANTE do cabecote fisico.
# O angulo e a faixa sao os valores de projeto do fabricante para impressao
# em passada unica na resolucao nominal.

VARIANTES: dict[str, dict] = {
    "128/80L": dict(cor="azul", gota_pl=80.0, angulo_deg=71.6,
                    faixa_mm=16.5, dpi_nominal=200.0, freq_khz=4.25,
                    placa="nao-molhante"),
    "128/40L": dict(cor="cinza escuro", gota_pl=40.0, angulo_deg=31.0,
                    faixa_mm=9.0, dpi_nominal=360.0, freq_khz=8.3,
                    placa="nao-molhante"),
    "128/80W": dict(cor="roxo", gota_pl=80.0, angulo_deg=71.6,
                    faixa_mm=16.5, dpi_nominal=200.0, freq_khz=5.5,
                    placa="molhante"),
    "128/40W": dict(cor="cinza claro", gota_pl=40.0, angulo_deg=31.0,
                    faixa_mm=9.0, dpi_nominal=360.0, freq_khz=8.3,
                    placa="molhante"),

    # Familias. Dentro de cada familia, TODO parametro que o fatiamento usa e
    # identico -- gota, angulo, faixa, DPI e frequencia. A unica diferenca
    # entre L e W e o revestimento da placa dos bicos, que e questao de
    # operacao e manutencao da tinta, nao de fatiamento.
    # Servem para quando se sabe a familia pela cor (cinza = /40, azul ou
    # roxo = /80) sem ter certeza do revestimento.
    "128/40": dict(cor="cinza (claro ou escuro)", gota_pl=40.0,
                   angulo_deg=31.0, faixa_mm=9.0, dpi_nominal=360.0,
                   freq_khz=8.3, placa=None),
    "128/80": dict(cor="azul ou roxo", gota_pl=80.0, angulo_deg=71.6,
                   faixa_mm=16.5, dpi_nominal=200.0, freq_khz=4.25,
                   placa=None),
}


# --------------------------------------------------------------------------
# Mesas conhecidas
# --------------------------------------------------------------------------
# Area util de impressao, em mm. Nao sao medidas da maquina de voces: sao os
# tamanhos de catalogo de cada modelo, para servirem de ponto de partida.
# O valor que vale e o medido na maquina.

MESAS: dict[str, tuple[float, float]] = {
    "Prusa i3 e clones": (200.0, 200.0),
    "Prusa i3 MK2 / MK3 / MK4": (250.0, 210.0),
    "Clones recentes / Ender": (220.0, 220.0),
    "Prusa Mini": (180.0, 180.0),
}

# A PAJM usa a mesa de 200 x 200 mm da Prusa i3.
MESA_PADRAO = "Prusa i3 e clones"


@dataclass(frozen=True)
class Pendencia:
    chave: str
    o_que: str
    fonte: str
    bloqueia: tuple[str, ...]


# --------------------------------------------------------------------------
# Cabecote
# --------------------------------------------------------------------------

@dataclass
class Cabecote:
    modelo: str = "Xaar 128"

    # ---- confirmado pelo datasheet e pelo manual -------------------------

    n_bicos: int = 128                     # [DS] 128 bicos ativos
    passo_bico_mm: float = 0.1371          # [DS] 137.1 um, 185 npi

    # [PCB] nSS1/nSS2.  [GO] 6.2.2: "A low level selects the corresponding
    # 64-channel driver chip".
    n_chips: int = 2
    bits_por_chip: int = 64

    # [GO] tabela 6.8: parede compartilhada impede disparar canais adjacentes
    # ao mesmo tempo, entao os bicos sao divididos em 3 ciclos:
    #   A = canais 1,4,7...126   B = 2,5,8...127   C = 3,6,9...128
    # ou seja, fase = (canal - 1) mod 3.
    n_fases: int = 3

    # [GO] tabela 6.10: atraso entre ciclos do XJ128. Fixo, nao configuravel.
    atraso_ciclo_s: float = 60e-6

    # [GO] tabela 6.12, T2 "print cycle time", maximo 180 us.
    # 180 us = 3 x 60 us  ->  taxa maxima de nFIRE = 5.56 kHz.
    tempo_ciclo_impressao_s: float = 180e-6

    # O isolante do cabecote da PAJM e CINZA, o que
    # o coloca na familia /40 (cinza escuro = 128/40L, cinza claro =
    # 128/40W). As duas sao identicas em tudo que o fatiamento usa, entao a
    # familia basta. Falta so o revestimento da placa, que e operacional.
    variante: str | None = "128/40"

    # Angulo de montagem REAL. Convencao do fabricante [DS]: medido de forma
    # que   passo_efetivo = passo_bico * sen(angulo)
    # 90 graus = cabecote perpendicular ao eixo de varredura (185 dpi nativo).
    # Calibrado na maquina em 03/11/2026 com o teste visual de angulo (aba
    # Calibracao): a opcao do meio, 31 graus, saiu sem degrau. A resolucao
    # do teste e de ~0,45 grau. None volta ao angulo de projeto da variante.
    angulo_montagem_deg: float | None = 31.0

    # Se o canal crescente aponta para +Y (1) ou -Y (-1) da maquina.
    # Conferido na maquina em 27/10/2026: o bico 1 fica do lado do Y menor,
    # entao o canal cresce para +Y.
    #
    # [GO] 4.1: "When looking towards the nozzle plate, the nozzles are
    # numbered from right to left". A placa tem 134 bicos visiveis, dos quais
    # 128 ativos; ao lado do bico 1 ha 4 bicos-guarda e ao lado do bico 128
    # ha apenas 2. Essa assimetria identifica a ponta na peca fisica.
    #
    # O que falta nao esta em documento: e como o cabecote ficou montado na
    # PAJM em relacao ao eixo Y. Precisa ser conferido na maquina.
    sentido_indice: int | None = 1         # +1 ou -1

    # ---- derivados -------------------------------------------------------

    @property
    def dados_variante(self) -> dict | None:
        return VARIANTES.get(self.variante) if self.variante else None

    @property
    def volume_gota_pl(self) -> float | None:
        v = self.dados_variante
        return v["gota_pl"] if v else None

    @property
    def angulo_efetivo_deg(self) -> float | None:
        """Angulo real se medido, senao o de projeto da variante."""
        if self.angulo_montagem_deg is not None:
            return self.angulo_montagem_deg
        v = self.dados_variante
        return v["angulo_deg"] if v else None

    @property
    def freq_disparo_max_hz(self) -> float | None:
        """
        O menor entre a frequencia da variante [DS] e o limite imposto pelo
        tempo de ciclo de impressao [GO] T2. Os dois documentos divergem
        (ex.: 8.3 kHz no datasheet contra 5.56 kHz pelo T2), entao ficamos
        com o conservador e sinalizamos.
        """
        limite_ciclo = 1.0 / self.tempo_ciclo_impressao_s
        v = self.dados_variante
        if not v:
            return limite_ciclo
        return min(v["freq_khz"] * 1000.0, limite_ciclo)

    @property
    def divergencia_frequencia(self) -> str | None:
        v = self.dados_variante
        if not v:
            return None
        ds = v["freq_khz"] * 1000.0
        go = 1.0 / self.tempo_ciclo_impressao_s
        if ds > go * 1.05:
            return (f"O datasheet indica {ds/1000:.2f} kHz para a variante "
                    f"{self.variante}, mas o tempo de ciclo de impressao do "
                    f"manual (T2 = {self.tempo_ciclo_impressao_s*1e6:.0f} us) "
                    f"limita a {go/1000:.2f} kHz. Usando o menor.")
        return None

    @property
    def passo_efetivo_mm(self) -> float | None:
        """Passo entre bicos projetado em Y.  [DS] passo * sen(angulo)."""
        a = self.angulo_efetivo_deg
        if a is None:
            return None
        return self.passo_bico_mm * math.sin(math.radians(a))

    @property
    def dpi_y_nativo(self) -> float | None:
        p = self.passo_efetivo_mm
        return None if not p else 25.4 / p

    @property
    def largura_faixa_mm(self) -> float | None:
        p = self.passo_efetivo_mm
        return None if p is None else self.n_bicos * p

    @property
    def avanco_x_por_bico_mm(self) -> float | None:
        """
        Deslocamento em X entre bicos consecutivos, causado pela inclinacao.
        [DS] passo * cos(angulo).
        """
        a = self.angulo_efetivo_deg
        if a is None:
            return None
        return self.passo_bico_mm * math.cos(math.radians(a))

    @property
    def cisalhamento_total_mm(self) -> float | None:
        d = self.avanco_x_por_bico_mm
        return None if d is None else (self.n_bicos - 1) * d

    def fase_do_bico(self, indice0: int) -> int:
        """
        Ciclo de disparo do bico. indice0 e 0-based; o canal e indice0 + 1.
        [GO] tabela 6.8:  A = canais 1,4,7...  B = 2,5,8...  C = 3,6,9...
        """
        return indice0 % self.n_fases

    @staticmethod
    def nome_da_fase(fase: int) -> str:
        return "ABC"[fase % 3]


# --------------------------------------------------------------------------
# Movimento
# --------------------------------------------------------------------------

@dataclass
class Movimento:
    # A4988 a 1/16 em X, Y e Z (step/dir, enable unico compartilhado),
    # fins de curso nos tres eixos, NENHUM encoder. (O [PCB] previa TMC2208;
    # a maquina montada usa A4988, conforme o firmware, maquina.h.) Sem
    # encoder, so se imprime em velocidade constante.

    # X e Y: GT2 com polia de 20 dentes a 1/16 = 80 passos/mm, confirmados
    # por medicao na maquina em 26/10/2026. Z: fuso T8 a 1/16 = 400
    # passos/mm, confirmados por medicao em 03/11/2026. Conectado, os tres
    # passam a vir do HELLO_ACK (envio.aplicar_hello_ack).
    passos_por_mm_x: float | None = 80.0
    passos_por_mm_y: float | None = 80.0
    passos_por_mm_z: float | None = 400.0

    # Valores iniciais do firmware (maquina.h): VMAX_X 250 mm/s (20 kHz de
    # passo; era 150 ate 03/11/2026) e ACCEL_X 1000 mm/s2. Sao do eixo X, o
    # da varredura. Conectado, o teto real vem do parametro VMAX_X.
    velocidade_max_mm_s: float | None = 250.0
    aceleracao_mm_s2: float | None = 1000.0

    # Area util da mesa: Prusa i3 de 200 x 200.
    # Z: 200 mm de curso medidos na maquina (firmware, maquina.h), com o
    # endstop embaixo e o bico a 1 mm da mesa no zero.
    curso_x_mm: float | None = 200.0
    curso_y_mm: float | None = 200.0
    curso_z_mm: float | None = 200.0
    mesa: str | None = MESA_PADRAO

    # Onde fica o (0, 0) da mesa dentro do curso da maquina, que tem 0 no
    # endstop. Medido em 26/10/2026: antes disso e zona morta, so para o
    # eixo acelerar. As coordenadas do fatiador sao da mesa; o envio soma
    # isto. O protocolo nao informa o valor: ele fica no perfil, e e
    # editavel na pagina Maquina.
    origem_mesa_x_mm: float = 35.0
    origem_mesa_y_mm: float = 35.0


# --------------------------------------------------------------------------
# Processo
# --------------------------------------------------------------------------

@dataclass
class Processo:
    dpi_x: float = 360.0
    velocidade_mm_s: float = 50.0
    espessura_camada_um: float = 20.0

    # Micropassos do motor de X entre um disparo e o seguinte.
    # Sem encoder, o disparo vem da contagem de pulsos de passo (ver
    # CONTEXTO-PAJM.md secao 6), entao E ESTE o parametro fisico: o DPI em X
    # e consequencia dele, nao o contrario.
    micropassos_por_disparo: int = 6

    # Correcao do avanco de Y entre faixas vizinhas, somada ao nominal
    # (n_bicos x passo efetivo). Vem do teste de alinhamento em Y: a opcao
    # sem lacuna nem sobreposicao entre as faixas.
    correcao_passo_faixa_um: float = 0.0

    # Purga antes de cada impressao: depois do HOME o cabecote fica no zero
    # (fora da mesa), onde fica o algodao, e todos os bicos disparam
    # `purga_disparos` vezes. 0 desliga a purga.
    purga_disparos: int = 1000
    purga_periodo_us: int = 500

    # Ao terminar: carro em X = 0 e mesa em Y = apresentar_y_mm (coordenada
    # da maquina, a partir do endstop), para a peca ficar a vista.
    apresentar_ao_fim: bool = True
    apresentar_y_mm: float = 200.0

    # Ao cancelar uma impressao: o Z sobe ate esta altura (ou fica onde
    # esta, se ja estiver acima) antes do carro ir para X = 0 e a mesa para
    # Y = apresentar_y_mm. Subir primeiro afasta o bico da peca.
    recolher_z_mm: float = 25.0

    def passo_faixa_mm(self, n_bicos: int, passo_y_mm: float) -> float:
        """Avanco de Y entre faixas vizinhas, ja com a correcao."""
        return n_bicos * passo_y_mm + self.correcao_passo_faixa_um / 1000.0


# --------------------------------------------------------------------------
# Perfil
# --------------------------------------------------------------------------

@dataclass
class Perfil:
    nome: str = "PAJM 1.0"
    cabecote: Cabecote = field(default_factory=Cabecote)
    movimento: Movimento = field(default_factory=Movimento)
    processo: Processo = field(default_factory=Processo)

    # ---- fisica que amarra fatiador e firmware ---------------------------

    def freq_disparo_hz(self, velocidade_mm_s: float, dpi_x: float) -> float:
        """f = v * dpi_x / 25.4. Velocidade e DPI em X nao sao livres: o
        produto e a frequencia que o cabecote precisa sustentar."""
        return velocidade_mm_s * dpi_x / 25.4

    def velocidade_max_para_dpi(self, dpi_x: float) -> float | None:
        f = self.cabecote.freq_disparo_max_hz
        return None if f is None else f * 25.4 / dpi_x

    # ---- quantizacao do DPI em X pelo micropasso --------------------------

    def dpi_x_de_micropassos(self, micropassos: int) -> float | None:
        """
        DPI em X que sai de disparar a cada N micropassos.

            espacamento = micropassos / micropassos_por_mm_x
            dpi_x       = 25.4 / espacamento

        Sem `micropassos_por_mm_x` definido, devolve None e o DPI volta a ser
        digitado a mao, com aviso.
        """
        mpm = self.movimento.passos_por_mm_x
        if not mpm or micropassos < 1:
            return None
        return 25.4 * mpm / micropassos

    def micropassos_para_dpi(self, dpi_alvo: float) -> int | None:
        """Quantos micropassos chegam mais perto do DPI pedido."""
        mpm = self.movimento.passos_por_mm_x
        if not mpm or dpi_alvo <= 0:
            return None
        return max(1, round(25.4 * mpm / dpi_alvo))

    def dpis_alcancaveis(self, n: int = 12) -> list[tuple[int, float]]:
        """
        Os DPI em X que a transmissao permite, do mais fino ao mais grosso.

        O DPI em X NAO e um numero livre quando o disparo vem da contagem de
        passos: so existem os multiplos inteiros do micropasso. Esta lista e o
        que a interface oferece no lugar de um campo onde se digita qualquer
        coisa.
        """
        mpm = self.movimento.passos_por_mm_x
        if not mpm:
            return []
        return [(k, 25.4 * mpm / k) for k in range(1, n + 1)]

    @property
    def dpi_x_efetivo(self) -> float:
        """DPI em X de fato: derivado do micropasso quando da, senao o digitado."""
        d = self.dpi_x_de_micropassos(self.processo.micropassos_por_disparo)
        return d if d is not None else self.processo.dpi_x

    def espessura_teorica_um(self, dpi_x: float, dpi_y: float) -> float | None:
        """
        Espessura depositada numa passada com 100% de preenchimento.
        gotas/mm2 = (dpi_x/25.4) * (dpi_y/25.4)
        espessura[um] = gotas/mm2 * V[pL] * 1e-3
        """
        v = self.cabecote.volume_gota_pl
        if v is None:
            return None
        return (dpi_x / 25.4) * (dpi_y / 25.4) * v * 1e-3

    def passadas_necessarias(self, dpi_x: float,
                             dpi_y: float) -> float | None:
        """Quantas passadas para atingir a espessura de camada configurada."""
        e = self.espessura_teorica_um(dpi_x, dpi_y)
        if not e:
            return None
        return self.processo.espessura_camada_um / e

    # ---- pendencias ------------------------------------------------------

    def pendencias(self) -> list[Pendencia]:
        c, m = self.cabecote, self.movimento
        out: list[Pendencia] = []

        def falta(cond, chave, o_que, fonte, *bloqueia):
            if cond:
                out.append(Pendencia(chave, o_que, fonte, tuple(bloqueia)))

        falta(c.variante is None, "cabecote.variante",
              "Qual das 4 variantes do Xaar 128 e a do laboratorio",
              "Cor do isolante do cabecote: azul=128/80L, "
              "cinza escuro=128/40L, roxo=128/80W, cinza claro=128/40W",
              "DPI em Y", "largura da faixa", "volume de gota",
              "frequencia maxima", "fatiamento")
        falta(c.angulo_montagem_deg is None, "cabecote.angulo_montagem_deg",
              "Angulo de montagem real do cabecote (graus)",
              "Imprimir o teste de angulo (pagina Calibracao) e aplicar "
              "a opcao sem degrau. Sem ele usamos o angulo de projeto da variante "
              "(71.6 ou 31.0 graus)",
              "precisao do DPI em Y", "precisao do cisalhamento")
        falta(c.sentido_indice not in (1, -1),
              "cabecote.sentido_indice",
              "Se o canal crescente aponta para +Y ou -Y da maquina",
              "Conferir na maquina montada. Para achar a ponta do bico 1 na "
              "placa: olhando de frente para os bicos eles sao numerados da "
              "direita para a esquerda, e o lado do bico 1 tem 4 bicos-guarda "
              "contra apenas 2 do lado do bico 128",
              "orientacao da peca em Y (risco de espelhamento)",
              "envio ao firmware")

        falta(m.passos_por_mm_x is None,
              "movimento.passos_por_mm_x",
              "Micropassos por mm no eixo X",
              "Medir a transmissao: passo da correia, dentes da polia e "
              "micropasso do driver. Ex.: GT2 2 mm + polia 20T + 16 "
              "micropassos = 80 micropassos/mm",
              "quantizacao do DPI em X",
              "o DPI em X so pode ser digitado a mao ate isso existir")
        falta(m.passos_por_mm_y is None,
              "movimento.passos_por_mm_y",
              "Micropassos por mm em Y",
              "Mesma medida da transmissao do eixo",
              "conversao de posicao para passos", "movimentacao manual")
        falta(m.passos_por_mm_z is None,
              "movimento.passos_por_mm_z",
              "Micropassos por mm em Z",
              "Medir na maquina: mandar um JOG conhecido e medir o "
              "deslocamento. Conectado, o valor vem do HELLO_ACK",
              "conversao de posicao para passos em Z")
        falta(m.velocidade_max_mm_s is None or m.aceleracao_mm_s2 is None,
              "movimento.velocidade_max_mm_s / aceleracao_mm_s2",
              "Velocidade maxima e aceleracao",
              "Ensaio na maquina",
              "distancia de aceleracao", "estimativa de tempo")
        falta(m.curso_x_mm is None or m.curso_y_mm is None,
              "movimento.curso_x_mm / curso_y_mm",
              "Area util da mesa (mm)",
              "Medir na maquina. Os presets em MESAS sao tamanhos de "
              "catalogo, nao medidas desta maquina",
              "modo de camadas lado a lado", "plataforma na vista 3D")
        falta(m.curso_z_mm is None,
              "movimento.curso_z_mm", "Curso util em Z (mm)",
              "Projeto mecanico da adaptacao",
              "validacao de altura da peca")

        return out

    def pronto_para_fatiar(self) -> tuple[bool, list[str]]:
        faltando = [p.o_que for p in self.pendencias()
                    if "fatiamento" in p.bloqueia]
        return (not faltando), faltando

    def avisos(self) -> list[str]:
        out = []
        d = self.cabecote.divergencia_frequencia
        if d:
            out.append(d)
        v = self.cabecote.dados_variante
        if v and v.get("placa") is None:
            out.append(
                f"Variante identificada so ate a familia {self.cabecote.variante} "
                "(cor do isolante). Para o fatiamento isso basta: gota, angulo, "
                "faixa, DPI e frequencia sao iguais nas duas. Falta apenas o "
                "revestimento da placa dos bicos (molhante ou nao-molhante), "
                "que muda a operacao e a manutencao da tinta, nao o corte.")
        if self.cabecote.angulo_montagem_deg is None and self.cabecote.variante:
            a = self.cabecote.angulo_efetivo_deg
            out.append(
                f"Usando o angulo de projeto da variante ({a:.1f} graus). "
                "Calibre o angulo real com o teste de angulo (pagina "
                "Calibracao) para fechar a precisao.")
        return out

    # ---- persistencia ----------------------------------------------------

    def salvar(self, caminho: str | Path) -> None:
        Path(caminho).write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8")

    @classmethod
    def carregar(cls, caminho: str | Path) -> "Perfil":
        d = json.loads(Path(caminho).read_text(encoding="utf-8"))

        def so_conhecidos(tipo, dados: dict) -> dict:
            # Um perfil salvo por uma versao anterior pode ter campos que ja
            # sairam (ex.: bidirecional, passadas_por_camada): sao ignorados,
            # em vez de impedir a leitura do resto
            nomes = {f.name for f in fields(tipo)}
            return {k: v for k, v in dados.items() if k in nomes}

        return cls(
            nome=d.get("nome", "PAJM 1.0"),
            cabecote=Cabecote(**so_conhecidos(Cabecote, d.get("cabecote", {}))),
            movimento=Movimento(
                **so_conhecidos(Movimento, d.get("movimento", {}))),
            processo=Processo(**so_conhecidos(Processo, d.get("processo", {}))),
        )

    # ---- ajustes de operacao ---------------------------------------------
    # Calibracao e preferencias de operacao mudam pela interface e tem de
    # sobreviver ao fechar o programa. Ficam num arquivo proprio, e nao no
    # perfil inteiro, para nao congelar os outros valores padrao do codigo.

    AJUSTES = ("correcao_passo_faixa_um", "purga_disparos",
               "purga_periodo_us", "apresentar_ao_fim", "apresentar_y_mm",
               "velocidade_mm_s", "recolher_z_mm")
    # Origem da mesa: medida na maquina, nao vem do protocolo
    AJUSTES_MOVIMENTO = ("origem_mesa_x_mm", "origem_mesa_y_mm")

    def salvar_ajustes(self, caminho: str | Path | None = None) -> None:
        caminho = Path(caminho) if caminho else arquivo_de_ajustes()
        d = {k: getattr(self.processo, k) for k in self.AJUSTES}
        # O angulo calibrado so vai se existir: None no arquivo apagaria o
        # angulo de um perfil carregado
        if self.cabecote.angulo_montagem_deg is not None:
            d["angulo_montagem_deg"] = self.cabecote.angulo_montagem_deg
        for k in self.AJUSTES_MOVIMENTO:
            d[k] = getattr(self.movimento, k)
        caminho.write_text(json.dumps(d, indent=2), encoding="utf-8")

    def carregar_ajustes(self, caminho: str | Path | None = None) -> bool:
        """Aplica os ajustes salvos. False se o arquivo nao existir."""
        caminho = Path(caminho) if caminho else arquivo_de_ajustes()
        if not caminho.exists():
            return False
        d = json.loads(caminho.read_text(encoding="utf-8"))
        for k in self.AJUSTES:
            if k in d:
                setattr(self.processo, k, type(getattr(self.processo, k))(d[k]))
        if d.get("angulo_montagem_deg") is not None:
            self.cabecote.angulo_montagem_deg = float(d["angulo_montagem_deg"])
        for k in self.AJUSTES_MOVIMENTO:
            if d.get(k) is not None:
                setattr(self.movimento, k, float(d[k]))
        return True


def arquivo_de_ajustes() -> Path:
    """ajustes_pajm.json, na pasta do programa (do .exe, se empacotado)."""
    if getattr(sys, "frozen", False):
        pasta = Path(sys.executable).resolve().parent
    else:
        pasta = Path(__file__).resolve().parent.parent
    return pasta / "ajustes_pajm.json"
