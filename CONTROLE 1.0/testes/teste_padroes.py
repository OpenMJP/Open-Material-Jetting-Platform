"""Padroes de calibracao e mosaico de camadas lado a lado."""
import math
import sys
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pajm.core import codificador as cod
from pajm.core import job as cjob
from pajm.core import mosaico as mos
from pajm.core import padroes as pad
from pajm.profile import Perfil

todos_ok = True


def checa(nome, cond, extra=""):
    global todos_ok
    todos_ok &= bool(cond)
    print(f"  [{'OK ' if cond else 'FALHA'}] {nome}" + (f"  {extra}" if extra else ""))


print("=" * 72)
print("1. VERIFICACAO DE BICOS (ESCADA)")
p = pad.teste_de_bicos(n_bicos=128, dpi_x=360, dpi_y=360, segmentos=8,
                       comprimento_segmento_mm=2.0)
print(f"   {p.largura_mm:.1f} x {p.altura_mm:.1f} mm, {p.n_gotas} gotas")

# A LINHA DO BITMAP E O BICO: numa passada so, o bitmap tem n_bicos linhas
checa("uma passada so, com exatamente 128 linhas",
      not p.passadas and p.bitmap.shape[0] == 128)
checa("todo bico jateia o seu segmento",
      all(p.bitmap[b].any() for b in range(128)))

# Segmento k da linha L e o bico 8L + k: as colunas de cada bico tem que
# ser so as do seu segmento, e segmentos da mesma linha nao se sobrepoem
larg = p.bitmap.shape[1] // 8
ok_seg = all(
    p.bitmap[b, (b % 8) * larg:(b % 8 + 1) * larg].all()
    and p.bitmap[b].sum() == larg
    for b in range(128))
checa("o bico b ocupa so o segmento b mod 8", ok_seg)
checa("16 linhas de 8 segmentos", p.referencia["linhas"] == 16)

try:
    pad.teste_de_bicos(128, 360, 360, segmentos=7)
    checa("segmentos que nao dividem 128 recusados", False)
except ValueError:
    checa("segmentos que nao dividem 128 recusados", True)

print()
print("2. ALINHAMENTO")
a = pad.teste_de_alinhamento(dpi_x=360, dpi_y=360, n_opcoes=7,
                             passo_correcao_um=20.0, eixo="x")
print(f"   {a.largura_mm:.1f} x {a.altura_mm:.1f} mm, {a.n_gotas} gotas")
d = a.referencia["deslocamentos (um)"]
checa("7 opcoes centradas no zero", len(d) == 7 and 0.0 in d, str(d))
checa("opcao do meio e a de deslocamento zero",
      a.referencia["opcao de deslocamento zero"] == 4)
checa("conversao de opcao para correcao",
      pad.correcao_escolhida(4, 7, 20.0) == 0.0
      and pad.correcao_escolhida(7, 7, 20.0) == 60.0
      and pad.correcao_escolhida(1, 7, 20.0) == -60.0)

try:
    pad.teste_de_alinhamento(360, 360, n_opcoes=6)
    checa("numero par de opcoes recusado", False)
except ValueError:
    checa("numero par de opcoes recusado (nao haveria meio)", True)

# Em Y o deslocamento vem do eixo, entre passadas
try:
    pad.teste_de_alinhamento(360, 360, eixo="y")
    checa("o alinhamento antigo em Y e recusado", False)
except ValueError:
    checa("o alinhamento antigo em Y e recusado", True)

passo_faixa = 128 * 25.4 / 360
# Geometria com os blocos cheios; o meio-tom tem testes proprios abaixo
ay = pad.teste_de_alinhamento_y(128, 360, 360, passo_faixa, n_opcoes=7,
                                passo_correcao_um=25.0, opacidade=1.0)
ys = [q.y_mm for q in ay.passadas]
checa("1 passada de referencia + 1 por opcao", len(ay.passadas) == 8)
checa("referencia em Y = 0, opcao do meio exatamente uma faixa abaixo",
      ys[0] == 0.0 and abs(ys[4] - passo_faixa) < 1e-9,
      f"{[round(y, 3) for y in ys]}")
