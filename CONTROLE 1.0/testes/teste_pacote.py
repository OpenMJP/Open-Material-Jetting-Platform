"""Pacote de camadas .pajm (IMPORTACAO.md): escrita, leitura, validacao."""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import trimesh

from pajm.core import job as cjob
from pajm.core import pacote as pac
from pajm.core.fatiador import GradeRaster
from pajm.profile import Perfil

todos_ok = True


def checa(nome, cond, extra=""):
    global todos_ok
    todos_ok &= bool(cond)
    print(f"  [{'OK ' if cond else 'FALHA'}] {nome}" + (f"  {extra}" if extra else ""))


TMP = Path(tempfile.mkdtemp(prefix="pajm_pacote_"))

print("=" * 72)
print("1. IDA E VOLTA COM UMA PECA FATIADA AQUI")
perfil = Perfil()
peca = trimesh.creation.box(extents=[10, 6, 0.2])
peca.apply_translation([60.0, 40.0, 0.1])
r = cjob.preparar(perfil, peca, dpi_x=perfil.dpi_x_efetivo,
                  velocidade_mm_s=50.0, espessura_camada_um=50.0)
cam = cjob.fatiar(peca, r)
n = len(cam)
arq = pac.escrever(TMP / "caixa.pajm", (cam.bitmap(i) for i in range(n)),
                   cam.z_mm[:n], cam.grade, nome_peca="caixa",
                   processo={"velocidade_mm_s": 50.0, "opacidade": 0.5},
                   espessura_mm=0.05)
checa("pacote escrito", arq.exists(), f"{arq.stat().st_size} bytes, {n} camadas")
with zipfile.ZipFile(arq) as z:
    m = json.loads(z.read("manifesto.json"))
checa("manifesto no formato da especificacao",
      m["formato"] == "pajm-camadas" and m["versao"] == "1.0"
      and len(m["camadas"]) == n and all("sha256" in c for c in m["camadas"]))

p = pac.abrir(arq)
checa("abre e valida", len(p) == n and p.nome_peca == "caixa",
      f"{p.largura_px} x {p.altura_px} px, gerador {p.gerador}")
g = cam.grade
checa("grade nativa reconhecida", p.grade_nativa(g.dpi_x, g.dpi_y))
iguais = all(np.array_equal(p.bitmap_na_grade(i, g.dpi_x, g.dpi_y),
                            cam.bitmap(i)) for i in range(n))
checa("ida e volta devolve as mesmas camadas, gota por gota", iguais)
gp = p.grade(g.dpi_x, g.dpi_y)
checa("mesma posicao na mesa",
      abs(gp.x0_mm - g.x0_mm) < 1e-9 and abs(gp.y0_mm - g.y0_mm) < 1e-9
      and (gp.n_colunas, gp.n_linhas) == (g.n_colunas, g.n_linhas))
checa("Z e processo preservados",
      np.allclose(p.z_mm, cam.z_mm[:n]) and p.processo["opacidade"] == 0.5)
cam.fechar()

# Orientacao no arquivo (secao 4): Y para cima. A linha 0 do bitmap (Y
# menor) tem que ser a ULTIMA linha do PNG, para a imagem aparecer como a
# peca vista de cima num visualizador comum
_t = np.zeros((6, 4), dtype=bool)
_t[0, 0] = True                         # gota no canto de X e Y menores
pac.escrever(TMP / "orient.pajm", [_t], [0.05],
             GradeRaster(10.0, 20.0, 360.0, 360.0, 4, 6))
with zipfile.ZipFile(TMP / "orient.pajm") as z:
    _png = pac._png_para_bitmap(z.read("camadas/0001.png"))
checa("no PNG, o Y menor e a ultima linha (Y para cima)",
      _png[-1, 0] and not _png[0, 0])
checa("e volta para a linha 0 na leitura",
      np.array_equal(pac.abrir(TMP / "orient.pajm").bitmap(0), _t))

print()
print("2. REAMOSTRAGEM PARA A GRADE DA MAQUINA")
# Retangulo de 10 x 6 mm desenhado a 0,05 mm, levado a 0,075 x 0,0706
fonte = np.zeros((200, 300), dtype=bool)
fonte[40:160, 50:250] = True            # 10 x 6 mm a 0,05 mm
alvo = (25.4 / 338.67, 25.4 / 360.0)
rs = pac.reamostrar(fonte, (0.05, 0.05), alvo)
cols = np.where(rs.any(axis=0))[0]
lins = np.where(rs.any(axis=1))[0]
larg = (cols[-1] - cols[0] + 1) * alvo[0]
alt = (lins[-1] - lins[0] + 1) * alvo[1]
checa("escala preservada em X e Y (dentro de um pixel)",
      abs(larg - 10.0) <= alvo[0] and abs(alt - 6.0) <= alvo[1],
      f"{larg:.3f} x {alt:.3f} mm")
checa("posicao preservada (borda esquerda em 2,5 mm)",
      abs(cols[0] * alvo[0] - 2.5) <= alvo[0], f"{cols[0] * alvo[0]:.3f} mm")
# Nos cantos a cobertura e o produto de duas bordas parciais (0,7 x 0,7 <
# 0,5): o pixel do canto pode cair, como deve numa reamostragem por area
checa("interior cheio, nada fora do retangulo, no maximo os 4 cantos caem",
      rs[lins[0] + 1:lins[-1], cols[0] + 1:cols[-1]].all()
      and len(lins) * len(cols) - rs.sum() <= 4,
      f"{len(lins) * len(cols) - rs.sum()} cantos")
