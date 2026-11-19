"""Verificacao do fatiador contra geometrias de area conhecida."""
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pajm.core.fatiador import (
    GradeRaster, alturas_camadas,
    rasterizar, segmentos_da_camada, compensar_bicos,
)


def area_fatiada(malha, z, dpi_x, dpi_y, margem=2.0):
    v = np.asarray(malha.vertices, dtype=float)
    f = np.asarray(malha.faces, dtype=np.int64)
    seg = segmentos_da_camada(v, f, z)
    b = malha.bounds
    grade = GradeRaster.para_area(b[0][0] - margem, b[0][1] - margem,
                                  b[1][0] + margem, b[1][1] + margem,
                                  dpi_x, dpi_y)
    bmp = rasterizar(seg, grade)
    return bmp.sum() * grade.passo_x_mm * grade.passo_y_mm, bmp, grade


def checar(nome, obtido, esperado, tol_pct):
    erro = abs(obtido - esperado) / esperado * 100.0
    ok = erro <= tol_pct
    print(f"  [{'OK ' if ok else 'FALHA'}] {nome}: "
          f"{obtido:.3f} mm2 (esperado {esperado:.3f}, erro {erro:.3f}%)")
    return ok


todos_ok = True
print("=" * 68)
print("1. CAIXA 20 x 10 x 5, corte em z=2.5  -> area esperada 200 mm2")
caixa = trimesh.creation.box(extents=[20, 10, 5])
for dpi in (150, 300, 600):
    a, _, _ = area_fatiada(caixa, 0.0, dpi, dpi)
    todos_ok &= checar(f"{dpi} dpi", a, 200.0, 1.0)

print()
print("2. PAR-IMPAR: anel externo r=10, furo r=4 -> area 263.894 mm2")
anel = trimesh.creation.annulus(r_min=4.0, r_max=10.0, height=6.0,
                                sections=512)
esperado_anel = np.pi * (10.0 ** 2 - 4.0 ** 2)
for dpi in (300, 600):
    a, bmp, g = area_fatiada(anel, 0.0, dpi, dpi)
    todos_ok &= checar(f"{dpi} dpi", a, esperado_anel, 1.0)

# O furo tem mesmo que estar vazio no centro
_, bmp, g = area_fatiada(anel, 0.0, 600, 600)
cy, cx = bmp.shape[0] // 2, bmp.shape[1] // 2
furo_vazio = not bmp[cy, cx]
print(f"  [{'OK ' if furo_vazio else 'FALHA'}] centro do furo vazio")
todos_ok &= furo_vazio

print()
print("3. DPI ANISOTROPICO (pixel nao quadrado): 600 x 185")
a, _, _ = area_fatiada(caixa, 0.0, 600, 185)
todos_ok &= checar("600 x 185 dpi", a, 200.0, 1.0)

print()
print("4. DUAS ILHAS SEPARADAS -> 2 x 25 = 50 mm2")
c1 = trimesh.creation.box(extents=[5, 5, 4])
c1.apply_translation([-10, 0, 0])
c2 = trimesh.creation.box(extents=[5, 5, 4])
c2.apply_translation([10, 0, 0])
duas = trimesh.util.concatenate([c1, c2])
a, _, _ = area_fatiada(duas, 0.0, 400, 400)
todos_ok &= checar("duas ilhas", a, 50.0, 1.0)

print()
print("5. MALHA ABERTA: degrada sem travar, e o reparo recupera a area")
from pajm.core.malha import reparar, validar

aberta = trimesh.creation.box(extents=[20, 10, 5])
aberta.update_faces(np.arange(len(aberta.faces)) != 0)   # tira uma face
rel = validar(aberta)
print(f"  validacao: estanque={rel.estanque}, "
      f"arestas abertas={rel.n_arestas_abertas}, "
      f"avisos={len(rel.avisos)}, erros={len(rel.erros)}")
ok = (not rel.estanque) and rel.n_arestas_abertas > 0 and rel.pode_fatiar
print(f"  [{'OK ' if ok else 'FALHA'}] detectada como aberta, mas fatiavel")
todos_ok &= ok

try:
    a_aberta, _, _ = area_fatiada(aberta, 0.0, 300, 300)
    print(f"  [OK ] fatiou sem excecao: {a_aberta:.2f} mm2 de 200 "
          f"(a parede que falta nao tem como ser inventada)")
except Exception as e:
    print(f"  [FALHA] excecao: {e}")
    todos_ok = False

corrigida, acoes = reparar(aberta)
a_corr, _, _ = area_fatiada(corrigida, 0.0, 300, 300)
print(f"  reparo: {'; '.join(acoes) if acoes else 'nada a fazer'}")
todos_ok &= checar("apos reparo", a_corr, 200.0, 1.0)

print()
print("5b. TRES CRUZAMENTOS numa linha nao podem estourar o preenchimento")
grade_t = GradeRaster(x0_mm=0.0, y0_mm=0.0, dpi_x=25.4, dpi_y=25.4,
                      n_colunas=20, n_linhas=3)