checa("opcoes espacadas de 25 um",
      all(abs((b - a) - 0.025) < 1e-9 for a, b in zip(ys[1:], ys[2:])))
checa("toda passada tem exatamente 128 linhas",
      all(q.bitmap.shape[0] == 128 for q in ay.passadas))
ref = ay.passadas[0].bitmap
checa("a faixa de cima usa os 128 bicos", ref.any(axis=1).all())
b4 = ay.passadas[4].bitmap
checa("a faixa de baixo usa os 128 bicos", b4.any(axis=1).all())
checa("emenda livre: primeiro bico da de baixo e ultimo da de cima cheios",
      all(q.bitmap[0].sum() == q.bitmap[127].sum() == ref[127].sum() / 7
          for q in ay.passadas[1:]))
# entalhes: buracos no meio do bloco de baixo
lin = 64
entalhes = []
for i, q in enumerate(ay.passadas[1:]):
    cols = np.where(q.bitmap.any(axis=0))[0]
    trecho = q.bitmap[lin, cols[0]:cols[-1] + 1].astype(int)
    entalhes.append(int((np.diff(trecho) == -1).sum()))
checa("a opcao i tem i entalhes", entalhes == list(range(1, 8)),
      str(entalhes))
try:
    pad.teste_de_alinhamento_y(128, 360, 360, passo_faixa, n_opcoes=21,
                               bloco_largura_mm=3.0)
    checa("bloco estreito demais para os entalhes recusado", False)
except ValueError:
    checa("bloco estreito demais para os entalhes recusado", True)

from pajm.core.imagem import mascara_de_opacidade
m0 = mascara_de_opacidade((64, 64), 0.5, ordem=0)
m1 = mascara_de_opacidade((64, 64), 0.5, ordem=1)
checa("meio-tom: duas impressoes a 50% pegam gotas complementares",
      abs(m0.mean() - 0.5) < 1e-9 and not (m0 & m1).any()
      and (m0 | m1).all())
ms = [mascara_de_opacidade((40, 40), 0.25, ordem=k) for k in range(4)]
checa("meio-tom: 4 camadas a 25% cobrem tudo, sem gota repetida",
      np.sum(ms, axis=0).max() == 1 and np.logical_or.reduce(ms).all())
checa("meio-tom: 100% e a mascara cheia",
      mascara_de_opacidade((9, 9), 1.0, ordem=3).all())
a50 = pad.teste_de_alinhamento_y(128, 360, 360, passo_faixa, n_opcoes=7)
pa, pb = a50.passadas[0].bitmap, a50.passadas[4].bitmap
usadas = np.where(pb.any(axis=0))[0]
cols = np.arange(usadas[0], usadas[-1] + 1)     # o bloco da opcao 4 inteiro
dens_a = pa[:, cols].mean()
dens_b = pb[:20, cols].mean()
checa("alinhamento em Y sai a 50% por padrao, nas duas faixas",
      abs(dens_a - 0.5) < 0.02 and abs(dens_b - 0.5) < 0.02,
      f"{dens_a:.2f} e {dens_b:.2f}")
# Faixa B k linhas acima (sobreposicao de k bicos): as linhas comuns ficam
# com as gotas das duas, bem mais escuras que o resto, qualquer que seja k
sobre = [float((pa[128 - k:, cols] | pb[:k, cols]).mean())
         for k in range(1, 9)]
checa("onde as faixas se sobrepoem fica escuro, para qualquer sobreposicao",
      min(sobre) > 0.9, f"{min(sobre):.0%} contra 50%")
from pajm.core.imagem import mascara_de_opacidade_por_coluna as mpc
c30 = mpc((3, 640), 0.3)
checa("meio-tom por coluna: fracao certa e igual em todas as linhas",
      abs(c30.mean() - 0.3) < 0.01 and (c30 == c30[0]).all(),
      f"{c30.mean():.3f}")

