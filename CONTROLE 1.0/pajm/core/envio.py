"""
Do bitmap fatiado ate os quadros do protocolo v1.1.

Substitui `envio.py`, que falava a minha proposta antiga. As diferencas que
obrigaram a reescrita:

1. **Unidades em passos.** O protocolo nao tem ponto flutuante nem milimetro:
   posicoes e velocidades vao em passos, e a conversao usa o `steps_per_m`
   que o firmware informa no HELLO_ACK.

2. **Z nao vai no cabecalho da swath.** A altura de cada camada e comandada
   por um MOVE(Z) antes das swaths daquela camada. A minha proposta antiga
   levava Z em cada faixa; a do firmware e mais simples e e a que vale.

3. **Recuo de aceleracao.** O firmware recusa a swath se `x_start - d` sair do
   curso, com `d = ceil(speed^2 / 2*ACCEL) + RUNUP_MARGIN`. A peca precisa de
   espaco livre antes e depois dela so para o eixo acelerar e frear. A
   validacao acontece aqui, antes de mandar, para o erro aparecer na tela em
   vez de abortar o job no meio.

4. **Sem bidirecional.** O firmware v1.0 recusa `dir = 1`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..machine import protocolo as pv
from ..machine.sessao import Evento, Sessao
from ..profile import Perfil
from . import codificador as cod
from .fatiador import CompensacaoBicos, GradeRaster


@dataclass(frozen=True)
class Unidade:
    """
    Uma coisa imprimivel: uma camada, ou uma chapa de camadas lado a lado.

    No modo empilhado cada camada e uma unidade e tem o seu Z. No modo de
    camadas lado a lado cada chapa e uma unidade e todas compartilham o Z.
    Dos dois jeitos o caminho daqui para baixo e identico.
    """
    rotulo: str
    bitmap: "np.ndarray"
    grade: GradeRaster
    z_mm: float

__all__ = ["Unidade", "PlanoEnvio", "planejar", "aplicar_hello_ack",
           "gerar_swaths", "Swath"]


@dataclass(frozen=True)
class Swath:
    """Uma passada pronta, ja em passos."""
    swath_id: int
    unidade: str
    z_passos: int
    y_passos: int
    x_start_passos: int
    pitch: int
    colunas: bytes

    @property
    def n_cols(self) -> int:
        return len(self.colunas) // pv.BYTES_POR_COLUNA


@dataclass
class PlanoEnvio:
    """O que sera enviado, conferido antes de mandar o primeiro byte."""
    n_unidades: int
    n_swaths: int
    bytes_totais: int
    pitch: int
    dpi_x_real: float
    vel_passos_s: int
    periodo_disparo_us: int
    recuo_passos: int
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def viavel(self) -> bool:
        return not self.erros

    @property
    def recuo_mm(self) -> float:
        return self._recuo_mm

    _recuo_mm: float = 0.0


def aplicar_hello_ack(perfil: Perfil, ha: pv.HelloAck) -> list[str]:
    """
    Copia para o perfil o que o firmware declarou, e diz o que mudou.

    Passos por mm e curso dos eixos deixam de ser pendencia no momento em que
    a maquina responde: ela e a fonte da verdade, nao o nosso palpite. E e o
    que faz o DPI em X do fatiador casar com o `pitch` que o firmware executa.
    """
    mudancas = []
    m = perfil.movimento
    for eixo, chave in ((pv.EIXO_X, "passos_por_mm_x"),
                        (pv.EIXO_Y, "passos_por_mm_y"),
                        (pv.EIXO_Z, "passos_por_mm_z")):
        novo = ha.passos_por_mm(eixo)
        if getattr(m, chave) != novo:
            mudancas.append(f"{chave}: {getattr(m, chave)} -> {novo:g}")
            setattr(m, chave, novo)

    # O curso da maquina inclui as zonas mortas antes da mesa. Em X e Y o
    # perfil guarda a AREA UTIL da mesa: a maquina so a limita (o que sobra
    # do curso depois da origem da mesa), nao a substitui.
    origem = {pv.EIXO_X: m.origem_mesa_x_mm, pv.EIXO_Y: m.origem_mesa_y_mm}
    for eixo, chave in ((pv.EIXO_X, "curso_x_mm"), (pv.EIXO_Y, "curso_y_mm"),
                        (pv.EIXO_Z, "curso_z_mm")):
        novo = ha.travel[eixo] / ha.passos_por_mm(eixo)
        if eixo in origem:
            novo -= origem[eixo]
            atual = getattr(m, chave)
            if atual is not None and atual <= novo:
                continue
        if getattr(m, chave) != novo:
            mudancas.append(f"{chave}: {getattr(m, chave)} -> {novo:g} mm")
            setattr(m, chave, novo)
    return mudancas


def planejar(sessao: Sessao, perfil: Perfil, unidades: list[Unidade],
             compensacao: CompensacaoBicos, velocidade_mm_s: float,
             accel_passos_s2: int, runup_margin: int,
             min_fire_period_us: int,
             fire_delay_us: int = 0,
             vmax_x_passos_s: int | None = None) -> PlanoEnvio:
    """
    Confere tudo que o firmware conferiria, antes de mandar qualquer byte.

    `vmax_x_passos_s` e o VMAX_X da maquina: o SWATH_BEGIN com velocidade
    acima dele e recusado com BAD_PARAM (detalhe 16). Sem ele o job comecava
    e abortava na primeira swath.
    """
    cab = perfil.cabecote
    erros: list[str] = []
    avisos: list[str] = []
    ha = sessao.hello_ack
    if ha is None:
        return PlanoEnvio(0, 0, 0, 0, 0.0, 0, 0, 0,
                          ["sem sessao aberta com a maquina"])

    pitch = int(perfil.processo.micropassos_por_disparo)
    passos_mm_x = ha.passos_por_mm(pv.EIXO_X)
    dpi_x_real = 25.4 * passos_mm_x / pitch
    vel = int(round(velocidade_mm_s * passos_mm_x))

    periodo_us = int(10 ** 6 * pitch / vel) if vel else 0
    recuo = pv.recuo_de_aceleracao(vel, accel_passos_s2, runup_margin) \
        if vel else 0
    recuo_mm = recuo / passos_mm_x

    if vel <= 0:
        erros.append("velocidade de impressao tem que ser maior que zero")
    if vmax_x_passos_s is not None and vel > vmax_x_passos_s:
        erros.append(
            f"a velocidade de impressao ({velocidade_mm_s:g} mm/s) passa da "
            f"maxima do eixo X da maquina "
            f"({vmax_x_passos_s / passos_mm_x:g} mm/s). Reduza a velocidade.")
    if periodo_us < min_fire_period_us:
        v_max = 10 ** 6 * pitch / min_fire_period_us / passos_mm_x
        erros.append(
            f"a {velocidade_mm_s:.0f} mm/s com pitch {pitch} o periodo de "
            f"disparo fica em {periodo_us} us, abaixo do minimo de "
            f"{min_fire_period_us} us do cabecote. Reduza para no maximo "
            f"{v_max:.0f} mm/s, ou aumente o pitch.")
    if fire_delay_us >= periodo_us > 0:
        erros.append(
            f"o atraso de disparo ({fire_delay_us} us) tem que ser menor que "
            f"o periodo entre colunas ({periodo_us} us)")

    # As camadas foram rasterizadas num DPI em X; a maquina vai executa-las
    # com `pitch` passos entre colunas. Se os dois nao baterem, cada coluna
    # vale uma distancia diferente da prevista e a peca sai com a escala
    # errada em X, sem nada acusar. Acontece de verdade: basta a maquina
    # informar um `steps_per_m` diferente do que o perfil supunha ao fatiar.
    if unidades:
        dpi_fatiado = unidades[0].grade.dpi_x
        if abs(dpi_fatiado - dpi_x_real) > 0.5:
            erros.append(
                f"as camadas foram fatiadas a {dpi_fatiado:.1f} dpi em X, mas "
                f"com pitch {pitch} a {passos_mm_x:g} passos/mm a maquina "
                f"executaria a {dpi_x_real:.1f} dpi. A peca sairia com a "
                f"escala errada em X. Refatie depois de conectar, para o "
                "fatiamento usar os passos por mm que a maquina informou.")

    n_swaths = 0
    bytes_totais = 0
    col_extra = int(compensacao.colunas.max())
    for u in unidades:
        faixas = int(np.ceil(u.bitmap.shape[0] / cab.n_bicos))
        n_cols = int(u.bitmap.shape[1]) + col_extra
        n_swaths += faixas
        bytes_totais += faixas * n_cols * pv.BYTES_POR_COLUNA
        if n_cols > ha.max_cols:
            erros.append(
                f"{u.rotulo} tem {n_cols} colunas, acima do maximo de "
                f"{ha.max_cols} que o firmware aceita por swath")

    # Z: a altura da camada vem da geometria da peca, que pode estar em
    # qualquer lugar do espaco, inclusive abaixo de zero. O Z da maquina vai
    # de 0 (endstop) ate o curso. Sem esta checagem o job abortava no meio,
    # com OUT_OF_TRAVEL vindo do firmware, em vez de avisar antes de comecar.
    if unidades:
        pmz = ha.passos_por_mm(pv.EIXO_Z)
        zs = [u.z_mm for u in unidades]
        z_min_passos = int(round(min(zs) * pmz))
        z_max_passos = int(round(max(zs) * pmz))
        if z_min_passos < 0:
            erros.append(
                f"a peca tem camadas em Z negativo (a mais baixa em "
                f"{min(zs):.2f} mm). O Z da maquina comeca em zero, no "
                "endstop. Ligue 'Assentar na plataforma' para apoiar a peca.")
        if z_max_passos > ha.travel[pv.EIXO_Z]:
            erros.append(
                f"a peca chega a {max(zs):.2f} mm em Z, acima do curso util "
                f"de {ha.travel[pv.EIXO_Z] / pmz:.1f} mm.")

    # Posicionamento: a peca precisa de espaco para acelerar e frear. As
    # coordenadas da peca sao da mesa; na maquina, somam a origem da mesa.
    # Todas as unidades: os padroes de teste tem uma por passada, cada uma
    # num lugar diferente.
    if unidades and vel > 0:
        ox = perfil.movimento.origem_mesa_x_mm
        oy = perfil.movimento.origem_mesa_y_mm
        pmy = ha.passos_por_mm(pv.EIXO_Y)
        x_ini = min(u.grade.x0_mm for u in unidades)
        x0_min = int(round((x_ini + ox) * passos_mm_x))
        x_fim = max(
            int(round((u.grade.x0_mm + ox) * passos_mm_x))
            + (int(u.bitmap.shape[1]) + col_extra - 1) * pitch
            for u in unidades)
        if x0_min - recuo < 0:
            erros.append(
                f"a peca comeca a {x_ini:.1f} mm em X, mas a "
                f"{velocidade_mm_s:.0f} mm/s o eixo precisa de "
                f"{recuo_mm:.1f} mm so para acelerar. Afaste a peca da origem "
                f"ou reduza a velocidade.")
        if x_fim + recuo > ha.travel[pv.EIXO_X]:
            erros.append(
                f"a peca termina perto demais do fim do curso em X: faltam "
                f"{recuo_mm:.1f} mm para o eixo frear.")
        y_pri = min(oy + u.grade.y0_mm for u in unidades)
        y_ult = max(
            oy + u.grade.y0_mm
            + (int(np.ceil(u.bitmap.shape[0] / cab.n_bicos)) - 1)
            * perfil.processo.passo_faixa_mm(cab.n_bicos, u.grade.passo_y_mm)
            for u in unidades)
        if y_pri < 0 or y_ult * pmy > ha.travel[pv.EIXO_Y]:
            erros.append(
                f"a peca sai do curso em Y: a ultima faixa ficaria a "
                f"{y_ult:.1f} mm do endstop, e o curso e de "
                f"{ha.travel[pv.EIXO_Y] / pmy:.0f} mm.")

    if n_swaths > 0 and not erros:
        avisos.append(
            f"DPI em X efetivo: {dpi_x_real:.1f} (pitch {pitch} a "
            f"{passos_mm_x:g} passos/mm). Periodo de disparo "
            f"{periodo_us} us.")

    p = PlanoEnvio(
        n_unidades=len(unidades), n_swaths=n_swaths,
        bytes_totais=bytes_totais, pitch=pitch, dpi_x_real=dpi_x_real,
        vel_passos_s=vel, periodo_disparo_us=periodo_us,
        recuo_passos=recuo, erros=erros, avisos=avisos)
    p._recuo_mm = recuo_mm
    return p


def gerar_swaths(unidades: list[Unidade], perfil: Perfil,
                 compensacao: CompensacaoBicos, ha: pv.HelloAck,
                 pitch: int):
    """
    Unidades -> swaths prontas, em passos, uma de cada vez.

    Gerador: um job pode ter dezenas de milhares de swaths e materializar
    todas antes de enviar traria de volta o problema de memoria que ja custou
    caro uma vez.

    O Y de cada swath e arredondado a partir da posicao ABSOLUTA, nunca somando
    incrementos: o passo entre faixas (70,6 um com o cabecote a 31 graus) nao
    e multiplo do micropasso (25 um), e somar arredondamentos acumularia erro
    ao longo das dezenas de faixas de uma peca.
    """
    cab = perfil.cabecote
    ox = perfil.movimento.origem_mesa_x_mm
    oy = perfil.movimento.origem_mesa_y_mm
    pmx = ha.passos_por_mm(pv.EIXO_X)
    pmy = ha.passos_por_mm(pv.EIXO_Y)
    pmz = ha.passos_por_mm(pv.EIXO_Z)
    swath_id = 0

    for u in unidades:
        blocos = cod.dividir_em_faixas(u.bitmap, cab.n_bicos)
        z_passos = int(round(u.z_mm * pmz))
        for k, bloco in enumerate(blocos):
            mat = cod.montar_matriz(bloco, u.grade.dpi_x, u.grade.dpi_y,
                                    compensacao)
            y_mm = oy + u.grade.y0_mm + k * perfil.processo.passo_faixa_mm(
                cab.n_bicos, u.grade.passo_y_mm)
            yield Swath(
                swath_id=swath_id & 0xFFFF,
                unidade=u.rotulo,
                z_passos=z_passos,
                y_passos=int(round(y_mm * pmy)),
                x_start_passos=int(round((ox + u.grade.x0_mm) * pmx)),
                pitch=pitch,
                colunas=cod.empacotar(mat, cab).tobytes())
            swath_id += 1


def contar_swaths(unidades: list[Unidade], n_bicos: int) -> int:
    return sum(int(np.ceil(u.bitmap.shape[0] / n_bicos)) for u in unidades)


def enviar_unidades(sessao: Sessao, perfil: Perfil, unidades: list[Unidade],
                    compensacao: CompensacaoBicos, plano: PlanoEnvio,
                    job_id: int = 1, fire_delay_us: int = 0,
                    progresso=None, cancelado=None) -> dict:
    """
    Manda o job inteiro: JOB_BEGIN, preparo, swaths, JOB_END.

    Vive aqui, e nao na interface, porque o caminho e identico para uma peca
    fatiada e para uma imagem: as duas chegam como lista de unidades. Ter uma
    copia em cada painel seria duas chances de divergir.

    `progresso(enviadas, total, rotulo)` e chamado a cada swath; a interface
    aproveita para redesenhar. `cancelado()` devolve True para abortar.

    Devolve o que aconteceu. Nao levanta excecao por erro da maquina: os
    codigos de ERR voltam na lista, porque a interface quer mostra-los, nao
    tratar excecao.
    """
    erros: list[str] = []
    ha = sessao.hello_ack
    if ha is None:
        return {"enviadas": 0, "erros": ["sem sessao aberta"],
                "cancelado": False}

    def pedido_cancelar() -> bool:
        # Do painel que enviou, ou um CANCEL mandado por fora (barra superior)
        return bool(cancelado and cancelado()) or sessao.cancelamento_pedido

    def servir() -> bool:
        """Bombeia e diz se deve continuar."""
        for aviso in sessao.bombear():
            if aviso.tipo == Evento.ERRO:
                erros.append(str(aviso.dado))
            elif aviso.tipo == Evento.ENLACE_PERDIDO:
                erros.append(f"enlace perdido: {aviso.dado}")
        return not erros and not pedido_cancelar()

    proc = perfil.processo
    vel_x = max(1, sessao.para_passos(pv.EIXO_X, 50.0))
    vel_y = max(1, sessao.para_passos(pv.EIXO_Y, 50.0))

    sessao.iniciar_job(job_id=job_id, total_swaths=plano.n_swaths)
    sessao.referenciar("xyz")
    sessao.cabecote(True)

    # Purga: o HOME deixa o carro no zero, fora da mesa, onde fica o algodao.
    # Os MOVE so deixam isso explicito, caso a ordem acima mude um dia.
    if proc.purga_disparos > 0:
        sessao.enfileirar(pv.Op.MOVE, pv.p_move(pv.EIXO_X, 0, vel_x))
        sessao.enfileirar(pv.Op.MOVE, pv.p_move(pv.EIXO_Y, 0, vel_y))
        sessao.disparar_parado(min(int(proc.purga_disparos), 0xFFFF),
                               int(proc.purga_periodo_us),
                               b"\xff" * pv.BYTES_POR_COLUNA)

    z_atual = None
    enviadas = 0
    vel_z = max(1, sessao.para_passos(pv.EIXO_Z, 5.0))

    for sw in gerar_swaths(unidades, perfil, compensacao, ha, plano.pitch):
        # Segura enquanto a janela e os creditos nao abrem. Sem isso, a fila
        # do PC cresce sem limite num job de centenas de MB.
        while sessao.pendentes > pv.WINDOW * 2:
            if not servir():
                break
            if progresso:
                progresso(enviadas, plano.n_swaths, sw.unidade)
        if not servir():
            break

        if sw.z_passos != z_atual:
            sessao.enfileirar(
                pv.Op.MOVE, pv.p_move(pv.EIXO_Z, sw.z_passos, vel_z))
            z_atual = sw.z_passos

        sessao.enviar_swath(
            sw.swath_id, sw.y_passos, sw.x_start_passos, sw.pitch,
            sw.colunas, fire_delay_us=fire_delay_us,
            vel_passos_s=plano.vel_passos_s)
        enviadas += 1
        if progresso:
            progresso(enviadas, plano.n_swaths, sw.unidade)

    foi_cancelado = pedido_cancelar()
    if erros or foi_cancelado:
        # Se o CANCEL ja foi mandado por fora, nao repete: um segundo
        # cancelamento descartaria o que quem cancelou enfileirou depois
        # (o recolhimento do carro)
        if not sessao.cancelamento_pedido:
            sessao.cancelar()
    else:
        # Apresentacao: carro no zero e mesa para frente, peca a vista
        if proc.apresentar_ao_fim:
            y_fim = min(sessao.para_passos(pv.EIXO_Y, proc.apresentar_y_mm),
                        ha.travel[pv.EIXO_Y])
            sessao.enfileirar(pv.Op.MOVE, pv.p_move(pv.EIXO_X, 0, vel_x))
            sessao.enfileirar(pv.Op.MOVE,
                              pv.p_move(pv.EIXO_Y, max(0, y_fim), vel_y))
        sessao.terminar_job()

    return {"enviadas": enviadas, "erros": erros, "cancelado": foi_cancelado}
