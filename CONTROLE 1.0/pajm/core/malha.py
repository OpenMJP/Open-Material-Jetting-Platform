"""
Carregamento, validacao e posicionamento da malha STL.

O STL nao carrega unidade nenhuma no arquivo. A convencao de fato da
manufatura aditiva e milimetro, e e isso que assumimos, mas a unidade fica
exposta em Transformacao.escala para o caso de um arquivo vir em polegada
ou em metro.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh


# --------------------------------------------------------------------------
# Validacao
# --------------------------------------------------------------------------

@dataclass
class RelatorioMalha:
    n_vertices: int = 0
    n_faces: int = 0
    n_componentes: int = 1

    estanque: bool = False
    normais_consistentes: bool = False
    n_arestas_abertas: int = 0
    n_faces_degeneradas: int = 0

    volume_mm3: float | None = None
    area_mm2: float = 0.0
    dimensoes_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    minimo_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)

    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def pode_fatiar(self) -> bool:
        """
        O fatiador por linhas de varredura usa a regra par-impar sobre os
        segmentos brutos da secao. Ele tolera malha nao estanque, mas o
        resultado passa a ser palpite nas regioes com buraco. Entao malha
        aberta e aviso, nao erro: fatia, mas avisa.
        """
        return not self.erros


def validar(malha: trimesh.Trimesh) -> RelatorioMalha:
    r = RelatorioMalha()
    r.n_vertices = int(len(malha.vertices))
    r.n_faces = int(len(malha.faces))

    if r.n_faces == 0:
        r.erros.append("A malha nao tem nenhuma face.")
        return r

    r.estanque = bool(malha.is_watertight)
    r.normais_consistentes = bool(malha.is_winding_consistent)
    r.area_mm2 = float(malha.area)

    dim = malha.bounds[1] - malha.bounds[0]
    r.dimensoes_mm = tuple(float(v) for v in dim)
    r.minimo_mm = tuple(float(v) for v in malha.bounds[0])

    # Volume so faz sentido em malha fechada.
    if r.estanque:
        try:
            r.volume_mm3 = float(abs(malha.volume))
        except Exception:
            r.volume_mm3 = None

    # Arestas de borda: aparecem uma unica vez na lista de arestas.
    try:
        grupos = trimesh.grouping.group_rows(malha.edges_sorted, require_count=1)
        r.n_arestas_abertas = int(len(grupos))
    except Exception:
        r.n_arestas_abertas = 0

    # Faces com area nula quebram o calculo de interseccao.
    try:
        areas = malha.area_faces
        r.n_faces_degeneradas = int(np.count_nonzero(areas <= 1e-12))
    except Exception:
        r.n_faces_degeneradas = 0

    try:
        r.n_componentes = int(len(malha.split(only_watertight=False)))
    except Exception:
        r.n_componentes = 1

    # ---- diagnostico -----------------------------------------------------

    if min(dim) <= 0:
        r.erros.append(
            "A malha e degenerada: uma das dimensoes e zero.")

    if not r.estanque:
        r.avisos.append(
            f"Malha nao estanque: {r.n_arestas_abertas} arestas de borda. "
            "O fatiamento continua funcionando, mas nas regioes do buraco o "
            "preenchimento vira palpite.")

    if not r.normais_consistentes:
        r.avisos.append(
            "Normais inconsistentes entre faces vizinhas. Isso nao afeta o "
            "fatiamento por par-impar, mas costuma indicar STL mal exportado.")

    if r.n_componentes > 1:
        r.avisos.append(
            f"A malha tem {r.n_componentes} componentes separados. Se isso nao "
            "for proposital, o arquivo pode ter geometria solta.")

    if r.n_faces_degeneradas:
        r.avisos.append(
            f"{r.n_faces_degeneradas} faces com area nula foram ignoradas.")

    return r


def reparar(malha: trimesh.Trimesh) -> tuple[trimesh.Trimesh, list[str]]:
    """
    Reparo conservador. Nao mexe na geometria, so na topologia.

    Nunca engole excecao em silencio: se um passo falhar, isso vira uma linha
    do relatorio. Reparo que diz "nada a fazer" quando na verdade quebrou e
    pior do que reparo nenhum.
    """
    acoes: list[str] = []
    m = malha.copy()

    antes = len(m.faces)
    m.update_faces(m.nondegenerate_faces())
    m.remove_unreferenced_vertices()
    if len(m.faces) != antes:
        acoes.append(f"Removidas {antes - len(m.faces)} faces degeneradas.")

    if not m.is_winding_consistent:
        try:
            trimesh.repair.fix_normals(m)
            acoes.append("Normais reorientadas.")
        except Exception as e:
            acoes.append(f"FALHOU ao reorientar normais: {e}")

    if not m.is_watertight:
        n_antes = len(m.faces)
        try:
            m.fill_holes()
            if m.is_watertight:
                acoes.append(
                    f"Buracos fechados ({len(m.faces) - n_antes} faces novas).")
            else:
                acoes.append(
                    "Buracos parcialmente fechados: a malha continua aberta.")
        except Exception as e:
            acoes.append(f"FALHOU ao fechar buracos: {e}")

    return m, acoes


# --------------------------------------------------------------------------
# Carregamento
# --------------------------------------------------------------------------

def carregar(caminho: str | Path) -> trimesh.Trimesh:
    """Carrega um STL (ASCII ou binario) e devolve uma unica malha."""
    obj = trimesh.load_mesh(str(caminho))

    if isinstance(obj, trimesh.Scene):
        geoms = [g for g in obj.geometry.values()
                 if isinstance(g, trimesh.Trimesh)]
        if not geoms:
            raise ValueError("O arquivo nao contem nenhuma malha triangular.")
        obj = trimesh.util.concatenate(geoms)

    if not isinstance(obj, trimesh.Trimesh):
        raise ValueError(f"Formato nao suportado: {type(obj).__name__}")

    return obj


# --------------------------------------------------------------------------
# Posicionamento
# --------------------------------------------------------------------------

@dataclass
class Transformacao:
    """Posicao, orientacao e escala do modelo sobre a plataforma."""
    pos_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rot_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)   # X, Y, Z
    escala: float = 1.0

    def matriz(self) -> np.ndarray:
        m = np.eye(4)
        m[:3, :3] *= self.escala

        for eixo, ang in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), self.rot_deg):
            if ang:
                r = trimesh.transformations.rotation_matrix(
                    np.radians(ang), eixo)
                m = r @ m

        m[:3, 3] += np.asarray(self.pos_mm, dtype=float)
        return m

    def aplicar(self, malha: trimesh.Trimesh) -> trimesh.Trimesh:
        m = malha.copy()
        m.apply_transform(self.matriz())
        return m


def assentar(malha: trimesh.Trimesh) -> trimesh.Trimesh:
    """
    Baixa a malha ate encostar em Z=0, SEM mexer em X e Y.

    A versao anterior tambem recentrava em X e Y na origem. Como esta funcao
    roda depois da transformacao, isso descartava os campos de posicao X e Y:
    o usuario mexia e nada acontecia. Centrar virou acao separada
    (`centro_xy`), que e o que se espera de um botao "centralizar".
    """
    m = malha.copy()
    m.apply_translation([0.0, 0.0, -m.bounds[0][2]])
    return m


def centro_xy(malha: trimesh.Trimesh) -> tuple[float, float]:
    """Centro da peca em X e Y, para o botao de centralizar na mesa."""
    b = malha.bounds
    return (float((b[0][0] + b[1][0]) / 2.0),
            float((b[0][1] + b[1][1]) / 2.0))


# --------------------------------------------------------------------------
# Deitar uma face na mesa
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FaceCandidata:
    """Um conjunto de triangulos coplanares que pode servir de base."""
    normal: tuple[float, float, float]
    area_mm2: float
    n_triangulos: int

    @property
    def rotulo(self) -> str:
        nx, ny, nz = self.normal
        return (f"{self.area_mm2:8.2f} mm2   normal "
                f"({nx:+.2f}, {ny:+.2f}, {nz:+.2f})")


def faces_candidatas(malha: trimesh.Trimesh,
                     maximo: int = 12,
                     angulo_graus: float = 1.0) -> list[FaceCandidata]:
    """
    Superficies planas da peca, da maior para a menor.

    Agrupa os triangulos por direcao da normal: um cubo tem milhares de
    triangulos mas so seis direcoes, e e por direcao que se escolhe a base.
    A area somada e o criterio de ordenacao, porque a face que mais interessa
    apoiar e a que tem mais contato com a mesa.
    """
    normais = np.asarray(malha.face_normals, dtype=float)
    areas = np.asarray(malha.area_faces, dtype=float)
    if len(normais) == 0:
        return []

    try:
        unicas, grupos = trimesh.grouping.group_vectors(
            normais, angle=np.radians(angulo_graus))
    except Exception:
        # Sem o agrupador do trimesh, arredondar a normal ja separa bem.
        chaves = np.round(normais, 2)
        unicas, inverso = np.unique(chaves, axis=0, return_inverse=True)
        grupos = [np.flatnonzero(inverso == i) for i in range(len(unicas))]

    saida = [
        FaceCandidata(normal=tuple(float(v) for v in unicas[i]),
                      area_mm2=float(areas[g].sum()),
                      n_triangulos=int(len(g)))
        for i, g in enumerate(grupos)
    ]
    saida.sort(key=lambda c: c.area_mm2, reverse=True)
    return saida[:maximo]


def rotacao_para_deitar(normal) -> np.ndarray:
    """
    Matriz 4x4 que gira a peca para a normal dada apontar para baixo.

    Apontar a normal da face para -Z e o que coloca essa face em contato com
    a mesa, que e o que se quer dizer com "deitar esta face".
    """
    n = np.asarray(normal, dtype=float)
    norma = np.linalg.norm(n)
    if norma < 1e-12:
        return np.eye(4)
    return trimesh.geometry.align_vectors(n / norma, np.array([0.0, 0.0, -1.0]))


def euler_de_matriz(m: np.ndarray) -> tuple[float, float, float]:
    """
    Matriz de rotacao -> os tres angulos em graus que `Transformacao` usa.

    `Transformacao` compoe as rotacoes como Rz @ Ry @ Rx, que e a convencao
    de eixos estaticos 'sxyz'. Converter para la permite que o resultado de
    "deitar face" caia nos MESMOS campos de giro da tela, em vez de virar um
    estado escondido: depois de deitar, da para continuar ajustando a mao.
    """
    ax, ay, az = trimesh.transformations.euler_from_matrix(m[:3, :3], "sxyz")
    return (float(np.degrees(ax)), float(np.degrees(ay)),
            float(np.degrees(az)))