xadrez = (np.indices((100, 100)).sum(axis=0) % 2).astype(bool)
r2 = pac.reamostrar(xadrez, (0.05, 0.05), (0.1, 0.1))
checa("detalhe menor que o pixel novo some sem inventar cheio",
      r2.mean() < 0.6, f"{r2.mean():.2f} de cobertura")
mesma = pac.reamostrar(fonte, (0.05, 0.05), (0.05, 0.05))
checa("pixel igual: identidade", np.array_equal(mesma, fonte))

# Pacote fora da grade: o programa reamostra
pac.escrever(TMP / "fino.pajm", [fonte, fonte], [0.05, 0.10],
             GradeRaster(20.0, 30.0, 25.4 / 0.05, 25.4 / 0.05, 300, 200))
pf = pac.abrir(TMP / "fino.pajm")
checa("pixel diferente: nao e grade nativa",
      not pf.grade_nativa(338.67, 360.0))
gf = pf.grade(338.67, 360.0)
bf = pf.bitmap_na_grade(0, 338.67, 360.0)
checa("camada reamostrada tem o tamanho da grade da maquina",
      bf.shape == (gf.n_linhas, gf.n_colunas), f"{bf.shape}")

print()
print("3. VALIDACAO: RECUSA COM O MOTIVO")


def pacote(manifesto, imagens, nome):
    caminho = TMP / nome
    with zipfile.ZipFile(caminho, "w") as z:
        if manifesto is not None:
            z.writestr("manifesto.json", json.dumps(manifesto))
        for arq, dados in imagens.items():
            z.writestr(arq, dados)
    return caminho


png_a = pac._bitmap_para_png(np.zeros((10, 20), dtype=bool))
png_b = pac._bitmap_para_png(np.zeros((11, 20), dtype=bool))
base = {"formato": "pajm-camadas", "versao": "1.0",
        "pixel_mm": {"x": 0.075, "y": 0.07},
        "origem_mm": {"x": 10, "y": 10},
        "camadas": [{"z_mm": 0.05, "arquivo": "camadas/0001.png"},
                    {"z_mm": 0.10, "arquivo": "camadas/0002.png"}]}
imgs = {"camadas/0001.png": png_a, "camadas/0002.png": png_a}


def recusa(nome, caminho, trecho):
    try:
        pac.abrir(caminho)
        checa(nome, False, "aceitou")
    except pac.PacoteInvalido as e:
        checa(nome, trecho in str(e), str(e)[:90])


checa("pacote minimo valido abre", len(pac.abrir(pacote(base, imgs, "ok.pajm"))) == 2)
(TMP / "lixo.pajm").write_bytes(b"isto nao e um zip")
recusa("nao e ZIP", TMP / "lixo.pajm", "ZIP")
recusa("sem manifesto", pacote(None, imgs, "a.pajm"), "manifesto.json")
recusa("formato errado", pacote({**base, "formato": "gcode"}, imgs, "b.pajm"),
       "formato")
recusa("versao maior desconhecida",
       pacote({**base, "versao": "2.0"}, imgs, "c.pajm"), "versao 2.0")
mais_nova = pac.abrir(pacote({**base, "versao": "1.7"}, imgs, "c2.pajm"))
checa("versao menor mais nova abre com aviso", mais_nova.avisos,
      mais_nova.avisos[0][:70] if mais_nova.avisos else "")
recusa("z_mm nao crescente",
       pacote({**base, "camadas": [base["camadas"][1], base["camadas"][0]]},
              imgs, "d.pajm"), "crescer")
recusa("imagem citada que nao existe",
       pacote(base, {"camadas/0001.png": png_a}, "e.pajm"), "nao esta")
recusa("camadas de tamanhos diferentes",
       pacote(base, {"camadas/0001.png": png_a, "camadas/0002.png": png_b},
              "f.pajm"), "mesmo tamanho")
ruim = json.loads(json.dumps(base))
ruim["camadas"][0]["sha256"] = "0" * 64
recusa("sha256 que nao confere", pacote(ruim, imgs, "g.pajm"), "sha256")
sem_px = {k: v for k, v in base.items() if k != "pixel_mm"}
recusa("falta campo obrigatorio", pacote(sem_px, imgs, "h.pajm"), "pixel_mm")
recusa("pixel nao positivo",
       pacote({**base, "pixel_mm": {"x": 0, "y": 0.07}}, imgs, "i.pajm"),
       "maior que zero")
recusa("opacidade fora da faixa",
       pacote({**base, "processo": {"opacidade": 1.5}}, imgs, "j.pajm"),
       "opacidade")
extra = pac.abrir(pacote({**base, "campo_futuro": 1}, imgs, "k.pajm"))
checa("campo desconhecido e ignorado", len(extra) == 2)

print()
print("4. PERFIL DA MAQUINA PARA O CAM")
pc = pac.perfil_para_cam(perfil)
checa("perfil com a grade atual",
      pc["formato"] == "pajm-perfil-maquina"
      and abs(pc["pixel_mm"]["y"] - 25.4 / perfil.cabecote.dpi_y_nativo) < 1e-12
      and pc["bicos"] == 128, f"pixel {pc['pixel_mm']}, faixa {pc['faixa_mm']} mm")
json.dumps(pc)
checa("perfil serializa em JSON", True)

print()
print("=" * 72)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
