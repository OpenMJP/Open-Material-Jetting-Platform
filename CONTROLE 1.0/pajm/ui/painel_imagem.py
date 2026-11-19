"""
Impressao de imagem: jatear PNG, PDF e outros formatos 2D.

Fica ao lado do fatiamento de STL, nao dentro dele, porque os controles sao
outros: nao ha camadas, nao ha orientacao em tres eixos, e o que importa e o
meio-tom. O que sai daqui e uma unidade imprimivel igual a uma camada, entao
compensacao do cabecote, swaths e protocolo sao os mesmos.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..core import envio as cenv
from ..core import imagem as cimg
from ..core.fatiador import compensar_bicos
from ..machine import protocolo as pv
from ..profile import Perfil
from . import tema
from .comum import (abrir_arquivo, coluna_com_rodape, em_rolagem,
                    garantir_alturas, salvar_arquivo)
from .preview2d import PreviaCamada


class PainelImagem(QWidget):
    def __init__(self, perfil: Perfil, obter_maquina=None, parent=None):
        super().__init__(parent)
        self.perfil = perfil
        self._obter_maquina = obter_maquina
        self.imagem: cimg.ImagemCarregada | None = None
        self.bitmap = None
        self.grade = None
        self._plano = None
        self._cache_params = None
        self._cancelar = False
        self._travar_proporcao = True

        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(250)
        self._t.timeout.connect(self.processar)

        # O meio-tom roda numa thread: numa imagem grande ele leva segundos,
        # e com a interface parada ninguem serve o protocolo e o ESP derruba
        # a sessao (LINK_TIMEOUT de 1 s). O GIL e cedido a cada poucos ms,
        # entao o temporizador do painel manual continua rodando.
        self._trabalho: threading.Thread | None = None
        self._resultado: dict = {}
        self._refazer = False
        self._espera = QTimer(self)
        self._espera.setInterval(50)
        self._espera.timeout.connect(self._processado)

        raiz = QHBoxLayout(self)

        esq = QWidget()
        lay = QVBoxLayout(esq)
        lay.setSpacing(8)

        # ---- arquivo -----------------------------------------------------
        g_arq = QGroupBox("Arquivo")
        f_arq = QVBoxLayout(g_arq)
        linha = QHBoxLayout()
        self.bt_abrir = QPushButton("Abrir imagem ou PDF...")
        self.bt_abrir.clicked.connect(self.abrir)
        linha.addWidget(self.bt_abrir)
        self.bt_padrao = QPushButton("Padrao de teste")
        self.bt_padrao.setToolTip(
            "Faixa sintetica numa swath so: cheio, barras verticais, barras "
            "horizontais, xadrez e gradiente. Para a primeira impressao.")
        self.bt_padrao.clicked.connect(self.usar_padrao)
        linha.addWidget(self.bt_padrao)
        f_arq.addLayout(linha)
        self.lb_arquivo = QLabel("Nenhum arquivo carregado.")
        self.lb_arquivo.setWordWrap(True)
        f_arq.addWidget(self.lb_arquivo)

        linha_pag = QHBoxLayout()
        linha_pag.addWidget(QLabel("Pagina do PDF"))
        self.sp_pagina = QSpinBox()
        self.sp_pagina.setRange(1, 1)
        self.sp_pagina.setEnabled(False)
        self.sp_pagina.valueChanged.connect(self._trocar_pagina)
        linha_pag.addWidget(self.sp_pagina)
        linha_pag.addStretch(1)
        f_arq.addLayout(linha_pag)
        lay.addWidget(g_arq)

        # ---- tamanho e posicao -------------------------------------------
        g_tam = QGroupBox("Tamanho e posicao na mesa")
        f_tam = QFormLayout(g_tam)

        self.sp_larg = QDoubleSpinBox()
        self.sp_larg.setRange(0.1, 2000.0)
        self.sp_larg.setDecimals(2)
        self.sp_larg.setSuffix(" mm")
        self.sp_larg.setValue(50.0)
        self.sp_larg.valueChanged.connect(self._largura_mudou)
        f_tam.addRow("Largura", self.sp_larg)

        self.sp_alt = QDoubleSpinBox()
        self.sp_alt.setRange(0.1, 2000.0)
        self.sp_alt.setDecimals(2)
        self.sp_alt.setSuffix(" mm")
        self.sp_alt.setValue(50.0)
        self.sp_alt.valueChanged.connect(self._altura_mudou)
        f_tam.addRow("Altura", self.sp_alt)

        self.ck_proporcao = QCheckBox("Manter a proporcao")
        self.ck_proporcao.setChecked(True)
        self.ck_proporcao.stateChanged.connect(self._proporcao_mudou)
        f_tam.addRow(self.ck_proporcao)

        self.bt_natural = QPushButton("Usar o tamanho do arquivo")
        self.bt_natural.setEnabled(False)
        self.bt_natural.clicked.connect(self._usar_natural)
        f_tam.addRow(self.bt_natural)

        self.sp_x0 = QDoubleSpinBox()
        self.sp_x0.setRange(0.0, 2000.0)
        self.sp_x0.setDecimals(2)
        self.sp_x0.setSuffix(" mm")
        self.sp_x0.setValue(20.0)
        self.sp_x0.valueChanged.connect(self._t.start)
        f_tam.addRow("Canto em X", self.sp_x0)

        self.sp_y0 = QDoubleSpinBox()
        self.sp_y0.setRange(0.0, 2000.0)
        self.sp_y0.setDecimals(2)
        self.sp_y0.setSuffix(" mm")
        self.sp_y0.setValue(20.0)
        self.sp_y0.valueChanged.connect(self._t.start)
        f_tam.addRow("Canto em Y", self.sp_y0)

        self.sp_z = QDoubleSpinBox()
        self.sp_z.setRange(0.0, 2000.0)
        self.sp_z.setDecimals(2)
        self.sp_z.setSuffix(" mm")
        self.sp_z.valueChanged.connect(self._t.start)
        f_tam.addRow("Altura Z do jateamento", self.sp_z)
        lay.addWidget(g_tam)

        # ---- meio-tom ----------------------------------------------------
        g_mt = QGroupBox("Meio-tom")
        f_mt = QFormLayout(g_mt)
        g_mt.setToolTip(
            "O cabecote e binario: cada bico jateia uma gota ou nenhuma, sem "
            "escala de cinza. Uma imagem com tons continuos precisa virar "
            "pontos, e e aqui que se escolhe como.")

        self.cb_modo = QComboBox()
        self.cb_modo.addItem("Limiar: desenho, texto, padrao ja binario",
                             "limiar")
        self.cb_modo.addItem("Bayer ordenado: tons, rapido", "bayer")
        self.cb_modo.addItem("Difusao de erro: fotografia, mais lento",
                             "difusao")
        self.cb_modo.currentIndexChanged.connect(self._t.start)
        f_mt.addRow("Metodo", self.cb_modo)

        self.sp_limiar = QDoubleSpinBox()
        self.sp_limiar.setRange(0.01, 0.99)
        self.sp_limiar.setSingleStep(0.05)
        self.sp_limiar.setDecimals(2)
        self.sp_limiar.setValue(0.50)
        self.sp_limiar.valueChanged.connect(self._t.start)
        f_mt.addRow("Limiar", self.sp_limiar)

        self.ck_inverter = QCheckBox("Inverter (jatear o claro)")
        self.ck_inverter.stateChanged.connect(self._t.start)
        f_mt.addRow(self.ck_inverter)

        self.ck_esp_x = QCheckBox("Espelhar em X")
        self.ck_esp_x.stateChanged.connect(self._t.start)
        f_mt.addRow(self.ck_esp_x)

        self.ck_esp_y = QCheckBox("Espelhar em Y")
        self.ck_esp_y.stateChanged.connect(self._t.start)
        f_mt.addRow(self.ck_esp_y)
        lay.addWidget(g_mt)

        # ---- consequencias e envio ---------------------------------------
        g_inf = QGroupBox("Resultado")
        l_inf = QVBoxLayout(g_inf)
        self.lb_info = QLabel("Carregue um arquivo.")
        self.lb_info.setWordWrap(True)
        l_inf.addWidget(self.lb_info)
        lay.addWidget(g_inf)

        g_env = QGroupBox("Enviar para a maquina")
        l_env = QVBoxLayout(g_env)
        self.lb_envio = QLabel("Carregue um arquivo.")
        self.lb_envio.setWordWrap(True)
        self.lb_envio.setStyleSheet("color:#9aa0a6;")
        l_env.addWidget(self.lb_envio)
        self.bt_enviar = tema.primario(QPushButton("Enviar e jatear"))
        self.bt_enviar.setEnabled(False)
        self.bt_enviar.clicked.connect(self.enviar)
        l_env.addWidget(self.bt_enviar)
        self.barra = QProgressBar()
        self.barra.setVisible(False)
        l_env.addWidget(self.barra)
        lay.addStretch(0)

        # O envio fica num rodape fixo, sempre a vista
        raiz.addWidget(coluna_com_rodape(esq, g_env, largura_min=400))

        # ---- previa ------------------------------------------------------
        dirw = QWidget()
        ld = QVBoxLayout(dirw)
        self.previa = PreviaCamada()
        ld.addWidget(self.previa, 1)
        raiz.addWidget(dirw, 1)

        garantir_alturas(self)

    # ---------------------------------------------------------------------

    def _dpis(self) -> tuple[float, float]:
        dpi_y = self.perfil.cabecote.dpi_y_nativo or 360.0
        return self.perfil.dpi_x_efetivo, dpi_y

    def abrir(self):
        caminho, _ = abrir_arquivo(
            self, "Abrir imagem ou PDF", "", cimg.filtro_de_arquivo())
        if not caminho:
            return
        self._carregar(caminho, 0)

    def _trocar_pagina(self):
        if self.imagem and getattr(self, "_caminho", None):
            self._carregar(self._caminho, self.sp_pagina.value() - 1)

    def _carregar(self, caminho, pagina):
        try:
            img = cimg.carregar(caminho, pagina=pagina)
        except cimg.ImagemInvalida as e:
            QMessageBox.critical(self, "Nao consegui abrir", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Nao consegui abrir",
                                 f"{type(e).__name__}: {e}")
            return
        self._mostrar_carregada(img, caminho, pagina)

    def usar_padrao(self):
        # Altura de exatamente uma faixa: os 128 bicos na grade em Y
        _, dpi_y = self._dpis()
        altura = self.perfil.cabecote.n_bicos * 25.4 / dpi_y
        self._mostrar_carregada(cimg.padrao_de_teste(60.0, altura), None, 0)

    def _mostrar_carregada(self, img, caminho, pagina):
        self.imagem = img
        self._caminho = caminho
        self.sp_pagina.blockSignals(True)
        self.sp_pagina.setRange(1, max(1, img.paginas))
        self.sp_pagina.setValue(pagina + 1)
        self.sp_pagina.setEnabled(img.paginas > 1)
        self.sp_pagina.blockSignals(False)

        nat = img.tamanho_natural_mm()
        self.bt_natural.setEnabled(nat is not None)
        texto = (f"<b>{img.nome}</b><br>{img.largura_px} x {img.altura_px} px")
        if nat:
            if img.tamanho_confiavel:
                texto += (f"<br>tamanho da pagina: {nat[0]:.1f} x "
                          f"{nat[1]:.1f} mm")
            else:
                texto += (f"<br><span style='color:#e0a030'>o arquivo declara "
                          f"{nat[0]:.1f} x {nat[1]:.1f} mm, mas um PNG nao "
                          "sabe que tamanho tem: quase todo programa grava um "
                          "DPI padrao. Confira a medida.</span>")
        self.lb_arquivo.setText(texto)

        if nat:
            self._definir_tamanho(nat[0], nat[1])
        else:
            self._largura_mudou()
        self.processar()

    def _usar_natural(self):
        if not self.imagem:
            return
        nat = self.imagem.tamanho_natural_mm()
        if nat:
            self._definir_tamanho(nat[0], nat[1])
            self.processar()

    def _definir_tamanho(self, w, h):
        for sp, v in ((self.sp_larg, w), (self.sp_alt, h)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)

    def _proporcao_mudou(self, estado):
        """
        Remarcar "manter a proporcao" reaplica a proporcao na hora.

        Quem destrava, ajusta a altura e trava de novo espera que a imagem
        volte a ficar proporcional. Guardar so o estado e esperar a proxima
        edicao deixaria a figura distorcida sem aviso.
        """
        self._travar_proporcao = bool(estado)
        if self._travar_proporcao:
            self._largura_mudou()

    def _proporcao(self) -> float | None:
        if not self.imagem or self.imagem.largura_px == 0:
            return None
        return self.imagem.altura_px / self.imagem.largura_px

    def _largura_mudou(self):
        r = self._proporcao()
        if self._travar_proporcao and r:
            self.sp_alt.blockSignals(True)
            self.sp_alt.setValue(self.sp_larg.value() * r)
            self.sp_alt.blockSignals(False)
        self._t.start()

    def _altura_mudou(self):
        r = self._proporcao()
        if self._travar_proporcao and r:
            self.sp_larg.blockSignals(True)
            self.sp_larg.setValue(self.sp_alt.value() / r)
            self.sp_larg.blockSignals(False)
        self._t.start()

    # ---------------------------------------------------------------------

    def opcoes(self) -> cimg.Opcoes:
        return cimg.Opcoes(
            largura_mm=self.sp_larg.value(),
            altura_mm=self.sp_alt.value(),
            modo=self.cb_modo.currentData(),
            limiar=self.sp_limiar.value(),
            inverter=self.ck_inverter.isChecked(),
            espelhar_x=self.ck_esp_x.isChecked(),
            espelhar_y=self.ck_esp_y.isChecked(),
            x0_mm=self.sp_x0.value(),
            y0_mm=self.sp_y0.value(),
            z_mm=self.sp_z.value())

    @property
    def processando(self) -> bool:
        return self._trabalho is not None

    def processar(self):
        if self.imagem is None:
            return
        if self.processando:
            self._refazer = True        # refaz com os valores novos no fim
            return
        dpi_x, dpi_y = self._dpis()
        args = (self.imagem, dpi_x, dpi_y, self.opcoes())
        resultado: dict = {}

        def rodar():
            try:
                resultado["ok"] = cimg.para_bitmap(*args)
            except Exception as e:      # mostrado na interface, no fim
                resultado["erro"] = e

        self._resultado = resultado
        self._trabalho = threading.Thread(target=rodar, daemon=True)
        self._trabalho.start()
        self._espera.start()
        self.lb_info.setText("Processando a imagem...")
        self._atualizar_envio()

    def _processado(self):
        if self._trabalho is None or self._trabalho.is_alive():
            return
        self._espera.stop()
        self._trabalho = None
        if self._refazer:
            self._refazer = False
            self.processar()
            return

        dpi_x, dpi_y = self._dpis()
        if "erro" in self._resultado:
            e = self._resultado["erro"]
            self.lb_info.setText(
                f"<span style='color:#ff6b6b'>{type(e).__name__}: {e}</span>")
            self.bitmap = None
            self._atualizar_envio()
            return
        self.bitmap, self.grade = self._resultado["ok"]

        cob = cimg.estimar_cobertura(self.bitmap)
        n_faixas = -(-self.grade.n_linhas // self.perfil.cabecote.n_bicos)
        esp = self.perfil.espessura_teorica_um(dpi_x, dpi_y)
        linhas = [
            f"<b>Grade</b> {self.grade.n_linhas} linhas x "
            f"{self.grade.n_colunas} colunas "
            f"({dpi_x:.0f} x {dpi_y:.0f} dpi)",
            f"<b>Tamanho</b> {self.grade.largura_mm:.2f} x "
            f"{self.grade.altura_mm:.2f} mm",
            f"<b>Cobertura</b> {cob*100:.1f}% da area "
            f"({int(self.bitmap.sum()):,} gotas)".replace(",", "."),
            f"<b>Passadas</b> {n_faixas} faixa(s) de "
            f"{self.perfil.cabecote.n_bicos} bicos",
        ]
        if esp:
            linhas.append(
                f"<b>Deposicao</b> {esp * cob:.2f} um de espessura media "
                f"({esp:.2f} um onde ha gota)")
        self.lb_info.setText("<br>".join(linhas))

        self.previa.mostrar(
            self.bitmap, self.grade,
            f"{self.imagem.nome}   |   {self.grade.largura_mm:.1f} x "
            f"{self.grade.altura_mm:.1f} mm   |   {cob*100:.1f}% de cobertura")
        self._atualizar_envio()

    # ---------------------------------------------------------------------

    def unidades(self) -> list:
        if self.bitmap is None or self.grade is None:
            return []
        nome = self.imagem.nome if self.imagem else "imagem"
        return [cenv.Unidade(rotulo=nome, bitmap=self.bitmap,
                             grade=self.grade, z_mm=self.sp_z.value())]

    def _atualizar_envio(self):
        if self.processando:
            self.lb_envio.setText("Aguardando o processamento da imagem.")
            self.bt_enviar.setEnabled(False)
            return
        uns = self.unidades()
        if not uns:
            self.lb_envio.setText("Carregue um arquivo.")
            self.bt_enviar.setEnabled(False)
            return

        sessao = self._obter_maquina() if self._obter_maquina else None
        motivos = []
        if sessao is None or not sessao.aberta:
            motivos.append("sem conexao: conecte na barra superior")
        if self.perfil.cabecote.sentido_indice not in (1, -1):
            motivos.append("falta o sentido do indice dos bicos (pagina Maquina)")
        if motivos:
            self.lb_envio.setText("<br>".join(
                f"<span style='color:#e0a030'>Bloqueado: {m}</span>"
                for m in motivos))
            self.bt_enviar.setEnabled(False)
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
            self.bt_enviar.setEnabled(False)
            return

        comp = compensar_bicos(self.perfil.cabecote, self.grade.dpi_x,
                               self.perfil.processo.velocidade_mm_s)
        self._compensacao = comp
        plano = cenv.planejar(
            sessao, self.perfil, uns, comp,
            velocidade_mm_s=self.perfil.processo.velocidade_mm_s,
            accel_passos_s2=p["accel"], runup_margin=p["runup"],
            min_fire_period_us=p["min_fire"], vmax_x_passos_s=p["vmax_x"])
        self._plano = plano

        txt = (f"{plano.n_swaths} swath(s), "
               f"{plano.bytes_totais / 1000:.0f} KB, pitch {plano.pitch} = "
               f"{plano.dpi_x_real:.0f} dpi, recuo {plano.recuo_mm:.1f} mm")
        for e in plano.erros:
            txt += f"<br><span style='color:#ff6b6b'>{e}</span>"
        self.lb_envio.setText(txt)
        self.bt_enviar.setEnabled(plano.viavel)

    def enviar(self):
        sessao = self._obter_maquina() if self._obter_maquina else None
        uns = self.unidades()
        if sessao is None or not uns or not self._plano \
                or not self._plano.viavel:
            return

        resp = QMessageBox.question(
            self, "Confirmar jateamento",
            "Isto vai MOVER A MAQUINA e JATEAR.\n\n"
            f"{self.imagem.nome}\n"
            f"{self.grade.largura_mm:.1f} x {self.grade.altura_mm:.1f} mm em "
            f"({self.sp_x0.value():.0f}, {self.sp_y0.value():.0f}) mm\n"
            f"{self._plano.n_swaths} swath(s), "
            f"{int(self.bitmap.sum()):,} gotas\n\n"
            "Confirma?".replace(",", "."))
        if resp != QMessageBox.StandardButton.Yes:
            return

        from PySide6.QtWidgets import QApplication
        self.bt_enviar.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, self._plano.n_swaths)
        self._cancelar = False

        def progresso(n, total, rotulo):
            self.barra.setValue(n)
            QApplication.processEvents()

        res = cenv.enviar_unidades(
            sessao, self.perfil, uns, self._compensacao, self._plano,
            progresso=progresso, cancelado=lambda: self._cancelar)

        self.barra.setVisible(False)
        if res["erros"]:
            QMessageBox.critical(
                self, "A maquina recusou",
                "O job foi cancelado.\n\n" + "\n".join(res["erros"][:5]))
        else:
            QMessageBox.information(
                self, "Enviado",
                f"{res['enviadas']} swath(s) enfileirada(s).")
        self._atualizar_envio()
