"""
Tema escuro do PAJM Controle, na linha do Mainsail.

Fundo quase preto, cartoes um tom acima, um azul de destaque e o vermelho
reservado para PARAR e erros. Os grupos (QGroupBox) viram cartoes com
cabecalho: os paineis continuam usando QGroupBox, e o visual vem daqui.
"""
from __future__ import annotations

FUNDO = "#121212"
CARTAO = "#1e1e1e"
CABECALHO = "#262626"
BORDA = "#2e2e2e"
TEXTO = "#e3e3e3"
TEXTO_FRACO = "#9aa0a6"
DESTAQUE = "#2196f3"
PERIGO = "#d32f2f"
OK = "#4caf50"
ALERTA = "#e0a030"

QSS = f"""
QWidget {{
    background: {FUNDO};
    color: {TEXTO};
    font-size: 13px;
}}
QToolTip {{
    background: {CABECALHO}; color: {TEXTO}; border: 1px solid {BORDA};
    padding: 4px;
}}

/* ---- cartoes ---------------------------------------------------------- */
QGroupBox {{
    background: {CARTAO};
    border: 1px solid {BORDA};
    border-radius: 6px;
    margin-top: 26px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0px;
    padding: 4px 12px;
    background: {CABECALHO};
    border: 1px solid {BORDA};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    color: {TEXTO};
    text-transform: uppercase;
}}
/* Rotulos e caixas de marcar herdam o fundo de onde estao (cartao, barra).
   Uma regra "QGroupBox QWidget" pintaria tambem botoes e campos, porque
   ganha em especificidade da regra de QPushButton. */
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}
QGroupBox QLabel {{ font-weight: normal; }}

/* ---- campos ----------------------------------------------------------- */
QLineEdit, QAbstractSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background: #2a2a2a;
    border: 1px solid #3a3a3a;
    border-radius: 4px;
    padding: 3px 6px;
    selection-background-color: {DESTAQUE};
}}
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus {{
    border: 1px solid {DESTAQUE};
}}
QAbstractSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {{
    color: #6b6b6b;
}}
QComboBox QAbstractItemView {{
    background: #2a2a2a; selection-background-color: {DESTAQUE};
}}

QPushButton {{
    background: #2f2f2f;
    border: 1px solid #3d3d3d;
    border-radius: 4px;
    padding: 5px 12px;
}}
QPushButton:hover {{ background: #3a3a3a; }}
QPushButton:pressed {{ background: {DESTAQUE}; }}
QPushButton:disabled {{ color: #6b6b6b; background: #262626; }}
QPushButton[papel="primario"] {{
    background: {DESTAQUE}; border: 1px solid {DESTAQUE}; color: white;
    font-weight: 600;
}}
QPushButton[papel="primario"]:hover {{ background: #42a5f5; }}
QPushButton[papel="primario"]:disabled {{
    background: #1d3b55; border-color: #1d3b55; color: #8aa0b4;
}}

QCheckBox {{ spacing: 6px; }}
QProgressBar {{
    background: #2a2a2a; border: none; border-radius: 3px; height: 8px;
    text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {DESTAQUE}; border-radius: 3px; }}
QSlider::groove:horizontal {{ background: #3a3a3a; height: 4px; }}
QSlider::handle:horizontal {{
    background: {DESTAQUE}; width: 14px; margin: -6px 0; border-radius: 7px;
}}
QTabWidget::pane {{ border: 1px solid {BORDA}; border-radius: 4px; }}
QTabBar::tab {{
    background: {CARTAO}; padding: 6px 12px; border: 1px solid {BORDA};
    border-bottom: none;
}}
QTabBar::tab:selected {{ background: {CABECALHO}; color: {DESTAQUE}; }}
QHeaderView::section {{
    background: {CABECALHO}; border: none; padding: 4px;
}}
QTableWidget {{ background: {CARTAO}; gridline-color: {BORDA}; }}
QScrollArea {{ border: none; }}
QSplitter::handle {{ background: {BORDA}; }}

/* ---- barra superior e navegacao -------------------------------------- */
#barraTopo {{
    background: {CARTAO};
    border-bottom: 1px solid {BORDA};
}}
#nomePrograma {{ font-size: 16px; font-weight: 700; }}
#navegacao {{
    background: {CARTAO};
    border: none;
    border-right: 1px solid {BORDA};
    outline: 0;
    padding-top: 6px;
}}
#navegacao::item {{
    padding: 10px 14px;
    border-left: 3px solid transparent;
}}
#navegacao::item:hover {{ background: #262626; }}
#navegacao::item:selected {{
    background: #1b2a38;
    border-left: 3px solid {DESTAQUE};
    color: {TEXTO};
}}
#tituloPagina {{ font-size: 18px; font-weight: 600; padding: 4px 2px; }}
"""


def nota(texto: str):
    """Texto secundario: menor, cinza e com quebra de linha."""
    from PySide6.QtWidgets import QLabel
    lab = QLabel(texto)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color:{TEXTO_FRACO}; font-weight:normal;")
    return lab


def primario(botao):
    """Marca o botao de acao principal de um cartao (azul)."""
    botao.setProperty("papel", "primario")
    return botao
