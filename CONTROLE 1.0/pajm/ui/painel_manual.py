"""
Painel: o comando manual da maquina, em cartoes, como o painel do Mainsail.

Como a maquina nao tem tela, este painel E a interface de operacao: e por aqui
que se posiciona X, Y e Z, referencia os eixos, liga o cabecote e acompanha o
estado. A conexao e a parada ficam na barra superior da janela.

MODO DE SIMULACAO
A conexao pode ser com a maquina real (porta COM) ou com o firmware simulado,
que roda no proprio programa. No simulado tudo responde como a maquina real
responderia, inclusive recusando movimento fora do curso. Serve para operar e
testar o software sem a maquina ligada, que hoje e a unica forma possivel.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QTextEdit,
    QVBoxLayout, QWidget,
)

from ..core.envio import aplicar_hello_ack
from ..machine import protocolo as pv
from ..machine.sessao import EnlacePerdido, Evento, Sessao, VersaoIncompativel
from ..machine.simulador import FirmwareSimulado, TransporteSimulado
from ..machine.transporte_serial import SerialIndisponivel, TransporteSerial
from ..profile import Perfil
from . import tema
from .comum import em_rolagem, garantir_alturas
from .tema import primario


class PainelManual(QWidget):
    conectou = Signal(bool)
    # "desconectado", "conectado", "simulado" ou "perdido": o indicador do
    # canto da janela segue este sinal
    estado_conexao = Signal(str)
    # Estado da maquina (pv.Estado) a cada STATUS, ou -1 sem sessao: o chip
    # da barra superior segue este sinal
    estado_maquina = Signal(int)
    # A velocidade de impressao mudou (vale para todas as paginas)
    velocidade_mudou = Signal(float)

    # VMAX de cada eixo, na ordem X, Y, Z (secao 8.2)
    _PARAM_VMAX = (pv.Param.VMAX_X, pv.Param.VMAX_Y, pv.Param.VMAX_Z)

    def __init__(self, perfil: Perfil, parent=None):
        super().__init__(parent)
        self.perfil = perfil
        self.sessao: Sessao | None = None
        self.firmware_sim: FirmwareSimulado | None = None

        # ---- conexao -----------------------------------------------------
        # Os widgets sao criados aqui, porque a sessao e deste painel, mas
        # ficam na barra superior da janela (widgets_conexao), visiveis em
        # todas as paginas.
        self.cb_porta = QComboBox()
        self.cb_porta.setMinimumWidth(220)
        self._listar_portas()
        self.bt_atualizar = QPushButton("↻")
        self.bt_atualizar.setToolTip("Procurar portas de novo")
        self.bt_atualizar.setMaximumWidth(34)
        self.bt_atualizar.clicked.connect(self._listar_portas)
        self.cb_baud = QComboBox()
        # A secao 2 preve cair para 921600 se 2 Mbaud nao passar no bring-up.
        for b in (pv.BAUD, pv.BAUD_ALTERNATIVO):
            self.cb_baud.addItem(f"{b} baud", b)
        self.bt_conectar = primario(QPushButton("Conectar"))
        self.bt_conectar.clicked.connect(self.alternar_conexao)
        self.widgets_conexao = [self.cb_porta, self.bt_atualizar, self.cb_baud,
                                self.bt_conectar]

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)
        corpo_w = QWidget()
        corpo = QHBoxLayout(corpo_w)
        corpo.setSpacing(12)
        raiz.addWidget(em_rolagem(corpo_w, largura_min=720), 1)
        col_esq = QVBoxLayout()
        col_dir = QVBoxLayout()
        corpo.addLayout(col_esq, 1)
        corpo.addLayout(col_dir, 1)

        # ---- posicao (coluna da esquerda, como o "Toolhead" do Mainsail) ----
        g_pos = QGroupBox("Posicao")
        l_pos = QGridLayout(g_pos)
        self.lb = {}
        for c, eixo in enumerate("xyz"):
            nome = QLabel(eixo.upper())
            nome.setStyleSheet(f"color:{tema.TEXTO_FRACO};")
            nome.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val = QLabel("-")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setStyleSheet("font-family:Consolas,monospace; font-size:18px;"
                              " font-weight:600;")
            self.lb[eixo] = val
            l_pos.addWidget(nome, 0, c)
            l_pos.addWidget(val, 1, c)
        col_esq.addWidget(g_pos)

        g_jog = QGroupBox("Movimento")
        lay_jog = QVBoxLayout(g_jog)
        grade = QGridLayout()
        lay_jog.addLayout(grade)
        self._bt_jog = []
        passos = [50.0, 10.0, 1.0, 0.1]
        for r, eixo in enumerate("XYZ"):
            grade.addWidget(QLabel(f"<b>{eixo}</b>"), r, 0)
            for c, p in enumerate(passos):
                b = QPushButton(f"-{p:g}")
                b.clicked.connect(
                    lambda _=False, e=eixo, d=-p: self.jog(e, d))
                grade.addWidget(b, r, 1 + c)
                self._bt_jog.append(b)
            for c, p in enumerate(reversed(passos)):
                b = QPushButton(f"+{p:g}")
                b.clicked.connect(
                    lambda _=False, e=eixo, d=p: self.jog(e, d))
                grade.addWidget(b, r, 1 + len(passos) + c)
                self._bt_jog.append(b)

        l_ref = QHBoxLayout()
        for rotulo, eixos in (("⌂ Referenciar tudo", "xyz"), ("X", "x"),
                              ("Y", "y"), ("Z", "z")):
            b = QPushButton(rotulo)
            b.clicked.connect(lambda _=False, e=eixos: self.home(e))
            l_ref.addWidget(b)
            self._bt_jog.append(b)
        # MOTORS_OFF (protocolo 1.3): solta os eixos para mover a mao
        self.bt_motores_off = QPushButton("Desligar motores")
        self.bt_motores_off.setToolTip(
            "Desliga os drivers dos tres eixos para mover a mao. Os eixos "
            "perdem a referencia e o proximo movimento religa os motores.")
        self.bt_motores_off.clicked.connect(self.desligar_motores)
        l_ref.addWidget(self.bt_motores_off)
        self._bt_jog.append(self.bt_motores_off)
        lay_jog.addLayout(l_ref)
        col_esq.addWidget(g_jog)

        # Uma velocidade por eixo: o Z (fuso) aceita bem menos que X e Y
        # (correia). O teto de cada campo e o VMAX que a maquina informa ao
        # conectar; acima dele o firmware recusa o comando com BAD_PARAM.
        g_ir = QGroupBox("Ir para posicao")
        f_ir = QGridLayout(g_ir)
        f_ir.addWidget(QLabel(""), 0, 0)
        f_ir.addWidget(QLabel("Destino"), 0, 1)
        f_ir.addWidget(QLabel("Velocidade"), 0, 2)
        self.sp_ir = {}
        self.sp_vel = {}
        self.lb_vmax = {}
        inicial = self.perfil.movimento.velocidade_max_mm_s or 20.0
        for r, eixo in enumerate("xyz", start=1):
            s = QDoubleSpinBox()
            s.setRange(-1000, 1000)
            s.setDecimals(3)
            s.setSuffix(" mm")
            self.sp_ir[eixo] = s
            v = QDoubleSpinBox()
            v.setDecimals(1)
            v.setRange(0.1, 1000.0)
            v.setSuffix(" mm/s")
            v.setValue(inicial)
            lab = QLabel("max. ?")
            lab.setStyleSheet(f"color:{tema.TEXTO_FRACO};")
            self.sp_vel[eixo] = v
            self.lb_vmax[eixo] = lab
            self._bt_jog.append(v)
            f_ir.addWidget(QLabel(f"<b>{eixo.upper()}</b>"), r, 0)
            f_ir.addWidget(s, r, 1)
            f_ir.addWidget(v, r, 2)
            f_ir.addWidget(lab, r, 3)
        self.bt_ir = QPushButton("Mover")
        self.bt_ir.clicked.connect(self.mover)
        f_ir.addWidget(self.bt_ir, 4, 1, 1, 3)
        self._bt_jog.append(self.bt_ir)
        f_ir.addWidget(tema.nota(
            "A velocidade vale tambem para o jog. O maximo de cada eixo vem "
            "da maquina ao conectar."), 5, 0, 1, 4)
        col_esq.addWidget(g_ir)
        col_esq.addStretch(1)

        # ---- coluna da direita: estado, cabecote, registro ------------------
        g_est = QGroupBox("Estado da maquina")
        f_est = QFormLayout(g_est)
        self.lb_conexao = QLabel("desconectado")
        f_est.addRow("Conexao", self.lb_conexao)
        for chave, rotulo in (("estado", "Estado"), ("cabecote", "Cabecote"),
                              ("buffer", "Faixas no buffer"),
                              ("erro", "Ultimo erro")):
            lab = QLabel("-")
            lab.setStyleSheet("font-family:Consolas,monospace;")
            lab.setWordWrap(True)
            self.lb[chave] = lab
            f_est.addRow(rotulo, lab)
        linha_extra = QHBoxLayout()
        self.bt_cancelar = QPushButton("Cancelar impressao")
        self.bt_cancelar.setToolTip("O mesmo da barra superior: para com "
                                    "rampa e recolhe a maquina.")
        self.bt_cancelar.clicked.connect(lambda: self.cancelar_impressao())
        self.bt_limpar = QPushButton("Limpar falha")
        self.bt_limpar.clicked.connect(self.limpar_falha)
        linha_extra.addWidget(self.bt_cancelar)
        linha_extra.addWidget(self.bt_limpar)
        self._bt_jog += [self.bt_cancelar, self.bt_limpar]
        f_est.addRow(linha_extra)
        col_dir.addWidget(g_est)

        # SPIT: dispara o mesmo padrao varias vezes com o carro parado.
        # Serve para disparar num recipiente antes de imprimir e para o teste
        # de bicos, e e o primeiro teste do cabecote real.
        g_cab = QGroupBox("Cabecote")
        f_spit = QFormLayout(g_cab)
        l_cab = QHBoxLayout()
        self.bt_cab_on = QPushButton("Ligar")
        self.bt_cab_on.clicked.connect(lambda: self.cabecote(True))
        self.bt_cab_off = QPushButton("Desligar")
        self.bt_cab_off.clicked.connect(lambda: self.cabecote(False))
        l_cab.addWidget(self.bt_cab_on)
        l_cab.addWidget(self.bt_cab_off)
        f_spit.addRow("Alimentacao", l_cab)
        self._bt_jog += [self.bt_cab_on, self.bt_cab_off]
        self.cb_padrao = QComboBox()
        for rotulo, canais in (
                ("Todos os bicos", range(1, 129)),
                ("So impares (1, 3, 5...)", range(1, 129, 2)),
                ("So pares (2, 4, 6...)", range(2, 129, 2)),
                ("So CH1 e CH128", (1, 128)),
                ("Nenhum (so os sinais)", ())):
            self.cb_padrao.addItem(rotulo, pv.coluna_de_canais(canais))
        f_spit.addRow("Bicos", self.cb_padrao)
        self.sp_disparos = QSpinBox()
        self.sp_disparos.setRange(1, 65535)
        self.sp_disparos.setValue(100)
        self.sp_periodo = QSpinBox()
        self.sp_periodo.setRange(180, 65535)    # piso de 180 us do manual
        self.sp_periodo.setValue(1000)
        self.sp_periodo.setSuffix(" us")
        l_disp = QHBoxLayout()
        l_disp.addWidget(self.sp_disparos)
        l_disp.addWidget(QLabel("a cada"))
        l_disp.addWidget(self.sp_periodo)
        f_spit.addRow("Disparos", l_disp)
        self.bt_disparar = QPushButton("Disparar (carro parado)")
        self.bt_disparar.clicked.connect(self.disparar)
        f_spit.addRow(self.bt_disparar)
        self._bt_jog += [self.cb_padrao, self.sp_disparos, self.sp_periodo,
                         self.bt_disparar]
        col_dir.addWidget(g_cab)

        g_log = QGroupBox("Console")
        l_log = QVBoxLayout(g_log)
        self.tx_log = QTextEdit()
        self.tx_log.setReadOnly(True)
        self.tx_log.setMinimumHeight(180)
        self.tx_log.setStyleSheet("font-family:Consolas,monospace;"
                                  " font-size:12px;")
        l_log.addWidget(self.tx_log)
        col_dir.addWidget(g_log, 1)

        # ---- cartoes que ficam na pagina Maquina ----------------------------
        # Valem para todo envio (imagem, peca fatiada, pacotes e padroes de
        # teste) e ficam salvos em ajustes_pajm.json. Nao dependem da conexao.
        proc = self.perfil.processo
        self.g_impressao = QGroupBox("Impressao")
        f_imp = QFormLayout(self.g_impressao)
        # Uma velocidade so, para tudo o que vai a maquina: fatiador, imagem,
        # pacotes e padroes. O teto e o VMAX_X da maquina, lido ao conectar.
        self.sp_vel_imp = QDoubleSpinBox()
        self.sp_vel_imp.setRange(1.0, self.perfil.movimento.velocidade_max_mm_s
                                 or 250.0)
        self.sp_vel_imp.setDecimals(1)
        self.sp_vel_imp.setSingleStep(10.0)
        self.sp_vel_imp.setSuffix(" mm/s")
        self.sp_vel_imp.setValue(proc.velocidade_mm_s)
        self.sp_vel_imp.setToolTip(
            "Velocidade do carro em X durante o jateamento, para todas as "
            "impressoes. Mais rapido pede mais espaco para acelerar antes e "
            "depois da peca, e a frequencia de disparo nao pode passar do "
            "limite do cabecote; o envio avisa se passar.")
        f_imp.addRow("Velocidade de impressao", self.sp_vel_imp)
        self.lb_vmax_imp = tema.nota("maximo da maquina: conecte para ler")
        f_imp.addRow("", self.lb_vmax_imp)
        self.sp_purga = QSpinBox()
        self.sp_purga.setRange(0, 65535)
        self.sp_purga.setValue(proc.purga_disparos)
        self.sp_purga.setSpecialValueText("sem purga")
        self.sp_purga.setToolTip(
            "Antes de imprimir, todos os bicos disparam esta quantidade de "
            "vezes com o carro no zero, fora da mesa (onde fica o algodao).")
        f_imp.addRow("Purga (disparos)", self.sp_purga)
        self.sp_purga_per = QSpinBox()
        self.sp_purga_per.setRange(180, 65535)
        self.sp_purga_per.setSuffix(" us")
        self.sp_purga_per.setValue(proc.purga_periodo_us)
        f_imp.addRow("Periodo da purga", self.sp_purga_per)
        self.ck_apresentar = QCheckBox("Ao terminar, carro no zero e mesa em Y =")
        self.ck_apresentar.setChecked(proc.apresentar_ao_fim)
        self.sp_apresentar = QDoubleSpinBox()
        self.sp_apresentar.setRange(0.0, 1000.0)
        self.sp_apresentar.setDecimals(1)
        self.sp_apresentar.setSuffix(" mm")
        self.sp_apresentar.setValue(proc.apresentar_y_mm)
        linha_apr = QHBoxLayout()
        linha_apr.addWidget(self.ck_apresentar)
        linha_apr.addWidget(self.sp_apresentar)
        f_imp.addRow(linha_apr)
        self.sp_recolher_z = QDoubleSpinBox()
        self.sp_recolher_z.setRange(0.0, 1000.0)
        self.sp_recolher_z.setDecimals(1)
        self.sp_recolher_z.setSuffix(" mm")
        self.sp_recolher_z.setValue(proc.recolher_z_mm)
        self.sp_recolher_z.setToolTip(
            "Ao cancelar: o Z sobe ate esta altura (ou fica, se ja estiver "
            "acima), depois o carro vai a X = 0 e a mesa ao Y de apresentacao "
            "acima.")
        f_imp.addRow("Ao cancelar, Z sobe ate", self.sp_recolher_z)
        for w in (self.sp_purga, self.sp_purga_per, self.sp_apresentar,
                  self.sp_vel_imp, self.sp_recolher_z):
            w.valueChanged.connect(self._ajustes_mudaram)
        self.ck_apresentar.toggled.connect(self._ajustes_mudaram)

        self.g_diagnostico = QGroupBox("Diagnostico")
        l_diag = QVBoxLayout(self.g_diagnostico)
        self.ck_ready = QCheckBox("Conferir o READY do cabecote")
        self.ck_ready.setChecked(True)
        self.ck_ready.setToolTip(
            "Desmarque SO para testar os sinais sem o cabecote ligado: sem "
            "ele o READY nao muda e todo disparo daria falha READY_TIMEOUT. "
            "Desmarcado, o firmware tambem deixa de vigiar o nFAULT.")
        self.ck_ready.toggled.connect(self._alternar_ready)
        self._bt_jog.append(self.ck_ready)
        l_diag.addWidget(self.ck_ready)
        self.ck_fio = QCheckBox(
            "Mostrar os bytes do fio no console")
        self.ck_fio.setToolTip(
            "Para diagnostico de primeira conexao: quando o primeiro HELLO "
            "falha, a unica pergunta que importa e o que chegou no fio. "
            "Marque ANTES de conectar.")
        l_diag.addWidget(self.ck_fio)

        self._habilitar(False)

        garantir_alturas(self)
        garantir_alturas(self.g_impressao)
        garantir_alturas(self.g_diagnostico)
        # 20 ms: o protocolo tem tempo proprio (STATUS a cada 100 ms, RTO de
        # 200 ms, timeout de enlace de 1 s) e precisa ser servido bem mais
        # rapido do que a tela precisa ser redesenhada.
        self._t = QTimer(self)
        self._t.setInterval(20)
        self._t.timeout.connect(self._bombear)

    # ---------------------------------------------------------------------

    def _listar_portas(self):
        atual = self.cb_porta.currentData()
        self.cb_porta.clear()
        self.cb_porta.addItem("Simulador (sem maquina)", "sim")
        for dev, desc in TransporteSerial.portas_disponiveis():
            self.cb_porta.addItem(f"{dev} - {desc}", dev)
        if atual:
            i = self.cb_porta.findData(atual)
            if i >= 0:
                self.cb_porta.setCurrentIndex(i)

    def _espiar(self, direcao: str, dados: bytes):
        """Mostra os bytes crus, cortados: o registro nao e um analisador."""
        corte = dados[:48]
        hx = " ".join(f"{b:02X}" for b in corte)
        if len(dados) > len(corte):
            hx += f" ... (+{len(dados) - len(corte)} B)"
        cor = "#8ab4f8" if direcao == "tx" else "#9aa0a6"
        self._log(f"{direcao.upper()} {hx}", cor)

    def _log(self, texto: str, cor: str = "#d0d4d8"):
        self.tx_log.append(f"<span style='color:{cor}'>{texto}</span>")

    def _habilitar(self, ligado: bool):
        for b in self._bt_jog:
            b.setEnabled(ligado)

    # ---------------------------------------------------------------------

    def alternar_conexao(self):
        if self.sessao is not None:
            self.desconectar()
        else:
            self.conectar()

    def conectar(self):
        alvo = self.cb_porta.currentData()
        try:
            if alvo == "sim":
                self.firmware_sim = FirmwareSimulado()
                transporte = TransporteSimulado(self.firmware_sim,
                                                   auto_executar=True)
            else:
                self.firmware_sim = None
                transporte = TransporteSerial(
                    alvo, baud=self.cb_baud.currentData(),
                    espiao=self._espiar if self.ck_fio.isChecked() else None)
            self.sessao = Sessao(transporte)
            ha = self.sessao.abrir()
        except VersaoIncompativel as e:
            self.sessao = None
            self.firmware_sim = None
            QMessageBox.critical(self, "Versao de protocolo incompativel",
                                 str(e))
            self._log(str(e), "#ff6b6b")
            return
        except (EnlacePerdido, SerialIndisponivel, Exception) as e:
            self.sessao = None
            self.firmware_sim = None
            QMessageBox.critical(self, "Nao consegui conectar", str(e))
            self._log(f"falha ao conectar: {e}", "#ff6b6b")
            return

        simulado = alvo == "sim"
        self.lb_conexao.setText(
            "<b style='color:#e0a030'>SIMULADO</b>" if simulado
            else "<b style='color:#4caf50'>conectado</b>")
        self.bt_conectar.setText("Desconectar")
        self.cb_porta.setEnabled(False)
        self.cb_baud.setEnabled(False)
        self._habilitar(True)
        self._log(
            f"conectado a {self.cb_porta.currentText()}: protocolo "
            f"{ha.proto_major}.{ha.proto_minor}, firmware "
            f"{ha.versao_firmware}", "#4caf50")

        # A maquina e a fonte da verdade para passos por mm e curso dos eixos:
        # o perfil deixa de adivinhar no momento em que ela responde.
        for m in aplicar_hello_ack(self.perfil, ha):
            self._log(f"perfil atualizado pela maquina: {m}", "#8ab4f8")
        self._limitar_velocidades()
        self.sp_periodo.setMinimum(ha.min_fire_period_us)
        self._ler_check_ready()

        if simulado:
            self._log("Modo simulado: nenhum movimento real acontece.",
                      "#e0a030")
        self._t.start()
        self.conectou.emit(True)
        self.estado_conexao.emit("simulado" if simulado else "conectado")

    def desconectar(self, perdido: bool = False):
        """
        Fecha a sessao. Desconectando de proposito, desliga antes o cabecote e
        os motores; com o enlace perdido nao ha como mandar nada, e quem
        desliga e o firmware (protocolo 1.4).
        """
        self._t.stop()
        if self.sessao and not perdido:
            try:
                if self.sessao.desligar_para_fechar():
                    self._log("cabecote e motores desligados")
            except Exception as e:
                self._log(f"nao consegui desligar antes de fechar ({e})",
                          "#e0a030")
        try:
            if self.sessao:
                self.sessao.fechar()
        except Exception:
            pass
        self.sessao = None
        self.lb_conexao.setText(
            "<b style='color:#ff6b6b'>conexao perdida</b>" if perdido
            else "desconectado")
        self.bt_conectar.setText("Conectar")
        self.cb_porta.setEnabled(True)
        self.cb_baud.setEnabled(True)
        self._habilitar(False)
        for lab in self.lb.values():
            lab.setText("-")
        self._log("desconectado")
        self.conectou.emit(False)
        self.estado_conexao.emit("perdido" if perdido else "desconectado")
        self.estado_maquina.emit(-1)

    # ---------------------------------------------------------------------

    def _limitar_velocidades(self):
        """
        Le o VMAX de cada eixo e usa como teto dos campos de velocidade.

        O VMAX e da maquina (secao 8.2) e pode mudar com SET_PARAM, entao e
        lido a cada conexao em vez de ficar no perfil.
        """
        try:
            vals = self.sessao.obter_parametros(self._PARAM_VMAX)
        except Exception as e:
            self._log(f"nao consegui ler o VMAX dos eixos ({e}); as "
                      "velocidades ficam sem teto", "#e0a030")
            return
        for i, eixo in enumerate("xyz"):
            vmax = vals[int(self._PARAM_VMAX[i])] / self.sessao.passos_por_mm(i)
            # Para baixo: arredondar para cima passaria do VMAX em passos/s
            vmax = math.floor(vmax * 10) / 10
            self.sp_vel[eixo].setMaximum(vmax)   # ja reduz o valor, se preciso
            self.lb_vmax[eixo].setText(f"max. {vmax:g} mm/s")
            if eixo == "x":
                # O setMaximum ja reduz o valor, se preciso, e isso salva
                self.sp_vel_imp.setMaximum(vmax)
                self.lb_vmax_imp.setText(f"maximo da maquina: {vmax:g} mm/s")
        self._log("velocidade maxima da maquina: " + ", ".join(
            f"{e.upper()} {self.sp_vel[e].maximum():g}" for e in "xyz")
            + " mm/s", "#8ab4f8")

    def _velocidade(self, eixo: int) -> float:
        """Velocidade escolhida na tela para o eixo, em mm/s."""
        return self.sp_vel["xyz"[eixo]].value()

    def jog(self, eixo: str, delta_mm: float):
        if self.sessao is None:
            return
        i = "xyz".index(eixo.lower())
        self.sessao.jog_mm(i, delta_mm, self._velocidade(i))
        self._log(f"jog {eixo} {delta_mm:+g} mm")

    def home(self, eixos: str):
        if self.sessao is None:
            return
        self.sessao.referenciar(eixos)
        self._log(f"referenciar {eixos.upper()}")

    def mover(self):
        if self.sessao is None:
            return
        for eixo, chave in ((pv.EIXO_X, "x"), (pv.EIXO_Y, "y"),
                            (pv.EIXO_Z, "z")):
            alvo = self.sp_ir[chave].value()
            self.sessao.mover_mm(eixo, alvo, self._velocidade(eixo))
        self._log("mover para ({:g}, {:g}, {:g}) mm".format(
            *(self.sp_ir[c].value() for c in "xyz")))

    def cabecote(self, ligado: bool):
        if self.sessao is None:
            return
        self.sessao.cabecote(ligado)
        self._log(f"cabecote {'ligado' if ligado else 'desligado'}")

    def desligar_motores(self):
        if self.sessao is None:
            return
        if not self.sessao.tem_motors_off:
            QMessageBox.warning(
                self, "Firmware antigo",
                "Desligar os motores exige o firmware com protocolo 1.3. "
                "Grave o firmware novo na placa.")
            return
        self.sessao.desligar_motores()
        self._log("motores desligados (os eixos voltam a exigir "
                  "referenciamento)", "#e0a030")

    def _ajustes_mudaram(self, *_):
        proc = self.perfil.processo
        proc.purga_disparos = self.sp_purga.value()
        proc.purga_periodo_us = self.sp_purga_per.value()
        proc.apresentar_ao_fim = self.ck_apresentar.isChecked()
        proc.apresentar_y_mm = self.sp_apresentar.value()
        proc.recolher_z_mm = self.sp_recolher_z.value()
        mudou_vel = proc.velocidade_mm_s != self.sp_vel_imp.value()
        proc.velocidade_mm_s = self.sp_vel_imp.value()
        try:
            self.perfil.salvar_ajustes()
        except OSError as e:
            self._log(f"nao consegui salvar os ajustes ({e})", "#e0a030")
        if mudou_vel:
            self.velocidade_mudou.emit(proc.velocidade_mm_s)

    def disparar(self):
        if self.sessao is None:
            return
        n = self.sp_disparos.value()
        periodo = self.sp_periodo.value()
        self.sessao.disparar_parado(n, periodo, self.cb_padrao.currentData())
        self._log(f"disparar {n} vezes a cada {periodo} us: "
                  f"{self.cb_padrao.currentText()}")

    def _ler_check_ready(self):
        """Mostra o CHECK_READY que a maquina esta usando de fato."""
        try:
            v = self.sessao.obter_parametros([pv.Param.CHECK_READY])
        except Exception as e:
            self._log(f"nao consegui ler o CHECK_READY ({e})", "#e0a030")
            return
        self.ck_ready.blockSignals(True)
        self.ck_ready.setChecked(bool(v[int(pv.Param.CHECK_READY)]))
        self.ck_ready.blockSignals(False)

    def _alternar_ready(self, ligado: bool):
        if self.sessao is None:
            return
        # Vale ate o ESP reiniciar: de proposito, nao e salvo na NVS
        self.sessao.definir_parametro(pv.Param.CHECK_READY, int(ligado))
        self._log("conferencia do READY " + ("LIGADA" if ligado else
                  "DESLIGADA (so para teste sem cabecote)"),
                  "#8ab4f8" if ligado else "#e0a030")

    def limpar_falha(self):
        if self.sessao is None:
            return
        self.sessao.limpar_falha()
        self._log("limpar falha (os eixos voltam a exigir referenciamento)")

    def parar(self):
        """
        ESTOP: quadro prioritario, fora da janela (secao 4.1).

        Nao espera a fila esvaziar e e repetido ate o STATUS confirmar FAULT.
        Continua nao substituindo a parada de emergencia fisica.
        """
        if self.sessao is None:
            return
        self.sessao.parar()
        self._log("PARADA enviada", "#ff6b6b")

    def cancelar(self):
        if self.sessao is None:
            return
        self.sessao.cancelar()
        self._log("cancelamento enviado", "#e0a030")

    def cancelar_impressao(self, confirmar: bool = True):
        """
        Cancela a impressao e recolhe a maquina, sem a parada de emergencia.

        O CANCEL (secao 7.7) para os disparos, desliga o VPP, freia os eixos
        com rampa e volta a IDLE com os eixos ainda referenciados. Ao ver o
        IDLE, _recolher manda o Z subir, o carro a X = 0 e a mesa ao Y de
        apresentacao. O ESTOP, ao contrario, corta sem rampa, desliga os
        drivers e tira a referencia: e para emergencia, nao para desistir de
        uma impressao.
        """
        if self.sessao is None:
            return
        if confirmar:
            resp = QMessageBox.question(
                self, "Cancelar impressao",
                "Cancelar a impressao? O jato para, o cabecote desliga e a "
                "maquina recolhe: Z sobe, carro em X = 0 e mesa em "
                f"Y = {self.perfil.processo.apresentar_y_mm:g} mm.")
            if resp != QMessageBox.StandardButton.Yes:
                return
        self.sessao.cancelar()
        self._recolher_pendente = True
        self._log("impressao cancelada; recolhendo quando a maquina parar",
                  "#e0a030")

    def _recolher(self):
        """Chamado a cada bombeada: recolhe depois que o CANCEL terminou."""
        s = self.sessao.status if self.sessao else None
        if not getattr(self, "_recolher_pendente", False) or s is None:
            return
        # Espera o IDLE e o fim das repeticoes do CANCEL: uma repeticao
        # atrasada cancelaria os proprios movimentos de recolher
        if (s.state != int(pv.Estado.IDLE) or self.sessao.pendentes
                or self.sessao.repetindo_prioritario):
            return
        self._recolher_pendente = False
        refs = (s.referenciado_x, s.referenciado_y, s.referenciado_z)
        if not all(refs):
            self._log("eixos sem referencia: a maquina nao foi recolhida "
                      "(referencie antes de mover)", "#e0a030")
            return
        proc = self.perfil.processo
        z_atual = self.sessao.para_mm(pv.EIXO_Z, s.pos[pv.EIXO_Z])
        z = max(proc.recolher_z_mm, z_atual)
        ha = self.sessao.hello_ack
        y_max = (ha.travel[pv.EIXO_Y] / ha.passos_por_mm(pv.EIXO_Y) if ha
                 else proc.apresentar_y_mm)
        y = min(proc.apresentar_y_mm, y_max)
        # Z primeiro: o bico sai de cima da peca antes de qualquer outro eixo
        for eixo, alvo in ((pv.EIXO_Z, z), (pv.EIXO_X, 0.0), (pv.EIXO_Y, y)):
            self.sessao.mover_mm(eixo, alvo, self._velocidade(eixo))
        self._log(f"recolhendo: Z {z:g} mm, X 0, Y {y:g} mm")

    # ---------------------------------------------------------------------

    def _bombear(self):
        """
        Serve o protocolo e atualiza a tela.

        Roda no temporizador porque o transporte tem tempo proprio: janela,
        retransmissao por RTO, heartbeat e timeout de enlace. Os comandos dos
        botoes so enfileiram; quem transmite e esta funcao.
        """
        if self.sessao is None:
            return
        for aviso in self.sessao.bombear():
            self._tratar_aviso(aviso)
        self._mostrar_status()
        if self.sessao is not None:
            self._recolher()

    def _tratar_aviso(self, aviso):
        if aviso.tipo == Evento.ERRO:
            e = aviso.dado
            self._log(f"RECUSADO: {e}", "#ff6b6b")
        elif aviso.tipo == Evento.NACK:
            self._log(f"quadro perdido, retransmitindo a partir do seq "
                      f"{aviso.dado.expected_seq}", "#e0a030")
        elif aviso.tipo == Evento.LOG:
            self._log(f"maquina: {aviso.dado}", "#9aa0a6")
        elif aviso.tipo == Evento.PARAMETRO:
            p = aviso.dado
            self._log(f"parametro 0x{p.param_id:04X} = {p.value}")
        elif aviso.tipo == Evento.ENLACE_PERDIDO:
            self._log(f"ENLACE PERDIDO: {aviso.dado}", "#ff6b6b")
            self.desconectar(perdido=True)

    def _mostrar_status(self):
        s = self.sessao.status if self.sessao else None
        if s is None:
            return
        self.lb["estado"].setText(s.nome_estado)
        self.estado_maquina.emit(int(s.state))
        for chave, eixo in (("x", pv.EIXO_X), ("y", pv.EIXO_Y),
                            ("z", pv.EIXO_Z)):
            try:
                mm = self.sessao.para_mm(eixo, s.pos[eixo])
                self.lb[chave].setText(f"{mm:.3f}")
            except RuntimeError:
                self.lb[chave].setText(f"{s.pos[eixo]} passos")
        refs = "".join(
            e.upper() if r else "-" for e, r in
            zip("xyz", (s.referenciado_x, s.referenciado_y, s.referenciado_z)))
        self.lb["cabecote"].setText(
            ("ligado" if s.vpp_ligado else "desligado")
            + f"   |   referenciados: {refs}")
        self.lb["buffer"].setText(
            f"{s.swath_free} slots de swath, {s.queue_free} de fila"
            + (f"   |   {self.sessao.pendentes} pendentes no PC"
               if self.sessao.pendentes else ""))

        partes = []
        if s.last_error:
            partes.append(pv.nome_do_erro(s.last_error))
        if s.fault:
            partes.append(f"falha: {pv.nome_da_falha(s.fault)}")
        if s.frames_dropped:
            partes.append(f"{s.frames_dropped} quadros descartados")
        self.lb["erro"].setText("; ".join(partes) if partes else "nenhum")
        self.lb["erro"].setStyleSheet(
            "font-family:Consolas,monospace;"
            + (f"color:{tema.PERIGO};" if partes else ""))

    def closeEvent(self, ev):
        self.desconectar()
        super().closeEvent(ev)
