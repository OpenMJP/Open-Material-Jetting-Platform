"""Imagens 2D como fonte de jateamento: carga, meio-tom e grade."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from pajm.core import imagem as im
from pajm.core import codificador as cod
from pajm.core.fatiador import compensar_bicos
from pajm.profile import Perfil

todos_ok = True


def checa(nome, cond, extra=""):
    global todos_ok
    todos_ok &= bool(cond)
    print(f"  [{'OK ' if cond else 'FALHA'}] {nome}" + (f"  {extra}" if extra else ""))


TMP = Path(tempfile.gettempdir())


def gravar_png(arr_bool, caminho, dpi=None):
    """Grava um PNG preto e branco a partir de um array booleano."""
    from PySide6.QtGui import QImage
    h, w = arr_bool.shape
    buf = np.ascontiguousarray(
        np.where(arr_bool, 0, 255).astype(np.uint8))
    img = QImage(buf.data, w, h, w, QImage.Format.Format_Grayscale8)
    if dpi:
        img.setDotsPerMeterX(int(dpi / 0.0254))
        img.setDotsPerMeterY(int(dpi / 0.0254))
    img.copy().save(str(caminho))
    return caminho


print("=" * 72)
print("1. FORMATOS ACEITOS")
f = im.formatos_suportados()
print(f"   {len(f)} formatos: {', '.join(f)}")
checa("PNG aceito", ".png" in f)
checa("PDF aceito quando o pymupdf esta presente",
      (".pdf" in f) == im.PDF_DISPONIVEL,
      f"pymupdf disponivel: {im.PDF_DISPONIVEL}")

print()
print("2. CARGA DE PNG")
alvo = np.zeros((40, 60), dtype=bool)
alvo[10:30, 15:45] = True              # retangulo preto no meio
p = gravar_png(alvo, TMP / "pajm_teste.png")
img = im.carregar(p)
checa("dimensoes preservadas",
      (img.largura_px, img.altura_px) == (60, 40),
      f"{img.largura_px} x {img.altura_px} px")
checa("o preto virou escuro (0) e o branco claro (1)",
      img.cinza.min() < 0.01 and img.cinza.max() > 0.99,
      f"min={img.cinza.min():.2f} max={img.cinza.max():.2f}")

p2 = gravar_png(alvo, TMP / "pajm_teste_dpi.png", dpi=300)
img2 = im.carregar(p2)
nat = img2.tamanho_natural_mm()
checa("DPI declarado vira tamanho sugerido",
      nat is not None and abs(nat[0] - 60 * 25.4 / 300) < 0.1,
      f"{nat[0]:.2f} x {nat[1]:.2f} mm" if nat else "sem DPI")
# O Qt grava 100 dpi em todo PNG que salva, entao o metadado de um PNG e
# quase sempre um valor padrao sem significado. Por isso ele e SUGESTAO.
checa("tamanho de PNG e marcado como nao confiavel",
      not img2.tamanho_confiavel and not img.tamanho_confiavel)

print()
print("3. ESCURO VIRA GOTA, E NAO O CONTRARIO")
# Um desenho preto no branco tem que sair desenhado, nao em negativo.
o = im.Opcoes(largura_mm=60.0, altura_mm=40.0, modo="limiar")
bmp, grade = im.para_bitmap(img, 25.4, 25.4, o)  # 1 px por mm
checa("area jateada bate com o retangulo preto",
      abs(int(bmp.sum()) - 20 * 30) <= 60,
      f"{int(bmp.sum())} gotas, esperado ~600")
checa("o canto branco nao jateia", not bmp[0, 0])
checa("o centro preto jateia", bmp[bmp.shape[0] // 2, bmp.shape[1] // 2])

o_inv = im.Opcoes(largura_mm=60.0, altura_mm=40.0, modo="limiar",
                  inverter=True)
bmp_inv, _ = im.para_bitmap(img, 25.4, 25.4, o_inv)
checa("inverter troca o que jateia",
      int(bmp_inv.sum()) == bmp.size - int(bmp.sum()),
      f"{int(bmp_inv.sum())} contra {int(bmp.sum())}")

print()
print("4. A GRADE NAO E QUADRADA")
# O caso real: 339 dpi em X (pitch 3) contra 360 dpi em Y (cabecote a 31 graus)
o = im.Opcoes(largura_mm=50.0, altura_mm=25.0, modo="limiar")
bmp, grade = im.para_bitmap(img, dpi_x=338.667, dpi_y=359.7, opcoes=o)
checa("colunas seguem o DPI em X",
      abs(grade.n_colunas - 50 * 338.667 / 25.4) < 1,
      f"{grade.n_colunas} colunas")
checa("linhas seguem o DPI em Y",
      abs(grade.n_linhas - 25 * 359.7 / 25.4) < 1,
      f"{grade.n_linhas} linhas")
checa("o tamanho em mm sai como pedido",
      abs(grade.largura_mm - 50.0) < 0.1 and abs(grade.altura_mm - 25.0) < 0.1,
      f"{grade.largura_mm:.2f} x {grade.altura_mm:.2f} mm")
checa("origem na posicao pedida",
      grade.x0_mm == 10.0 and grade.y0_mm == 10.0)

print()
print("5. MEIO-TOM: o cabecote e binario")
# Um degrade continuo nao pode virar cinza; tem que virar pontos.
h, w = 64, 256
degrade = np.tile(np.linspace(0.0, 1.0, w, dtype=np.float32), (h, 1))
grad = im.ImagemCarregada(cinza=degrade, nome="degrade")

for modo in im.MODOS:
    t0 = time.perf_counter()
    o = im.Opcoes(largura_mm=64.0, altura_mm=16.0, modo=modo)
    b, _ = im.para_bitmap(grad, 101.6, 101.6, o)   # 4 px por mm
    dt = (time.perf_counter() - t0) * 1000
    cob = im.estimar_cobertura(b)
    print(f"   {modo:8s}: cobertura {cob*100:5.1f}%, {dt:6.1f} ms")
    checa(f"{modo} produz saida binaria", b.dtype == bool)
    # Num degrade de 0 a 1 com limiar 0,5, a cobertura media fica perto de 50%
    checa(f"{modo} preserva a densidade media",
          0.35 < cob < 0.65, f"{cob*100:.1f}%")

# O limiar puro corta seco; os outros dois dao tons intermediarios
o = im.Opcoes(largura_mm=64.0, altura_mm=16.0, modo="limiar")
b_lim, _ = im.para_bitmap(grad, 101.6, 101.6, o)
o = im.Opcoes(largura_mm=64.0, altura_mm=16.0, modo="bayer")
b_bay, _ = im.para_bitmap(grad, 101.6, 101.6, o)


def transicoes(linha):
    return int(np.count_nonzero(np.diff(linha.astype(np.int8))))


meio = b_lim.shape[0] // 2
checa("limiar faz uma unica transicao no degrade",
      transicoes(b_lim[meio]) == 1, f"{transicoes(b_lim[meio])} transicoes")
checa("bayer faz muitas, que e o ponto do meio-tom",
      transicoes(b_bay[meio]) > 10, f"{transicoes(b_bay[meio])} transicoes")

print()
print("6. ESPELHAMENTO")
assimetrica = np.ones((10, 10), dtype=np.float32)
assimetrica[0:3, 0:3] = 0.0            # canto escuro em cima e a esquerda
asm = im.ImagemCarregada(cinza=assimetrica, nome="asm")
o = im.Opcoes(largura_mm=10.0, altura_mm=10.0, modo="limiar")
b0, _ = im.para_bitmap(asm, 25.4, 25.4, o)
# A linha 0 do bitmap e o Y menor (endstop), que quem olha a mesa ve
# embaixo: o topo do arquivo tem que ir para o Y MAIOR, a ultima linha.
# Antes ia para a linha 0, e a imagem saia espelhada em Y na maquina.
checa("sem espelhar, o topo do arquivo vai para o Y maior (ultima linha)",
      b0[-1, 0] and not b0[0, 0] and not b0[-1, -1])

o.espelhar_x = True
bx, _ = im.para_bitmap(asm, 25.4, 25.4, o)
checa("espelhar em X leva o canto para a direita",
      bx[-1, -1] and not bx[-1, 0])

o.espelhar_x, o.espelhar_y = False, True
by, _ = im.para_bitmap(asm, 25.4, 25.4, o)
checa("espelhar em Y leva o topo do arquivo para o Y menor",
      by[0, 0] and not by[-1, 0])

print()
print("7. A IMAGEM PERCORRE O MESMO CAMINHO DE UMA PECA")
perfil = Perfil()
perfil.cabecote.sentido_indice = 1
comp = compensar_bicos(perfil.cabecote, 338.667, 50.0)
o = im.Opcoes(largura_mm=20.0, altura_mm=12.0, modo="bayer")
bmp, grade = im.para_bitmap(img, 338.667, 359.7, o)
faixas = cod.dividir_em_faixas(bmp, perfil.cabecote.n_bicos)
mat = cod.montar_matriz(faixas[0], grade.dpi_x, grade.dpi_y, comp)
b = cod.empacotar(mat, perfil.cabecote)
checa("imagem vira vetor de 16 bytes por posicao",
      b.shape[1] == 16,
      f"{len(faixas)} faixa(s), {b.shape[0]} posicoes W")
checa("as gotas sobrevivem ao empacotamento",
      int(np.unpackbits(b).sum()) == int(bmp.sum()),
      f"{int(np.unpackbits(b).sum())} de {int(bmp.sum())}")

print()
print("8. RECUSAS")
try:
    im.carregar(TMP / "nao_existe_mesmo.png")
    checa("arquivo inexistente recusado", False)
except im.ImagemInvalida:
    checa("arquivo inexistente recusado", True)

lixo = TMP / "pajm_lixo.png"
lixo.write_bytes(b"isto nao e um png")
try:
    im.carregar(lixo)
    checa("arquivo corrompido recusado", False)
except im.ImagemInvalida as e:
    checa("arquivo corrompido recusado", True, str(e)[:44])

try:
    im.para_bitmap(img, 300, 300, im.Opcoes(0.0, 10.0))
    checa("largura zero recusada", False)
except ValueError:
    checa("largura zero recusada", True)

try:
    im.para_bitmap(img, 300, 300, im.Opcoes(10.0, 10.0, modo="invencao"))
    checa("modo desconhecido recusado", False)
except ValueError:
    checa("modo desconhecido recusado", True)

print()
print("9. PDF")
if not im.PDF_DISPONIVEL:
    print("   pymupdf ausente; PDF nao foi exercitado.")
else:
    import pymupdf
    doc = pymupdf.open()
    pg = doc.new_page(width=283.46, height=141.73)   # 100 x 50 mm em pontos
    pg.draw_rect(pymupdf.Rect(28, 28, 142, 113), color=0, fill=0)
    pdf = TMP / "pajm_teste.pdf"
    doc.save(str(pdf))
    doc.close()

    ipdf = im.carregar(pdf, pagina=0, dpi_pdf=300)
    nat = ipdf.tamanho_natural_mm()
    checa("PDF carregado", ipdf.largura_px > 0,
          f"{ipdf.largura_px} x {ipdf.altura_px} px, {ipdf.paginas} pagina(s)")
    checa("tamanho da pagina preservado",
          nat is not None and abs(nat[0] - 100.0) < 1.0,
          f"{nat[0]:.1f} x {nat[1]:.1f} mm" if nat else "sem tamanho")
    checa("tamanho de PDF e confiavel: a pagina tem medida fisica",
          ipdf.tamanho_confiavel)
    o = im.Opcoes(largura_mm=nat[0], altura_mm=nat[1], modo="limiar")
    b, g = im.para_bitmap(ipdf, 338.667, 359.7, o)
    checa("o retangulo do PDF virou gotas",
          0.1 < im.estimar_cobertura(b) < 0.6,
          f"cobertura {im.estimar_cobertura(b)*100:.1f}%")

    try:
        im.carregar(pdf, pagina=5)
        checa("pagina inexistente recusada", False)
    except im.ImagemInvalida:
        checa("pagina inexistente recusada", True)

print()
print("10. SEM PYMUPDF: e assim que o executavel se comporta")
# O .exe NAO embute o pymupdf (decisao de 27/10: so PNG, o PDF custava 19 MB).
# Entao o caminho sem ele precisa degradar com clareza, e nao quebrar.
_tinha = im.PDF_DISPONIVEL
im.PDF_DISPONIVEL = False
try:
    checa("PDF sai da lista de formatos", ".pdf" not in im.formatos_suportados())
    checa("a caixa de abrir nao oferece PDF",
          "pdf" not in im.filtro_de_arquivo().lower(),
          im.filtro_de_arquivo().split(";;")[0][:46])
    # O cenario real: o PDF existe, quem falta e a biblioteca.
    pdf_real = TMP / "pajm_teste.pdf"
    if pdf_real.exists():
        try:
            im.carregar(pdf_real)
            checa("abrir PDF da mensagem util", False)
        except im.ImagemInvalida as e:
            checa("abrir PDF existente diz o que instalar",
                  "pymupdf" in str(e), str(e)[:52])
    else:
        print("   (o PDF da secao 9 nao foi criado; pulando)")
    # E o PNG continua funcionando normalmente
    teste = im.carregar(p)
    checa("PNG nao e afetado", teste.largura_px == 60)
finally:
    im.PDF_DISPONIVEL = _tinha

print()
print("=" * 72)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
