"""
Pacote de camadas `.pajm`: o arquivo que um CAM externo (o RP3 do
laboratorio) gera para o PAJM Controle imprimir.

A especificacao e o IMPORTACAO.md, na raiz do projeto. Este modulo e a
implementacao de referencia dos dois lados: le e valida o pacote, reamostra
as camadas para a grade da maquina quando o pixel declarado nao bate, e
escreve pacotes a partir de uma peca fatiada aqui mesmo (o exemplo para quem
implementa o RP3, e o teste de ida e volta).
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .fatiador import GradeRaster

FORMATO = "pajm-camadas"
FORMATO_PERFIL = "pajm-perfil-maquina"
VERSAO = (1, 0)

#: Diferenca relativa de pixel abaixo da qual a grade e considerada a mesma
TOLERANCIA_GRADE = 0.005
#: Protecao contra arquivo corrompido ou malicioso (IMPORTACAO.md secao 7)
MAX_DESCOMPACTADO = 4 * 1024 ** 3
MAX_LADO_PX = 20000


class PacoteInvalido(ValueError):
    """O arquivo nao segue o IMPORTACAO.md. A mensagem diz o que falta."""


# --------------------------------------------------------------------------
# PNG <-> bitmap (preto = gota)
# --------------------------------------------------------------------------

def _png_para_bitmap(dados: bytes) -> np.ndarray:
    from PySide6.QtGui import QImage

    from .imagem import _qimage_para_cinza

    img = QImage.fromData(dados)
    if img.isNull():
        raise PacoteInvalido("imagem que nao abre como PNG")
    # Transparente vira branco (sem gota) dentro de _qimage_para_cinza
    return _qimage_para_cinza(img) < 0.5


def _bitmap_para_png(bitmap: np.ndarray) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage

    h, w = bitmap.shape
    cinza = np.where(bitmap, 0, 255).astype(np.uint8)
    cinza = np.ascontiguousarray(cinza)
    img = QImage(cinza.data, w, h, w, QImage.Format.Format_Grayscale8)
    # 1 bit: o menor arquivo, e sem duvida no limiar (secao 3)
    img = img.convertToFormat(QImage.Format.Format_Mono)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba.data())


# --------------------------------------------------------------------------
# Reamostragem para a grade da maquina
# --------------------------------------------------------------------------

def reamostrar(bitmap: np.ndarray, px_origem: tuple[float, float],
               px_destino: tuple[float, float]) -> np.ndarray:
    """
    Bitmap com pixel `px_origem` (x, y, em mm) -> grade com `px_destino`.

    Por area (IMPORTACAO.md secao 5): um ponto da grade nova e jateado se
    mais da metade da sua area cai em pixels pretos. O pixel c representa a
    gota em c * passo, entao cobre [c - 1/2, c + 1/2) passos. A integral de
    uma imagem constante por pixel e bilinear dentro de cada pixel, entao a
    tabela de somas acumuladas interpolada da a cobertura exata.
    """
    h, w = bitmap.shape
    sx, sy = px_origem
    tx, ty = px_destino
    n_col = int(round((w - 1) * sx / tx)) + 1
    n_lin = int(round((h - 1) * sy / ty)) + 1

    sat = np.zeros((h + 1, w + 1), dtype=np.float64)
    sat[1:, 1:] = np.cumsum(np.cumsum(bitmap, axis=0, dtype=np.float64),
                            axis=1)

    def bordas(n, t, s, lado):
        b = (np.arange(n + 1) - 0.5) * t / s + 0.5
        return np.clip(b, 0.0, float(lado))

    def interp(tab, pos, eixo):
        i = np.minimum(np.floor(pos).astype(np.int64), tab.shape[eixo] - 2)
        f = pos - i
        a = np.take(tab, i, axis=eixo)
        b = np.take(tab, i + 1, axis=eixo)
        forma = [1, 1]
        forma[eixo] = -1
        f = f.reshape(forma)
        return a * (1.0 - f) + b * f

    xb = bordas(n_col, tx, sx, w)
    yb = bordas(n_lin, ty, sy, h)
    s = interp(interp(sat, xb, 1), yb, 0)          # (n_lin+1, n_col+1)
    cobertura = s[1:, 1:] - s[:-1, 1:] - s[1:, :-1] + s[:-1, :-1]
    area = np.outer(np.diff(yb), np.diff(xb))
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = np.where(area > 0, cobertura / area, 0.0)
    return frac > 0.5


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------

def _numero(d: dict, chave: str, onde: str, positivo: bool = False) -> float:
    if chave not in d:
        raise PacoteInvalido(f"falta o campo obrigatorio {onde}.{chave}")
    v = d[chave]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise PacoteInvalido(f"{onde}.{chave} tem que ser um numero")
    if positivo and not v > 0:
        raise PacoteInvalido(f"{onde}.{chave} tem que ser maior que zero")
    return float(v)


@dataclass
class Pacote:
    """Pacote aberto e validado. As camadas sao lidas do ZIP sob demanda."""
    caminho: Path
    manifesto: dict
    pixel_mm: tuple[float, float]
    origem_mm: tuple[float, float]
    z_mm: list[float]
    arquivos: list[str]
    largura_px: int
    altura_px: int
    avisos: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.z_mm)

    @property
    def gerador(self) -> str:
        g = self.manifesto.get("gerador") or {}
        return " ".join(str(g.get(k, "")) for k in ("nome", "versao")).strip() \
            or "nao informado"

    @property
    def nome_peca(self) -> str:
        return str((self.manifesto.get("peca") or {}).get("nome")
                   or self.caminho.stem)

    @property
    def processo(self) -> dict:
        return dict(self.manifesto.get("processo") or {})

    @property
    def dimensoes_mm(self) -> tuple[float, float]:
        return (self.largura_px * self.pixel_mm[0],
                self.altura_px * self.pixel_mm[1])

    def bitmap(self, i: int) -> np.ndarray:
        """
        Camada i no sentido do fatiador: linha 0 = Y menor. No arquivo a
        imagem esta com o Y para cima (IMPORTACAO.md secao 4), entao as
        linhas sao invertidas na leitura.
        """
        with zipfile.ZipFile(self.caminho) as z:
            return _png_para_bitmap(z.read(self.arquivos[i]))[::-1]

    # ---- grade da maquina -------------------------------------------------

    def grade_nativa(self, dpi_x: float, dpi_y: float) -> bool:
        tx, ty = 25.4 / dpi_x, 25.4 / dpi_y
        return (abs(self.pixel_mm[0] - tx) / tx < TOLERANCIA_GRADE
                and abs(self.pixel_mm[1] - ty) / ty < TOLERANCIA_GRADE)

    def grade(self, dpi_x: float, dpi_y: float,
              origem_mm: tuple[float, float] | None = None) -> GradeRaster:
        """A grade da maquina em que as camadas vao ser impressas."""
        ox, oy = origem_mm if origem_mm is not None else self.origem_mm
        if self.grade_nativa(dpi_x, dpi_y):
            n_col, n_lin = self.largura_px, self.altura_px
        else:
            tx, ty = 25.4 / dpi_x, 25.4 / dpi_y
            n_col = int(round((self.largura_px - 1) * self.pixel_mm[0] / tx)) + 1
            n_lin = int(round((self.altura_px - 1) * self.pixel_mm[1] / ty)) + 1
        return GradeRaster(float(ox), float(oy), float(dpi_x), float(dpi_y),
                           n_col, n_lin)

    def bitmap_na_grade(self, i: int, dpi_x: float,
                        dpi_y: float) -> np.ndarray:
        """Camada i na grade da maquina: igual, ou reamostrada."""
        b = self.bitmap(i)
        if self.grade_nativa(dpi_x, dpi_y):
            return b
        return reamostrar(b, self.pixel_mm, (25.4 / dpi_x, 25.4 / dpi_y))


def abrir(caminho: str | Path) -> Pacote:
    """Abre e valida um `.pajm`. Levanta PacoteInvalido com o motivo."""
    caminho = Path(caminho)
    try:
        z = zipfile.ZipFile(caminho)
    except (zipfile.BadZipFile, OSError) as e:
        raise PacoteInvalido(f"nao e um arquivo .pajm (ZIP) valido: {e}")

    with z:
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_DESCOMPACTADO:
            raise PacoteInvalido(
                f"o pacote descompactado teria {total / 1024 ** 3:.1f} GB, "
                f"acima do limite de {MAX_DESCOMPACTADO / 1024 ** 3:.0f} GB")
        try:
            m = json.loads(z.read("manifesto.json").decode("utf-8"))
        except KeyError:
            raise PacoteInvalido("falta o manifesto.json na raiz do pacote")
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise PacoteInvalido(f"manifesto.json nao e um JSON valido: {e}")
        if not isinstance(m, dict):
            raise PacoteInvalido("manifesto.json tem que ser um objeto JSON")

        if m.get("formato") != FORMATO:
            raise PacoteInvalido(
                f"formato {m.get('formato')!r} desconhecido; esperado "
                f"{FORMATO!r}")
        try:
            maior, menor = (int(x) for x in str(m.get("versao", "")).split("."))
        except ValueError:
            raise PacoteInvalido(
                f"versao {m.get('versao')!r} invalida; use MAIOR.MENOR")
        if maior != VERSAO[0]:
            raise PacoteInvalido(
                f"o pacote usa a versao {maior}.{menor} do formato, e este "
                f"programa le a {VERSAO[0]}.x")
        avisos = []
        if menor > VERSAO[1]:
            avisos.append(
                f"pacote da versao {maior}.{menor}, mais nova que a "
                f"{VERSAO[0]}.{VERSAO[1]} deste programa: campos novos foram "
                "ignorados")

        px = m.get("pixel_mm")
        org = m.get("origem_mm")
        if not isinstance(px, dict) or not isinstance(org, dict):
            raise PacoteInvalido("faltam pixel_mm e origem_mm, com x e y")
        pixel = (_numero(px, "x", "pixel_mm", True),
                 _numero(px, "y", "pixel_mm", True))
        origem = (_numero(org, "x", "origem_mm"),
                  _numero(org, "y", "origem_mm"))

        camadas = m.get("camadas")
        if not isinstance(camadas, list) or not camadas:
            raise PacoteInvalido("camadas tem que ser uma lista com ao "
                                 "menos uma camada")
        zs, arquivos = [], []
        nomes = set(z.namelist())
        tamanho = None
        for k, c in enumerate(camadas, start=1):
            onde = f"camadas[{k}]"
            if not isinstance(c, dict):
                raise PacoteInvalido(f"{onde} tem que ser um objeto")
            zk = _numero(c, "z_mm", onde, True)
            if zs and zk <= zs[-1]:
                raise PacoteInvalido(
                    f"{onde}.z_mm = {zk} nao e maior que o da camada "
                    f"anterior ({zs[-1]}): o Z tem que crescer")
            arq = c.get("arquivo")
            if not isinstance(arq, str) or arq not in nomes:
                raise PacoteInvalido(f"{onde}: a imagem {arq!r} nao esta no "
                                     "pacote")
            dados = z.read(arq)
            esperado = c.get("sha256")
            if esperado is not None and \
                    hashlib.sha256(dados).hexdigest() != str(esperado).lower():
                raise PacoteInvalido(f"{onde}: {arq} nao confere com o sha256 "
                                     "do manifesto (arquivo corrompido)")
            # So o cabecalho importa aqui: o tamanho. A camada inteira e
            # decodificada quando for usada.
            dim = _tamanho_png(dados)
            if dim is None:
                raise PacoteInvalido(f"{onde}: {arq} nao e um PNG")
            if max(dim) > MAX_LADO_PX:
                raise PacoteInvalido(
                    f"{onde}: {dim[0]} x {dim[1]} pixels, acima do limite de "
                    f"{MAX_LADO_PX}")
            if tamanho is None:
                tamanho = dim
            elif dim != tamanho:
                raise PacoteInvalido(
                    f"{onde}: {arq} tem {dim[0]} x {dim[1]} pixels e as "
                    f"anteriores {tamanho[0]} x {tamanho[1]}: todas as "
                    "camadas tem que ter o mesmo tamanho")
            zs.append(zk)
            arquivos.append(arq)

    proc = m.get("processo") or {}
    op = proc.get("opacidade")
    if op is not None and not (isinstance(op, (int, float)) and 0 < op <= 1):
        raise PacoteInvalido("processo.opacidade tem que estar em (0, 1]")

    return Pacote(caminho=caminho, manifesto=m, pixel_mm=pixel,
                  origem_mm=origem, z_mm=zs, arquivos=arquivos,
                  largura_px=tamanho[0], altura_px=tamanho[1], avisos=avisos)


def _tamanho_png(dados: bytes) -> tuple[int, int] | None:
    """(largura, altura) do cabecalho IHDR, sem decodificar a imagem."""
    if len(dados) < 24 or dados[:8] != b"\x89PNG\r\n\x1a\n" \
            or dados[12:16] != b"IHDR":
        return None
    return (int.from_bytes(dados[16:20], "big"),
            int.from_bytes(dados[20:24], "big"))


# --------------------------------------------------------------------------
# Escrita
# --------------------------------------------------------------------------

def escrever(caminho: str | Path, bitmaps, z_mm, grade: GradeRaster,
             nome_peca: str = "", processo: dict | None = None,
             gerador: tuple[str, str] = ("PAJM Controle", ""),
             espessura_mm: float | None = None) -> Path:
    """
    Escreve um `.pajm`. `bitmaps` e uma sequencia (ou gerador) de bitmaps
    booleanos, todos com o tamanho da `grade`, na ordem de `z_mm`.
    """
    caminho = Path(caminho)
    camadas = []
    with zipfile.ZipFile(caminho, "w", zipfile.ZIP_DEFLATED) as z:
        for k, (b, zk) in enumerate(zip(bitmaps, z_mm), start=1):
            # Y para cima no arquivo: a imagem aparece como a peca vista de
            # cima (IMPORTACAO.md secao 4)
            png = _bitmap_para_png(np.asarray(b, dtype=bool)[::-1])
            arq = f"camadas/{k:04d}.png"
            z.writestr(arq, png)
            camadas.append({"z_mm": round(float(zk), 6), "arquivo": arq,
                            "sha256": hashlib.sha256(png).hexdigest()})
        m = {
            "formato": FORMATO,
            "versao": f"{VERSAO[0]}.{VERSAO[1]}",
            "gerador": {"nome": gerador[0], "versao": gerador[1]},
            "criado_em": datetime.now().astimezone().isoformat(
                timespec="seconds"),
            "peca": {"nome": nome_peca or caminho.stem},
            "pixel_mm": {"x": grade.passo_x_mm, "y": grade.passo_y_mm},
            "origem_mm": {"x": grade.x0_mm, "y": grade.y0_mm},
            "camadas": camadas,
        }
        if espessura_mm:
            m["espessura_camada_mm"] = espessura_mm
        if processo:
            m["processo"] = processo
        z.writestr("manifesto.json",
                   json.dumps(m, indent=2, ensure_ascii=False))
    return caminho


def perfil_para_cam(perfil) -> dict:
    """
    O que um CAM precisa para gerar ja na grade nativa (IMPORTACAO.md
    secao 5). Vale para a calibracao do momento em que foi exportado.
    """
    cab, mov = perfil.cabecote, perfil.movimento
    dpi_x = perfil.dpi_x_efetivo
    dpi_y = cab.dpi_y_nativo
    if dpi_y is None:
        raise ValueError("sem a variante ou o angulo do cabecote nao ha "
                         "grade em Y")
    return {
        "formato": FORMATO_PERFIL,
        "versao": f"{VERSAO[0]}.{VERSAO[1]}",
        "exportado_em": datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "pixel_mm": {"x": 25.4 / dpi_x, "y": 25.4 / dpi_y},
        "dpi": {"x": round(dpi_x, 3), "y": round(dpi_y, 3)},
        "area_util_mm": {"x": mov.curso_x_mm, "y": mov.curso_y_mm},
        "curso_z_mm": mov.curso_z_mm,
        "faixa_mm": round(cab.largura_faixa_mm, 4),
        "bicos": cab.n_bicos,
        "velocidade_max_mm_s": mov.velocidade_max_mm_s,
    }
