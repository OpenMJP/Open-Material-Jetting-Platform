"""Aba de fatiamento: carregar STL, posicionar, parametrizar, fatiar, ver."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import trimesh
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QSlider,
    QApplication, QGridLayout, QSpinBox, QSplitter, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..core import codificador as ccod
from ..core import job as cjob
from ..core import envio as cenv
from ..core import imagem as cimg
from ..core import pacote as cpac
from ..machine import protocolo as pv
from ..core import mosaico as cmos
from ..core import malha as cmalha
from ..profile import Perfil
from . import tema
from .comum import (abrir_arquivo, coluna_com_rodape, em_rolagem,
                    garantir_alturas, salvar_arquivo)
from .preview2d import PreviaCamada
from .view3d import Vista3D


class _Trabalhador(QThread):
    progresso = Signal(int, int)
    terminou = Signal(object)
    falhou = Signal(str)

    def __init__(self, malha, resumo):
        super().__init__()
        self._malha = malha
        self._resumo = resumo
        self._cancelar = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            # Emitir um sinal por camada entope a fila de eventos da janela:
            # com 4638 camadas, a interface para de responder mesmo com o
            # fatiamento rodando em outra thread. Reportamos no maximo 200
            # vezes, que ja da uma barra de progresso suave.
            passo = max(1, self._resumo.n_camadas // 200)

            def cb(i, n):
                if i % passo == 0 or i == n:
                    self.progresso.emit(i, n)
                return not self._cancelar
            self.terminou.emit(cjob.fatiar(self._malha, self._resumo, cb))
        except Exception as e:
            self.falhou.emit(f"{type(e).__name__}: {e}")


class PainelFatiar(QWidget):
    def __init__(self, perfil: Perfil, obter_maquina=None, parent=None):
        super().__init__(parent)
        self.perfil = perfil
        self._obter_maquina = obter_maquina
        self._cancelar_envio = False
        self._cache_params = None
        self._plano = None
        self.malha_original: trimesh.Trimesh | None = None
        self.malha_posicionada: trimesh.Trimesh | None = None
        self.camadas: cjob.Camadas | None = None
        self.resumo: cjob.ResumoJob | None = None
        self._trab: _Trabalhador | None = None
        self._relatorio = None          # validacao da malha, em cache
        self.chapas: list = []
        self.plano_mosaico = None

        # Recalcular a cada tecla digitada num campo travava a janela: cada
        # mudanca copiava a malha, reconstruia o item OpenGL e revalidava um
        # modelo de milhares de faces. Os temporizadores juntam as mudancas
        # de uma rajada de digitacao num unico recalculo.
        self._t_modelo = QTimer(self)
        self._t_modelo.setSingleShot(True)
        self._t_modelo.setInterval(250)
        self._t_modelo.timeout.connect(self._atualizar_modelo)

        self._t_calculo = QTimer(self)
        self._t_calculo.setSingleShot(True)
        self._t_calculo.setInterval(250)
        self._t_calculo.timeout.connect(self._recalcular)

        raiz = QVBoxLayout(self)

        # ---- topo: arquivo ----------------------------------------------
        topo = QHBoxLayout()
        self.bt_abrir = QPushButton("Abrir STL...")
        self.bt_abrir.clicked.connect(self.abrir)
        self.bt_reparar = QPushButton("Reparar malha")
        self.bt_reparar.clicked.connect(self.reparar)
        self.bt_reparar.setEnabled(False)
        self.lb_arquivo = QLabel("Nenhum arquivo carregado.")
        topo.addWidget(self.bt_abrir)
        topo.addWidget(self.bt_reparar)
        topo.addWidget(self.lb_arquivo, 1)
        raiz.addLayout(topo)

        divisor = QSplitter(Qt.Orientation.Horizontal)
        raiz.addWidget(divisor, 1)

        # ---- coluna esquerda: controles ---------------------------------
        esq = QWidget()
        lay_esq = QVBoxLayout(esq)
        lay_esq.setSpacing(8)

        # X, Y e Z lado a lado em vez de uma linha cada: o grupo cai de oito
        # linhas para quatro, e os rotulos param de disputar espaco com os
        # campos.
        g_pos = QGroupBox("Posicao e orientacao")
        gp = QGridLayout(g_pos)
        gp.setHorizontalSpacing(6)
        for c, eixo in enumerate("XYZ"):
            gp.addWidget(QLabel(f"<b>{eixo}</b>"), 0, 1 + c,
                         Qt.AlignmentFlag.AlignCenter)

        self.sp = {}

        def _campo(chave, faixa, passo, valor=0.0):
            s = QDoubleSpinBox()
            s.setRange(*faixa)
            s.setSingleStep(passo)
            s.setDecimals(2)
            s.setValue(valor)
            s.setMinimumWidth(78)
            s.valueChanged.connect(self._t_modelo.start)
            self.sp[chave] = s
            return s

        gp.addWidget(QLabel("Posicao (mm)"), 1, 0)
        for c, chave in enumerate(("px", "py", "pz")):
            gp.addWidget(_campo(chave, (-500, 500), 1.0), 1, 1 + c)

        gp.addWidget(QLabel("Giro (graus)"), 2, 0)
        for c, chave in enumerate(("rx", "ry", "rz")):
            gp.addWidget(_campo(chave, (-360, 360), 15.0), 2, 1 + c)

        gp.addWidget(QLabel("Escala"), 3, 0)
        gp.addWidget(_campo("es", (0.01, 100), 0.1, 1.0), 3, 1)

        self.ck_assentar = QCheckBox("Assentar na plataforma (Z=0)")
        self.ck_assentar.setChecked(True)
        self.ck_assentar.stateChanged.connect(self._t_modelo.start)
        gp.addWidget(self.ck_assentar, 4, 0, 1, 4)

        # Giros de 90 graus: e o que se usa em 90% das vezes, e digitar
        # "90" em tres campos para virar uma peca e trabalho a toa.
        lr = QHBoxLayout()
        lr.addWidget(QLabel("Girar 90:"))
        for eixo, chave in (("X", "rx"), ("Y", "ry"), ("Z", "rz")):
            b = QPushButton(eixo)
            b.setMaximumWidth(34)
            b.clicked.connect(lambda _=False, c=chave: self._girar90(c))
            lr.addWidget(b)
        bz = QPushButton("Zerar giro")
        bz.clicked.connect(self._zerar_giro)
        lr.addWidget(bz)
        lr.addStretch(1)
        gp.addLayout(lr, 5, 0, 1, 4)

        lc = QHBoxLayout()
        self.bt_centralizar = QPushButton("Centralizar na mesa")
        self.bt_centralizar.clicked.connect(self.centralizar)
        self.bt_caber = QPushButton("Ajustar a mesa")
        self.bt_caber.clicked.connect(self.ajustar_a_mesa)
        lc.addWidget(self.bt_centralizar)
        lc.addWidget(self.bt_caber)
        gp.addLayout(lc, 6, 0, 1, 4)

        # Escolher a face de apoio: o que o usuario quer dizer com "qual lado
        # fica para baixo". As candidatas sao as direcoes de normal da peca,
        # ordenadas por area de contato.
        gp.addWidget(QLabel("Base na mesa"), 7, 0)
        lb = QHBoxLayout()
        self.cb_base = QComboBox()
        self.cb_base.setMinimumWidth(190)
        self.bt_deitar = QPushButton("Deitar")
        self.bt_deitar.clicked.connect(self.deitar_face)
        lb.addWidget(self.cb_base, 1)
        lb.addWidget(self.bt_deitar)
        gp.addLayout(lb, 7, 1, 1, 3)

        self.lb_dim = QLabel("-")
        self.lb_dim.setStyleSheet("color:#9aa0a6;")
        gp.addWidget(QLabel("Dimensoes"), 8, 0)
        gp.addWidget(self.lb_dim, 8, 1, 1, 3)

        gp.setColumnStretch(0, 0)
        lay_esq.addWidget(g_pos)

        g_par = QGroupBox("Parametros de impressao")
        f_par = QFormLayout(g_par)
        self._f_par = f_par

        # Sem encoder, o disparo vem da contagem de pulsos de passo, entao
        # ESTE e o parametro fisico: o DPI em X e consequencia dele. Enquanto
        # os micropassos por mm nao forem medidos, o DPI volta a ser digitado.
        self.sp_micro = QSpinBox()
        self.sp_micro.setRange(1, 200)
        self.sp_micro.setValue(perfil.processo.micropassos_por_disparo)
        self.sp_micro.valueChanged.connect(self._t_calculo.start)
        f_par.addRow("Micropassos por disparo", self.sp_micro)

        self.sp_dpix = QDoubleSpinBox()
        self.sp_dpix.setRange(20, 2400)
        self.sp_dpix.setDecimals(0)
        self.sp_dpix.setValue(perfil.processo.dpi_x)
        self.sp_dpix.valueChanged.connect(self._t_calculo.start)
        f_par.addRow("DPI em X", self.sp_dpix)

        self.lb_dpix = QLabel("")
        self.lb_dpix.setWordWrap(True)
        self.lb_dpix.setStyleSheet("color:#9aa0a6;")
        f_par.addRow("", self.lb_dpix)

        self.lb_dpiy = QLabel("-")
        self.lb_dpiy.setStyleSheet("color:#9aa0a6;")
        f_par.addRow("DPI em Y (do cabecote)", self.lb_dpiy)

        # A velocidade e uma so para todas as impressoes: fica na pagina
        # Maquina. Aqui so se mostra a que vale.
        self.lb_vel = QLabel("-")
        self.lb_vel.setStyleSheet("color:#9aa0a6;")
        self.lb_vel.setToolTip("Ajuste na pagina Maquina, cartao Impressao.")
        f_par.addRow("Velocidade", self.lb_vel)

        self.sp_esp = QDoubleSpinBox()
        # Ate 20 mm: na demonstracao em papel, camadas grossas dao poucas
        # camadas bem distintas (50 mm de peca em camadas de 5 mm = 10)
        self.sp_esp.setRange(0.1, 20000)
        self.sp_esp.setDecimals(1)
        self.sp_esp.setSingleStep(100.0)
        self.sp_esp.setValue(perfil.processo.espessura_camada_um)
        self.sp_esp.valueChanged.connect(self._t_calculo.start)
        f_par.addRow("Espessura de camada (um)", self.sp_esp)
        lay_esq.addWidget(g_par)

        # ---- modo de impressao -------------------------------------------
        # O modo decide o que E o job: uma peca empilhada, ou uma folha de
        # contato com as camadas lado a lado. Tudo daqui para baixo segue
        # essa escolha, inclusive o dado que vai para o cabecote.
        g_mos = QGroupBox("Modo de impressao")
        f_mos = QFormLayout(g_mos)

        self.cb_modo = QComboBox()
        self.cb_modo.addItem("Peca empilhada (camadas sobrepostas)", "peca")
        self.cb_modo.addItem("Camadas lado a lado (mesmo plano)", "lado")
        self.cb_modo.currentIndexChanged.connect(self._modo_mudou)
        f_mos.addRow("Modo", self.cb_modo)

        self.lb_modo = QLabel()
        self.lb_modo.setWordWrap(True)
        self.lb_modo.setStyleSheet("color:#9aa0a6;")
        f_mos.addRow(self.lb_modo)

        # Demonstracao de impressao 3D com fatias 2D: as camadas caem umas
        # sobre as outras no papel, cada uma com so uma fracao das gotas, e
        # onde ha mais camadas fica mais escuro (curvas de nivel da peca).
        self.sp_opac_cam = QSpinBox()
        self.sp_opac_cam.setRange(1, 100)
        self.sp_opac_cam.setValue(100)
        self.sp_opac_cam.setSuffix(" %")
        self.sp_opac_cam.setToolTip(
            "Fracao das gotas de cada camada. Cada camada usa gotas "
            "diferentes das anteriores, entao onde ha N camadas sobrepostas a "
            "cobertura e N vezes esta (ate 100%). Com 100% tudo sai preto e "
            "a sobreposicao nao aparece.")
        self.sp_opac_cam.valueChanged.connect(self._camadas_mudaram)
        f_mos.addRow("Opacidade por camada", self.sp_opac_cam)
        self.ck_mover_z = QCheckBox("Mover o Z entre camadas")
        self.ck_mover_z.setChecked(True)
        self.ck_mover_z.setToolTip(
            "Desmarcado, todas as camadas sao jateadas na altura da primeira: "
            "serve para demonstrar a peca em papel, com fatias 2D "
            "sobrepostas.")
        self.ck_mover_z.toggled.connect(self._camadas_mudaram)
        f_mos.addRow(self.ck_mover_z)
        # Contorno: cada camada ganha a borda cheia por cima do meio-tom, e
        # as camadas sobrepostas viram curvas de nivel distinguiveis
        self.ck_contorno = QCheckBox("Contorno de cada camada")
        self.ck_contorno.setToolTip(
            "Jateia a borda de cada camada com todas as gotas, por cima do "
            "preenchimento na opacidade escolhida. Com camadas sobrepostas, "
            "cada camada fica identificavel pelo proprio contorno.")
        self.sp_contorno = QDoubleSpinBox()
        self.sp_contorno.setRange(0.05, 5.0)
        self.sp_contorno.setDecimals(2)
        self.sp_contorno.setSingleStep(0.05)
        self.sp_contorno.setValue(0.2)
        self.sp_contorno.setSuffix(" mm")
        self.sp_contorno.setToolTip("Espessura do contorno")
        linha_ct = QHBoxLayout()
        linha_ct.addWidget(self.ck_contorno)
        linha_ct.addWidget(self.sp_contorno)
        f_mos.addRow(linha_ct)
        self.ck_contorno.toggled.connect(self._camadas_mudaram)
        self.sp_contorno.valueChanged.connect(self._camadas_mudaram)
        self._w_peca = [self.sp_opac_cam, self.ck_mover_z, self.ck_contorno,
                        self.sp_contorno]
        self._sobreposicao = None       # (chave, bitmap) da previa, em cache

        linha_int = QHBoxLayout()
        self.sp_cam_de = QSpinBox()
        self.sp_cam_de.setRange(1, 1_000_000)
        self.sp_cam_ate = QSpinBox()
        self.sp_cam_ate.setRange(1, 1_000_000)
        self.sp_cam_passo = QSpinBox()
        self.sp_cam_passo.setRange(1, 10_000)
        for w, t in ((self.sp_cam_de, "da"), (self.sp_cam_ate, "ate"),
                     (self.sp_cam_passo, "1 a cada")):
            linha_int.addWidget(QLabel(t))
            linha_int.addWidget(w)
        f_mos.addRow("Camadas", linha_int)

        self.sp_espaco = QDoubleSpinBox()
        self.sp_espaco.setRange(0.0, 50.0)
        self.sp_espaco.setValue(2.0)
        self.sp_espaco.setSuffix(" mm")
        f_mos.addRow("Espaco entre camadas", self.sp_espaco)

        self.bt_mosaico = QPushButton("Montar chapas")
        self.bt_mosaico.setEnabled(False)
        self.bt_mosaico.clicked.connect(self.montar_mosaico)
        f_mos.addRow(self.bt_mosaico)

        self.lb_mosaico = QLabel("Fatie a peca primeiro.")
        self.lb_mosaico.setWordWrap(True)
        self.lb_mosaico.setStyleSheet("color:#9aa0a6;")
        f_mos.addRow(self.lb_mosaico)

        self.bt_rel_mosaico = QPushButton("Salvar mapa das chapas...")
        self.bt_rel_mosaico.setEnabled(False)
        self.bt_rel_mosaico.clicked.connect(self.salvar_relatorio_mosaico)
        f_mos.addRow(self.bt_rel_mosaico)
        self._w_mosaico = [self.sp_cam_de, self.sp_cam_ate, self.sp_cam_passo,
                           self.sp_espaco, self.bt_mosaico,
                           self.bt_rel_mosaico]
        lay_esq.addWidget(g_mos)

        g_der = QGroupBox("Consequencias")
        l_der = QVBoxLayout(g_der)
        self.tx_derivado = QTextEdit()
        self.tx_derivado.setReadOnly(True)
        self.tx_derivado.setMinimumHeight(130)
        l_der.addWidget(self.tx_derivado)
        lay_esq.addWidget(g_der, 1)

        self.bt_fatiar = tema.primario(QPushButton("Fatiar"))
        self.bt_fatiar.setEnabled(False)
        self.bt_fatiar.clicked.connect(self.fatiar)

        self.barra = QProgressBar()
        self.barra.setVisible(False)

        # ---- envio para a maquina ----------------------------------------
        g_env = QGroupBox("Enviar para a maquina")
        l_env = QVBoxLayout(g_env)
        self.lb_envio = QLabel("Fatie a peca primeiro.")
        self.lb_envio.setWordWrap(True)
        self.lb_envio.setStyleSheet("color:#9aa0a6;")
        l_env.addWidget(self.lb_envio)
        self.bt_enviar = tema.primario(QPushButton("Enviar e imprimir"))
        self.bt_enviar.setEnabled(False)
        self.bt_enviar.clicked.connect(self.enviar)
        l_env.addWidget(self.bt_enviar)
        # O mesmo formato que o RP3 vai gerar (IMPORTACAO.md): serve de
        # exemplo para quem implementa o outro lado, e para guardar um job
        self.bt_exportar = QPushButton("Exportar camadas como .pajm...")
        self.bt_exportar.setEnabled(False)
        self.bt_exportar.clicked.connect(lambda: self.exportar_pacote())
        l_env.addWidget(self.bt_exportar)

        lay_esq.addStretch(0)
        # Fatiar e enviar ficam num rodape fixo, sempre a vista
        rodape = QWidget()
        l_rod = QVBoxLayout(rodape)
        l_rod.setContentsMargins(0, 0, 0, 0)
        l_rod.addWidget(self.bt_fatiar)
        l_rod.addWidget(self.barra)
        l_rod.addWidget(g_env)
        divisor.addWidget(coluna_com_rodape(esq, rodape, largura_min=400))

        # ---- coluna direita: visualizacao --------------------------------
        dir_ = QSplitter(Qt.Orientation.Vertical)
        self.vista3d = Vista3D()
        dir_.addWidget(self.vista3d)

        cx = QWidget()
        lcx = QVBoxLayout(cx)

        # A camada guardada e a geometria real da peca. O dado que vai para o
        # cabecote e essa mesma camada cisalhada pela inclinacao e pelas tres
        # fases, e nesse formato nao da para reconhecer a peca. O seletor
        # deixa ver os dois: o padrao e a geometria, que e o que interessa
        # para conferir o modelo.
        sel = QHBoxLayout()
        sel.addWidget(QLabel("Visualizacao:"))
        self.cb_vista = QComboBox()
        self.cb_vista.addItem("Geometria da camada", "geometria")
        self.cb_vista.addItem("Dado enviado ao cabecote", "jateamento")
        self.cb_vista.addItem("Todas as camadas sobrepostas (resultado)",
                              "sobreposicao")
        self.cb_vista.currentIndexChanged.connect(self._mostrar_camada)
        sel.addWidget(self.cb_vista, 1)
        lcx.addLayout(sel)

        self.previa = PreviaCamada()
        lcx.addWidget(self.previa, 1)
        linha = QHBoxLayout()
        self.sl_camada = QSlider(Qt.Orientation.Horizontal)
        self.sl_camada.setEnabled(False)
        self.sl_camada.valueChanged.connect(self._mostrar_camada)
        self.lb_camada = QLabel("-")
        linha.addWidget(QLabel("Camada"))
        linha.addWidget(self.sl_camada, 1)
        linha.addWidget(self.lb_camada)
        lcx.addLayout(linha)
        dir_.addWidget(cx)
        dir_.setSizes([320, 320])

        divisor.addWidget(dir_)
        divisor.setSizes([420, 760])

        self.vista3d.definir_plataforma(perfil.movimento.curso_x_mm,
                                        perfil.movimento.curso_y_mm)
        garantir_alturas(self)
        self.bt_deitar.setEnabled(False)
        self._faces = []
        self._modo_mudou()
        self._recalcular()

    # ---- carregamento ----------------------------------------------------

    def abrir(self):
        caminho, _ = abrir_arquivo(
            self, "Abrir modelo", "", "Malhas (*.stl *.obj *.ply);;Tudo (*)")
        if not caminho:
            return
        try:
            m = cmalha.carregar(caminho)
        except Exception as e:
            QMessageBox.critical(self, "Erro ao abrir",
                                 f"Nao consegui ler o arquivo:\n\n{e}")
            return

        self.malha_original = m
        self._relatorio = cmalha.validar(m)
        self._popular_faces()
        self.lb_arquivo.setText(Path(caminho).name)
        self.bt_reparar.setEnabled(True)
        self.bt_fatiar.setEnabled(True)
        self._descartar_camadas()
        self.previa.limpar("Modelo carregado. Clique em Fatiar.")
        self._atualizar_modelo()

    def reparar(self):
        if self.malha_original is None:
            return
        m, acoes = cmalha.reparar(self.malha_original)
        self.malha_original = m
        self._relatorio = cmalha.validar(m)
        self._popular_faces()
        QMessageBox.information(
            self, "Reparo",
            "\n".join(acoes) if acoes else "Nada a reparar nesta malha.")
        self._atualizar_modelo()

    def _descartar_camadas(self):
        """Apaga o arquivo temporario do fatiamento anterior."""
        if self.camadas is not None:
            self.camadas.fechar()
            self.camadas = None
        self._sobreposicao = None
        self.chapas = []
        self.plano_mosaico = None
        self.bt_mosaico.setEnabled(False)
        self.bt_rel_mosaico.setEnabled(False)
        self.sl_camada.setEnabled(False)

    # ---- atualizacao -----------------------------------------------------

    # ---- ferramentas de posicionamento -----------------------------------

    def _girar90(self, chave: str):
        s = self.sp[chave]
        s.setValue((s.value() + 90.0) % 360.0)

    def _zerar_giro(self):
        for c in ("rx", "ry", "rz"):
            self.sp[c].blockSignals(True)
            self.sp[c].setValue(0.0)
            self.sp[c].blockSignals(False)
        self._atualizar_modelo()

    def _modelo_agora(self):
        """
        Garante que `malha_posicionada` reflete os campos da tela.

        Os campos passam por um temporizador de 250 ms antes de recalcular o
        modelo. Uma ferramenta que leia `malha_posicionada` dentro dessa
        janela leria o estado ANTERIOR: digitar uma posicao e clicar em
        Centralizar em seguida dava conta errada.
        """
        if self._t_modelo.isActive():
            self._t_modelo.stop()
            self._atualizar_modelo()

    def centralizar(self):
        """
        Leva o centro da peca para o centro da mesa.

        A mesa vai de (0, 0) ao curso util, no mesmo sistema do fatiamento:
        o centro e (curso/2, curso/2), nao a origem. Antes a peca ia para
        (0, 0) e metade dela ficava em coordenada negativa.
        """
        self._modelo_agora()
        if self.malha_posicionada is None:
            return
        m = self.perfil.movimento
        if not m.curso_x_mm or not m.curso_y_mm:
            QMessageBox.information(
                self, "Falta a mesa",
                "Defina a area util da mesa na pagina Maquina (ou conecte a "
                "maquina, que informa o curso).")
            return
        cx, cy = cmalha.centro_xy(self.malha_posicionada)
        alvo = {"px": m.curso_x_mm / 2.0, "py": m.curso_y_mm / 2.0}
        for chave, atual in (("px", cx), ("py", cy)):
            s = self.sp[chave]
            s.blockSignals(True)
            s.setValue(s.value() + alvo[chave] - atual)
            s.blockSignals(False)
        self._atualizar_modelo()

    def ajustar_a_mesa(self):
        """Escala a peca para caber na mesa, com uma folga de 5 mm."""
        self._modelo_agora()
        if self.malha_posicionada is None:
            return
        m = self.perfil.movimento
        if not m.curso_x_mm or not m.curso_y_mm:
            QMessageBox.information(
                self, "Falta a mesa",
                "Defina a area util da mesa na pagina Maquina.")
            return
        d = self.malha_posicionada.bounds[1] - self.malha_posicionada.bounds[0]
        if d[0] <= 0 or d[1] <= 0:
            return
        fator = min((m.curso_x_mm - 10.0) / d[0], (m.curso_y_mm - 10.0) / d[1])
        if fator >= 1.0:
            QMessageBox.information(
                self, "Ja cabe",
                f"A peca ({d[0]:.1f} x {d[1]:.1f} mm) ja cabe na mesa.")
            return
        self.sp["es"].setValue(self.sp["es"].value() * fator)

    def _popular_faces(self):
        """Lista as direcoes de normal da peca, da maior area para a menor."""
        self.cb_base.clear()
        self._faces = []
        if self.malha_original is None:
            return
        try:
            self._faces = cmalha.faces_candidatas(self.malha_original)
        except Exception:
            self._faces = []
        for i, f in enumerate(self._faces):
            self.cb_base.addItem(f"{i + 1}. {f.rotulo}", i)
        self.bt_deitar.setEnabled(bool(self._faces))

    def deitar_face(self):
        """
        Gira a peca para a face escolhida ficar em contato com a mesa.

        O resultado e escrito nos MESMOS campos de giro da tela, em vez de
        virar um estado escondido: depois de deitar, da para continuar
        ajustando a mao.
        """
        i = self.cb_base.currentData()
        if i is None or not getattr(self, "_faces", None):
            return
        face = self._faces[i]
        r = cmalha.rotacao_para_deitar(face.normal)
        ax, ay, az = cmalha.euler_de_matriz(r)
        for chave, v in (("rx", ax), ("ry", ay), ("rz", az)):
            s = self.sp[chave]
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        self.ck_assentar.setChecked(True)
        self._atualizar_modelo()

    def _atualizar_modelo(self):
        if self.malha_original is None:
            return
        t = cmalha.Transformacao(
            pos_mm=(self.sp["px"].value(), self.sp["py"].value(),
                    self.sp["pz"].value()),
            rot_deg=(self.sp["rx"].value(), self.sp["ry"].value(),
                     self.sp["rz"].value()),
            escala=self.sp["es"].value())
        m = t.aplicar(self.malha_original)
        if self.ck_assentar.isChecked():
            m = cmalha.assentar(m)
        self.malha_posicionada = m
        d = m.bounds[1] - m.bounds[0]
        self.lb_dim.setText(
            f"{d[0]:.2f} x {d[1]:.2f} x {d[2]:.2f} mm")
        self.vista3d.definir_malha(m)
        self._recalcular()

    def _dpi_x(self) -> float:
        """
        DPI em X de fato.

        Com os micropassos por mm medidos, o DPI e DERIVADO do micropasso e o
        campo de digitar fica travado: so existem os multiplos inteiros do
        micropasso, e deixar digitar 360 quando 360 nao e alcancavel seria
        mentir para o usuario. Sem a medida, volta a ser digitado, com aviso.
        """
        self.perfil.processo.micropassos_por_disparo = self.sp_micro.value()
        d = self.perfil.dpi_x_de_micropassos(self.sp_micro.value())
        if d is None:
            self.sp_dpix.setEnabled(True)
            self._f_par.setRowVisible(self.sp_dpix, True)
            self.lb_dpix.setText(
                "DPI em X digitado a mao. Meca os micropassos por mm em X "
                "(pagina Maquina) para ele passar a sair do micropasso.")
            self.lb_dpix.setStyleSheet("color:#e0a030;")
            return self.sp_dpix.value()

        self.sp_dpix.setEnabled(False)
        # Derivado do micropasso: o campo de digitar so atrapalharia
        self._f_par.setRowVisible(self.sp_dpix, False)
        if abs(self.sp_dpix.value() - d) > 0.5:
            self.sp_dpix.blockSignals(True)
            self.sp_dpix.setValue(d)
            self.sp_dpix.blockSignals(False)
        mpm = self.perfil.movimento.passos_por_mm_x
        viz = self.perfil.dpis_alcancaveis(12)
        perto = ", ".join(
            f"{k}:{v:.0f}" for k, v in viz
            if abs(k - self.sp_micro.value()) <= 2)
        self.lb_dpix.setText(
            f"{d:.1f} dpi em X ({25400.0 / d:.1f} um entre gotas)")
        self.lb_dpix.setToolTip(
            f"{d:.1f} dpi = 25,4 x {mpm:.0f} / {self.sp_micro.value()}. "
            f"Vizinhos possiveis, micropassos:dpi = {perto}")
        self.lb_dpix.setStyleSheet("color:#9aa0a6;")
        return d

    def _recalcular(self):
        # Mesmo sem modelo: o campo de DPI mostra (ou esconde) o que vale
        dpi_x = self._dpi_x()
        self.lb_vel.setText(f"{self.perfil.processo.velocidade_mm_s:g} mm/s "
                            "(pagina Maquina)")
        cab = self.perfil.cabecote
        dpi_y = cab.dpi_y_nativo
        self.lb_dpiy.setText(
            "indefinido - falta a variante ou o angulo" if dpi_y is None
            else f"{dpi_y:.1f} dpi  (passo efetivo "
                 f"{cab.passo_efetivo_mm*1000:.1f} um)")

        linhas: list[str] = []
        # Validar custa caro (estanqueidade, separacao de componentes) e so
        # muda quando a malha muda. Girar ou escalar nao altera topologia,
        # entao o relatorio vem do cache preenchido em abrir() e reparar().
        rel = self._relatorio

        if self.malha_posicionada is None:
            linhas.append("Carregue um modelo para ver as consequencias dos "
                          "parametros.")
        else:
            self.resumo = cjob.preparar(
                self.perfil, self.malha_posicionada,
                dpi_x, self.perfil.processo.velocidade_mm_s,
                self.sp_esp.value())
            r = self.resumo
            d = self.malha_posicionada.bounds[1] - self.malha_posicionada.bounds[0]

            linhas.append(f"<b>Modelo</b>  {d[0]:.1f} x {d[1]:.1f} x {d[2]:.1f} mm")
            linhas.append(f"<b>Camadas</b>  {r.n_camadas}")
            linhas.append(
                f"<b>Bitmap</b>  {r.grade.n_linhas} linhas x "
                f"{r.grade.n_colunas} colunas")
            disco = r.disco_estimado_mb
            linhas.append(
                "<b>Disco temporario</b>  "
                + (f"{disco/1000:.2f} GB" if disco >= 1000
                   else f"{disco:.0f} MB"))
            linhas.append(
                f"<b>Frequencia de disparo</b>  {r.freq_disparo_hz:.0f} Hz"
                + (f" (max {r.freq_max_hz:.0f} Hz)" if r.freq_max_hz else ""))
            if r.espessura_por_passada_um:
                linhas.append(
                    f"<b>Deposicao</b>  {r.espessura_por_passada_um:.2f} um por "
                    f"passada &rarr; {r.passadas_necessarias:.2f} passadas para "
                    f"a camada de {r.espessura_camada_um:.1f} um")
            if r.largura_faixa_mm:
                linhas.append(
                    f"<b>Faixa do cabecote</b>  {r.largura_faixa_mm:.2f} mm "
                    f"&rarr; {r.n_faixas} passada(s) em Y por camada")
            c = r.compensacao
            linhas.append(
                f"<b>Compensacao dos bicos</b>  cisalhamento "
                f"{c.cisalhamento_mm:.2f} mm + fase {c.fase_mm*1000:.1f} um "
                f"= {int(c.colunas.max())} colunas, residuo {c.residuo_um:.0f} um")

            for e in r.erros:
                linhas.append(f"<span style='color:#ff6b6b'><b>ERRO</b> {e}</span>")
            for a in r.avisos:
                linhas.append(f"<span style='color:#e0a030'>Aviso: {a}</span>")
            self.bt_fatiar.setEnabled(r.viavel)

        if rel is not None:
            linhas.append("")
            linhas.append(
                f"<b>Malha</b>  {rel.n_faces} faces, "
                f"{'estanque' if rel.estanque else 'NAO estanque'}"
                + (f", {rel.n_componentes} componentes"
                   if rel.n_componentes > 1 else ""))
            for a in rel.avisos:
                linhas.append(f"<span style='color:#e0a030'>Aviso: {a}</span>")
            for e in rel.erros:
                linhas.append(f"<span style='color:#ff6b6b'><b>ERRO</b> {e}</span>")

        for a in self.perfil.avisos():
            linhas.append(f"<span style='color:#8ab4f8'>Perfil: {a}</span>")

        self.tx_derivado.setHtml("<br>".join(linhas))
        self._atualizar_envio()

    # ---- fatiamento ------------------------------------------------------

    def fatiar(self):
        if self.malha_posicionada is None or self.resumo is None:
            return
        if not self.resumo.viavel:
            QMessageBox.warning(self, "Nao da para fatiar",
                                "\n\n".join(self.resumo.erros))
            return

        self._descartar_camadas()
        self.previa.limpar("Fatiando...")
        self.bt_fatiar.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, self.resumo.n_camadas)

        self._trab = _Trabalhador(self.malha_posicionada, self.resumo)
        self._trab.progresso.connect(
            lambda i, n: self.barra.setValue(i))
        self._trab.terminou.connect(self._pronto)
        self._trab.falhou.connect(self._erro_fatiar)
        self._trab.start()

    def _erro_fatiar(self, msg: str):
        self.barra.setVisible(False)
        self.bt_fatiar.setEnabled(True)
        QMessageBox.critical(self, "Falha no fatiamento", msg)

    def _pronto(self, camadas: cjob.Camadas):
        self.barra.setVisible(False)
        self.bt_fatiar.setEnabled(True)
        self.camadas = camadas
        if not len(camadas):
            self.previa.limpar("O fatiamento nao produziu nenhuma camada.")
            return
        self.sl_camada.setEnabled(True)
        self.sl_camada.setRange(0, len(camadas) - 1)
        self.sl_camada.setValue(len(camadas) // 2)
        self.chapas = []
        self.bt_rel_mosaico.setEnabled(False)
        self.bt_mosaico.setEnabled(True)
        self.sp_cam_de.setMaximum(len(camadas))
        self.sp_cam_ate.setMaximum(len(camadas))
        self.sp_cam_de.setValue(1)
        self.sp_cam_ate.setValue(len(camadas))
        self.lb_mosaico.setText(
            f"{len(camadas)} camadas disponiveis. Escolha o intervalo e "
            "clique em Montar chapas.")
        self._modo_mudou()

    # ---- ensaio de camadas lado a lado -----------------------------------

    def montar_mosaico(self):
        if not self.camadas:
            return
        m = self.perfil.movimento
        idx = cmos.selecionar_camadas(
            len(self.camadas), self.sp_cam_de.value(),
            self.sp_cam_ate.value(), self.sp_cam_passo.value())
        # O cisalhamento do cabecote inclinado estende cada swath para a
        # direita, e o eixo precisa de recuo para frear depois disso. Esse
        # espaco nao pode ser ocupado por celulas da chapa.
        folga = 5.0
        if self.resumo is not None:
            folga += self.resumo.compensacao.cisalhamento_mm
        plano = cmos.planejar(self.camadas, idx, m.curso_x_mm, m.curso_y_mm,
                              espacamento_mm=self.sp_espaco.value(),
                              folga_x_mm=folga)
        if not plano.viavel:
            self.lb_mosaico.setText(
                "<span style='color:#ff6b6b'>"
                + "<br>".join(plano.erros) + "</span>")
            self.chapas = []
            self.bt_rel_mosaico.setEnabled(False)
            return

        self.plano_mosaico = plano
        self.chapas = [cmos.montar_chapa(self.camadas, plano, i)
                       for i in range(plano.n_chapas)]
        self.bt_rel_mosaico.setEnabled(True)

        txt = (f"{len(idx)} camadas, {plano.n_colunas} x {plano.n_linhas} "
               f"= {plano.por_chapa} por chapa, "
               f"<b>{plano.n_chapas} chapa(s)</b>")
        for a in plano.avisos:
            txt += f"<br><span style='color:#e0a030'>{a}</span>"
        self.lb_mosaico.setText(txt)

        self._mostrar_camada()
        self._atualizar_envio()

    def salvar_relatorio_mosaico(self):
        if not self.chapas:
            return
        caminho, _ = salvar_arquivo(
            self, "Salvar mapa das chapas", "chapas_pajm.txt", "Texto (*.txt)")
        if not caminho:
            return
        Path(caminho).write_text(
            cmos.relatorio(self.plano_mosaico, self.chapas), encoding="utf-8")
        QMessageBox.information(
            self, "Salvo",
            "Mapa salvo. Ele diz qual retangulo da chapa impressa "
            "corresponde a qual camada da peca.")

    # ---- envio -----------------------------------------------------------

    def _atualizar_envio(self):
        """
        Diz o que seria enviado, e por que nao da, quando nao da.

        Toda validacao que o firmware faria acontece aqui antes: periodo de
        disparo, recuo de aceleracao, Z dentro do curso, colunas por swath.
        Melhor recusar na tela do que abortar o job na metade.
        """
        self.bt_exportar.setEnabled(
            bool(self.camadas) and self.cb_modo.currentData() == "peca")
        if not self.camadas:
            self.lb_envio.setText("Fatie a peca primeiro.")
            self.bt_enviar.setEnabled(False)
            return

        uns = self.unidades()
        if not uns:
            self.lb_envio.setText("Monte as chapas antes de enviar.")
            self.bt_enviar.setEnabled(False)
            return

        sessao = self._obter_maquina() if self._obter_maquina else None
        motivos = []
        if sessao is None or not sessao.aberta:
            motivos.append("sem conexao: conecte na barra superior "
                           "(o simulador serve)")
        if self.perfil.cabecote.sentido_indice not in (1, -1):
            motivos.append(
                "falta o sentido do indice dos bicos (pagina Maquina): sem ele o "
                "dado sairia espelhado e nada acusaria o erro")

        if motivos:
            n = cenv.contar_swaths(uns, self.perfil.cabecote.n_bicos)
            self.lb_envio.setText(
                f"{len(uns)} unidade(s), {n} swaths<br>"
                + "<br>".join(
                    f"<span style='color:#e0a030'>Bloqueado: {m}</span>"
                    for m in motivos))
            self.bt_enviar.setEnabled(False)
            return

        try:
            params = self._parametros_da_maquina(sessao)
        except Exception as e:
            self.lb_envio.setText(
                f"<span style='color:#ff6b6b'>nao consegui ler os parametros "
                f"da maquina: {e}</span>")
            self.bt_enviar.setEnabled(False)
            return

        plano = cenv.planejar(
            sessao, self.perfil, uns, self.resumo.compensacao,
            velocidade_mm_s=self.perfil.processo.velocidade_mm_s,
            accel_passos_s2=params["accel"],
            runup_margin=params["runup"],
            min_fire_period_us=params["min_fire"],
            vmax_x_passos_s=params["vmax_x"])
        self._plano = plano

        txt = (f"{plano.n_unidades} unidade(s), {plano.n_swaths} swaths, "
               f"{plano.bytes_totais / 1e6:.1f} MB<br>"
               f"pitch {plano.pitch} = {plano.dpi_x_real:.0f} dpi em X, "
               f"periodo de disparo {plano.periodo_disparo_us} us, "
               f"recuo {plano.recuo_mm:.1f} mm")
        for a in plano.avisos:
            txt += f"<br><span style='color:#9aa0a6'>{a}</span>"
        for e in plano.erros:
            txt += f"<br><span style='color:#ff6b6b'>{e}</span>"
        self.lb_envio.setText(txt)
        self.bt_enviar.setEnabled(plano.viavel)

    def _parametros_da_maquina(self, sessao) -> dict:
        """
        Aceleracao, margem de recuo, periodo minimo e VMAX_X, lidos da maquina.

        Sao do firmware, nao nossos: chutar um valor padrao daria uma
        validacao que parece correta e nao corresponde a maquina. Ficam em
        cache enquanto a sessao durar.
        """
        if getattr(self, "_cache_params", None) is not None:
            return self._cache_params
        v = sessao.obter_parametros([
            pv.Param.ACCEL_X, pv.Param.RUNUP_MARGIN, pv.Param.MIN_FIRE_PERIOD,
            pv.Param.VMAX_X])
        self._cache_params = {
            "accel": v[int(pv.Param.ACCEL_X)],
            "runup": v[int(pv.Param.RUNUP_MARGIN)],
            "min_fire": v[int(pv.Param.MIN_FIRE_PERIOD)],
            "vmax_x": v[int(pv.Param.VMAX_X)],
        }
        return self._cache_params

    def enviar(self):
        sessao = self._obter_maquina() if self._obter_maquina else None
        if sessao is None or not self.camadas:
            return
        uns = self.unidades()
        plano = getattr(self, "_plano", None)
        if not uns or plano is None or not plano.viavel:
            return

        modo = ("camadas lado a lado" if self.cb_modo.currentData() == "lado"
                else "peca empilhada")
        resposta = QMessageBox.question(
            self, "Confirmar impressao",
            "Isto vai MOVER A MAQUINA e JATEAR.\n\n"
            f"Modo: {modo}\n"
            f"{plano.n_unidades} unidade(s), {plano.n_swaths} swaths, "
            f"{plano.bytes_totais / 1e6:.1f} MB\n"
            f"DPI em X: {plano.dpi_x_real:.0f} (pitch {plano.pitch})\n\n"
            "Confirma?")
        if resposta != QMessageBox.StandardButton.Yes:
            return

        self.bt_enviar.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, plano.n_swaths)
        self._cancelar_envio = False

        def progresso(n, total, rotulo):
            self.barra.setValue(n)
            self.lb_envio.setText(f"enviando {rotulo}: swath {n} de {total}")
            QApplication.processEvents()

        # O mesmo envio da imagem e dos padroes: purga antes, apresentacao
        # da peca no fim
        try:
            res = cenv.enviar_unidades(
                sessao, self.perfil, uns, self.resumo.compensacao, plano,
                progresso=progresso,
                cancelado=lambda: self._cancelar_envio)
        except Exception as e:
            sessao.cancelar()
            self.barra.setVisible(False)
            QMessageBox.critical(self, "Falha no envio",
                                 f"{type(e).__name__}: {e}")
            self._atualizar_envio()
            return

        self.barra.setVisible(False)
        if res["erros"]:
            QMessageBox.critical(
                self, "A maquina recusou",
                "O job foi cancelado.\n\n" + "\n".join(res["erros"][:5]))
        else:
            QMessageBox.information(
                self, "Enviado",
                f"{plano.n_swaths} swaths enfileiradas.\n\n"
                "A impressao acompanha o ritmo da maquina; o estado aparece "
                "no Painel.")
        self._atualizar_envio()

    def exportar_pacote(self, caminho: str | None = None) -> bool:
        """Camadas do modo empilhado como pacote .pajm (IMPORTACAO.md)."""
        if not self.camadas:
            return False
        if caminho is None:
            caminho, _ = salvar_arquivo(
                self, "Exportar camadas", "peca.pajm",
                "Pacote de camadas (*.pajm)")
            if not caminho:
                return False
        n = len(self.camadas)
        # As camadas vao inteiras: a opacidade e o Z parado vao como
        # parametros de processo, para quem importar decidir
        cpac.escrever(
            caminho, (self.camadas.bitmap(i) for i in range(n)),
            self.camadas.z_mm[:n], self.camadas.grade,
            nome_peca=self.lb_arquivo.text(),
            processo={"velocidade_mm_s": self.perfil.processo.velocidade_mm_s,
                      "opacidade": self.sp_opac_cam.value() / 100.0,
                      "mover_z": self.ck_mover_z.isChecked()},
            espessura_mm=self.sp_esp.value() / 1000.0)
        return True

    # ---- o que esta sendo visto e impresso -------------------------------

    def _camadas_mudaram(self, *_):
        self._sobreposicao = None
        self._mostrar_camada()
        self._atualizar_envio()

    def _bitmap_da_camada(self, i: int) -> np.ndarray:
        """
        Camada i como vai ao cabecote no modo empilhado: com a opacidade e,
        se marcado, o contorno cheio por cima.
        """
        cheia = self.camadas.bitmap(i)
        bmp = cheia
        op = self.sp_opac_cam.value() / 100.0
        if op < 1.0:
            # Mesma grade em todas as camadas, entao a mascara alinha no papel
            bmp = bmp & cimg.mascara_de_opacidade(bmp.shape, op, ordem=i)
        if self.ck_contorno.isChecked():
            g = self.camadas.grade
            mm = self.sp_contorno.value()
            bmp = bmp | cimg.contorno(
                cheia, max(1, int(round(mm / g.passo_x_mm))),
                max(1, int(round(mm / g.passo_y_mm))))
        return bmp

    def _z_da_camada(self, i: int) -> float:
        if self.ck_mover_z.isChecked():
            return float(self.camadas.z_mm[i])
        return float(self.camadas.z_mm[0])

    def _modo_mudou(self):
        lado = self.cb_modo.currentData() == "lado"
        for w in self._w_mosaico:
            w.setEnabled(lado and self.camadas is not None)
        for w in self._w_peca:
            w.setEnabled(not lado)
        if lado:
            self.bt_rel_mosaico.setEnabled(lado and bool(self.chapas))
            self.lb_modo.setText(
                "Camadas lado a lado no mesmo Z, em folha de contato. "
                "O cabecote recebe as chapas, nao a peca.")
        else:
            self.lb_modo.setText(
                "Camadas sobrepostas, cada uma no seu Z. Impressao normal. "
                "Para demonstrar em papel: opacidade baixa e Z parado.")
        self.sl_camada.setValue(0)
        self._mostrar_camada()
        self._atualizar_envio()

    def unidades(self) -> list:
        """
        O que vai ser impresso, na ordem. Camadas no modo empilhado, chapas
        no modo lado a lado. E a mesma lista que a previa mostra e que o
        envio manda ao cabecote, para nao haver divergencia entre o que se ve
        e o que se imprime.
        """
        if not self.camadas:
            return []
        if self.cb_modo.currentData() == "lado":
            return [
                cenv.Unidade(rotulo=f"chapa {c.numero + 1}", bitmap=c.bitmap,
                             grade=c.grade, z_mm=0.0)
                for c in self.chapas]
        return [
            cenv.Unidade(rotulo=f"camada {i + 1}",
                         bitmap=self._bitmap_da_camada(i),
                         grade=self.camadas.grade,
                         z_mm=self._z_da_camada(i))
            for i in range(len(self.camadas))]

    def _unidade_atual(self):
        """So a unidade sob o cursor, sem materializar a lista inteira."""
        if not self.camadas:
            return None
        if self.cb_modo.currentData() == "lado":
            if not self.chapas:
                return None
            i = min(self.sl_camada.value(), len(self.chapas) - 1)
            c = self.chapas[i]
            return cenv.Unidade(f"chapa {i + 1} de {len(self.chapas)}",
                                c.bitmap, c.grade, 0.0), i, len(self.chapas)
        i = min(self.sl_camada.value(), len(self.camadas) - 1)
        return (cenv.Unidade(f"camada {i + 1} de {len(self.camadas)}",
                             self._bitmap_da_camada(i), self.camadas.grade,
                             self._z_da_camada(i)),
                i, len(self.camadas))

    def _mostrar_sobreposicao(self) -> bool:
        """
        Todas as camadas uma sobre a outra, como ficam no papel com o Z
        parado. Devolve False se nao for o caso de mostrar.
        """
        if self.cb_vista.currentData() != "sobreposicao":
            return False
        if self.cb_modo.currentData() == "lado" or not self.camadas:
            self.previa.limpar(
                "A sobreposicao e do modo de peca empilhada.")
            return True
        chave = (id(self.camadas), self.sp_opac_cam.value(),
                 self.ck_contorno.isChecked(), self.sp_contorno.value())
        if self._sobreposicao is None or self._sobreposicao[0] != chave:
            soma = np.zeros(self.camadas.bitmap(0).shape, dtype=bool)
            for i in range(len(self.camadas)):
                soma |= self._bitmap_da_camada(i)
                if i % 50 == 0:
                    QApplication.processEvents()
            self._sobreposicao = (chave, soma)
        soma = self._sobreposicao[1]
        g = self.camadas.grade
        self.previa.mostrar(
            soma, g,
            f"{len(self.camadas)} camadas sobrepostas a "
            f"{self.sp_opac_cam.value()}% cada   |   {int(soma.sum())} gotas"
            f"   |   {g.largura_mm:.1f} x {g.altura_mm:.1f} mm")
        return True

    def _mostrar_camada(self):
        if self._mostrar_sobreposicao():
            return
        atual = self._unidade_atual()
        if atual is None:
            if self.cb_modo.currentData() == "lado" and self.camadas:
                self.previa.limpar(
                    "Escolha o intervalo de camadas e clique em Montar chapas.")
            return
        u, i, n = atual

        total = len(self.chapas) if self.cb_modo.currentData() == "lado" \
            else len(self.camadas)
        self.sl_camada.setRange(0, max(0, total - 1))
        self.lb_camada.setText(f"{i + 1} / {n}")

        bmp, grade = u.bitmap, u.grade
        gotas = int(bmp.sum())
        extra = ""
        if self.cb_modo.currentData() == "lado":
            extra = f"   |   {self.chapas[i].n_camadas} camadas"
        else:
            extra = f"   |   z = {u.z_mm:.3f} mm"

        if self.cb_vista.currentData() == "jateamento":
            comp = self.resumo.compensacao if self.resumo else None
            if comp is None:
                self.previa.limpar(
                    "Sem a compensacao nao da para montar o dado de "
                    "jateamento.")
                return
            bmp = ccod.camada_como_jateada(
                bmp, self.perfil.cabecote.n_bicos, comp)
            grade = replace(grade, n_colunas=int(bmp.shape[1]))
            extra += f"   |   cisalhado em {int(comp.colunas.max())} colunas"

        self.previa.mostrar(
            bmp, grade,
            f"{u.rotulo}{extra}   |   {gotas} gotas   |   "
            f"{grade.largura_mm:.1f} x {grade.altura_mm:.1f} mm")
