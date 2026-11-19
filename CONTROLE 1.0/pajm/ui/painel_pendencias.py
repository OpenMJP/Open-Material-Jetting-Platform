"""
Pendencias e perfil da maquina (pagina Maquina).

A lista do que ainda nao foi definido nao e um documento a parte que envelhece:
e uma tela do programa, calculada do perfil. Cada linha diz o que falta, onde
obter e o que aquilo bloqueia. Os campos editaveis sao preenchidos conforme as
medidas forem sendo feitas na maquina.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from . import tema
from .comum import (abrir_arquivo, em_rolagem, garantir_alturas,
                    salvar_arquivo)
from ..profile import MESAS, VARIANTES, Perfil


class _OpcionalSpin(QDoubleSpinBox):
    """Spin que representa None como o valor minimo, mostrando 'nao definido'."""

    def __init__(self, minimo, maximo, decimais=2, sufixo=""):
        super().__init__()
        self.setRange(minimo - 1, maximo)
        self.setDecimals(decimais)
        self.setSpecialValueText("nao definido")
        self.setSuffix(sufixo)
        self.setValue(minimo - 1)
        self._vazio = minimo - 1

    def valor(self):
        v = self.value()
        return None if v == self._vazio else v

    def definir(self, v):
        self.setValue(self._vazio if v is None else v)


class PainelPendencias(QWidget):
    def __init__(self, perfil: Perfil, mudou=None, parent=None):
        super().__init__(parent)
        self.perfil = perfil
        self._mudou = mudou

        raiz = QVBoxLayout(self)

        raiz.addWidget(tema.nota(
            "Perfil da maquina. Nada aqui e chutado: enquanto um campo estiver "
            "vazio, o que depende dele fica bloqueado ou usa um valor de "
            "projeto declarado. Conectada, a maquina informa os passos por mm "
            "e o curso."))

        corpo = QHBoxLayout()
        raiz.addLayout(corpo, 1)

        # ---- editaveis ---------------------------------------------------
        esq = QWidget()
        lay = QVBoxLayout(esq)

        g_cab = QGroupBox("Cabecote")
        f = QFormLayout(g_cab)
        self.cb_variante = QComboBox()
        self.cb_variante.addItem("nao definido", None)
        for nome, d in VARIANTES.items():
            self.cb_variante.addItem(f"{nome}  ({d['cor']})", nome)
        self.cb_variante.currentIndexChanged.connect(self._aplicar)
        f.addRow("Variante", self.cb_variante)

        self.sp_ang = _OpcionalSpin(0.0, 90.0, 2, " graus")
        self.sp_ang.valueChanged.connect(self._aplicar)
        f.addRow("Angulo de montagem real", self.sp_ang)

        self.cb_sentido = QComboBox()
        self.cb_sentido.addItem("nao definido", None)
        self.cb_sentido.addItem("canal 1 no menor Y  (+1)", 1)
        self.cb_sentido.addItem("canal 1 no maior Y  (-1)", -1)
        self.cb_sentido.currentIndexChanged.connect(self._aplicar)
        f.addRow("Sentido do indice em Y", self.cb_sentido)
        lay.addWidget(g_cab)

        g_mesa = QGroupBox("Mesa de impressao")
        fmesa = QFormLayout(g_mesa)
        self.cb_mesa = QComboBox()
        self.cb_mesa.addItem("personalizado", None)
        for nome, (x, y) in MESAS.items():
            self.cb_mesa.addItem(f"{nome}  ({x:.0f} x {y:.0f} mm)", nome)
        self.cb_mesa.currentIndexChanged.connect(self._mesa_escolhida)
        fmesa.addRow("Modelo", self.cb_mesa)
        fmesa.addRow(tema.nota(
            "Os presets sao tamanhos de catalogo de cada modelo, nao medidas "
            "desta maquina. Confira com uma regua e ajuste abaixo se "
            "precisar."))
        # Onde fica o (0, 0) da mesa no curso da maquina: antes disso e zona
        # morta, so para o eixo acelerar. Medido, nao vem do protocolo, entao
        # fica salvo nos ajustes, como a calibracao.
        self.sp_origem = {}
        for chave, rotulo in (("origem_mesa_x_mm", "Origem da mesa em X"),
                              ("origem_mesa_y_mm", "Origem da mesa em Y")):
            s = QDoubleSpinBox()
            s.setRange(0.0, 500.0)
            s.setDecimals(2)
            s.setSuffix(" mm")
            s.setToolTip(
                "Distancia do fim de curso da maquina ao (0, 0) da mesa. As "
                "coordenadas do fatiador e das imagens sao da mesa; o envio "
                "soma este valor. A area util e o curso menos a origem, "
                "recalculada ao conectar.")
            s.valueChanged.connect(self._origem_mudou)
            self.sp_origem[chave] = s
            fmesa.addRow(rotulo, s)
        lay.addWidget(g_mesa)

        g_mov = QGroupBox("Movimento")
        fm = QFormLayout(g_mov)
        self.sp_mov = {}
        for chave, rotulo, mx, dec, suf in (
            ("passos_por_mm_x", "Passos por mm em X", 100000, 3, ""),
            ("passos_por_mm_y", "Passos por mm em Y", 100000, 3, ""),
            ("passos_por_mm_z", "Passos por mm em Z", 100000, 3, ""),
            ("velocidade_max_mm_s", "Velocidade maxima", 5000, 1, " mm/s"),
            ("aceleracao_mm_s2", "Aceleracao", 100000, 1, " mm/s2"),
            ("curso_x_mm", "Curso em X", 2000, 1, " mm"),
            ("curso_y_mm", "Curso em Y", 2000, 1, " mm"),
            ("curso_z_mm", "Curso em Z", 2000, 1, " mm"),
        ):
            s = _OpcionalSpin(0.0, mx, dec, suf)
            s.valueChanged.connect(self._aplicar)
            self.sp_mov[chave] = s
            fm.addRow(rotulo, s)
        lay.addWidget(g_mov)

        botoes = QHBoxLayout()
        bt_salvar = QPushButton("Salvar perfil...")
        bt_salvar.clicked.connect(self.salvar)
        bt_abrir = QPushButton("Carregar perfil...")
        bt_abrir.clicked.connect(self.carregar)
        botoes.addWidget(bt_salvar)
        botoes.addWidget(bt_abrir)
        botoes.addStretch(1)
        lay.addLayout(botoes)
        lay.addStretch(1)
        corpo.addWidget(em_rolagem(esq, largura_min=420), 1)

        # ---- lista -------------------------------------------------------
        # Cartao proprio: na pagina Maquina ele vai para a coluna lateral
        self.g_pendencias = QGroupBox("Pendencias e avisos")
        ld = QVBoxLayout(self.g_pendencias)
        self.tabela = QTableWidget(0, 3)
        self.tabela.setHorizontalHeaderLabels(
            ["O que falta", "Onde obter", "O que bloqueia"])
        self.tabela.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.tabela.setWordWrap(True)
        self.tabela.verticalHeader().setVisible(False)
        self.tabela.setMinimumHeight(110)
        ld.addWidget(self.tabela, 2)
        self.tx_avisos = QTextEdit()
        self.tx_avisos.setReadOnly(True)
        self.tx_avisos.setMinimumHeight(110)
        ld.addWidget(self.tx_avisos, 1)
        corpo.addWidget(self.g_pendencias, 1)

        garantir_alturas(self)
        self._carregar_da_memoria()
        self.atualizar()

    # ---------------------------------------------------------------------

    def _mesa_escolhida(self):
        """Preset da mesa preenche curso X e Y; o valor medido continua
        podendo ser ajustado a mao depois."""
        if getattr(self, "_silencio", False):
            return
        nome = self.cb_mesa.currentData()
        if nome and nome in MESAS:
            x, y = MESAS[nome]
            self._silencio = True
            self.sp_mov["curso_x_mm"].definir(x)
            self.sp_mov["curso_y_mm"].definir(y)
            self._silencio = False
        self._aplicar()

    def _carregar_da_memoria(self):
        c, m = self.perfil.cabecote, self.perfil.movimento
        self._silencio = True
        i = self.cb_variante.findData(c.variante)
        self.cb_variante.setCurrentIndex(max(i, 0))
        self.sp_ang.definir(c.angulo_montagem_deg)
        j = self.cb_sentido.findData(c.sentido_indice)
        self.cb_sentido.setCurrentIndex(max(j, 0))
        for chave, s in self.sp_mov.items():
            s.definir(getattr(m, chave))
        i = self.cb_mesa.findData(getattr(m, "mesa", None))
        self.cb_mesa.setCurrentIndex(max(i, 0))
        for chave, s in self.sp_origem.items():
            s.setValue(getattr(m, chave))
        self._silencio = False

    def _origem_mudou(self):
        if getattr(self, "_silencio", False):
            return
        for chave, s in self.sp_origem.items():
            setattr(self.perfil.movimento, chave, s.value())
        try:
            self.perfil.salvar_ajustes()
        except OSError:
            pass
        if self._mudou:
            self._mudou()

    def _aplicar(self):
        if getattr(self, "_silencio", False):
            return
        c, m = self.perfil.cabecote, self.perfil.movimento
        c.variante = self.cb_variante.currentData()
        c.angulo_montagem_deg = self.sp_ang.valor()
        c.sentido_indice = self.cb_sentido.currentData()
        for chave, s in self.sp_mov.items():
            setattr(m, chave, s.valor())
        m.mesa = self.cb_mesa.currentData()
        self.atualizar()
        if self._mudou:
            self._mudou()

    def atualizar(self):
        pend = self.perfil.pendencias()
        self.tabela.setRowCount(len(pend))
        for r, p in enumerate(pend):
            for col, txt in enumerate(
                    (p.o_que, p.fonte, ", ".join(p.bloqueia))):
                it = QTableWidgetItem(txt)
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tabela.setItem(r, col, it)
        self.tabela.resizeRowsToContents()

        partes = []
        ok, faltando = self.perfil.pronto_para_fatiar()
        partes.append(
            "<b style='color:#4caf50'>O fatiamento esta liberado.</b>" if ok
            else "<b style='color:#ff6b6b'>O fatiamento esta bloqueado por: "
                 + "; ".join(faltando) + "</b>")
        for a in self.perfil.avisos():
            partes.append(f"<span style='color:#e0a030'>{a}</span>")
        self.tx_avisos.setHtml("<br><br>".join(partes))

    # ---------------------------------------------------------------------

    def salvar(self):
        caminho, _ = salvar_arquivo(
            self, "Salvar perfil", "perfil_pajm.json", "JSON (*.json)")
        if caminho:
            self.perfil.salvar(caminho)

    def carregar(self):
        from PySide6.QtWidgets import QMessageBox
        caminho, _ = abrir_arquivo(
            self, "Carregar perfil", "", "JSON (*.json)")
        if not caminho:
            return
        try:
            novo = Perfil.carregar(caminho)
        except Exception as e:
            QMessageBox.critical(self, "Erro", f"Nao consegui ler o perfil:\n{e}")
            return
        self.perfil.cabecote = novo.cabecote
        self.perfil.movimento = novo.movimento
        self.perfil.processo = novo.processo
        self._carregar_da_memoria()
        self.atualizar()
        if self._mudou:
            self._mudou()
