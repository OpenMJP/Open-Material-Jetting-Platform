"""Pecinhas de interface usadas por mais de um painel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QLineEdit, QScrollArea, QWidget,
)


def garantir_alturas(raiz: QWidget, folga: int = 2) -> int:
    """
    Garante que todo campo de entrada tenha pelo menos a altura que pede.

    Uma linha de formulario que contem um layout aninhado nem sempre repassa a
    altura minima dos filhos, e o campo acaba um ou dois pixels mais baixo que
    o necessario: o texto encosta na borda e as setas do spin ficam cortadas.
    Fixar a altura minima no proprio widget resolve de uma vez e vale para
    qualquer campo que venha a ser acrescentado depois.

    Devolve quantos widgets foram ajustados.
    """
    n = 0
    for tipo in (QAbstractSpinBox, QComboBox, QLineEdit):
        for w in raiz.findChildren(tipo):
            alvo = w.minimumSizeHint().height() + folga
            if w.minimumHeight() < alvo:
                w.setMinimumHeight(alvo)
                n += 1
    return n


def em_rolagem(conteudo: QWidget, largura_min: int = 360) -> QScrollArea:
    """
    Poe um painel dentro de uma area rolavel.

    POR QUE ISTO EXISTE
    Um QVBoxLayout sem rolagem, quando o conteudo e mais alto que a janela,
    nao corta nem rola: ele ESMAGA os filhos abaixo do tamanho minimo deles.
    O resultado sao rotulos cortados pela metade e campos encavalados, que foi
    exatamente o que aconteceu com a coluna de controles do fatiamento depois
    que ela cresceu.

    Com a area de rolagem, o conteudo mantem o tamanho natural e sobra barra
    de rolagem quando nao couber, que e o comportamento esperado.
    """
    area = QScrollArea()
    area.setWidget(conteudo)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setMinimumWidth(largura_min)
    conteudo.setMinimumWidth(largura_min - 20)
    return area


# --------------------------------------------------------------------------
# Dialogos de arquivo
# --------------------------------------------------------------------------
# O dialogo NATIVO do Windows roda o proprio laco de mensagens, e enquanto o
# usuario navega (pastas do OneDrive principalmente) o programa pode passar
# mais de 1 s sem servir o protocolo: o ESP encerra a sessao por
# LINK_TIMEOUT e desliga o cabecote e os motores. O dialogo do Qt roda
# dentro do laco do programa, e o temporizador do protocolo continua.

def abrir_arquivo(parent, titulo: str, pasta: str = "",
                  filtro: str = "") -> tuple[str, str]:
    from PySide6.QtWidgets import QFileDialog
    return QFileDialog.getOpenFileName(
        parent, titulo, pasta, filtro,
        options=QFileDialog.Option.DontUseNativeDialog)


def salvar_arquivo(parent, titulo: str, pasta: str = "",
                   filtro: str = "") -> tuple[str, str]:
    from PySide6.QtWidgets import QFileDialog
    return QFileDialog.getSaveFileName(
        parent, titulo, pasta, filtro,
        options=QFileDialog.Option.DontUseNativeDialog)


def coluna_com_rodape(conteudo: QWidget, rodape: QWidget,
                      largura_min: int = 360) -> QWidget:
    """
    Coluna de controles rolavel com um rodape FIXO embaixo.

    O rodape leva a acao principal da pagina (fatiar, enviar): numa coluna
    longa ela ficava depois de varios cartoes e so aparecia rolando. Assim
    rolam os cartoes e o botao fica sempre a vista.
    """
    from PySide6.QtWidgets import QVBoxLayout
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    lay.addWidget(em_rolagem(conteudo, largura_min=largura_min), 1)
    lay.addWidget(rodape)
    return w
