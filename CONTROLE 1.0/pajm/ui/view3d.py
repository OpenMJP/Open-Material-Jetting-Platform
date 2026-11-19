"""
Vista 3D da plataforma com o modelo.

O PC pode ter uma GPU integrada antiga, sem o OpenGL que a vista precisa.
Se o OpenGL nao subir, o widget cai para uma vista 2D de topo em vez de
derrubar o programa -- perder a vista 3D e aceitavel, nao abrir nao e.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QVector3D
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

OPENGL_OK = True
_ERRO_GL = ""
try:
    import pyqtgraph.opengl as gl
except Exception as e:                                  # pragma: no cover
    OPENGL_OK = False
    _ERRO_GL = str(e)


class _VistaTopo(QWidget):
    """Alternativa sem OpenGL: contorno do modelo visto de cima."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pontos = None
        self._plataforma = None
        self.setMinimumSize(320, 260)

    def definir(self, vertices, plataforma):
        self._pontos = vertices[:, :2] if vertices is not None else None
        self._plataforma = plataforma
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 22, 25))
        p.setPen(QColor(150, 150, 155))

        if self._pontos is None or len(self._pontos) == 0:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Sem modelo carregado.")
            return

        mn = self._pontos.min(axis=0)
        mx = self._pontos.max(axis=0)
        ext = np.maximum(mx - mn, 1e-6)
        d = self.rect().adjusted(20, 20, -20, -20)
        esc = min(d.width() / ext[0], d.height() / ext[1])

        xy = (self._pontos - mn) * esc
        xy[:, 1] = d.height() - xy[:, 1]
        xy += np.array([d.x(), d.y()])

        p.setPen(QPen(QColor(90, 200, 255, 90), 1))
        passo = max(1, len(xy) // 4000)
        for x, y in xy[::passo]:
            p.drawPoint(int(x), int(y))

        p.setPen(QColor(170, 175, 182))
        p.drawText(self.rect().adjusted(8, 0, -8, -6),
                   Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
                   f"Vista de topo (OpenGL indisponivel)  "
                   f"{ext[0]:.1f} x {ext[1]:.1f} mm")


class Vista3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.usando_opengl = OPENGL_OK
        self._item_malha = None
        self._item_grade = None

        if OPENGL_OK:
            try:
                self._gl = gl.GLViewWidget()
                self._gl.setBackgroundColor(QColor(20, 22, 25))
                self._gl.setCameraPosition(distance=140, elevation=22,
                                           azimuth=-60)
                lay.addWidget(self._gl)
            except Exception as e:                       # pragma: no cover
                self.usando_opengl = False
                self._erro = str(e)

        if not self.usando_opengl:
            self._fallback = _VistaTopo()
            aviso = QLabel(
                "Vista 3D indisponivel nesta maquina; usando vista de topo.")
            aviso.setStyleSheet("color:#e0a030; padding:4px;")
            lay.addWidget(aviso)
            lay.addWidget(self._fallback)

    # ---- API ------------------------------------------------------------

    def definir_plataforma(self, x_mm: float | None, y_mm: float | None):
        """
        Desenha a plataforma de (0, 0) ate (x, y), que e o sistema de
        coordenadas do fatiamento e da mesa da maquina. Sem o curso dos eixos,
        nao desenha nada.
        """
        if not self.usando_opengl:
            return
        if self._item_grade is not None:
            self._gl.removeItem(self._item_grade)
            self._item_grade = None
        if not x_mm or not y_mm:
            return
        g = gl.GLGridItem()
        g.setSize(x=x_mm, y=y_mm)
        g.setSpacing(x=10, y=10)
        # O GLGridItem nasce centrado na origem: sem isto a mesa aparecia de
        # -x/2 a +x/2 e uma peca "no centro" caia em coordenada negativa
        g.translate(x_mm / 2.0, y_mm / 2.0, 0.0)
        self._gl.addItem(g)
        self._item_grade = g

    def definir_malha(self, malha):
        if not self.usando_opengl:
            self._fallback.definir(
                None if malha is None else np.asarray(malha.vertices), None)
            return

        if self._item_malha is not None:
            self._gl.removeItem(self._item_malha)
            self._item_malha = None
        if malha is None:
            return

        dados = gl.MeshData(vertexes=np.asarray(malha.vertices, dtype=float),
                            faces=np.asarray(malha.faces))
        item = gl.GLMeshItem(meshdata=dados, smooth=False,
                             color=(0.35, 0.78, 1.0, 1.0),
                             shader="shaded", drawEdges=False)
        self._gl.addItem(item)
        self._item_malha = item

        ext = malha.bounds[1] - malha.bounds[0]
        c = (malha.bounds[0] + malha.bounds[1]) / 2.0
        self._gl.setCameraPosition(
            pos=QVector3D(float(c[0]), float(c[1]), float(c[2])),
            distance=float(max(ext) * 2.6 + 30))
