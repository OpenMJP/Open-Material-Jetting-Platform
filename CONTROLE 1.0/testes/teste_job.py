"""Fluxo completo: STL em disco -> validacao -> job -> camadas -> vetor SPI."""
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pajm.core import codificador as cod
from pajm.core import job as cjob
from pajm.core import malha as cmalha
from pajm.profile import Perfil

todos_ok = True
print("=" * 70)

# ---- 1. STL de verdade, escrito e lido do disco -----------------------
tmp = Path(tempfile.gettempdir()) / "pajm_teste.stl"
peca = trimesh.creation.box(extents=[12, 6, 3])
furo = trimesh.creation.cylinder(radius=1.5, height=10, sections=64)
# Sem um motor booleano (manifold3d) instalado, algumas versoes do trimesh
# devolvem uma malha VAZIA em vez de levantar excecao, e o teste exportava
# um STL de 0 faces. So aceita o furo se o resultado for uma peca de verdade.
tem_furo = False
try:
    com_furo = trimesh.boolean.difference([peca, furo])
    if len(com_furo.faces) and com_furo.is_watertight:
        peca, tem_furo = com_furo, True
except Exception:
    pass
peca.export(tmp)
print(f"1. STL escrito em disco ({tmp.stat().st_size} bytes, "
      f"furo passante: {tem_furo})")

m = cmalha.carregar(tmp)
rel = cmalha.validar(m)
print(f"   carregado: {rel.n_faces} faces, estanque={rel.estanque}, "
      f"dim={tuple(round(d,2) for d in rel.dimensoes_mm)}")
todos_ok &= rel.pode_fatiar

# ---- 2. Assentar e preparar o job -------------------------------------
m = cmalha.assentar(m)
p = Perfil()          # variante 128/40 (cinza), como informado
print()
print(f"2. Perfil: {p.cabecote.variante}, DPI Y {p.cabecote.dpi_y_nativo:.0f}, "
      f"faixa {p.cabecote.largura_faixa_mm:.2f} mm")

r = cjob.preparar(p, m, dpi_x=360, velocidade_mm_s=50, espessura_camada_um=8.0)
print(f"   camadas={r.n_camadas}  bitmap={r.grade.n_linhas}x{r.grade.n_colunas}"
      f"  faixas em Y={r.n_faixas}")
print(f"   disparo {r.freq_disparo_hz:.0f} Hz de {r.freq_max_hz:.0f} Hz max")
print(f"   deposicao {r.espessura_por_passada_um:.2f} um/passada -> "
      f"{r.passadas_necessarias:.2f} passadas")
print(f"   compensacao {int(r.compensacao.colunas.max())} colunas, "
      f"residuo {r.compensacao.residuo_um:.0f} um")
for a in r.avisos:
    print(f"   aviso: {a}")
for e in r.erros:
    print(f"   ERRO: {e}")
todos_ok &= r.viavel

# ---- 3. Limite de frequencia tem que barrar --------------------------
print()
print("3. O limite de frequencia do cabecote tem que barrar combinacao invalida")
r_mau = cjob.preparar(p, m, dpi_x=360, velocidade_mm_s=1500,
                      espessura_camada_um=8.0)
ok = not r_mau.viavel and any("disparo" in e for e in r_mau.erros)
print(f"   [{'OK ' if ok else 'FALHA'}] 1500 mm/s a 360 dpi recusado")
if r_mau.erros:
    print(f"        {r_mau.erros[0]}")
todos_ok &= ok

# ---- 4. Fatiar de verdade ---------------------------------------------
print()
print("4. Fatiamento completo")
t0 = time.perf_counter()
vistos = []
camadas = cjob.fatiar(m, r, progresso=lambda i, n: vistos.append(i) or True)
dt = time.perf_counter() - t0
print(f"   {len(camadas)} camadas em {dt:.2f} s "
      f"({dt/max(len(camadas),1)*1000:.1f} ms/camada)")
print(f"   progresso reportado {len(vistos)} vezes")
print(f"   total de gotas: {camadas.n_gotas:,}")
ok = len(camadas) == r.n_camadas and len(vistos) == r.n_camadas
print(f"   [{'OK ' if ok else 'FALHA'}] contagem de camadas confere")
todos_ok &= ok