print()
print("2b. ANGULO DO CABECOTE")
from pajm.profile import Cabecote
cab = Cabecote()
dpi_y = cab.dpi_y_nativo
px = 25.4 / 339.0
fase = (np.arange(128) % 3) * 50.0 * 60e-6
av = pad.teste_de_angulo(128, 339.0, dpi_y, passo_faixa_mm=128 * 25.4 / dpi_y,
                         passo_bico_mm=cab.passo_bico_mm,
                         angulo_base_deg=31.0, n_opcoes=9, passo_deg=0.5,
                         fase_mm=fase)
checa("visual: duas faixas inteiras, a segunda uma faixa abaixo",
      len(av.passadas) == 2
      and all(q.bitmap.shape[0] == 128 for q in av.passadas)
      and abs(av.passadas[1].y_mm - 128 * 25.4 / dpi_y) < 1e-9)
checa("opcao do meio = angulo atual",
      pad.angulo_da_opcao(5, 9, 0.5, 31.0) == 31.0
      and pad.angulo_da_opcao(7, 9, 0.5, 31.0) == 32.0)


def inicios_das_opcoes(bm, n_opcoes=9, n_linhas=3):
    """Coluna da 1a linha de cada opcao, bico a bico (passada sem marcas)."""
    out = np.zeros((bm.shape[0], n_opcoes), dtype=np.int64)
    for i in range(bm.shape[0]):
        r = bm[i].astype(int)
        ini = np.where(np.diff(np.r_[0, r]) == 1)[0]
        out[i] = ini[::n_linhas][:n_opcoes]
    return out


# Simula a maquina com o angulo REAL diferente do perfil. O envio compensa
# com o angulo do perfil (31); o bico i fica fisicamente adiantado de
# i*passo*cos(real) e dispara com a fase dele.
base = np.rint((np.arange(128) * cab.passo_bico_mm
                * math.cos(math.radians(31.0)) + fase) / px)
cols = inicios_das_opcoes(av.passadas[0].bitmap)


def degraus(real):
    a_r = cab.passo_bico_mm * math.cos(math.radians(real))
    x = ((cols + base[:, None]) * px
         - (np.arange(128) * a_r + fase)[:, None])
    # degrau na emenda: bico 128 da faixa de cima contra bico 1 da de baixo
    return np.abs(x[127] - x[0]), np.ptp(x, axis=0)


for real, certa in ((31.0, 5), (32.0, 7), (29.5, 2)):
    d, espalho = degraus(real)
    melhor = int(np.argmin(d)) + 1
    checa(f"angulo real {real}: a opcao sem degrau e a {certa}",
          melhor == certa and d[certa - 1] <= px,
          f"degraus (um): {[int(v * 1000) for v in d]}")
    checa(f"angulo real {real}: a opcao certa sai reta dentro da faixa",
          espalho[certa - 1] <= 1.01 * px,
          f"{espalho[certa - 1] * 1000:.0f} um")
d, _ = degraus(32.0)
checa("opcoes vizinhas a certa ja mostram degrau de mais de meia coluna",
      d[5] > px / 2 and d[7] > px / 2,
      f"{d[5] * 1000:.0f} e {d[7] * 1000:.0f} um")
bmb = av.passadas[1].bitmap
r = bmb[100].astype(int)
runs = len(np.where(np.diff(np.r_[0, r]) == 1)[0])
checa("marcas so na faixa de baixo, longe da emenda: 3 linhas + k marcas",
      runs == 9 * 3 + sum(range(1, 10))
      and len(np.where(np.diff(np.r_[0, bmb[10].astype(int)]) == 1)[0])
      == 27, f"{runs} tracos na linha 100")
try:
    pad.teste_de_angulo(128, 339.0, dpi_y, 9.0, cab.passo_bico_mm, 31.0,
                        n_opcoes=8)
    checa("numero par de opcoes recusado", False)
except ValueError:
    checa("numero par de opcoes recusado", True)

print("   ajuste fino por medida")
ang = pad.teste_de_angulo_distancia(128, 360, 360, bico_a=3, bico_b=120,
                                    comprimento_mm=30.0)
bm = ang.bitmap
cheias = np.where(bm[:, :10].any(axis=1))[0]
checa("linhas horizontais so nos bicos A e B", list(cheias) == [2, 119],
      str(list(cheias)))