# dois segmentos fecham um vao de 0..5; um terceiro fica solto em 10
segs = np.array([[0.0, -1.0, 0.0, 5.0],
                 [5.0, -1.0, 5.0, 5.0],
                 [10.0, -1.0, 10.0, 5.0]])
bmp_t = rasterizar(segs, grade_t)
preenchido = bmp_t[0].sum()
ok = preenchido == 5      # colunas 0..4, e nada depois de 10
print(f"  [{'OK ' if ok else 'FALHA'}] {preenchido} colunas preenchidas "
      f"(esperado 5, sem vazamento ate o fim da linha)")
todos_ok &= ok

print()
print("6. GEOMETRIA DAS VARIANTES contra a folha de dados")
from pajm.profile import Perfil, VARIANTES
from pajm.core.fatiador import compensar_bicos

for var, esp in VARIANTES.items():
    p = Perfil()
    p.cabecote.variante = var
    # O perfil padrao traz o angulo calibrado da PAJM (31 graus); aqui se
    # confere a geometria de projeto de cada variante
    p.cabecote.angulo_montagem_deg = None
    faixa = p.cabecote.largura_faixa_mm
    dpi_y = p.cabecote.dpi_y_nativo
    ok_faixa = abs(faixa - esp["faixa_mm"]) < 0.2
    ok_dpi = abs(dpi_y - esp["dpi_nominal"]) / esp["dpi_nominal"] < 0.03
    print(f"  [{'OK ' if ok_faixa and ok_dpi else 'FALHA'}] {var} "
          f"({esp['cor']}): faixa {faixa:.2f} mm (folha {esp['faixa_mm']}), "
          f"DPI Y {dpi_y:.0f} (folha {esp['dpi_nominal']:.0f})")
    todos_ok &= ok_faixa and ok_dpi

print()
print("6b. COMPENSACAO DE BICOS (cisalhamento + 3 fases)")
for var in ("128/80L", "128/40L"):
    p = Perfil()
    p.cabecote.variante = var
    dpi = VARIANTES[var]["dpi_nominal"]
    for v in (50.0, 200.0):
        c = compensar_bicos(p.cabecote, dpi_x=dpi, velocidade_mm_s=v)
        print(f"  {var} a {v:5.0f} mm/s, {dpi:.0f} dpi: "
              f"cisalhamento {c.cisalhamento_mm:6.2f} mm, "
              f"fase {c.fase_mm*1000:5.1f} um, "
              f"{c.colunas.max():4d} colunas, "
              f"residuo {c.residuo_um:.1f} um")
        ok = c.colunas.max() > 0 and c.residuo_colunas <= 0.5
        todos_ok &= ok
        if not ok:
            print("    [FALHA] compensacao invalida")

# O cisalhamento tem que bater com passo * cos(angulo) * 127
p = Perfil(); p.cabecote.variante = "128/40L"
c = compensar_bicos(p.cabecote, dpi_x=360, velocidade_mm_s=0.0)
teorico = 127 * 0.1371 * np.cos(np.radians(31.0))
todos_ok &= checar("cisalhamento 128/40L (mm)", c.cisalhamento_mm, teorico, 1.0)

print()
print("6c. FISICA DO PROCESSO")
for var in VARIANTES:
    p = Perfil(); p.cabecote.variante = var
    dpi = VARIANTES[var]["dpi_nominal"]
    e = p.espessura_teorica_um(dpi, p.cabecote.dpi_y_nativo)
    vmax = p.velocidade_max_para_dpi(dpi)
    print(f"  {var}: {e:.2f} um por passada a {dpi:.0f} dpi | "
          f"gota {p.cabecote.volume_gota_pl:.0f} pL | "
          f"v max {vmax:.0f} mm/s (f max {p.cabecote.freq_disparo_max_hz:.0f} Hz)")
    todos_ok &= (e is not None and 0.5 < e < 50)

print()
print("7. DESEMPENHO")
esfera = trimesh.creation.icosphere(subdivisions=5, radius=25.0)
print(f"  malha: {len(esfera.faces)} faces")
v = np.asarray(esfera.vertices, float)
f = np.asarray(esfera.faces, np.int64)
zs = alturas_camadas(-25.0, 25.0, 0.020)
print(f"  camadas a 20 um: {len(zs)}")
grade = GradeRaster.para_area(-27, -27, 27, 27, 600, 185)
print(f"  bitmap: {grade.n_linhas} x {grade.n_colunas}")
t0 = time.perf_counter()
n = 200
for z in zs[len(zs) // 2: len(zs) // 2 + n]:
    rasterizar(segmentos_da_camada(v, f, z), grade)
dt = time.perf_counter() - t0
print(f"  {n} camadas em {dt:.2f} s  ->  {dt / n * 1000:.1f} ms/camada")
print(f"  extrapolando: {len(zs)} camadas em {dt / n * len(zs):.0f} s")

print()
print("=" * 68)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
