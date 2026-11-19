"""
Orquestracao de um job de fatiamento.

Junta perfil + malha + parametros e produz as camadas, uma a uma, reportando
progresso. Nao conhece Qt: a interface so chama e escuta.

DUAS DECISOES QUE JA DERAM PROBLEMA E ESTAO EXPLICADAS AQUI

1. O FATIADOR GUARDA A GEOMETRIA VERDADEIRA, NAO O DADO COMPENSADO.
   A compensacao de cisalhamento e por BICO, e so faz sentido quando uma
   faixa de 128 linhas e atribuida aos 128 bicos, ou seja na codificacao.
   Aplicar antes, no corte, deslocava cada linha do raster em ate 212 colunas
   repetindo a cada 128 linhas, o que produzia listras diagonais no lugar da
   peca. A camada guardada aqui e a secao real; quem distorce e o codificador.

2. AS CAMADAS VAO PARA DISCO, NAO PARA A RAM.
   Uma peca de 3532 camadas a 1276x1659 ocupa ~935 MB empacotada em bits.
   Guardamos num arquivo temporario mapeado em memoria: o sistema operacional
   pagina o que for preciso e o uso de RAM fica limitado. Chame fechar()
   para apagar o arquivo.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import trimesh

from ..profile import Perfil
from .fatiador import (
    CompensacaoBicos, GradeRaster, alturas_camadas, compensar_bicos,
    rasterizar, segmentos_da_camada,
)


@dataclass
class ResumoJob:
    """Tudo que da para saber ANTES de fatiar. Alimenta a previa da interface."""
    dpi_x: float
    dpi_y: float
    velocidade_mm_s: float
    espessura_camada_um: float

    n_camadas: int
    n_faixas: int
    grade: GradeRaster
    compensacao: CompensacaoBicos

    freq_disparo_hz: float
    freq_max_hz: float | None
    espessura_por_passada_um: float | None
    passadas_necessarias: float | None
    largura_faixa_mm: float | None

    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def viavel(self) -> bool:
        return not self.erros

    @property
    def bytes_por_camada(self) -> int:
        return self.grade.n_linhas * ((self.grade.n_colunas + 7) // 8)

    @property
    def disco_estimado_mb(self) -> float:
        return self.bytes_por_camada * self.n_camadas / 1e6


class Camadas:
    """
    Camadas fatiadas, empacotadas em bits e guardadas em disco.

    Toda camada ocupa exatamente o mesmo numero de bytes, entao a posicao da
    camada i no arquivo e i * bytes_por_camada e nao precisa de indice.

    POR QUE E/S NORMAL E NAO numpy.memmap
    A primeira versao usava memmap, e nao resolveu: o Windows contabiliza as
    paginas sujas do arquivo mapeado dentro do working set do processo, entao
    escrever 815 MB sequencialmente deixava os mesmos 815 MB residentes.
    Medido: pico de 914 MB. Escrevendo com E/S normal, as paginas vao para o
    cache do sistema, que nao pertence ao processo, e a leitura por demanda
    traz uma camada de cada vez.
    """

    def __init__(self, grade: GradeRaster, n_camadas: int):
        self.grade = grade
        self.z_mm = np.zeros(max(n_camadas, 1), dtype=float)
        self._n_bytes_linha = (grade.n_colunas + 7) // 8
        self._n_bytes_camada = grade.n_linhas * self._n_bytes_linha
        self._n = 0

        fd, self._caminho = tempfile.mkstemp(prefix="pajm_", suffix=".camadas")
        self._escrita = os.fdopen(fd, "wb", buffering=1024 * 1024)
        self._leitura = None

    def _acrescentar(self, bitmap: np.ndarray, z: float) -> None:
        self._escrita.write(np.packbits(bitmap, axis=1).tobytes())
        self.z_mm[self._n] = z
        self._n += 1

    def _finalizar(self) -> None:
        """Fecha a escrita e abre para leitura aleatoria."""
        if self._escrita is not None:
            self._escrita.close()
            self._escrita = None
        if self._leitura is None and self._caminho:
            self._leitura = open(self._caminho, "rb")

    def __len__(self) -> int:
        return self._n

    def bitmap(self, i: int) -> np.ndarray:
        """Camada i como booleano. So esta camada sai do disco."""
        if not 0 <= i < self._n:
            raise IndexError(f"camada {i} fora de 0..{self._n - 1}")
        if self._leitura is None:
            raise RuntimeError("o fatiamento ainda nao foi finalizado")
        self._leitura.seek(i * self._n_bytes_camada)
        bruto = np.frombuffer(
            self._leitura.read(self._n_bytes_camada), dtype=np.uint8)
        return np.unpackbits(
            bruto.reshape(self.grade.n_linhas, self._n_bytes_linha),
            axis=1, count=self.grade.n_colunas).astype(bool)

    @property
    def n_gotas(self) -> int:
        total = 0
        for i in range(self._n):
            total += int(self.bitmap(i).sum())
        return total

    @property
    def bytes_em_disco(self) -> int:
        return self._n * self._n_bytes_camada

    def fechar(self) -> None:
        """Fecha os arquivos e apaga o temporario."""
        for atr in ("_escrita", "_leitura"):
            f = getattr(self, atr, None)
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass
                setattr(self, atr, None)
        caminho = getattr(self, "_caminho", None)
        if caminho and os.path.exists(caminho):
            try:
                os.unlink(caminho)
            except OSError:
                pass          # se ficar preso, some com a limpeza do TEMP
        self._caminho = None

    def __del__(self):
        try:
            self.fechar()
        except Exception:
            pass


# --------------------------------------------------------------------------

def preparar(perfil: Perfil, malha: trimesh.Trimesh, dpi_x: float,
             velocidade_mm_s: float, espessura_camada_um: float,
             margem_mm: float = 1.0) -> ResumoJob:
    """Calcula tudo que depende dos parametros, sem fatiar nada."""
    cab = perfil.cabecote
    erros: list[str] = []
    avisos: list[str] = []

    dpi_y = cab.dpi_y_nativo
    if dpi_y is None:
        erros.append(
            "Sem a variante ou o angulo do cabecote nao da para determinar o "
            "DPI em Y. Defina na pagina Maquina.")
        dpi_y = 360.0

    b = malha.bounds
    # A grade cobre a PECA. A folga que a compensacao exige e problema do
    # codificador, nao do corte.
    grade = GradeRaster.para_area(
        b[0][0] - margem_mm, b[0][1] - margem_mm,
        b[1][0] + margem_mm, b[1][1] + margem_mm, dpi_x, dpi_y)

    try:
        comp = compensar_bicos(cab, dpi_x, velocidade_mm_s)
    except ValueError as e:
        erros.append(str(e))
        comp = CompensacaoBicos(np.zeros(cab.n_bicos, np.int64), 0.0,
                                0.0, 0.0, dpi_x)

    n_camadas = len(alturas_camadas(b[0][2], b[1][2],
                                    espessura_camada_um / 1000.0))
    n_faixas = int(np.ceil(grade.n_linhas / cab.n_bicos))

    freq = perfil.freq_disparo_hz(velocidade_mm_s, dpi_x)
    freq_max = cab.freq_disparo_max_hz
    if freq_max and freq > freq_max:
        v_max = perfil.velocidade_max_para_dpi(dpi_x)
        erros.append(
            f"A combinacao pede {freq:.0f} Hz de disparo, acima do maximo do "
            f"cabecote ({freq_max:.0f} Hz). Reduza a velocidade para no maximo "
            f"{v_max:.0f} mm/s, ou reduza o DPI em X.")

    esp_passada = perfil.espessura_teorica_um(dpi_x, dpi_y)
    passadas = None
    if esp_passada:
        passadas = espessura_camada_um / esp_passada
        if passadas < 0.95:
            avisos.append(
                f"Uma unica passada deposita {esp_passada:.2f} um, mais que a "
                f"camada de {espessura_camada_um:.1f} um configurada. Vai "
                "inundar. Aumente a camada ou reduza o DPI.")
        elif abs(passadas - round(passadas)) > 0.05:
            avisos.append(
                f"A camada de {espessura_camada_um:.1f} um exige "
                f"{passadas:.2f} passadas de {esp_passada:.2f} um, que nao e "
                "inteiro. Ajuste a espessura para um multiplo, ou aceite a "
                "diferenca.")

    if comp.residuo_colunas > 0.25:
        avisos.append(
            f"A compensacao de cisalhamento deixa ate {comp.residuo_um:.0f} um "
            f"de residuo ({comp.residuo_colunas:.2f} de coluna). E o limite de "
            "quantizacao com colunas inteiras, nao um defeito do corte.")

    largura = cab.largura_faixa_mm
    if largura and grade.altura_mm > largura:
        avisos.append(
            f"A peca tem {grade.altura_mm:.1f} mm em Y e a faixa do cabecote e "
            f"de {largura:.1f} mm: sao {n_faixas} passadas de costura por "
            "camada.")

    r = ResumoJob(
        dpi_x=dpi_x, dpi_y=dpi_y, velocidade_mm_s=velocidade_mm_s,
        espessura_camada_um=espessura_camada_um,
        n_camadas=n_camadas, n_faixas=n_faixas, grade=grade, compensacao=comp,
        freq_disparo_hz=freq, freq_max_hz=freq_max,
        espessura_por_passada_um=esp_passada, passadas_necessarias=passadas,
        largura_faixa_mm=largura, erros=erros, avisos=avisos)

    if r.disco_estimado_mb > 2000:
        avisos.append(
            f"O fatiamento vai gravar ~{r.disco_estimado_mb/1000:.1f} GB em "
            "disco temporario. Aumente a espessura de camada ou reduza o DPI "
            "se o disco for apertado.")
    return r


def fatiar(malha: trimesh.Trimesh, resumo: ResumoJob,
           progresso: Callable[[int, int], bool] | None = None) -> Camadas:
    """
    Fatia a malha inteira, gravando as camadas em disco.

    progresso(i, total) e chamado a cada camada; se devolver False, o
    fatiamento e interrompido e as camadas ja prontas sao devolvidas.
    """
    v = np.asarray(malha.vertices, dtype=float)
    f = np.asarray(malha.faces, dtype=np.int64)
    b = malha.bounds
    zs = alturas_camadas(b[0][2], b[1][2], resumo.espessura_camada_um / 1000.0)

    camadas = Camadas(resumo.grade, len(zs))
    try:
        for i, z in enumerate(zs):
            seg = segmentos_da_camada(v, f, float(z))
            # Sem deslocamento: aqui guardamos a geometria real da secao.
            camadas._acrescentar(rasterizar(seg, resumo.grade), float(z))
            if progresso and not progresso(i + 1, len(zs)):
                break
        camadas._finalizar()
    except BaseException:
        camadas.fechar()
        raise
    return camadas