# A camada do meio tem que ter area proxima da secao da peca
meio = camadas.bitmap(len(camadas) // 2)
area = meio.sum() * camadas.grade.passo_x_mm * camadas.grade.passo_y_mm
esperada = 12 * 6 - (np.pi * 1.5 ** 2 if tem_furo else 0)
erro = abs(area - esperada) / esperada * 100
ok = erro < 2.0
print(f"   [{'OK ' if ok else 'FALHA'}] area da camada do meio {area:.2f} mm2 "
      f"(esperada {esperada:.2f}, erro {erro:.2f}%)")
todos_ok &= ok

# ---- 5. Cancelamento ---------------------------------------------------
print()
print("5. Cancelamento no meio do fatiamento")
parciais = cjob.fatiar(m, r, progresso=lambda i, n: i < 10)
ok = len(parciais) == 10
print(f"   [{'OK ' if ok else 'FALHA'}] parou em {len(parciais)} camadas")
todos_ok &= ok

# ---- 6. Faixas e vetor de jateamento -----------------------------------
print()
print("6. Divisao em faixas e vetor de jateamento")
bmp = camadas.bitmap(len(camadas) // 2)
faixas = cod.dividir_em_faixas(bmp, p.cabecote.n_bicos)
print(f"   {len(faixas)} faixa(s) de {p.cabecote.n_bicos} bicos")
ok = all(f.shape[0] == 128 for f in faixas)
print(f"   [{'OK ' if ok else 'FALHA'}] toda faixa tem 128 linhas")
todos_ok &= ok

mat = cod.montar_matriz(faixas[0], r.dpi_x, r.dpi_y)
print(f"   matriz: {mat.n_w} posicoes W x {mat.n_bicos} bicos, "
      f"passo W {mat.passo_w_mm*1000:.1f} um, "
      f"{mat.n_posicoes_ativas} posicoes ativas")

# ---- 6b. A compensacao pertence a codificacao, nao ao corte -----------
print()
print("6b. Compensacao aplicada na codificacao, nao no fatiamento")

# A camada guardada tem que ser a geometria REAL: uma caixa cortada no meio
# da um retangulo cheio, sem listras diagonais.
plana = trimesh.creation.box(extents=[10, 5, 4])
r_p = cjob.preparar(p, plana, dpi_x=360, velocidade_mm_s=50,
                    espessura_camada_um=100.0)
cam_p = cjob.fatiar(plana, r_p)
b = cam_p.bitmap(len(cam_p) // 2)
# linhas nao vazias tem que comecar e terminar na MESMA coluna
ativas = [np.flatnonzero(l) for l in b if l.any()]
inicios = {int(a[0]) for a in ativas}
fins = {int(a[-1]) for a in ativas}
ok = len(inicios) == 1 and len(fins) == 1
print(f"   secao da caixa: {len(ativas)} linhas ativas, "
      f"{len(inicios)} inicio(s) distinto(s), {len(fins)} fim(ns)")
print(f"   [{'OK ' if ok else 'FALHA'}] camada guardada e retangulo reto "
      f"(sem cisalhamento embutido)")
todos_ok &= ok

# Ja a matriz de jateamento TEM que sair cisalhada.
f0 = cod.dividir_em_faixas(b, p.cabecote.n_bicos)[0]
m_sem = cod.montar_matriz(f0, r_p.dpi_x, r_p.dpi_y)
m_com = cod.montar_matriz(f0, r_p.dpi_x, r_p.dpi_y, r_p.compensacao)
alarg = m_com.n_w - m_sem.n_w
esperado = int(r_p.compensacao.colunas.max())
ok = alarg == esperado
print(f"   matriz sem compensacao: {m_sem.n_w} W | "
      f"com compensacao: {m_com.n_w} W (+{alarg})")
print(f"   [{'OK ' if ok else 'FALHA'}] alargamento = deslocamento maximo "
      f"({esperado} colunas)")
todos_ok &= ok

# Mesmo numero de gotas: a compensacao move dado, nao cria nem destroi.
ok = m_com.n_gotas == m_sem.n_gotas
print(f"   [{'OK ' if ok else 'FALHA'}] gotas preservadas "
      f"({m_sem.n_gotas} -> {m_com.n_gotas})")
todos_ok &= ok

# Cada bico deslocado exatamente do previsto
d = r_p.compensacao.colunas
bico = int(np.argmax(f0.any(axis=1)))
col0_sem = int(np.flatnonzero(m_sem.logica[:, bico])[0])
col0_com = int(np.flatnonzero(m_com.logica[:, bico])[0])
ok = (col0_com - col0_sem) == int(d[bico])
print(f"   [{'OK ' if ok else 'FALHA'}] bico {bico} adiantado em "
      f"{col0_com - col0_sem} W (previsto {int(d[bico])})")
todos_ok &= ok

# ---- 6c. As duas vistas da previa --------------------------------------
print()
print("6c. Vistas da previa: geometria contra dado de jateamento")

geo = b                                    # o que a previa mostra por padrao
jat = cod.camada_como_jateada(geo, p.cabecote.n_bicos, r_p.compensacao)

ok = jat.shape[0] == geo.shape[0]
print(f"   [{'OK ' if ok else 'FALHA'}] mesma altura "
      f"({geo.shape[0]} linhas)")
todos_ok &= ok

alarg = jat.shape[1] - geo.shape[1]
esperado = int(r_p.compensacao.colunas.max())
ok = alarg == esperado
print(f"   [{'OK ' if ok else 'FALHA'}] vista de jateamento mais larga em "
      f"{alarg} colunas (previsto {esperado})")
todos_ok &= ok

ok = int(jat.sum()) == int(geo.sum())
print(f"   [{'OK ' if ok else 'FALHA'}] mesma contagem de gotas nas duas "
      f"vistas ({int(geo.sum())})")
todos_ok &= ok

# A geometria e reta; o dado de jateamento nao pode ser.
def bordas(bmp):
    a = [np.flatnonzero(l) for l in bmp if l.any()]
    return len({int(x[0]) for x in a}), len({int(x[-1]) for x in a})

ig, fg = bordas(geo)
ij, fj = bordas(jat)
ok = (ig, fg) == (1, 1) and ij > 1
print(f"   [{'OK ' if ok else 'FALHA'}] geometria reta ({ig} inicio) e "
      f"jateamento cisalhado ({ij} inicios distintos)")
todos_ok &= ok

# O cisalhamento repete a cada faixa de 128 bicos, porque toda faixa usa os
# mesmos bicos. Numa camada mais alta que o cabecote isso tem que aparecer.
alta = np.zeros((int(p.cabecote.n_bicos * 2.5), 60), dtype=bool)
alta[:, 10:50] = True
ja = cod.camada_como_jateada(alta, p.cabecote.n_bicos, r_p.compensacao)
lin0 = np.flatnonzero(ja[0])
lin128 = np.flatnonzero(ja[p.cabecote.n_bicos])
ok = len(lin0) and len(lin128) and lin0[0] == lin128[0]
print(f"   [{'OK ' if ok else 'FALHA'}] padrao se repete a cada 128 linhas "
      f"(linha 0 e linha 128 comecam na mesma coluna)")
todos_ok &= ok

# Trocar de vista nao pode mexer no dado guardado.
ok = np.array_equal(geo, cam_p.bitmap(len(cam_p) // 2))
print(f"   [{'OK ' if ok else 'FALHA'}] montar a vista nao alterou a camada "
      f"gravada")
todos_ok &= ok

cam_p.fechar()

# Sem o sentido definido tem que recusar. O perfil padrao ja vem com o
# sentido medido na maquina (+1), entao o teste o apaga de proposito.
p.cabecote.sentido_indice = None
try:
    cod.empacotar(mat, p.cabecote)
    print("   [FALHA] empacotou sem o sentido do indice definido")
    todos_ok = False
except cod.FaltaDefinicao as e:
    print(f"   [OK ] recusou empacotar: {e.faltando[0][:60]}...")

# Com o sentido definido, empacota
p.cabecote.sentido_indice = 1
b = cod.empacotar(mat, p.cabecote)
print(f"   empacotado: {b.shape[0]} posicoes x {b.shape[1]} bytes "
      f"= {b.nbytes/1024:.1f} KB por faixa")
ok = b.shape == (mat.n_w, 16)
print(f"   [{'OK ' if ok else 'FALHA'}] 16 bytes por posicao W")
todos_ok &= ok

# Ida e volta: desempacotar tem que devolver a matriz logica
volta = np.unpackbits(b, axis=1, bitorder="big")[:, ::-1]
ok = np.array_equal(volta.astype(bool), mat.logica)
print(f"   [{'OK ' if ok else 'FALHA'}] desempacotar devolve a matriz original")
todos_ok &= ok

# ---- 7. Memoria --------------------------------------------------------
print()
print("7. Memoria")
bruto = len(camadas) * r.grade.n_linhas * r.grade.n_colunas

print(f"   em disco: {camadas.bytes_em_disco/1e6:.1f} MB "
      f"(em RAM seria {bruto/1e6:.1f} MB de bool)")

tmp.unlink(missing_ok=True)
print()
print("=" * 70)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
