"""
Pagina Arquivos: imprimir um pacote de camadas `.pajm` gerado por um CAM
externo (o RP3 do laboratorio). O formato esta no IMPORTACAO.md.

O pacote traz a geometria; daqui para a frente o caminho e o de uma peca
fatiada aqui: camadas na grade da maquina, opacidade, Z, planejamento e o
mesmo envio. Se o pixel do pacote nao bate com a grade da maquina, as
camadas sao reamostradas, e a tela avisa.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ..core import envio as cenv
from ..core import imagem as cimg
from ..core import pacote as cpac
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


class PainelArquivos(QWidget):
    def __init__(self, perfil: Perfil, obter_maquina=None, parent=None):
        super().__init__(parent)
        self.perfil = perfil
        self._obter_maquina = obter_maquina
        self.pacote: cpac.Pacote | None = None
        self._plano = None
        self._compensacao = None
        self._cache_params = None
        self._cancelar = False

        raiz = QHBoxLayout(self)
        esq = QWidget()
        lay = QVBoxLayout(esq)

        g_arq = QGroupBox("Pacote de camadas")
        l_arq = QVBoxLayout(g_arq)
        self.bt_abrir = tema.primario(QPushButton("Abrir .pajm..."))
        self.bt_abrir.clicked.connect(lambda: self.abrir())
        l_arq.addWidget(self.bt_abrir)
        self.lb_info = tema.nota(
            "Arquivo gerado por um programa de planejamento de processo "
            "(RP3), no formato do IMPORTACAO.md.")
        l_arq.addWidget(self.lb_info)
        lay.addWidget(g_arq)

        g_par = QGroupBox("Posicao e processo")
        f_par = QFormLayout(g_par)
        self.sp_x0 = _spin_mm(-1000, 2000, 0.0)
        self.sp_y0 = _spin_mm(-1000, 2000, 0.0)
        f_par.addRow("Canto em X (mesa)", self.sp_x0)
        f_par.addRow("Canto em Y (mesa)", self.sp_y0)
        # A velocidade e a da pagina Maquina, a mesma de todas as impressoes;
        # a do pacote aparece so como sugestao
        self.lb_vel = QLabel("-")
        self.lb_vel.setWordWrap(True)
        self.lb_vel.setToolTip("Ajuste na pagina Maquina, cartao Impressao.")
        f_par.addRow("Velocidade", self.lb_vel)
        self.sp_opac = QSpinBox()
        self.sp_opac.setRange(1, 100)
        self.sp_opac.setValue(100)
        self.sp_opac.setSuffix(" %")
        self.sp_opac.setToolTip(
            "Fracao das gotas de cada camada. Camadas sobrepostas usam gotas "
            "diferentes, entao onde ha mais camadas fica mais escuro.")
        f_par.addRow("Opacidade por camada", self.sp_opac)
        self.ck_mover_z = QCheckBox("Mover o Z entre camadas")
        self.ck_mover_z.setChecked(True)
        f_par.addRow(self.ck_mover_z)
        self.ck_contorno = QCheckBox("Contorno de cada camada")
        self.ck_contorno.setToolTip(
            "Jateia a borda de cada camada com todas as gotas, por cima do "
            "preenchimento na opacidade escolhida: as camadas sobrepostas "
            "ficam identificaveis.")
        self.sp_contorno = _spin_mm(0.05, 5.0, 0.2)
        linha_ct = QHBoxLayout()
        linha_ct.addWidget(self.ck_contorno)
        linha_ct.addWidget(self.sp_contorno)
        f_par.addRow(linha_ct)
        self.ck_contorno.toggled.connect(self._parametros_mudaram)
        self.sp_contorno.valueChanged.connect(self._parametros_mudaram)
        f_par.addRow(tema.nota(
            "Os valores de processo do pacote sao sugestoes: valem os desta "
            "tela."))
        for w in (self.sp_x0, self.sp_y0):
            w.valueChanged.connect(self._atualizar_envio)
        self.sp_opac.valueChanged.connect(self._parametros_mudaram)
        self.ck_mover_z.toggled.connect(self._parametros_mudaram)
        lay.addWidget(g_par)

        g_env = QGroupBox("Enviar para a maquina")
        l_env = QVBoxLayout(g_env)
        self.lb_envio = tema.nota("Abra um pacote.")
        l_env.addWidget(self.lb_envio)
        self.bt_enviar = tema.primario(QPushButton("Imprimir"))
        self.bt_enviar.setEnabled(False)
        self.bt_enviar.clicked.connect(self.enviar)
        l_env.addWidget(self.bt_enviar)
        self.barra = QProgressBar()
        self.barra.setVisible(False)
        l_env.addWidget(self.barra)

        g_cam = QGroupBox("Para o programa de CAM")
        l_cam = QVBoxLayout(g_cam)
        l_cam.addWidget(tema.nota(
            "Grade, faixa e area util da maquina, para o CAM gerar o pacote "
            "ja na grade nativa, sem reamostragem. Vale para a calibracao "
            "atual: exporte de novo se o angulo for recalibrado."))
        self.bt_perfil = QPushButton("Exportar perfil da maquina...")
        self.bt_perfil.clicked.connect(lambda: self.exportar_perfil())
        l_cam.addWidget(self.bt_perfil)
        lay.addWidget(g_cam)
        lay.addStretch(1)
        # O envio fica num rodape fixo, sempre a vista
        raiz.addWidget(coluna_com_rodape(esq, g_env, largura_min=400))

        dirw = QWidget()
        ld = QVBoxLayout(dirw)
        self.previa = PreviaCamada()
        ld.addWidget(self.previa, 1)
        linha = QHBoxLayout()
        linha.addWidget(QLabel("Camada"))
        self.sl_camada = QSlider(Qt.Orientation.Horizontal)
        self.sl_camada.setEnabled(False)
        self.sl_camada.valueChanged.connect(self._mostrar_camada)
        linha.addWidget(self.sl_camada, 1)
        self.lb_camada = QLabel("-")
        linha.addWidget(self.lb_camada)
        ld.addLayout(linha)
        raiz.addWidget(dirw, 1)

        garantir_alturas(self)
        self.previa.limpar("Abra um pacote .pajm.")
        self.mostrar_velocidade()

    # ---------------------------------------------------------------------

    def _dpis(self) -> tuple[float, float]:
        dpi_y = self.perfil.cabecote.dpi_y_nativo or 360.0
        return self.perfil.dpi_x_efetivo, dpi_y

    def abrir(self, caminho: str | None = None) -> bool:
        if caminho is None:
            caminho, _ = abrir_arquivo(
                self, "Abrir pacote de camadas", "",
                "Pacote de camadas (*.pajm);;Tudo (*)")
            if not caminho:
                return False
        try:
            p = cpac.abrir(caminho)
        except cpac.PacoteInvalido as e:
            QMessageBox.critical(self, "Pacote recusado", str(e))
            return False
        self.pacote = p

        proc = p.processo
        for sp, v in ((self.sp_x0, p.origem_mm[0]), (self.sp_y0, p.origem_mm[1]),
                      (self.sp_opac, None if proc.get("opacidade") is None
                       else round(100 * proc["opacidade"]))):
            if v is not None:
                sp.blockSignals(True)
                sp.setValue(v)
                sp.blockSignals(False)
        self.ck_mover_z.blockSignals(True)
        self.ck_mover_z.setChecked(bool(proc.get("mover_z", True)))
        self.ck_mover_z.blockSignals(False)

        dpi_x, dpi_y = self._dpis()
        larg, alt = p.dimensoes_mm
        txt = (f"<b>{p.nome_peca}</b><br>gerado por {p.gerador}<br>"
               f"{len(p)} camada(s), {larg:.1f} x {alt:.1f} mm, Z de "
               f"{p.z_mm[0]:.3f} a {p.z_mm[-1]:.3f} mm<br>"
               f"pixel {p.pixel_mm[0] * 1000:.1f} x "
               f"{p.pixel_mm[1] * 1000:.1f} um")
        if p.grade_nativa(dpi_x, dpi_y):
            txt += (f"<br><span style='color:{tema.OK}'>na grade da "
                    "maquina: usado pixel a pixel</span>")
        else:
            txt += (f"<br><span style='color:{tema.ALERTA}'>fora da grade da "
                    f"maquina ({25.4 / dpi_x * 1000:.1f} x "
                    f"{25.4 / dpi_y * 1000:.1f} um): as camadas serao "
                    "reamostradas. Para evitar, exporte o perfil da maquina "
                    "para o CAM.</span>")
        for a in p.avisos:
            txt += f"<br><span style='color:{tema.ALERTA}'>{a}</span>"
        self.lb_info.setText(txt)

        self.mostrar_velocidade()
        self.sl_camada.setEnabled(True)
        self.sl_camada.setRange(0, len(p) - 1)
        self.sl_camada.setValue(0)
        self._mostrar_camada()
        self._atualizar_envio()
        return True

    def mostrar_velocidade(self):
        vel = self.perfil.processo.velocidade_mm_s
        txt = f"{vel:g} mm/s (pagina Maquina)"
        sug = (self.pacote.processo.get("velocidade_mm_s")
               if self.pacote else None)
        if sug is not None and abs(sug - vel) > 1e-9:
            txt += (f"<br><span style='color:{tema.ALERTA}'>o pacote sugere "
                    f"{sug:g} mm/s</span>")
        self.lb_vel.setText(txt)

    def _parametros_mudaram(self, *_):
        self._mostrar_camada()
        self._atualizar_envio()

    def bitmap_da_camada(self, i: int) -> np.ndarray:
        """Camada i na grade da maquina, com a opacidade e o contorno."""
        dpi_x, dpi_y = self._dpis()
        cheia = self.pacote.bitmap_na_grade(i, dpi_x, dpi_y)
        b = cheia
        op = self.sp_opac.value() / 100.0
        if op < 1.0:
            b = b & cimg.mascara_de_opacidade(b.shape, op, ordem=i)
        if self.ck_contorno.isChecked():
            mm = self.sp_contorno.value()
            b = b | cimg.contorno(
                cheia, max(1, int(round(mm * dpi_x / 25.4))),
                max(1, int(round(mm * dpi_y / 25.4))))
        return b

    def _z(self, i: int) -> float:
        p = self.pacote
        return float(p.z_mm[i] if self.ck_mover_z.isChecked() else p.z_mm[0])

    def _grade(self):
        dpi_x, dpi_y = self._dpis()
        return self.pacote.grade(dpi_x, dpi_y,
                                 (self.sp_x0.value(), self.sp_y0.value()))

    def unidades(self) -> list:
        if self.pacote is None:
            return []
        g = self._grade()
        return [cenv.Unidade(rotulo=f"camada {i + 1}",
                             bitmap=self.bitmap_da_camada(i), grade=g,
                             z_mm=self._z(i))
                for i in range(len(self.pacote))]

    def _mostrar_camada(self, *_):
        if self.pacote is None:
            return
        i = self.sl_camada.value()
        self.lb_camada.setText(f"{i + 1} / {len(self.pacote)}")
        b = self.bitmap_da_camada(i)
        self.previa.mostrar(
            b, self._grade(),
            f"camada {i + 1} de {len(self.pacote)}   |   z = {self._z(i):.3f} mm"
            f"   |   {int(b.sum())} gotas")

    # ---- envio -----------------------------------------------------------

    def _atualizar_envio(self, *_):
        self._plano = None
        self.bt_enviar.setEnabled(False)
        if self.pacote is None:
            self.lb_envio.setText("Abra um pacote.")
            return
        sessao = self._obter_maquina() if self._obter_maquina else None
        if sessao is None or not sessao.aberta:
            self.lb_envio.setText(
                f"<span style='color:{tema.ALERTA}'>Bloqueado: sem conexao. "
                "Conecte na barra superior.</span>")
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
                f"<span style='color:{tema.PERIGO}'>nao consegui ler os "
                f"parametros da maquina: {e}</span>")
            return
        vel = self.perfil.processo.velocidade_mm_s
        dpi_x, _ = self._dpis()
        self._compensacao = compensar_bicos(self.perfil.cabecote, dpi_x, vel)
        plano = cenv.planejar(
            sessao, self.perfil, self.unidades(), self._compensacao,
            velocidade_mm_s=vel, accel_passos_s2=p["accel"],
            runup_margin=p["runup"], min_fire_period_us=p["min_fire"],
            vmax_x_passos_s=p["vmax_x"])
        self._plano = plano
        txt = (f"{plano.n_swaths} passada(s), "
               f"{plano.bytes_totais / 1e6:.1f} MB, "
               f"{plano.dpi_x_real:.0f} dpi em X")
        for e in plano.erros:
            txt += f"<br><span style='color:{tema.PERIGO}'>{e}</span>"
        self.lb_envio.setText(txt)
        self.bt_enviar.setEnabled(plano.viavel)

    def enviar(self):
        sessao = self._obter_maquina() if self._obter_maquina else None
        if sessao is None or self._plano is None or not self._plano.viavel:
            return
        resp = QMessageBox.question(
            self, "Confirmar impressao",
            "Isto vai MOVER A MAQUINA e JATEAR.\n\n"
            f"{self.pacote.nome_peca}: {len(self.pacote)} camada(s), "
            f"{self._plano.n_swaths} passada(s)\n\nConfirma?")
        if resp != QMessageBox.StandardButton.Yes:
            return
        self.bt_enviar.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, self._plano.n_swaths)
        self._cancelar = False

        def progresso(n, total, rotulo):
            self.barra.setValue(n)
            self.lb_envio.setText(f"enviando {rotulo}: passada {n} de {total}")
            QApplication.processEvents()

        res = cenv.enviar_unidades(
            sessao, self.perfil, self.unidades(), self._compensacao,
            self._plano, progresso=progresso,
            cancelado=lambda: self._cancelar)
        self.barra.setVisible(False)
        if res["erros"]:
            QMessageBox.critical(
                self, "A maquina recusou",
                "O trabalho foi cancelado.\n\n" + "\n".join(res["erros"][:5]))
        else:
            QMessageBox.information(
                self, "Enviado",
                f"{res['enviadas']} passada(s) enfileirada(s).")
        self._atualizar_envio()

    # ---- perfil para o CAM ----------------------------------------------

    def exportar_perfil(self, caminho: str | None = None) -> bool:
        if caminho is None:
            caminho, _ = salvar_arquivo(
                self, "Exportar perfil da maquina", "perfil_maquina_pajm.json",
                "JSON (*.json)")
            if not caminho:
                return False
        try:
            d = cpac.perfil_para_cam(self.perfil)
        except ValueError as e:
            QMessageBox.critical(self, "Sem perfil", str(e))
            return False
        Path(caminho).write_text(json.dumps(d, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
        return True