checa("uma passada so, de 128 linhas",
      not ang.passadas and bm.shape[0] == 128)
cols_v = np.where(bm[60])[0]
checa("verticais vao do bico A ao B e nao alem",
      len(cols_v) > 0 and bm[2:120, cols_v].all() and
      not bm[:2, cols_v].any() and not bm[120:, cols_v].any())
from pajm.profile import Cabecote
cab = Cabecote()
d31 = 127 * cab.passo_bico_mm * math.sin(math.radians(31.0))
checa("distancia de 31 graus volta 31 graus",
      abs(pad.angulo_medido(d31, 1, 128, cab.passo_bico_mm) - 31.0) < 1e-9)
d32 = 127 * cab.passo_bico_mm * math.sin(math.radians(32.0))
checa("1 grau muda a distancia em ~0,26 mm", abs((d32 - d31) - 0.26) < 0.01,
      f"{(d32 - d31) * 1000:.0f} um")
try:
    pad.angulo_medido(20.0, 1, 128, cab.passo_bico_mm)
    checa("distancia impossivel recusada", False)
except ValueError:
    checa("distancia impossivel recusada", True)

print()
print("3. GRADE E LINHAS VERTICAIS")
g = pad.grade_dimensional(360, 360, largura_mm=50, altura_mm=30,
                          espacamento_mm=10, espessura_mm=0.3)
col_cheias = np.where(g.bitmap.all(axis=0))[0]
inicios = [c for c in col_cheias if c - 1 not in col_cheias]
px = 25.4 / 360
checa("6 linhas verticais a cada 10 mm",
      len(inicios) == 6
      and all(abs((b - a) * px - 10.0) < px for a, b in
              zip(inicios, inicios[1:])), f"{len(inicios)} linhas")
lin_cheias = np.where(g.bitmap.all(axis=1))[0]
checa("4 linhas horizontais",
      len([l for l in lin_cheias if l - 1 not in lin_cheias]) == 4)

lv = pad.linhas_verticais(128, 360, 360, passo_faixa, faixas=4)
checa("4 faixas de 128 linhas", lv.bitmap.shape[0] == 512)
checa("marcas no primeiro bico das faixas 2 a 4",
      all(lv.bitmap[f * 128, 0] for f in (1, 2, 3))
      and not lv.bitmap[1:128, 0].any())

print()
print("4. PADROES PASSAM PELO MESMO CAMINHO DE UMA PECA")
perf = Perfil()
perf.cabecote.sentido_indice = 1
from pajm.core.fatiador import compensar_bicos
comp_b = compensar_bicos(perf.cabecote, 360, 50.0)
faixas = cod.dividir_em_faixas(p.bitmap, perf.cabecote.n_bicos)
mat = cod.montar_matriz(faixas[0], 360, 360, comp_b)
b = cod.empacotar(mat, perf.cabecote)
checa("teste de bicos vira vetor de 16 bytes", b.shape[1] == 16,
      f"{b.shape[0]} posicoes W")

uns = pad.padrao_para_unidades(ay, 20.0, 30.0, 0.0)
checa("alinhamento em Y vira uma unidade por passada, no Y dela",
      len(uns) == 8 and abs(uns[4].grade.y0_mm - (30.0 + passo_faixa)) < 1e-9)
ug = pad.padrao_para_unidades(g, 20.0, 30.0, 0.0)
checa("a grade vai inteira, para o envio dividir em faixas",
      len(ug) == 1 and ug[0].bitmap.shape == g.bitmap.shape)
print()
print("5. MOSAICO DE CAMADAS LADO A LADO")
peca = trimesh.creation.box(extents=[18, 12, 6])
pr = Perfil()
r = cjob.preparar(pr, peca, dpi_x=360, velocidade_mm_s=50,
                  espessura_camada_um=200.0)
cam = cjob.fatiar(peca, r)
print(f"   peca fatiada: {len(cam)} camadas de "
      f"{cam.grade.largura_mm:.1f} x {cam.grade.altura_mm:.1f} mm")

