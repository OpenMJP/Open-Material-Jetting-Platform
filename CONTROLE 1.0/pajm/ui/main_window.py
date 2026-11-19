"""
Janela principal do PAJM Controle, o programa de controle da PAJM.

Organizada como o Mainsail: barra superior fixa com a conexao, o estado da
maquina e a parada de emergencia; barra lateral com as paginas; cada pagina
em cartoes. O fatiador e a impressao de imagem sao secoes do programa de
controle, ao lado do comando manual (Painel), da importacao de pacotes de um
CAM externo (Arquivos), da calibracao e da configuracao da maquina.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

from ..core.codificador import autoteste
from ..machine import protocolo as pv
from ..profile import Perfil
from . import tema
from .comum import em_rolagem
from .painel_arquivos import PainelArquivos
from .painel_fatiar import PainelFatiar
from .painel_imagem import PainelImagem
from .painel_manual import PainelManual
from .painel_padroes import PainelPadroes
from .painel_pendencias import PainelPendencias

NOME = "PAJM Controle"


class BarraParada(QWidget):
    """
    Indicador da conexao e parada de emergencia, na barra superior:
    visiveis em todas as paginas.
    """
    _ESTADOS = {
        "desconectado": (tema.ALERTA, "Desconectado"),
        "conectado": (tema.OK, "Conectado"),
        "simulado": (tema.OK, "Conectado (simulador)"),
        "perdido": ("#ff6b6b", "Conexao perdida"),
    }

    def __init__(self, parar, cancelar, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(10)
        self.lb_estado = QLabel()
        lay.addWidget(self.lb_estado)
        # Cancelar e o jeito normal de interromper: para com rampa e recolhe.
        # O PARAR fica para emergencia.
        self.bt_cancelar = QPushButton("Cancelar impressao")
        self.bt_cancelar.setStyleSheet(
            f"QPushButton {{ background:#5c4313; color:{tema.ALERTA};"
            f" font-weight:bold; border:1px solid {tema.ALERTA};"
            " border-radius:4px; padding:5px 12px; }"
            "QPushButton:hover { background:#73541a; }"
            "QPushButton:disabled { background:#2f2f2f; color:#6b6b6b;"
            " border-color:#3d3d3d; }")
        self.bt_cancelar.setToolTip(
            "Para a impressao com rampa, desliga o cabecote e recolhe a "
            "maquina: Z sobe, carro em X = 0 e mesa no Y de apresentacao. Os "
            "eixos continuam referenciados. Ajustes na pagina Maquina.")
        self.bt_cancelar.clicked.connect(lambda: cancelar())
        lay.addWidget(self.bt_cancelar)
        self.bt_parar = QPushButton("PARAR")
        self.bt_parar.setMinimumWidth(110)
        self.bt_parar.setStyleSheet(
            f"QPushButton {{ background:{tema.PERIGO}; color:white;"
            " font-size:14px; font-weight:bold; border-radius:4px;"
            " padding:5px 14px; border:none; }"
            "QPushButton:hover { background:#e53935; }"
            "QPushButton:disabled { background:#5a3330; color:#b0a0a0; }")
        self.bt_parar.setToolTip(
            "Parada imediata por software (ESTOP), fora da fila de comandos: "
            "corta o jato e os motores. NAO substitui uma parada de emergencia "
            "fisica em serie com a alimentacao: se o programa travar ou o "
            "cabo cair, ela nao age.")
        self.bt_parar.clicked.connect(parar)
        lay.addWidget(self.bt_parar)
        self.mostrar("desconectado")

    def mostrar(self, estado: str):
        cor, texto = self._ESTADOS.get(estado, self._ESTADOS["desconectado"])
        self.estado = estado
        self.lb_estado.setText(
            f"<span style='color:{cor}; font-size:16px'>&#9679;</span> {texto}")
        ligado = estado in ("conectado", "simulado")
        self.bt_parar.setEnabled(ligado)
        self.bt_cancelar.setEnabled(ligado)


class ChipEstado(QLabel):
    """Estado da maquina na barra superior, como o do Mainsail."""
    _TEXTO = {
        int(pv.Estado.IDLE): ("Pronta", tema.OK),
        int(pv.Estado.JOB): ("Imprimindo", tema.DESTAQUE),
        int(pv.Estado.STOPPING): ("Parando", tema.ALERTA),
        int(pv.Estado.FAULT): ("Falha", tema.PERIGO),
    }

    def __init__(self):
        super().__init__()
        self.mostrar(-1)

    def mostrar(self, estado: int):
        texto, cor = self._TEXTO.get(estado, ("Sem maquina", "#555555"))
        if getattr(self, "_atual", None) == estado:
            return
        self._atual = estado
        self.texto = texto
        self.setText(texto)
        self.setStyleSheet(
            f"background:{cor}; color:white; border-radius:10px;"
            " padding:2px 12px; font-weight:600;")


class JanelaPrincipal(QMainWindow):
    # (chave, rotulo na barra lateral, titulo da pagina)
    PAGINAS = (
        ("painel", "◉  Painel", "Painel"),
        ("fatiador", "≡  Fatiador", "Fatiador (STL para camadas)"),
        ("imagem", "▨  Impressao de imagem", "Impressao de imagem"),
        ("arquivos", "⇪  Arquivos (.pajm)",
         "Arquivos: pacotes de camadas de outro CAM"),
        ("calibracao", "◎  Calibracao", "Calibracao e padroes de teste"),
        ("maquina", "⚙  Maquina", "Configuracao da maquina"),
    )

    def __init__(self, perfil: Perfil):
        super().__init__()
        self.perfil = perfil
        self.setWindowTitle(NOME)
        # Na aplicacao, e antes de criar os paineis: cada painel mede a
        # altura minima dos seus campos (garantir_alturas) ao ser criado, e
        # tem de medir ja com o tema. Vale tambem para as caixas de dialogo.
        QApplication.instance().setStyleSheet(tema.QSS)
        self.resize(1440, 920)
        # Abaixo disto as colunas de controles comecam a rolar em vez
        # de espremer os campos.
        self.setMinimumSize(1100, 700)

        # O Painel e dono da conexao; as outras paginas so perguntam por ela
        # na hora de enviar, para nao existirem duas conexoes.
        self.aba_manual = PainelManual(perfil)
        sessao = lambda: self.aba_manual.sessao       # noqa: E731
        self.aba_fatiar = PainelFatiar(perfil, obter_maquina=sessao)
        self.aba_imagem = PainelImagem(perfil, obter_maquina=sessao)
        self.aba_arquivos = PainelArquivos(perfil, obter_maquina=sessao)
        self.aba_padroes = PainelPadroes(
            perfil, obter_maquina=sessao, perfil_mudou=self._calibracao_mudou)
        self.aba_pend = PainelPendencias(perfil, mudou=self._perfil_mudou)

        central = QWidget()
        self.setCentralWidget(central)
        raiz = QVBoxLayout(central)
        raiz.setContentsMargins(0, 0, 0, 0)
        raiz.setSpacing(0)
        raiz.addWidget(self._barra_topo())

        corpo = QHBoxLayout()
        corpo.setContentsMargins(0, 0, 0, 0)
        corpo.setSpacing(0)
        raiz.addLayout(corpo, 1)

        self.nav = QListWidget()
        self.nav.setObjectName("navegacao")
        self.nav.setFixedWidth(220)
        self.paginas = QStackedWidget()
        widgets = {
            "painel": self.aba_manual, "fatiador": self.aba_fatiar,
            "imagem": self.aba_imagem, "arquivos": self.aba_arquivos,
            "calibracao": self.aba_padroes, "maquina": self._pagina_maquina(),
        }
        self._indice = {}
        for chave, rotulo, titulo in self.PAGINAS:
            self._indice[chave] = self.paginas.count()
            self.paginas.addWidget(self._com_titulo(titulo, widgets[chave]))
            QListWidgetItem(rotulo, self.nav)
        self.nav.currentRowChanged.connect(self.paginas.setCurrentIndex)
        self.nav.setCurrentRow(0)
        corpo.addWidget(self.nav)
        corpo.addWidget(self.paginas, 1)

        # conectar ou desconectar muda o que o envio pode fazer
        def _conexao_mudou(_):
            for aba in (self.aba_fatiar, self.aba_imagem, self.aba_padroes,
                        self.aba_arquivos):
                aba._cache_params = None
                aba._atualizar_envio()

        self.aba_manual.conectou.connect(_conexao_mudou)
        self.aba_manual.estado_conexao.connect(self.barra_parada.mostrar)
        self.aba_manual.estado_maquina.connect(self.chip_estado.mostrar)
        self.aba_manual.velocidade_mudou.connect(self._velocidade_mudou)

        self.statusBar().showMessage(self._status())

    # ---- montagem ----------------------------------------------------------

    def _barra_topo(self) -> QWidget:
        barra = QWidget()
        barra.setObjectName("barraTopo")
        lay = QHBoxLayout(barra)
        lay.setContentsMargins(14, 8, 12, 8)
        lay.setSpacing(10)
        nome = QLabel(NOME)
        nome.setObjectName("nomePrograma")
        lay.addWidget(nome)
        self.chip_estado = ChipEstado()
        lay.addWidget(self.chip_estado)
        lay.addStretch(1)
        lay.addWidget(QLabel("Porta"))
        for w in self.aba_manual.widgets_conexao:
            lay.addWidget(w)
        self.barra_parada = BarraParada(self.aba_manual.parar,
                                        self.aba_manual.cancelar_impressao)
        lay.addWidget(self.barra_parada)
        return barra

    @staticmethod
    def _com_titulo(titulo: str, widget: QWidget) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 10, 14, 6)
        rot = QLabel(titulo)
        rot.setObjectName("tituloPagina")
        lay.addWidget(rot)
        lay.addWidget(widget, 1)
        return w

    def _pagina_maquina(self) -> QWidget:
        """Perfil e pendencias, mais os ajustes de operacao do Painel."""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.aba_pend, 3)
        lateral = QWidget()
        col = QVBoxLayout(lateral)
        col.addWidget(self.aba_pend.g_pendencias, 1)
        col.addWidget(self.aba_manual.g_impressao)
        col.addWidget(self.aba_manual.g_diagnostico)
        lay.addWidget(em_rolagem(lateral, largura_min=440), 2)
        return w

    def ir_para(self, chave: str) -> None:
        self.nav.setCurrentRow(self._indice[chave])

    def pagina(self, chave: str) -> QWidget:
        return self.paginas.widget(self._indice[chave])

    # ---------------------------------------------------------------------

    def _perfil_mudou(self):
        self.aba_fatiar._recalcular()
        self.aba_fatiar.vista3d.definir_plataforma(
            self.perfil.movimento.curso_x_mm, self.perfil.movimento.curso_y_mm)
        self.statusBar().showMessage(self._status())

    def _velocidade_mudou(self, _):
        """
        A velocidade de impressao vale para todas as paginas. No fatiador ela
        entra na compensacao (fase de disparo), entao ele recalcula; nas
        outras muda o planejamento do envio.
        """
        self.aba_fatiar._recalcular()
        self.aba_arquivos.mostrar_velocidade()
        for aba in (self.aba_imagem, self.aba_padroes, self.aba_arquivos):
            aba._atualizar_envio()

    def closeEvent(self, ev):
        # Fechar o programa conectado desliga o cabecote e os motores
        if self.aba_manual.sessao is not None:
            self.aba_manual.desconectar()
        super().closeEvent(ev)

    def _calibracao_mudou(self):
        """A calibracao gravou um valor do perfil (o angulo)."""
        self.aba_pend._carregar_da_memoria()
        self.aba_pend.atualizar()
        self._perfil_mudou()

    def _status(self) -> str:
        c = self.perfil.cabecote
        pend = len(self.perfil.pendencias())
        dpi_y = c.dpi_y_nativo
        parte = (f"{c.modelo} {c.variante or '(variante indefinida)'}"
                 + (f" | DPI Y {dpi_y:.0f}" if dpi_y else "")
                 + (f" | faixa {c.largura_faixa_mm:.1f} mm"
                    if c.largura_faixa_mm else ""))
        gl = "OpenGL" if self.aba_fatiar.vista3d.usando_opengl else "sem OpenGL"
        return f"{parte}  |  {pend} pendencia(s)  |  {gl}"

    def verificar_integridade(self):
        """Autoteste do empacotamento, na abertura."""
        falhas = autoteste()
        if falhas:
            QMessageBox.critical(
                self, "Falha no autoteste",
                "O empacotamento do vetor de jateamento nao confere com a "
                "tabela 6.11 do manual do cabecote:\n\n- "
                + "\n- ".join(falhas)
                + "\n\nNao use este build para imprimir.")
