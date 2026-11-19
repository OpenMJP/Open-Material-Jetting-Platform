"""
Previa de uma camada fatiada.

O pixel do bitmap NAO e quadrado (dpi_x != dpi_y). O widget desenha na
proporcao correta em milimetros, senao a peca aparece esticada na tela e o
usuario ajusta o modelo para corrigir uma distorcao que so existe na previa.

ORIENTACAO: Y cresce para CIMA, como num grafico XY e na vista de topo de um
CAD. A linha 0 do bitmap (Y menor, lado do endstop e do bico 1) fica
embaixo. E como o impresso e visto de frente para a maquina: com a linha 0
no alto, a previa mostrava o espelho do que saia na mesa.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget


class PreviaCamada(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._img: QImage | None = None
        self._grade = None
        self._info = "Nenhuma camada fatiada."
        self.setMinimumSize(320, 260)

    def limpar(self, texto: str = "Nenhuma camada fatiada.") -> None:
        self._img = None
        self._grade = None
        self._info = texto
        self.update()

    @staticmethod
    def imagem_do_bitmap(bitmap: np.ndarray, invertido: bool = True):
        """
        Bitmap -> QImage em preto no branco, para salvar em arquivo.

        `invertido` desenha gota em preto sobre fundo branco, que e o que serve
        para imprimir a referencia em papel e comparar com o jateado.
        """
        # Mesma orientacao da previa (Y para cima), para a referencia salva
        # bater com o impresso escaneado
        buf = np.ascontiguousarray(bitmap[::-1].astype(np.uint8))
        h, w = buf.shape
        img = QImage(buf.data, w, h, w, QImage.Format.Format_Indexed8)
        if invertido:
            img.setColorTable([QColor(255, 255, 255).rgb(),
                               QColor(0, 0, 0).rgb()])
        else:
            img.setColorTable([QColor(28, 30, 34).rgb(),
                               QColor(90, 200, 255).rgb()])
        return img.copy()

    def mostrar(self, bitmap: np.ndarray, grade, info: str = "") -> None:
        # 0 -> fundo, 1 -> gota. QImage de 8 bits com paleta de 2 cores.
        # Linhas invertidas: Y para cima (ver o cabecalho do modulo).
        buf = np.ascontiguousarray(bitmap[::-1].astype(np.uint8))
        h, w = buf.shape
        img = QImage(buf.data, w, h, w, QImage.Format.Format_Indexed8)
        img.setColorTable([QColor(28, 30, 34).rgb(), QColor(90, 200, 255).rgb()])
        self._img = img.copy()          # copia: buf sai de escopo
        self._grade = grade
        self._info = info
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 22, 25))

        if self._img is None:
            p.setPen(QColor(150, 150, 155))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._info)
            return

        margem = 10
        disp = self.rect().adjusted(margem, margem, -margem, -margem - 18)

        # Proporcao em MILIMETROS, nao em pixels do bitmap.
        larg_mm = self._grade.largura_mm
        alt_mm = self._grade.altura_mm
        if larg_mm <= 0 or alt_mm <= 0:
            return
        escala = min(disp.width() / larg_mm, disp.height() / alt_mm)
        w = larg_mm * escala
        h = alt_mm * escala
        alvo = QRectF(disp.x() + (disp.width() - w) / 2,
                      disp.y() + (disp.height() - h) / 2, w, h)

        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(alvo, self._img)

        p.setPen(QPen(QColor(70, 75, 82), 1))
        p.drawRect(alvo)

        p.setPen(QColor(170, 175, 182))
        p.drawText(self.rect().adjusted(margem, 0, -margem, -4),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                   self._info)