idx = mos.selecionar_camadas(len(cam), primeira=1, ultima=len(cam))
checa("intervalo 1..N devolve todas", len(idx) == len(cam))
checa("intervalo e 1-based e inclusivo",
      mos.selecionar_camadas(30, 5, 7) == [4, 5, 6])
checa("intervalo com passo",
      mos.selecionar_camadas(30, 1, 10, 3) == [0, 3, 6, 9])
checa("intervalo fora da faixa e aparado",
      mos.selecionar_camadas(10, 8, 999) == [7, 8, 9])

plano = mos.planejar(cam, idx, mesa_x_mm=200.0, mesa_y_mm=200.0,
                     espacamento_mm=2.0)
print(f"   arranjo {plano.n_colunas} x {plano.n_linhas} = "
      f"{plano.por_chapa} por chapa, {plano.n_chapas} chapa(s)")
checa("arranjo cabe na mesa",
      plano.viavel
      and plano.n_colunas * plano.largura_celula_mm <= 200
      and plano.n_linhas * plano.altura_celula_mm <= 200)

chapas = [mos.montar_chapa(cam, plano, i) for i in range(plano.n_chapas)]
total = sum(c.n_camadas for c in chapas)
checa("toda camada selecionada entrou em alguma chapa",
      total == len(idx), f"{total} de {len(idx)}")

# As gotas da chapa tem que bater com a soma das camadas que ela contem
ch = chapas[0]
esperado = sum(int(cam.bitmap(c.indice_camada).sum()) for c in ch.celulas)
checa("gotas da chapa = soma das camadas nela",
      int(ch.bitmap.sum()) == esperado,
      f"{int(ch.bitmap.sum())} de {esperado}")

# Nenhuma celula pode encostar na outra
ok_sep = True
for c in ch.celulas:
    if c.coluna > 0:
        col0 = int(round(c.x0_mm / ch.grade.passo_x_mm))
        faixa = ch.bitmap[:, col0 - 2:col0]
        if faixa.any():
            ok_sep = False
checa("ha espaco em branco entre as celulas", ok_sep)

print(f"   chapa 1: {ch.grade.largura_mm:.1f} x {ch.grade.altura_mm:.1f} mm, "
      f"{ch.n_camadas} camadas")

# Mesa pequena demais tem que recusar, nao truncar
mau = mos.planejar(cam, idx, mesa_x_mm=10.0, mesa_y_mm=10.0)
checa("mesa pequena demais e recusada com explicacao",
      not mau.viavel and "nao cabe" in mau.erros[0], mau.erros[0][:60])

# Sem mesa definida tambem
sem = mos.planejar(cam, idx, mesa_x_mm=None, mesa_y_mm=None)
checa("sem o tamanho da mesa, recusa", not sem.viavel)

rel = mos.relatorio(plano, chapas)
checa("relatorio lista todas as celulas",
      rel.count("L1C1") >= 1 and str(len(idx)) in rel)

cam.fechar()

print()
print("6. QUANTIZACAO DO DPI EM X PELO MICROPASSO")
pf = Perfil()
pf.movimento.passos_por_mm_x = None        # o perfil nasce com 80 medidos
checa("sem micropassos por mm, nao da para derivar DPI",
      pf.dpi_x_de_micropassos(6) is None)

pf.movimento.passos_por_mm_x = 80.0        # GT2 2mm, polia 20T, 16 micropassos
tabela = pf.dpis_alcancaveis(10)
print("   micropassos -> DPI:", ", ".join(
    f"{k}:{d:.0f}" for k, d in tabela[3:9]))
checa("6 micropassos dao ~339 dpi", abs(tabela[5][1] - 338.67) < 0.1)
checa("5 micropassos dao ~406 dpi", abs(tabela[4][1] - 406.4) < 0.1)
checa("360 dpi nao e alcancavel: cai em 6 micropassos",
      pf.micropassos_para_dpi(360) == 6)
checa("dpi efetivo usa o micropasso quando da",
      abs(pf.dpi_x_efetivo - 338.67) < 0.1, f"{pf.dpi_x_efetivo:.2f}")

print()
print("=" * 72)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
