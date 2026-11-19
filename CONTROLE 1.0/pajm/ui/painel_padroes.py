"""
Pagina Calibracao: padroes de teste e calibracao.

Todos os padroes sao gerados pelo mesmo caminho de dados de uma peca fatiada
(faixas, compensacao, 16 bytes por posicao W), de proposito: um padrao que
exercitasse outro caminho nao validaria o caminho que vai imprimir.

Cada padrao tem o seu botao de imprimir. Para o alinhamento em Y isso nao e
so conveniencia: as duas metades de cada par saem em passadas separadas, com
o eixo Y deslocado entre elas, e isso so da para fazer daqui (a pagina de imagem
imprime uma faixa depois da outra, sempre com o avanco nominal).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ..core import envio as cenv
from ..core import padroes as pad
from ..core.fatiador import compensar_bicos
from ..machine import protocolo as pv
from ..profile import Perfil
from . import tema
from .comum import (abrir_arquivo, coluna_com_rodape, em_rolagem,
                    garantir_alturas, salvar_arquivo)
from .preview2d import PreviaCamada


def _spin_mm(minimo, maximo, valor, decimais=2, sufixo=" mm"):
    s = QDoubleSpinBox()
    s.setRange(minimo, maximo)
    s.setDecimals(decimais)
    s.setValue(valor)
    s.setSuffix(sufixo)
    return s


class PainelPadroes(QWidget):
    def __init__(self, perfil: Perfil, obter_maquina=None, perfil_mudou=None,
                 parent=None):
        super().__init__(parent)
        self.perfil = perfil
        self._obter_maquina = obter_maquina
        self._perfil_mudou = perfil_mudou
        self._bicos_ang: tuple[int, int] | None = None
        # Angulo em uso quando o teste visual foi gerado, com as opcoes dele
        self._ang_vis: tuple[float, int, float] | None = None
        self.padrao: pad.Padrao | None = None
        self._plano = None
        self._compensacao = None
        self._cache_params = None
        self._cancelar = False
        # Correcao do perfil quando o alinhamento em Y foi gerado: a opcao
        # escolhida se soma a ela, nao ao valor de agora.
        self._base_y_um: float | None = None
        # Padrao gerado com outra resolucao que a do perfil (so o teste de
        # bicos): micropassos por disparo para o envio, ou None
        self._micro_padrao: int | None = None

        raiz = QHBoxLayout(self)

        # ---- controles ---------------------------------------------------
        esq = QWidget()
        lay = QVBoxLayout(esq)

        self.abas = QTabWidget()
        lay.addWidget(self.abas)

        # Verificacao de bicos
        wb = QWidget()
        fb = QFormLayout(wb)
        fb.addRow(self._nota(
            "Escada de linhas numa passada so, como a verificacao de bicos "
            "das impressoras de mesa. Linha interrompida = bico entupido."))
        self.cb_segmentos = QComboBox()
        for s in (4, 8, 16, 32):
            self.cb_segmentos.addItem(f"{s} (linhas a {s} bicos)", s)
        self.cb_segmentos.setCurrentIndex(1)
        fb.addRow("Segmentos por linha", self.cb_segmentos)
        self.sp_seg = _spin_mm(0.2, 20, 2.0)
        fb.addRow("Comprimento do segmento", self.sp_seg)
        # Resolucao do teste: o DPI em X e o espacamento entre disparos, que
        # e um numero inteiro de micropassos. Vale so para este padrao: o
        # perfil continua com o seu. Demonstra o controle da resolucao, e a
        # linha reta da escada deixa medir o espacamento entre as gotas.
        self.sp_micro_b = QSpinBox()
        self.sp_micro_b.setRange(1, 64)
        self.sp_micro_b.setValue(self.perfil.processo.micropassos_por_disparo)
        self.sp_micro_b.setToolTip(
            "Micropassos do motor de X entre um disparo e o seguinte, so para "
            "este teste. Menos micropassos = gotas mais juntas = mais DPI.")
        self.sp_micro_b.valueChanged.connect(self._mostrar_resolucao_b)
        fb.addRow("Micropassos por disparo", self.sp_micro_b)
        self.lb_res_b = QLabel("-")
        self.lb_res_b.setWordWrap(True)
        fb.addRow("Resolucao em X", self.lb_res_b)
        bb = QPushButton("Gerar verificacao de bicos")
        bb.clicked.connect(self.gerar_bicos)
        fb.addRow(bb)
        self.abas.addTab(wb, "Bicos")

        # Alinhamento em X (inalterado: os dois blocos na mesma passada)
        wa = QWidget()
        fa = QFormLayout(wa)
        self.cb_eixo = QComboBox()
        self.cb_eixo.addItem("X: deslocamento ao longo da varredura", "x")
        fa.addRow("O que calibrar", self.cb_eixo)
        self.sp_opcoes = QSpinBox()
        self.sp_opcoes.setRange(3, 21)
        self.sp_opcoes.setSingleStep(2)
        self.sp_opcoes.setValue(7)
        fa.addRow("Numero de opcoes (impar)", self.sp_opcoes)
        self.sp_passo = _spin_mm(1, 2000, 20.0, 1, " um")
        fa.addRow("Passo entre opcoes", self.sp_passo)
        self.sp_bl = _spin_mm(1, 100, 12.0)
        fa.addRow("Largura do bloco", self.sp_bl)
        self.sp_ba = _spin_mm(0.2, 50, 2.5)
        fa.addRow("Altura do bloco", self.sp_ba)
        ba = QPushButton("Gerar alinhamento em X")
        ba.clicked.connect(self.gerar_alinhamento)
        fa.addRow(ba)
        fa.addRow(QLabel("<hr>"))
        fa.addRow(QLabel("<b>Depois de imprimir</b>, a opcao alinhada:"))
        self.sp_escolha = QSpinBox()
        self.sp_escolha.setRange(1, 21)
        self.sp_escolha.setValue(4)
        fa.addRow("Opcao escolhida", self.sp_escolha)
        self.lb_correcao = QLabel("-")
        fa.addRow("Correcao", self.lb_correcao)
        fa.addRow(self._nota(
            "Sem impressao bidirecional ainda nao ha onde aplicar este "
            "valor; ele fica para o registro do ensaio."))
        self.sp_escolha.valueChanged.connect(self._mostrar_correcao)
        self.sp_opcoes.valueChanged.connect(self._mostrar_correcao)
        self.sp_passo.valueChanged.connect(self._mostrar_correcao)
        self.abas.addTab(wa, "Alinh. X")
        self.abas.setTabToolTip(self.abas.indexOf(wa), "Alinhamento em X (ida e volta)")

        # Alinhamento em Y: avanco entre faixas, em passadas separadas
        wy = QWidget()
        fy = QFormLayout(wy)
        fy.addRow(self._nota(
            "Duas faixas inteiras por opcao, todos os bicos: a primeira numa "
            "passada, a segunda (acima dela) numa passada separada, com o Y "
            "avancado de uma faixa mais o deslocamento da opcao. Os entalhes "
            "brancos na segunda faixa contam o numero da opcao. Calibre o "
            "angulo antes: "
            "ele muda a largura nominal da faixa."))
        self.sp_opcoes_y = QSpinBox()
        self.sp_opcoes_y.setRange(3, 21)
        self.sp_opcoes_y.setSingleStep(2)
        self.sp_opcoes_y.setValue(7)
        fy.addRow("Numero de opcoes (impar)", self.sp_opcoes_y)
        self.sp_passo_y = _spin_mm(1, 2000, 25.0, 1, " um")
        self.sp_passo_y.setToolTip(
            "A 80 passos/mm o Y anda de 12,5 em 12,5 um: multiplos disso dao "
            "opcoes igualmente espacadas.")
        fy.addRow("Passo entre opcoes", self.sp_passo_y)
        self.sp_bl_y = _spin_mm(1, 50, 8.0)
        fy.addRow("Largura do bloco", self.sp_bl_y)
        self.sp_opac_y = QSpinBox()
        self.sp_opac_y.setRange(10, 100)
        self.sp_opac_y.setSingleStep(10)
        self.sp_opac_y.setValue(50)
        self.sp_opac_y.setSuffix(" %")
        self.sp_opac_y.setToolTip(
            "Fracao das gotas de cada faixa (meio-tom). Abaixo de 100% a "
            "sobreposicao entre as faixas aparece como uma linha mais escura; "
            "com 100% preto sobre preto nao muda nada.")
        fy.addRow("Opacidade das faixas", self.sp_opac_y)
        by = QPushButton("Gerar alinhamento em Y")
        by.clicked.connect(self.gerar_alinhamento_y)
        fy.addRow(by)
        fy.addRow(QLabel("<hr>"))
        fy.addRow(QLabel("<b>Depois de imprimir</b>, a opcao sem lacuna nem "
                         "faixa escura:"))
        self.sp_escolha_y = QSpinBox()
        self.sp_escolha_y.setRange(1, 21)
        self.sp_escolha_y.setValue(4)
        fy.addRow("Opcao escolhida", self.sp_escolha_y)
        self.lb_correcao_y = QLabel("-")
        self.lb_correcao_y.setWordWrap(True)
        fy.addRow("Nova correcao", self.lb_correcao_y)
        self.bt_aplicar_y = QPushButton("Aplicar ao perfil")
        self.bt_aplicar_y.setEnabled(False)
        self.bt_aplicar_y.clicked.connect(self.aplicar_y)
        fy.addRow(self.bt_aplicar_y)
        self.lb_atual_y = QLabel()
        fy.addRow("Correcao em uso", self.lb_atual_y)
        self.sp_escolha_y.valueChanged.connect(self._mostrar_correcao_y)

        # Angulo do cabecote: vem antes do Y na ordem de calibracao
        wn = QWidget()
        fn = QFormLayout(wn)
        fn.addRow(self._nota(
            "Grupos de linhas verticais, cada um compensado com um angulo, em "
            "duas faixas emendadas. Com o angulo errado as linhas dao um "
            "degrau na emenda; escolha o grupo que atravessa reto. As marcas "
            "na segunda faixa (a de cima) contam o numero da opcao."))
        self.sp_opcoes_ang = QSpinBox()
        self.sp_opcoes_ang.setRange(3, 21)
        self.sp_opcoes_ang.setSingleStep(2)
        self.sp_opcoes_ang.setValue(9)
        fn.addRow("Numero de opcoes (impar)", self.sp_opcoes_ang)
        self.sp_passo_ang = _spin_mm(0.05, 5, 0.5, 2, " graus")
        self.sp_passo_ang.setToolTip(
            "Uma coluna no bico 128 equivale a ~0,45 grau: passos menores "
            "fazem opcoes vizinhas sairem iguais.")
        fn.addRow("Passo entre opcoes", self.sp_passo_ang)
        bv = QPushButton("Gerar teste visual de angulo")
        bv.clicked.connect(self.gerar_angulo_visual)
        fn.addRow(bv)
        fn.addRow(QLabel("<b>Depois de imprimir</b>, o grupo sem degrau na "
                         "emenda:"))
        self.sp_escolha_ang = QSpinBox()
        self.sp_escolha_ang.setRange(1, 21)
        self.sp_escolha_ang.setValue(5)
        self.sp_escolha_ang.valueChanged.connect(self._mostrar_angulo)
        fn.addRow("Opcao escolhida", self.sp_escolha_ang)
        self.lb_angulo_vis = QLabel("-")
        self.lb_angulo_vis.setWordWrap(True)
        fn.addRow("Angulo", self.lb_angulo_vis)
        self.bt_aplicar_vis = QPushButton("Aplicar ao perfil")
        self.bt_aplicar_vis.setEnabled(False)
        self.bt_aplicar_vis.clicked.connect(
            lambda: self.aplicar_angulo(self._angulo_visual()))
        fn.addRow(self.bt_aplicar_vis)

        # Ajuste fino por medida: opcional, recolhido para a aba nao empurrar
        # o botao de imprimir para fora da tela
        self.bt_fino = QPushButton("Mostrar ajuste fino por medida")
        self.bt_fino.setCheckable(True)
        fn.addRow(self.bt_fino)
        self.w_fino = QWidget()
        ff = QFormLayout(self.w_fino)
        ff.setContentsMargins(0, 0, 0, 0)
        ff.addRow(self._nota(
            "Duas linhas, de dois bicos, numa passada so: o Y nao anda, entao "
            "a distancia entre elas depende so do angulo. Medida com "
            "paquimetro ou scanner, chega a ~0,1 grau. Escolha bicos que a "
            "verificacao mostrou funcionando."))
        n = self.perfil.cabecote.n_bicos
        self.sp_bico_a = QSpinBox()
        self.sp_bico_a.setRange(1, n)
        self.sp_bico_a.setValue(1)
        ff.addRow("Bico A", self.sp_bico_a)
        self.sp_bico_b = QSpinBox()
        self.sp_bico_b.setRange(1, n)
        self.sp_bico_b.setValue(n)
        ff.addRow("Bico B", self.sp_bico_b)
        self.sp_n_comp = _spin_mm(5, 150, 30.0, 1)
        ff.addRow("Comprimento das linhas", self.sp_n_comp)
        bn = QPushButton("Gerar teste de angulo por medida")
        bn.clicked.connect(self.gerar_angulo)
        ff.addRow(bn)
        ff.addRow(QLabel("<hr>"))
        ff.addRow(QLabel("<b>Depois de imprimir</b>, a distancia entre as "
                         "linhas, centro a centro:"))
        self.sp_dist = _spin_mm(0, 30, 0.0, 3)
        self.sp_dist.valueChanged.connect(self._mostrar_angulo)
        ff.addRow("Distancia medida", self.sp_dist)
        self.lb_angulo = QLabel("-")
        self.lb_angulo.setWordWrap(True)
        ff.addRow("Angulo medido", self.lb_angulo)
        self.bt_aplicar_ang = QPushButton("Aplicar ao perfil")
        self.bt_aplicar_ang.setEnabled(False)
        self.bt_aplicar_ang.clicked.connect(
            lambda: self.aplicar_angulo(self._angulo_novo()))
        ff.addRow(self.bt_aplicar_ang)
        fn.addRow(self.w_fino)
        self.w_fino.setVisible(False)

        def _fino(ligado):
            self.w_fino.setVisible(ligado)
            self.bt_fino.setText(("Esconder" if ligado else "Mostrar")
                                 + " ajuste fino por medida")
            self._ajustar_abas()
        self.bt_fino.toggled.connect(_fino)
        self.lb_atual_ang = QLabel()
        self.lb_atual_ang.setWordWrap(True)
        fn.addRow("Angulo em uso", self.lb_atual_ang)
        self.abas.addTab(wn, "Angulo")
        self.abas.addTab(wy, "Alinh. Y")
        self.abas.setTabToolTip(self.abas.indexOf(wy), "Alinhamento em Y: avanco entre faixas")

        # Grade dimensional
        wg = QWidget()
        fg = QFormLayout(wg)
        fg.addRow(self._nota(
            "Quadriculado de espacamento conhecido: escaneie e meca a "
            "escala e o esquadro nos dois eixos."))
        self.sp_g_larg = _spin_mm(5, 200, 50.0, 1)
        fg.addRow("Largura", self.sp_g_larg)
        self.sp_g_alt = _spin_mm(5, 200, 50.0, 1)
        fg.addRow("Altura", self.sp_g_alt)
        self.sp_g_esp = _spin_mm(1, 100, 10.0, 1)
        fg.addRow("Espacamento", self.sp_g_esp)
        self.sp_g_esp_linha = _spin_mm(0.05, 3, 0.3)
        fg.addRow("Espessura da linha", self.sp_g_esp_linha)
        bg = QPushButton("Gerar grade dimensional")
        bg.clicked.connect(self.gerar_grade)
        fg.addRow(bg)
        self.abas.addTab(wg, "Grade")

        # Linhas verticais
        wl = QWidget()
        fl = QFormLayout(wl)
        fl.addRow(self._nota(
            "Linhas verticais atravessando as emendas entre faixas. Cada "
            "linha deve sair reta e continua; degrau na emenda = "
            "deslocamento em X a cada avanco de Y."))
        self.sp_l_faixas = QSpinBox()
        self.sp_l_faixas.setRange(2, 20)
        self.sp_l_faixas.setValue(4)
        fl.addRow("Faixas", self.sp_l_faixas)
        self.sp_l_larg = _spin_mm(2, 200, 40.0, 1)
        fl.addRow("Largura", self.sp_l_larg)
        self.sp_l_esp = _spin_mm(0.5, 50, 2.0, 1)
        fl.addRow("Espacamento", self.sp_l_esp)
        self.sp_l_esp_linha = _spin_mm(0.05, 3, 0.2)
        fl.addRow("Espessura da linha", self.sp_l_esp_linha)
        bl = QPushButton("Gerar linhas verticais")
        bl.clicked.connect(self.gerar_linhas)
        fl.addRow(bl)
        self.abas.addTab(wl, "Verticais")
        self.abas.setTabToolTip(self.abas.indexOf(wl), "Linhas verticais atravessando as emendas")

        # ---- impressao ---------------------------------------------------
        g_imp = QGroupBox("Imprimir na maquina")
        f_imp = QFormLayout(g_imp)
        self.sp_x0 = _spin_mm(0, 2000, 20.0)
        self.sp_y0 = _spin_mm(0, 2000, 20.0)
        self.sp_z = _spin_mm(0, 2000, 0.0)
        f_imp.addRow("Canto em X (mesa)", self.sp_x0)
        f_imp.addRow("Canto em Y (mesa)", self.sp_y0)
        f_imp.addRow("Altura Z do jateamento", self.sp_z)
        for s in (self.sp_x0, self.sp_y0, self.sp_z):
            s.valueChanged.connect(self._atualizar_envio)
        self.lb_envio = QLabel("Gere um padrao.")
        self.lb_envio.setWordWrap(True)
        self.lb_envio.setStyleSheet("color:#9aa0a6;")
        f_imp.addRow(self.lb_envio)
        self.bt_imprimir = tema.primario(QPushButton("Imprimir este padrao"))
        self.bt_imprimir.setEnabled(False)
        self.bt_imprimir.clicked.connect(self.imprimir)
        f_imp.addRow(self.bt_imprimir)
        self.barra = QProgressBar()
        self.barra.setVisible(False)
        f_imp.addRow(self.barra)
        lay.addStretch(1)

        # Imprimir fica num rodape fixo, sempre a vista
        raiz.addWidget(coluna_com_rodape(esq, g_imp, largura_min=400))

        # ---- previa ------------------------------------------------------
        dirw = QWidget()
        ld = QVBoxLayout(dirw)
        ld.addWidget(QLabel(
            "Como o padrao cai no substrato, todas as passadas juntas, visto "
            "como a peca: Y menor (lado do bico 1) embaixo."))
        self.previa = PreviaCamada()
        ld.addWidget(self.previa, 3)

        # A referencia e longa: fica embaixo da previa, e nao na coluna dos
        # controles, onde empurrava o botao de imprimir para fora da tela
        g_ref = QGroupBox("Referencia do padrao")
        l_ref = QVBoxLayout(g_ref)
        self.tx_ref = QTextEdit()
        self.tx_ref.setReadOnly(True)
        self.tx_ref.setStyleSheet("font-family:Consolas,monospace;")
        self.tx_ref.setMinimumHeight(90)
        l_ref.addWidget(self.tx_ref)
        self.bt_salvar = QPushButton("Salvar referencia e imagem...")
        self.bt_salvar.setEnabled(False)
        self.bt_salvar.clicked.connect(self.salvar)
        l_ref.addWidget(self.bt_salvar)
        ld.addWidget(g_ref, 1)
        raiz.addWidget(dirw, 1)

        # O QTabWidget assume a altura da aba MAIS ALTA (a do angulo), e o
        # cartao de imprimir ficava longe. So a aba atual conta.
        self.abas.currentChanged.connect(self._ajustar_abas)
        self._ajustar_abas()
        garantir_alturas(self)
        self._mostrar_resolucao_b()
        self._mostrar_correcao()
        self._mostrar_correcao_y()
        self._mostrar_angulo()

    def _ajustar_abas(self, *_):
        from PySide6.QtWidgets import QSizePolicy
        atual = self.abas.currentIndex()
        for i in range(self.abas.count()):
            pol = (QSizePolicy.Policy.Preferred if i == atual
                   else QSizePolicy.Policy.Ignored)
            self.abas.widget(i).setSizePolicy(pol, pol)
        # O sizeHint do QTabWidget considera todas as abas mesmo assim:
        # a altura vem da aba atual, fixada a cada troca
        h = (self.abas.widget(atual).sizeHint().height()
             + self.abas.tabBar().sizeHint().height() + 6)
        self.abas.setFixedHeight(h)

    def _dpi_x_de(self, micropassos: int) -> float:
        d = self.perfil.dpi_x_de_micropassos(micropassos)
        return d if d is not None else self.perfil.dpi_x_efetivo

    def _mostrar_resolucao_b(self, *_):
        m = self.sp_micro_b.value()
        d = self._dpi_x_de(m)
        txt = f"<b>{d:.0f} dpi</b>, gotas a cada {25400.0 / d:.1f} um"
        do_perfil = self.perfil.processo.micropassos_por_disparo
        if m != do_perfil:
            txt += (f" (o perfil usa {self._dpi_x_de(do_perfil):.0f} dpi; "
                    "so este teste muda)")
        self.lb_res_b.setText(txt)

    def _perfil_envio(self) -> Perfil:
        """
        O perfil para planejar e enviar o padrao atual. So o teste de bicos
        pode ter outra resolucao: uma copia com os micropassos dele, para o
        pitch das swaths bater com a grade em que o padrao foi gerado.
        """
        m = self._micro_padrao
        if m is None or m == self.perfil.processo.micropassos_por_disparo:
            return self.perfil
        import copy
        p = copy.deepcopy(self.perfil)
        p.processo.micropassos_por_disparo = m
        return p

    @staticmethod
    def _nota(texto: str) -> QLabel:
        lab = QLabel(texto)
        lab.setWordWrap(True)
        lab.setStyleSheet("color:#9aa0a6;")
        return lab

    # ---------------------------------------------------------------------

    def _dpis(self) -> tuple[float, float]:
        dpi_y = self.perfil.cabecote.dpi_y_nativo or 360.0
        return self.perfil.dpi_x_efetivo, dpi_y

    def _passo_faixa_mm(self) -> float:
        _, dpi_y = self._dpis()
        return self.perfil.processo.passo_faixa_mm(
            self.perfil.cabecote.n_bicos, 25.4 / dpi_y)

    def _mostrar_correcao(self):
        n = self.sp_opcoes.value()
        self.sp_escolha.setMaximum(n)
        try:
            c = pad.correcao_escolhida(self.sp_escolha.value(), n,
                                       self.sp_passo.value())
        except ValueError:
            self.lb_correcao.setText("-")
            return
        self.lb_correcao.setText(f"<b>{c:+.1f} um</b>")

    def _nova_correcao_y(self) -> float | None:
        if self._base_y_um is None:
            return None
        try:
            d = pad.correcao_escolhida(
                self.sp_escolha_y.value(), self._n_y, self._passo_y)
        except ValueError:
            return None
        return self._base_y_um + d

    def _mostrar_correcao_y(self):
        atual = self.perfil.processo.correcao_passo_faixa_um
        self.lb_atual_y.setText(
            f"{atual:+.1f} um (avanco entre faixas "
            f"{self._passo_faixa_mm():.4f} mm)")
        nova = self._nova_correcao_y()
        if nova is None:
            self.lb_correcao_y.setText("gere e imprima o padrao primeiro")
            self.bt_aplicar_y.setEnabled(False)
            return
        self.lb_correcao_y.setText(
            f"<b>{nova:+.1f} um</b> (era {self._base_y_um:+.1f} um quando o "
            "padrao foi gerado)")
        self.bt_aplicar_y.setEnabled(True)

    def aplicar_y(self):
        nova = self._nova_correcao_y()
        if nova is None:
            return
        self.perfil.processo.correcao_passo_faixa_um = nova
        try:
            self.perfil.salvar_ajustes()
            onde = "Salvo em ajustes_pajm.json."
        except OSError as e:
            onde = f"Nao consegui salvar ({e}); vale ate fechar o programa."
        # A base passa a ser a nova: um segundo clique nao soma de novo
        self._base_y_um = nova
        self.sp_escolha_y.setValue((self._n_y + 1) // 2)
        self._mostrar_correcao_y()
        QMessageBox.information(
            self, "Correcao aplicada",
            f"Avanco entre faixas corrigido em {nova:+.1f} um. {onde}\n\n"
            "Imprima o alinhamento em Y de novo para conferir: a opcao do "
            "meio deve ser a sem lacuna.")

    def _mostrar(self, p: pad.Padrao):
        self.padrao = p
        self.bt_salvar.setEnabled(True)
        self.tx_ref.setPlainText(p.texto_referencia())
        self.previa.mostrar(
            p.bitmap, p.grade,
            f"{p.nome}   |   {p.largura_mm:.1f} x {p.altura_mm:.1f} mm   |   "
            f"{p.n_gotas} gotas"
            + (f"   |   {len(p.passadas)} passadas" if p.passadas else ""))
        self._atualizar_envio()

    def _gerar(self, fabrica, micropassos: int | None = None):
        """`micropassos`: so quando o padrao foi gerado em outra grade em X."""
        try:
            p = fabrica()
        except Exception as e:
            QMessageBox.critical(self, "Erro", str(e))
            return None
        self._micro_padrao = micropassos
        self._mostrar(p)
        return p

    def gerar_bicos(self):
        _, dpi_y = self._dpis()
        m = self.sp_micro_b.value()
        dpi_x = self._dpi_x_de(m)
        self._gerar(lambda: pad.teste_de_bicos(
            n_bicos=self.perfil.cabecote.n_bicos,
            dpi_x=dpi_x, dpi_y=dpi_y,
            segmentos=self.cb_segmentos.currentData(),
            comprimento_segmento_mm=self.sp_seg.value()), micropassos=m)

    def gerar_alinhamento(self):
        dpi_x, dpi_y = self._dpis()
        n = self.sp_opcoes.value()
        if n % 2 == 0:
            n += 1
            self.sp_opcoes.setValue(n)
        self._gerar(lambda: pad.teste_de_alinhamento(
            dpi_x=dpi_x, dpi_y=dpi_y, n_opcoes=n,
            passo_correcao_um=self.sp_passo.value(),
            bloco_largura_mm=self.sp_bl.value(),
            bloco_altura_mm=self.sp_ba.value(),
            eixo="x"))

    def gerar_alinhamento_y(self):
        dpi_x, dpi_y = self._dpis()
        n = self.sp_opcoes_y.value()
        if n % 2 == 0:
            n += 1
            self.sp_opcoes_y.setValue(n)
        p = self._gerar(lambda: pad.teste_de_alinhamento_y(
            n_bicos=self.perfil.cabecote.n_bicos,
            dpi_x=dpi_x, dpi_y=dpi_y,
            passo_faixa_mm=self._passo_faixa_mm(),
            n_opcoes=n, passo_correcao_um=self.sp_passo_y.value(),
            bloco_largura_mm=self.sp_bl_y.value(),
            opacidade=self.sp_opac_y.value() / 100.0))
        if p is not None:
            self._base_y_um = self.perfil.processo.correcao_passo_faixa_um
            self._n_y = n
            self._passo_y = self.sp_passo_y.value()
            self.sp_escolha_y.setMaximum(n)
            self.sp_escolha_y.setValue((n + 1) // 2)
            self._mostrar_correcao_y()

    def gerar_angulo_visual(self):
        dpi_x, dpi_y = self._dpis()
        cab = self.perfil.cabecote
        base = cab.angulo_efetivo_deg
        if base is None:
            QMessageBox.critical(self, "Erro", "Defina a variante do cabecote.")
            return
        n = self.sp_opcoes_ang.value()
        if n % 2 == 0:
            n += 1
            self.sp_opcoes_ang.setValue(n)
        passo = self.sp_passo_ang.value()
        # A mesma fase por bico que compensar_bicos usa, para o desenho
        # arredondar igual ao envio
        vel = self.perfil.processo.velocidade_mm_s
        fase = ((np.arange(cab.n_bicos) % cab.n_fases)
                * vel * cab.atraso_ciclo_s)
        p = self._gerar(lambda: pad.teste_de_angulo(
            cab.n_bicos, dpi_x, dpi_y,
            passo_faixa_mm=self._passo_faixa_mm(),
            passo_bico_mm=cab.passo_bico_mm, angulo_base_deg=base,
            n_opcoes=n, passo_deg=passo, fase_mm=fase))
        if p is not None:
            self._ang_vis = (base, n, passo)
            self.sp_escolha_ang.setMaximum(n)
            self.sp_escolha_ang.setValue((n + 1) // 2)
            self._mostrar_angulo()

    def _angulo_visual(self) -> float | None:
        if self._ang_vis is None:
            return None
        base, n, passo = self._ang_vis
        try:
            return pad.angulo_da_opcao(self.sp_escolha_ang.value(), n, passo,
                                       base)
        except ValueError:
            return None

    def gerar_angulo(self):
        dpi_x, dpi_y = self._dpis()
        a, b = self.sp_bico_a.value(), self.sp_bico_b.value()
        p = self._gerar(lambda: pad.teste_de_angulo_distancia(
            self.perfil.cabecote.n_bicos, dpi_x, dpi_y, bico_a=a, bico_b=b,
            comprimento_mm=self.sp_n_comp.value()))
        if p is not None:
            # A medida vale para os bicos do padrao impresso, nao para os
            # campos, que podem mudar depois
            self._bicos_ang = (a, b)
            self.sp_dist.setValue(round((b - a) * 25.4 / dpi_y, 3))
            self._mostrar_angulo()

    def _angulo_novo(self) -> float | None:
        if self._bicos_ang is None or self.sp_dist.value() <= 0:
            return None
        a, b = self._bicos_ang
        try:
            return pad.angulo_medido(self.sp_dist.value(), a, b,
                                     self.perfil.cabecote.passo_bico_mm)
        except ValueError:
            return None

    def _mostrar_angulo(self, *_):
        c = self.perfil.cabecote
        origem = ("calibrado" if c.angulo_montagem_deg is not None
                  else "de projeto da variante")
        ang = c.angulo_efetivo_deg
        self.lb_atual_ang.setText(
            "-" if ang is None else
            f"{ang:.2f} graus ({origem}); faixa de "
            f"{c.largura_faixa_mm:.3f} mm")
        vis = self._angulo_visual()
        if vis is None:
            self.lb_angulo_vis.setText("gere e imprima o padrao primeiro")
            self.bt_aplicar_vis.setEnabled(False)
        else:
            base = self._ang_vis[0]
            self.lb_angulo_vis.setText(
                f"<b>{vis:.2f} graus</b> ({vis - base:+.2f} em relacao ao "
                f"de quando o padrao foi gerado)")
            self.bt_aplicar_vis.setEnabled(True)

        novo = self._angulo_novo()
        if novo is None:
            self.lb_angulo.setText(
                "gere e imprima o padrao primeiro" if self._bicos_ang is None
                else "distancia fora da geometria do cabecote")
            self.bt_aplicar_ang.setEnabled(False)
            return
        faixa = c.n_bicos * c.passo_bico_mm * math.sin(math.radians(novo))
        txt = f"<b>{novo:.2f} graus</b>, faixa de {faixa:.3f} mm"
        if ang is not None:
            txt += f" ({novo - ang:+.2f} graus em relacao ao atual)"
        self.lb_angulo.setText(txt)
        self.bt_aplicar_ang.setEnabled(True)

    def aplicar_angulo(self, novo: float | None):
        if novo is None:
            return
        self.perfil.cabecote.angulo_montagem_deg = round(novo, 3)
        try:
            self.perfil.salvar_ajustes()
            onde = "Salvo em ajustes_pajm.json."
        except OSError as e:
            onde = f"Nao consegui salvar ({e}); vale ate fechar o programa."
        # Base nova: aplicar de novo a mesma opcao nao soma outra vez
        if self._ang_vis is not None:
            _, n, passo = self._ang_vis
            self._ang_vis = (self.perfil.cabecote.angulo_montagem_deg, n,
                             passo)
            self.sp_escolha_ang.setValue((n + 1) // 2)
        self._mostrar_angulo()
        self._mostrar_correcao_y()
        if self._perfil_mudou:
            self._perfil_mudou()
        QMessageBox.information(
            self, "Angulo aplicado",
            f"Angulo do cabecote: {novo:.2f} graus. {onde}\n\n"
            "Isso muda o DPI em Y e a largura nominal da faixa. Gere de novo "
            "o que ja estava fatiado ou processado e refaca o alinhamento em "
            "Y. Para conferir, gere e imprima o teste de novo: no visual, o "
            "grupo do meio deve atravessar a emenda sem degrau.")

    def gerar_grade(self):
        dpi_x, dpi_y = self._dpis()
        self._gerar(lambda: pad.grade_dimensional(
            dpi_x, dpi_y, largura_mm=self.sp_g_larg.value(),
            altura_mm=self.sp_g_alt.value(),
            espacamento_mm=self.sp_g_esp.value(),
            espessura_mm=self.sp_g_esp_linha.value()))

    def gerar_linhas(self):
        dpi_x, dpi_y = self._dpis()
        self._gerar(lambda: pad.linhas_verticais(
            self.perfil.cabecote.n_bicos, dpi_x, dpi_y,
            passo_faixa_mm=self._passo_faixa_mm(),
            faixas=self.sp_l_faixas.value(),
            largura_mm=self.sp_l_larg.value(),
            espacamento_mm=self.sp_l_esp.value(),
            espessura_mm=self.sp_l_esp_linha.value()))

    # ---- impressao -------------------------------------------------------

    def unidades(self) -> list:
        if self.padrao is None:
            return []
        return pad.padrao_para_unidades(
            self.padrao, self.sp_x0.value(), self.sp_y0.value(),
            self.sp_z.value())

    def _atualizar_envio(self, *_):
        self._plano = None
        self.bt_imprimir.setEnabled(False)
        if self.padrao is None:
            self.lb_envio.setText("Gere um padrao.")
            return
        sessao = self._obter_maquina() if self._obter_maquina else None
        if sessao is None or not sessao.aberta:
            self.lb_envio.setText(
                "<span style='color:#e0a030'>Bloqueado: sem conexao: conecte "
                "na barra superior</span>")
            return
        try:
            if self._cache_params is None:
                v = sessao.obter_parametros([
                    pv.Param.ACCEL_X, pv.Param.RUNUP_MARGIN,
                    pv.Param.MIN_FIRE_PERIOD, pv.Param.VMAX_X])
                self._cache_params = {
                    "accel": v[int(pv.Param.ACCEL_X)],
                    "runup": v[int(pv.Param.RUNUP_MARGIN)],
                    "min_fire": v[int(pv.Param.MIN_FIRE_PERIOD)],
                    "vmax_x": v[int(pv.Param.VMAX_X)]}
            p = self._cache_params
        except Exception as e:
            self.lb_envio.setText(
                f"<span style='color:#ff6b6b'>nao consegui ler os parametros "
                f"da maquina: {e}</span>")
            return

        vel = self.perfil.processo.velocidade_mm_s
        self._compensacao = compensar_bicos(
            self.perfil.cabecote, self.padrao.grade.dpi_x, vel)
        plano = cenv.planejar(
            sessao, self._perfil_envio(), self.unidades(), self._compensacao,
            velocidade_mm_s=vel, accel_passos_s2=p["accel"],
            runup_margin=p["runup"], min_fire_period_us=p["min_fire"],
            vmax_x_passos_s=p["vmax_x"])
        self._plano = plano
        txt = (f"{plano.n_swaths} passada(s), "
               f"{plano.bytes_totais / 1000:.0f} KB, "
               f"{plano.dpi_x_real:.0f} dpi em X")
        for e in plano.erros:
            txt += f"<br><span style='color:#ff6b6b'>{e}</span>"
        self.lb_envio.setText(txt)
        self.bt_imprimir.setEnabled(plano.viavel)

    def imprimir(self):
        sessao = self._obter_maquina() if self._obter_maquina else None
        if sessao is None or self._plano is None or not self._plano.viavel:
            return
        resp = QMessageBox.question(
            self, "Confirmar jateamento",
            "Isto vai MOVER A MAQUINA e JATEAR.\n\n"
            f"{self.padrao.nome}\n"
            f"canto em ({self.sp_x0.value():.0f}, {self.sp_y0.value():.0f}) "
            f"mm da mesa, {self._plano.n_swaths} passada(s)\n\nConfirma?")
        if resp != QMessageBox.StandardButton.Yes:
            return

        self.bt_imprimir.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, self._plano.n_swaths)
        self._cancelar = False

        def progresso(n, total, rotulo):
            self.barra.setValue(n)
            QApplication.processEvents()

        res = cenv.enviar_unidades(
            sessao, self._perfil_envio(), self.unidades(), self._compensacao,
            self._plano, progresso=progresso,
            cancelado=lambda: self._cancelar)
        self.barra.setVisible(False)
        if res["erros"]:
            QMessageBox.critical(
                self, "A maquina recusou",
                "O job foi cancelado.\n\n" + "\n".join(res["erros"][:5]))
        else:
            QMessageBox.information(
                self, "Enviado",
                f"{res['enviadas']} passada(s) enfileirada(s).")
        self._atualizar_envio()

    # ---------------------------------------------------------------------

    def salvar(self):
        if self.padrao is None:
            return
        caminho, _ = salvar_arquivo(
            self, "Salvar referencia do padrao",
            self.padrao.nome.lower().replace(" ", "_") + ".txt",
            "Texto (*.txt)")
        if not caminho:
            return
        base = Path(caminho)
        base.write_text(self.padrao.texto_referencia(), encoding="utf-8")

        # O plano de validacao do TCC preve escanear o impresso e comparar com
        # o original, entao a imagem vai junto com o texto de referencia.
        png = base.with_suffix(".png")
        img = self.previa.imagem_do_bitmap(self.padrao.bitmap)
        if img is not None and img.save(str(png)):
            QMessageBox.information(
                self, "Salvo",
                f"Referencia em {base.name}\nImagem em {png.name}")
        else:
            QMessageBox.information(self, "Salvo", f"Referencia em {base.name}")
