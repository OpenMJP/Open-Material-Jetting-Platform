"""
Exercita as paginas da janela de verdade, sem maquina e sem monitor.

Nao e teste de aparencia: e teste de que clicar nos botoes faz o que diz.
"""
import os
import sys
import pathlib
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Os rotulos da barra lateral tem simbolos que o console do Windows (cp1252)
# nao codifica; o teste os imprime nas mensagens
sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import trimesh
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from pajm.core import malha as cmalha
from pajm.profile import MESAS, Perfil
from pajm.ui.main_window import JanelaPrincipal

todos_ok = True


def checa(nome, cond, extra=""):
    global todos_ok
    todos_ok &= bool(cond)
    print(f"  [{'OK ' if cond else 'FALHA'}] {nome}" + (f"  {extra}" if extra else ""))


perfil = Perfil()
# O perfil nasce com o sentido conferido na maquina; o teste parte sem ele
perfil.cabecote.sentido_indice = None
# As contagens de gotas sao so da peca; a purga e testada no teste_envio
perfil.processo.purga_disparos = 0
# O simulador tem curso de 200 mm e os testes posicionam em coordenadas de
# maquina; a origem da mesa e testada no teste_envio.
perfil.movimento.origem_mesa_x_mm = 0.0
perfil.movimento.origem_mesa_y_mm = 0.0
janela = JanelaPrincipal(perfil)
janela.show()
app.processEvents()

print("=" * 72)
print("1. JANELA")
nav, paginas = janela.nav, janela.paginas
nomes = [nav.item(i).text() for i in range(nav.count())]
checa("titulo e o do programa de controle",
      janela.windowTitle() == "PAJM Controle", janela.windowTitle())
checa("seis paginas na barra lateral, uma por secao",
      len(nomes) == 6 == paginas.count(), str(nomes))
for chave, widget in (("painel", janela.aba_manual),
                      ("fatiador", janela.aba_fatiar),
                      ("imagem", janela.aba_imagem),
                      ("arquivos", janela.aba_arquivos),
                      ("calibracao", janela.aba_padroes)):
    checa(f"pagina '{chave}' mostra o seu painel",
          widget.parent() is janela.pagina(chave))
checa("a primeira pagina e o Painel (conexao e jog)",
      paginas.currentIndex() == 0 and "Painel" in nomes[0], nomes[0])
nav.setCurrentRow(2)
checa("clicar na barra lateral troca a pagina", paginas.currentIndex() == 2)
nav.setCurrentRow(0)
barra = janela.barra_parada
topo = janela.centralWidget().layout().itemAt(0).widget()
checa("PARAR, indicador e conexao ficam na barra superior",
      topo.objectName() == "barraTopo" and barra.parent() is topo
      and janela.aba_manual.bt_conectar.parent() is topo)
checa("sem maquina: chip de estado cinza",
      janela.chip_estado.texto == "Sem maquina")
checa("purga e diagnostico ficam na pagina Maquina",
      janela.pagina("maquina").isAncestorOf(janela.aba_manual.g_impressao)
      and janela.pagina("maquina").isAncestorOf(janela.aba_manual.ck_fio))
checa("desconectado: indicador amarelo e PARAR desabilitado",
      barra.estado == "desconectado" and "e0a030" in barra.lb_estado.text()
      and not barra.bt_parar.isEnabled())

print()
print("2. MESA")
pend = janela.aba_pend
checa("presets de mesa oferecidos",
      pend.cb_mesa.count() == len(MESAS) + 1, f"{pend.cb_mesa.count()} itens")
i200 = pend.cb_mesa.findData("Prusa i3 e clones")
pend.cb_mesa.setCurrentIndex(i200)
app.processEvents()
checa("escolher preset preenche o curso",
      (perfil.movimento.curso_x_mm, perfil.movimento.curso_y_mm) == (200.0, 200.0),
      f"{perfil.movimento.curso_x_mm} x {perfil.movimento.curso_y_mm}")

print()
print("3. MICROPASSOS E DPI")
fat = janela.aba_fatiar
perfil.movimento.passos_por_mm_x = None
d = fat._dpi_x()
checa("sem micropassos por mm, DPI fica digitavel",
      fat.sp_dpix.isEnabled() and d == fat.sp_dpix.value())

perfil.movimento.passos_por_mm_x = 80.0
fat.sp_micro.setValue(6)
d = fat._dpi_x()
checa("com a medida, DPI passa a ser derivado e travado",
      (not fat.sp_dpix.isEnabled()) and abs(d - 338.67) < 0.1, f"{d:.2f} dpi")
fat.sp_micro.setValue(5)
checa("mudar micropassos muda o DPI", abs(fat._dpi_x() - 406.4) < 0.1)
fat.sp_micro.setValue(6)

print()
print("3b. FERRAMENTAS DE POSICIONAMENTO")
# Caso real: piramide que chega deitada de lado no STL.
pir = trimesh.creation.cone(radius=10, height=18, sections=4)
pir.apply_transform(
    trimesh.transformations.rotation_matrix(np.radians(90), (1, 0, 0)))
fat.malha_original = pir
fat._relatorio = cmalha.validar(pir)
fat._popular_faces()
fat._atualizar_modelo()
app.processEvents()

d0 = fat.malha_posicionada.bounds[1] - fat.malha_posicionada.bounds[0]
checa("faces candidatas listadas", fat.cb_base.count() > 0,
      f"{fat.cb_base.count()} direcoes, maior = "
      f"{fat._faces[0].area_mm2:.0f} mm2")
checa("dimensoes mostradas", "x" in fat.lb_dim.text(), fat.lb_dim.text())

fat.cb_base.setCurrentIndex(0)
fat.deitar_face()
app.processEvents()
d1 = fat.malha_posicionada.bounds[1] - fat.malha_posicionada.bounds[0]
checa("deitar a maior face muda a orientacao",
      abs(d1[2] - d0[2]) > 0.5,
      f"{d0[0]:.0f}x{d0[1]:.0f}x{d0[2]:.0f} -> "
      f"{d1[0]:.0f}x{d1[1]:.0f}x{d1[2]:.0f} mm")
checa("a face apoiada encosta em Z=0",
      abs(fat.malha_posicionada.bounds[0][2]) < 1e-6)
vz = np.asarray(fat.malha_posicionada.vertices)[:, 2]
checa("varios vertices no plano da mesa", int((vz < 0.01).sum()) >= 3,
      f"{int((vz < 0.01).sum())} vertices")

# ESTE era o defeito: assentar descartava X e Y, entao mover nao fazia nada.
fat.sp["px"].setValue(30.0)
fat.sp["py"].setValue(-20.0)
fat._modelo_agora()
cx, cy = cmalha.centro_xy(fat.malha_posicionada)
checa("mover em X e Y funciona com 'assentar' ligado",
      abs(cx - 30.0) < 1e-6 and abs(cy + 20.0) < 1e-6,
      f"centro ({cx:.1f}, {cy:.1f})")

# Centralizar tem que funcionar mesmo sem esperar o temporizador
fat.sp["px"].setValue(77.0)
fat.centralizar()
cx, cy = cmalha.centro_xy(fat.malha_posicionada)
mx, my = perfil.movimento.curso_x_mm / 2, perfil.movimento.curso_y_mm / 2
checa("centralizar leva ao centro da mesa (curso/2), sem usar estado velho",
      abs(cx - mx) < 1e-6 and abs(cy - my) < 1e-6,
      f"centro ({cx:.2f}, {cy:.2f}), mesa ({mx:.1f}, {my:.1f})")
checa("peca centralizada fica inteira em coordenada positiva",
      (fat.malha_posicionada.bounds[0][:2] >= 0).all(),
      str(fat.malha_posicionada.bounds[0][:2]))
if fat.vista3d.usando_opengl:
    from PySide6.QtGui import QVector3D as _V3
    c3 = fat.vista3d._item_grade.transform().map(_V3(0.0, 0.0, 0.0))
    checa("a mesa da vista 3D vai de 0 ao curso, nao centrada na origem",
          abs(c3.x() - mx) < 1e-6 and abs(c3.y() - my) < 1e-6,
          f"centro da grade em ({c3.x():.1f}, {c3.y():.1f})")

fat.sp["es"].setValue(30.0)
fat._modelo_agora()
fat.ajustar_a_mesa()
fat._modelo_agora()
d = fat.malha_posicionada.bounds[1] - fat.malha_posicionada.bounds[0]
checa("ajustar a mesa faz caber",
      d[0] <= perfil.movimento.curso_x_mm
      and d[1] <= perfil.movimento.curso_y_mm,
      f"{d[0]:.0f} x {d[1]:.0f} mm em mesa de "
      f"{perfil.movimento.curso_x_mm:.0f} x {perfil.movimento.curso_y_mm:.0f}")

fat.sp["es"].setValue(1.0)
fat._girar90("rz")
checa("giro rapido de 90 graus", abs(fat.sp["rz"].value() % 90.0) < 1e-9,
      f"rz = {fat.sp['rz'].value():.0f}")
fat._zerar_giro()
checa("zerar giro",
      all(fat.sp[c].value() == 0.0 for c in ("rx", "ry", "rz")))

print()
print("4. FATIAMENTO E CHAPAS")
peca = trimesh.creation.box(extents=[18, 12, 6])
fat.malha_original = peca
fat._relatorio = cmalha.validar(peca)
fat._popular_faces()
for _c in ("px", "py", "pz", "rx", "ry", "rz"):
    fat.sp[_c].setValue(0.0)
fat.sp["es"].setValue(1.0)
fat.sp_esp.setValue(300.0)
fat._atualizar_modelo()
app.processEvents()
fat.fatiar()
fat._trab.wait()
app.processEvents()
n = len(fat.camadas)
print(f"   {n} camadas de {fat.camadas.grade.largura_mm:.1f} x "
      f"{fat.camadas.grade.altura_mm:.1f} mm")
checa("fatiou", n > 0)
checa("intervalo preenchido automaticamente",
      fat.sp_cam_de.value() == 1 and fat.sp_cam_ate.value() == n)

checa("controles do ensaio desabilitados no modo empilhado",
      not fat.bt_mosaico.isEnabled())
fat.cb_modo.setCurrentIndex(fat.cb_modo.findData("lado"))
app.processEvents()
checa("escolher lado a lado habilita os controles do ensaio",
      fat.bt_mosaico.isEnabled())

fat.montar_mosaico()
app.processEvents()
checa("chapas montadas", len(fat.chapas) >= 1,
      f"{len(fat.chapas)} chapa(s), {fat.plano_mosaico.por_chapa} por chapa")
total = sum(c.n_camadas for c in fat.chapas)
checa("todas as camadas do intervalo entraram", total == n, f"{total} de {n}")
checa("chapa cabe na mesa",
      fat.chapas[0].grade.largura_mm <= 200
      and fat.chapas[0].grade.altura_mm <= 200,
      f"{fat.chapas[0].grade.largura_mm:.1f} x "
      f"{fat.chapas[0].grade.altura_mm:.1f} mm")

# intervalo parcial
fat.sp_cam_de.setValue(2)
fat.sp_cam_ate.setValue(5)
fat.montar_mosaico()
app.processEvents()
checa("intervalo parcial respeitado",
      sum(c.n_camadas for c in fat.chapas) == 4)

print()
print("5. O MODO DECIDE A UNIDADE, E A VISTA SEGUE O MODO")
from pajm.core import envio as cenv

fat.sp_cam_de.setValue(1)
fat.sp_cam_ate.setValue(n)
fat.montar_mosaico()
app.processEvents()

fat.cb_modo.setCurrentIndex(fat.cb_modo.findData("peca"))
app.processEvents()
u_peca = fat.unidades()
checa("modo empilhado: uma unidade por camada", len(u_peca) == n,
      f"{len(u_peca)} unidades")
checa("cada camada tem o seu Z",
      len({u.z_mm for u in u_peca}) == n)
checa("por padrao as camadas vao inteiras (opacidade 100%)",
      all(np.array_equal(u.bitmap, fat.camadas.bitmap(i))
          for i, u in enumerate(u_peca)))
fat.ck_mover_z.setChecked(False)
fat.sp_opac_cam.setValue(25)
app.processEvents()
u_demo = fat.unidades()
checa("Z parado: todas as camadas na altura da primeira",
      {u.z_mm for u in u_demo} == {float(fat.camadas.z_mm[0])})
g_cheia = sum(int(fat.camadas.bitmap(i).sum()) for i in range(n))
g_demo = sum(int(u.bitmap.sum()) for u in u_demo)
checa("opacidade de 25%: cerca de um quarto das gotas",
      0.2 < g_demo / g_cheia < 0.3, f"{g_demo / g_cheia:.2f}")
fat.cb_vista.setCurrentIndex(fat.cb_vista.findData("sobreposicao"))
app.processEvents()
uniao = np.logical_or.reduce([u.bitmap for u in u_demo])
checa("previa da sobreposicao mostra todas as camadas juntas",
      fat._sobreposicao is not None
      and np.array_equal(fat._sobreposicao[1], uniao)
      and fat.previa._img is not None)
# 4 ou mais camadas a 25% cobrem tudo onde a peca e cheia
c0 = fat.camadas.bitmap(0)
for i in range(1, min(n, 4)):
    c0 = c0 & fat.camadas.bitmap(i)
checa("onde ha 4 camadas a 25%, a cobertura e total",
      n < 4 or (uniao[c0].all() and c0.any()))
fat.cb_vista.setCurrentIndex(fat.cb_vista.findData("geometria"))
fat.sp_opac_cam.setValue(100)
fat.ck_mover_z.setChecked(True)
app.processEvents()

fat.cb_modo.setCurrentIndex(fat.cb_modo.findData("lado"))
app.processEvents()
u_lado = fat.unidades()
checa("modo lado a lado: uma unidade por chapa",
      len(u_lado) == len(fat.chapas), f"{len(u_lado)} unidades")
checa("todas as chapas no mesmo Z",
      {u.z_mm for u in u_lado} == {0.0})

# O dado do cabecote tem que ser o das chapas, nao so a previa.
f_peca = cenv.contar_swaths(u_peca, perfil.cabecote.n_bicos)
f_lado = cenv.contar_swaths(u_lado, perfil.cabecote.n_bicos)
checa("o job muda com o modo, nao so a previa",
      f_peca != f_lado, f"empilhado {f_peca} swaths, lado a lado {f_lado}")

# E com intervalo parcial, so as camadas escolhidas vao para o cabecote
fat.sp_cam_de.setValue(2)
fat.sp_cam_ate.setValue(5)
fat.montar_mosaico()
app.processEvents()
gotas_chapas = sum(int(u.bitmap.sum()) for u in fat.unidades())
gotas_sel = sum(int(fat.camadas.bitmap(i).sum()) for i in range(1, 5))
gotas_tudo = sum(int(fat.camadas.bitmap(i).sum()) for i in range(n))
checa("so as camadas do intervalo entram no job",
      gotas_chapas == gotas_sel and gotas_sel != gotas_tudo,
      f"chapas={gotas_chapas} intervalo={gotas_sel} tudo={gotas_tudo}")

for modo in ("peca", "lado"):
    fat.cb_modo.setCurrentIndex(fat.cb_modo.findData(modo))
    for vista in ("geometria", "jateamento"):
        fat.cb_vista.setCurrentIndex(fat.cb_vista.findData(vista))
        app.processEvents()
        checa(f"modo '{modo}' + vista '{vista}' renderiza",
              fat.previa._img is not None,
              f"{fat.previa._img.width()} x {fat.previa._img.height()} px"
              if fat.previa._img else "")

print()
print("5b. ENVIO PARA A MAQUINA")
from pajm.core import envio as cenv11
from pajm.machine import protocolo as pvv
from pajm.machine.sessao import Evento as EvV11

checa("sem conexao, envio bloqueado com motivo",
      not fat.bt_enviar.isEnabled() and "sem conexao" in fat.lb_envio.text())

pm0 = janela.aba_manual
pm0.cb_porta.setCurrentIndex(0)
pm0.conectar()
app.processEvents()
checa("conectou ao simulador",
      pm0.sessao is not None and pm0.sessao.aberta)
checa("o HELLO_ACK preencheu passos por mm no perfil",
      perfil.movimento.passos_por_mm_x == 40.0,
      f"{perfil.movimento.passos_por_mm_x} passos/mm")

fat._cache_params = None
fat._atualizar_envio()
checa("conectado mas sem sentido do indice, continua bloqueado",
      not fat.bt_enviar.isEnabled()
      and "sentido do indice" in fat.lb_envio.text())

perfil.cabecote.sentido_indice = 1
fat._atualizar_envio()
plano = fat._plano

# As camadas foram fatiadas com os 80 passos/mm que o teste tinha chutado; a
# maquina informou 40. O software TEM que recusar, senao a peca sairia com o
# dobro do tamanho em X sem nada acusar.
checa("fatiamento velho, feito com outra escala, e recusado",
      plano is not None and not plano.viavel
      and any("escala errada" in e for e in plano.erros),
      plano.erros[0][:90] if plano and plano.erros else "aceitou")

# Refatiar depois de conectar resolve: agora o DPI sai dos passos por mm reais
fat._cache_params = None
fat._atualizar_modelo()
fat.fatiar()
fat._trab.wait()
app.processEvents()
fat.cb_modo.setCurrentIndex(fat.cb_modo.findData("lado"))
fat.sp_cam_de.setValue(1)
fat.sp_cam_ate.setValue(len(fat.camadas))
fat.montar_mosaico()
app.processEvents()
plano = fat._plano
checa("depois de refatiar, o plano fecha",
      plano is not None and plano.viavel,
      "; ".join(plano.erros)[:90] if plano and plano.erros
      else f"{plano.n_swaths} swaths a {plano.dpi_x_real:.0f} dpi")
checa("envio liberado", fat.bt_enviar.isEnabled())

# Envia de verdade, mas sem a caixa de confirmacao
from PySide6.QtWidgets import QMessageBox as _QMB
_orig = _QMB.question, _QMB.information, _QMB.critical
_QMB.question = staticmethod(lambda *a, **k: _QMB.StandardButton.Yes)
_QMB.information = staticmethod(lambda *a, **k: None)
_vistos = []
_QMB.critical = staticmethod(lambda *a, **k: _vistos.append(a[2] if len(a) > 2 else ""))
try:
    fat.enviar()
finally:
    _QMB.question, _QMB.information, _QMB.critical = _orig

checa("nenhuma caixa de erro durante o envio", not _vistos,
      str(_vistos[:1]))

fwsim = pm0.firmware_sim
for _ in range(4000):
    pm0._bombear()
    fwsim.avancar(4)
    fwsim.enviar_status()
    app.processEvents()
    if pm0.sessao.pendentes == 0 and not fwsim._fila:
        break

gotas_esperadas = sum(int(u.bitmap.sum()) for u in fat.unidades())
checa("todas as swaths executadas",
      fwsim.swaths_executadas == plano.n_swaths,
      f"{fwsim.swaths_executadas} de {plano.n_swaths}")
checa("as gotas chegaram inteiras ao firmware",
      fwsim.gotas == gotas_esperadas,
      f"{fwsim.gotas} de {gotas_esperadas}")
checa("job encerrado em IDLE sem erro",
      fwsim.estado == pvv.Estado.IDLE and fwsim.last_error == 0,
      f"{fwsim.status().nome_estado}, last_error={fwsim.last_error}")

pm0.desconectar()
app.processEvents()

print("5c. ABA DE IMAGEM (PNG e PDF)")
import tempfile
from pajm.core import imagem as cimg

pimg = janela.aba_imagem
checa("pagina de impressao de imagem existe e esta na janela",
      any("Impressao de imagem" in nav.item(i).text()
          for i in range(nav.count())))

# Desenho preto no branco, com proporcao 2:1 para conferir o travamento
TMPI = pathlib.Path(tempfile.gettempdir()) / "pajm_ui_img.png"
alvo = np.zeros((50, 100), dtype=bool)
alvo[12:38, 25:75] = True
from PySide6.QtGui import QImage as _QI
_buf = np.ascontiguousarray(np.where(alvo, 0, 255).astype(np.uint8))
_QI(_buf.data, 100, 50, 100, _QI.Format.Format_Grayscale8).copy().save(str(TMPI))

pimg._carregar(str(TMPI), 0)
app.processEvents()
checa("imagem carregada", pimg.imagem is not None,
      f"{pimg.imagem.largura_px} x {pimg.imagem.altura_px} px")
checa("PNG avisa que o tamanho declarado nao e confiavel",
      "nao sabe que tamanho tem" in pimg.lb_arquivo.text())

pimg.sp_larg.setValue(40.0)
app.processEvents()
checa("travar proporcao ajusta a altura",
      abs(pimg.sp_alt.value() - 20.0) < 0.01,
      f"{pimg.sp_alt.value():.2f} mm para 40 mm de largura")

def processar_e_esperar():
    """O meio-tom roda numa thread; espera ela terminar e o painel mostrar."""
    pimg.processar()
    while pimg.processando or pimg._t.isActive():
        app.processEvents()
        if not pimg.processando and pimg._t.isActive():
            pimg._t.stop()
            pimg.processar()
    app.processEvents()


pimg.ck_proporcao.setChecked(False)
pimg.sp_alt.setValue(30.0)
app.processEvents()
checa("destravar proporcao deixa os dois livres",
      abs(pimg.sp_larg.value() - 40.0) < 0.01
      and abs(pimg.sp_alt.value() - 30.0) < 0.01)
pimg.ck_proporcao.setChecked(True)
pimg.sp_larg.setValue(40.0)
processar_e_esperar()

g = pimg.grade
checa("a grade sai no tamanho pedido em mm",
      abs(g.largura_mm - 40.0) < 0.2 and abs(g.altura_mm - 20.0) < 0.2,
      f"{g.largura_mm:.2f} x {g.altura_mm:.2f} mm")
checa("a grade usa o DPI da maquina em cada eixo",
      abs(g.dpi_x - perfil.dpi_x_efetivo) < 0.1
      and abs(g.dpi_y - perfil.cabecote.dpi_y_nativo) < 0.1,
      f"{g.dpi_x:.0f} x {g.dpi_y:.0f} dpi")

cob = cimg.estimar_cobertura(pimg.bitmap)
checa("cobertura condiz com o desenho (26x50 de 50x100 = 26%)",
      0.2 < cob < 0.32, f"{cob*100:.1f}%")

for modo in ("limiar", "bayer", "difusao"):
    pimg.cb_modo.setCurrentIndex(pimg.cb_modo.findData(modo))
    processar_e_esperar()
    checa(f"meio-tom '{modo}' renderiza",
          pimg.bitmap is not None and pimg.previa._img is not None)
pimg.cb_modo.setCurrentIndex(pimg.cb_modo.findData("limiar"))
processar_e_esperar()

print()
print("5d. JATEAR A IMAGEM PELA INTERFACE")
pm1 = janela.aba_manual
pm1.cb_porta.setCurrentIndex(0)
pm1.conectar()
app.processEvents()
pimg._cache_params = None
pimg.sp_x0.setValue(40.0)
pimg.sp_y0.setValue(40.0)
processar_e_esperar()
checa("envio liberado com a conexao e o sentido definidos",
      pimg.bt_enviar.isEnabled(),
      pimg.lb_envio.text().replace("<br>", " | ")[:100])

gotas_esperadas = int(pimg.bitmap.sum())
fw1 = pm1.firmware_sim
_QMB = __import__("PySide6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox
_o = _QMB.question, _QMB.information, _QMB.critical
_err = []
_QMB.question = staticmethod(lambda *a, **k: _QMB.StandardButton.Yes)
_QMB.information = staticmethod(lambda *a, **k: None)
_QMB.critical = staticmethod(lambda *a, **k: _err.append(a[2] if len(a) > 2 else ""))
try:
    pimg.enviar()
finally:
    _QMB.question, _QMB.information, _QMB.critical = _o
checa("nenhuma caixa de erro no envio", not _err, str(_err[:1])[:80])

for _ in range(3000):
    pm1._bombear()
    fw1.avancar(4)
    fw1.enviar_status()
    app.processEvents()
    if pm1.sessao.pendentes == 0 and not fw1._fila:
        break
checa("a imagem chegou inteira ao firmware",
      fw1.gotas == gotas_esperadas,
      f"{fw1.gotas} de {gotas_esperadas} gotas")
checa("job encerrado em IDLE sem erro",
      fw1.estado == pvv.Estado.IDLE and fw1.last_error == 0,
      f"{fw1.status().nome_estado}, last_error={fw1.last_error}")
pm1.desconectar()
app.processEvents()

print()
print("6. PADROES")
pp = janela.aba_padroes
pp.gerar_bicos()
app.processEvents()
checa("verificacao de bicos gerada numa passada so",
      pp.padrao is not None and not pp.padrao.passadas
      and pp.padrao.bitmap.shape[0] == 128,
      f"{pp.padrao.largura_mm:.1f} x {pp.padrao.altura_mm:.1f} mm")
checa("sem conexao, imprimir fica bloqueado",
      not pp.bt_imprimir.isEnabled() and "conexao" in pp.lb_envio.text())

pp.cb_eixo.setCurrentIndex(0)
pp.gerar_alinhamento()
app.processEvents()
checa("alinhamento em X gerado", pp.padrao.n_gotas > 0,
      f"{pp.padrao.largura_mm:.1f} x {pp.padrao.altura_mm:.1f} mm")
pp.sp_escolha.setValue(7)
checa("correcao calculada da opcao escolhida",
      "+60" in pp.lb_correcao.text(), pp.lb_correcao.text())

base_y = perfil.processo.correcao_passo_faixa_um
pp.gerar_alinhamento_y()
app.processEvents()
checa("alinhamento em Y gerado em passadas separadas",
      len(pp.padrao.passadas) == pp.sp_opcoes_y.value() + 1)
pp.sp_escolha_y.setValue(7)
checa("a nova correcao soma a opcao a correcao de quando foi gerado",
      f"{base_y + 75.0:+.1f}" in pp.lb_correcao_y.text(),
      pp.lb_correcao_y.text())

_base_ang = perfil.cabecote.angulo_efetivo_deg
pp.gerar_angulo_visual()
app.processEvents()
checa("teste visual de angulo em duas faixas emendadas",
      len(pp.padrao.passadas) == 2)
pp.sp_escolha_ang.setValue(pp.sp_opcoes_ang.value())
_esp = _base_ang + (pp.sp_opcoes_ang.value() - 1) / 2 * pp.sp_passo_ang.value()
checa("a opcao escolhida vira angulo",
      f"{_esp:.2f}" in pp.lb_angulo_vis.text()
      and pp.bt_aplicar_vis.isEnabled(), pp.lb_angulo_vis.text())

pp.sp_bico_a.setValue(1)
pp.sp_bico_b.setValue(128)
pp.gerar_angulo()
app.processEvents()
checa("teste de angulo gerado numa passada so",
      not pp.padrao.passadas and pp.padrao.bitmap.shape[0] == 128)
import math as _m
import tempfile as _tf
cab = perfil.cabecote
pp.sp_dist.setValue(round(127 * cab.passo_bico_mm
                          * _m.sin(_m.radians(32.0)), 3))
checa("distancia medida vira angulo", "32.0" in pp.lb_angulo.text(),
      pp.lb_angulo.text())
_ang_antes, _dpi_antes = cab.angulo_montagem_deg, cab.dpi_y_nativo
_salvar = perfil.salvar_ajustes
_tmp = _tf.mkdtemp()
perfil.salvar_ajustes = lambda c=None: _salvar(Path(_tmp) / "a.json")
_o = _QMB.information
_QMB.information = staticmethod(lambda *a, **k: None)
try:
    pp.aplicar_angulo(pp._angulo_novo())
finally:
    _QMB.information = _o
    perfil.salvar_ajustes = _salvar
checa("aplicar grava o angulo no perfil e muda o DPI em Y",
      abs(cab.angulo_montagem_deg - 32.0) < 0.01
      and cab.dpi_y_nativo != _dpi_antes,
      f"{cab.angulo_montagem_deg} graus, {cab.dpi_y_nativo:.1f} dpi")
checa("o angulo calibrado vai para o arquivo de ajustes",
      '"angulo_montagem_deg": 32.0' in
      (Path(_tmp) / "a.json").read_text(encoding="utf-8"))
checa("a aba Pendencias ve o angulo novo",
      abs((janela.aba_pend.sp_ang.valor() or 0) - 32.0) < 0.01)
cab.angulo_montagem_deg = _ang_antes
janela._calibracao_mudou()

pp.gerar_grade()
app.processEvents()
checa("grade dimensional gerada", pp.padrao.n_gotas > 0)
pp.gerar_linhas()
app.processEvents()
checa("linhas verticais geradas",
      pp.padrao.bitmap.shape[0] == 128 * pp.sp_l_faixas.value())

print()
print("7. MODO SIMULADO")
pm = janela.aba_manual
checa("simulador oferecido como porta", pm.cb_porta.itemData(0) == "sim")
pm.cb_porta.setCurrentIndex(0)
pm.conectar()
app.processEvents()
checa("conectou ao simulador", pm.sessao is not None)
checa("rotulo avisa que e simulado", "SIMULADO" in pm.lb_conexao.text())
checa("conectado: indicador verde e PARAR habilitado",
      barra.estado == "simulado" and "4caf50" in barra.lb_estado.text()
      and barra.bt_parar.isEnabled())


def servir(n=60):
    for _ in range(n):
        pm._bombear()
        pm.firmware_sim.enviar_status()
        app.processEvents()


pm.home("xyz")
pm.jog("X", 25.0)
servir()
st = pm.sessao.status
checa("jog move a maquina simulada", st.pos[0] == 1000,
      f"{st.pos} passos")
checa("posicao em mm aparece na tela", "25.000" in pm.lb["x"].text(),
      pm.lb["x"].text().strip())
checa("eixos referenciados aparecem",
      "XYZ" in pm.lb["cabecote"].text(), pm.lb["cabecote"].text())

# O simulador declara VMAX_Z = 2000 passos/s a 160 passos/mm: 12,5 mm/s.
# Antes, o jog usava a mesma velocidade nos tres eixos e o Z era recusado.
checa("velocidade de cada eixo limitada ao VMAX da maquina",
      pm.sp_vel["z"].maximum() == 12.5 and pm.sp_vel["z"].value() <= 12.5,
      f"Z: {pm.sp_vel['z'].value():g} de no maximo "
      f"{pm.sp_vel['z'].maximum():g} mm/s")
pm.jog("Z", 2.0)
servir()
checa("jog do Z aceito", pm.sessao.status.pos[2] == 320,
      f"{pm.sessao.status.pos} passos")

pm.cabecote(True)
servir()
checa("cabecote liga", pm.sessao.status.vpp_ligado)

gotas_antes = pm.firmware_sim.gotas
pm.cb_padrao.setCurrentIndex(0)            # todos os bicos
pm.sp_disparos.setValue(10)
pm.disparar()
servir()
checa("disparo com todos os bicos chega a maquina",
      pm.firmware_sim.gotas - gotas_antes == 10 * 128,
      f"{pm.firmware_sim.gotas - gotas_antes} gotas")

antes = pm.firmware_sim.estops
barra.bt_parar.click()              # o da barra superior, visivel em todas as paginas
servir(10)
checa("PARAR do canto chega como quadro prioritario",
      pm.firmware_sim.estops == antes + 1)
checa("maquina em FAULT", pm.sessao.status.state == pvv.Estado.FAULT,
      pm.sessao.status.nome_estado)

pm.limpar_falha()
servir()
checa("limpar falha volta para IDLE",
      pm.sessao.status.state == pvv.Estado.IDLE,
      pm.sessao.status.nome_estado)

# Movimento fora do curso tem que ser recusado e registrado
pm.home("x")
servir()
pm.jog("X", 5000.0)
servir()
checa("movimento fora do curso e recusado e registrado",
      "RECUSADO" in pm.tx_log.toPlainText())

pm.home("xyz")
pm.cabecote(True)
servir()
fw_sim = pm.firmware_sim
checa("antes de desconectar: cabecote ligado e eixos referenciados",
      fw_sim.vpp and all(fw_sim.referenciado))
pm.desconectar()
app.processEvents()
checa("desconectar desabilita os controles",
      not pm.bt_ir.isEnabled() and pm.sessao is None)
# Na placa, comando confirmado ja esta na fila e executa sozinho; o
# simulador so executa quando alguem le a porta, entao roda a fila aqui
fw_sim.executar_tudo()
checa("desconectar desliga o cabecote e os motores",
      not fw_sim.vpp and not any(fw_sim.referenciado))
checa("desconectado de proposito: indicador amarelo",
      barra.estado == "desconectado")

from pajm.machine.sessao import Aviso as _Aviso, Evento as _Evento
pm.conectar()
servir(5)
pm._tratar_aviso(_Aviso(_Evento.ENLACE_PERDIDO, "teste"))
app.processEvents()
checa("conexao perdida: indicador vermelho e PARAR desabilitado",
      barra.estado == "perdido" and "ff6b6b" in barra.lb_estado.text()
      and not barra.bt_parar.isEnabled() and pm.sessao is None)

print()
print("7b. ARQUIVOS: PACOTE .pajm DE OUTRO CAM")
import json as _json
import tempfile as _tf2
pa = janela.aba_arquivos
_d = Path(_tf2.mkdtemp())
fat.cb_modo.setCurrentIndex(fat.cb_modo.findData("peca"))
app.processEvents()
checa("o fatiador exporta as camadas como .pajm",
      fat.exportar_pacote(str(_d / "peca.pajm")) and (_d / "peca.pajm").exists())
checa("a pagina Arquivos abre o pacote",
      pa.abrir(str(_d / "peca.pajm")) and len(pa.pacote) == len(fat.camadas),
      f"{len(pa.pacote)} camadas")
checa("pacote na grade da maquina: avisa que usa pixel a pixel",
      "na grade da maquina" in pa.lb_info.text())
u_pac = pa.unidades()
u_fat = fat.unidades()
checa("importar devolve as mesmas camadas, no mesmo lugar e Z",
      len(u_pac) == len(u_fat)
      and all(np.array_equal(a.bitmap, b.bitmap) and abs(a.z_mm - b.z_mm) < 1e-6
              and abs(a.grade.x0_mm - b.grade.x0_mm) < 1e-9
              for a, b in zip(u_pac, u_fat)))
checa("perfil da maquina exportado para o CAM",
      pa.exportar_perfil(str(_d / "perfil.json"))
      and _json.loads((_d / "perfil.json").read_text())["formato"]
      == "pajm-perfil-maquina")
pa.sp_opac.setValue(50)
checa("opacidade da pagina vale sobre o pacote",
      0.4 < sum(int(u.bitmap.sum()) for u in pa.unidades())
      / max(1, sum(int(u.bitmap.sum()) for u in u_fat)) < 0.6)
pa.sp_opac.setValue(100)

pm.conectar()
servir(5)
# A peca deste teste esta em X negativo; o canto da pagina a reposiciona
pa.sp_x0.setValue(60.0)
pa.sp_y0.setValue(40.0)
pa._atualizar_envio()
checa("conectado, o pacote pode ser impresso", pa.bt_enviar.isEnabled(),
      pa.lb_envio.text().replace("<br>", " | ")[:120])
fwp = pm.firmware_sim
esperadas = sum(int(u.bitmap.sum()) for u in pa.unidades())
_o = _QMB.question, _QMB.information, _QMB.critical
_errp = []
_QMB.question = staticmethod(lambda *a, **k: _QMB.StandardButton.Yes)
_QMB.information = staticmethod(lambda *a, **k: None)
_QMB.critical = staticmethod(lambda *a, **k: _errp.append(a[2] if len(a) > 2 else ""))
try:
    pa.enviar()
finally:
    _QMB.question, _QMB.information, _QMB.critical = _o
for _ in range(4000):
    pm._bombear()
    fwp.avancar(4)
    fwp.enviar_status()
    app.processEvents()
    if pm.sessao.pendentes == 0 and not fwp._fila:
        break
checa("o pacote chegou inteiro ao firmware", not _errp
      and fwp.gotas == esperadas, f"{fwp.gotas} de {esperadas} gotas")
pm.desconectar()
app.processEvents()

print()
print("8. LAYOUT: nenhum campo pode ficar esmagado")
# Um QVBoxLayout sem rolagem, com conteudo mais alto que a janela, esmaga os
# filhos abaixo do minimo em vez de rolar: rotulos cortados e campos
# encavalados. Aconteceu de verdade quando a coluna de controles cresceu.
# Este teste mede, no menor tamanho de janela permitido, se algum campo ficou
# mais baixo do que pede.
from PySide6.QtWidgets import (QAbstractSpinBox, QCheckBox, QComboBox,
                               QLabel, QPushButton)

TIPOS = (QAbstractSpinBox, QComboBox, QPushButton, QCheckBox, QLabel)
minimo = janela.minimumSize()
for larg, alt in ((minimo.width(), minimo.height()), (1400, 900)):
    janela.resize(larg, alt)
    app.processEvents()
    esmagados = []
    for i in range(paginas.count()):
        nav.setCurrentRow(i)
        app.processEvents()
        alvos = []
        for tipo in TIPOS:
            alvos += paginas.widget(i).findChildren(tipo)
        for w in alvos:
            if not w.isVisible():
                continue
            m = w.minimumSizeHint().height()
            if m > 0 and w.height() < m:
                esmagados.append(
                    f"{nav.item(i).text().strip()}/{type(w).__name__}"
                    f"({w.height()}px de {m}px)")
    checa(f"janela {larg}x{alt}: nada esmagado", not esmagados,
          "; ".join(esmagados[:3]))

# A coluna de controles tem que rolar, nao espremer
from PySide6.QtWidgets import QScrollArea
checa("colunas de controles ficam em area rolavel",
      all(len(paginas.widget(i).findChildren(QScrollArea)) >= 1
          for i in range(paginas.count())),
      "todas as paginas")

print()
print("9. ORIENTACAO, DIALOGOS, CONTORNO, ORIGEM E RESOLUCAO")
# Previa com Y para cima: a linha 0 do bitmap (Y menor) desenhada embaixo
from pajm.ui.preview2d import PreviaCamada as _Pv
from pajm.core.fatiador import GradeRaster as _GR
_pv = _Pv()
_b = np.zeros((4, 3), dtype=bool)
_b[0, 0] = True
_pv.mostrar(_b, _GR(0, 0, 25.4, 25.4, 3, 4))
checa("previa: a linha 0 (Y menor) fica embaixo",
      _pv._img.pixelIndex(0, 3) == 1 and _pv._img.pixelIndex(0, 0) == 0)

# Dialogo de arquivo nao nativo: o nativo do Windows trava o protocolo
from PySide6.QtWidgets import QFileDialog as _QFD
_pedidos = []
_orig_open = _QFD.getOpenFileName
_QFD.getOpenFileName = staticmethod(
    lambda *a, **k: (_pedidos.append(k.get("options")), ("", ""))[1])
try:
    fat.abrir()
    janela.aba_imagem.abrir()
    janela.aba_arquivos.abrir()
finally:
    _QFD.getOpenFileName = _orig_open
checa("abrir arquivo usa o dialogo do Qt, que nao derruba a conexao",
      len(_pedidos) == 3 and all(
          o is not None and o & _QFD.Option.DontUseNativeDialog
          for o in _pedidos))

# Contorno das camadas na simulacao sobreposta
from pajm.core import imagem as _ci
_q = np.zeros((20, 20), dtype=bool)
_q[5:15, 5:15] = True
_c = _ci.contorno(_q, 2, 2)
checa("contorno de 2 px de um quadrado de 10 px",
      _c.sum() == 100 - 36 and not _c[7:13, 7:13].any() and _c[5, 5])
fat.cb_modo.setCurrentIndex(fat.cb_modo.findData("peca"))
fat.sp_opac_cam.setValue(25)
fat.ck_contorno.setChecked(True)
app.processEvents()
_cam0 = fat.camadas.bitmap(0)
_g = fat.camadas.grade
_borda = _ci.contorno(_cam0, max(1, round(0.2 / _g.passo_x_mm)),
                      max(1, round(0.2 / _g.passo_y_mm)))
_u0 = fat.unidades()[0].bitmap
checa("com contorno, a borda da camada sai cheia sobre o meio-tom",
      _borda.any() and _u0[_borda].all()
      and _u0[_cam0 & ~_borda].mean() < 0.5)
fat.ck_contorno.setChecked(False)
fat.sp_opac_cam.setValue(100)
fat.sp_esp.setValue(5000.0)
checa("espessura de camada acima de 1 mm aceita",
      fat.sp_esp.value() == 5000.0)
fat.sp_esp.setValue(300.0)

# Origem da mesa editavel na pagina Maquina, salva nos ajustes
import tempfile as _tf3
_dir = Path(_tf3.mkdtemp())
_salvar_orig = perfil.salvar_ajustes
perfil.salvar_ajustes = lambda c=None: _salvar_orig(_dir / "a.json")
try:
    janela.aba_pend.sp_origem["origem_mesa_x_mm"].setValue(42.5)
finally:
    perfil.salvar_ajustes = _salvar_orig
checa("origem da mesa editavel e salva nos ajustes",
      perfil.movimento.origem_mesa_x_mm == 42.5
      and _json.loads((_dir / "a.json").read_text())["origem_mesa_x_mm"]
      == 42.5)
janela.aba_pend.sp_origem["origem_mesa_x_mm"].blockSignals(True)
janela.aba_pend.sp_origem["origem_mesa_x_mm"].setValue(0.0)
janela.aba_pend.sp_origem["origem_mesa_x_mm"].blockSignals(False)
perfil.movimento.origem_mesa_x_mm = 0.0

# Teste de bicos em outra resolucao em X, sem mexer no perfil
pp = janela.aba_padroes
_micro_perfil = perfil.processo.micropassos_por_disparo
pp.sp_micro_b.setValue(3)
pp.gerar_bicos()
_dpi3 = 25.4 * perfil.movimento.passos_por_mm_x / 3
checa("teste de bicos gerado na resolucao pedida",
      abs(pp.padrao.grade.dpi_x - _dpi3) < 0.01
      and f"{_dpi3:.0f} dpi" in pp.lb_res_b.text(),
      f"{pp.padrao.grade.dpi_x:.0f} dpi")
checa("o perfil continua com a sua resolucao",
      perfil.processo.micropassos_por_disparo == _micro_perfil
      and pp._perfil_envio().processo.micropassos_por_disparo == 3)
pm.conectar()
servir(5)
pp.sp_x0.setValue(60.0)
pp.sp_y0.setValue(40.0)
pp._cache_params = None
pp._atualizar_envio()
checa("o teste em outra resolucao pode ser impresso (pitch casa com a grade)",
      pp.bt_imprimir.isEnabled() and pp._plano.pitch == 3,
      pp.lb_envio.text().replace("<br>", " | ")[:120])
fwb = pm.firmware_sim
_esp = int(pp.padrao.bitmap.sum())
_o = _QMB.question, _QMB.information, _QMB.critical
_QMB.question = staticmethod(lambda *a, **k: _QMB.StandardButton.Yes)
_QMB.information = staticmethod(lambda *a, **k: None)
_QMB.critical = staticmethod(lambda *a, **k: None)
try:
    pp.imprimir()
finally:
    _QMB.question, _QMB.information, _QMB.critical = _o
for _ in range(3000):
    pm._bombear()
    fwb.avancar(4)
    fwb.enviar_status()
    app.processEvents()
    if pm.sessao.pendentes == 0 and not fwb._fila:
        break
checa("as gotas do teste chegam com pitch 3",
      fwb.gotas == _esp, f"{fwb.gotas} de {_esp}")
pm.desconectar()
app.processEvents()
pp.sp_micro_b.setValue(_micro_perfil)
pp.gerar_grade()
checa("os outros padroes voltam a resolucao do perfil",
      pp._perfil_envio() is perfil)

print("10. VELOCIDADE DE IMPRESSAO CENTRALIZADA NA PAGINA MAQUINA")
_pmv = janela.aba_manual
checa("o campo de velocidade fica na pagina Maquina",
      janela.pagina("maquina").isAncestorOf(_pmv.sp_vel_imp))
checa("o fatiador e a pagina Arquivos nao tem campo proprio",
      not hasattr(fat, "sp_vel") and not hasattr(janela.aba_arquivos, "sp_vel"))
_fase_antes = fat.resumo.compensacao.fase_mm if fat.resumo else None
_vel_antes = perfil.processo.velocidade_mm_s
_dir2 = Path(_tf3.mkdtemp())
_salvar_orig = perfil.salvar_ajustes
perfil.salvar_ajustes = lambda c=None: _salvar_orig(_dir2 / "a.json")
try:
    _pmv.sp_vel_imp.setValue(80.0)
    app.processEvents()
finally:
    perfil.salvar_ajustes = _salvar_orig
checa("mudar a velocidade grava no perfil e nos ajustes",
      perfil.processo.velocidade_mm_s == 80.0
      and _json.loads((_dir2 / "a.json").read_text())["velocidade_mm_s"] == 80.0)
checa("o fatiador recalcula com a velocidade nova (fase de disparo)",
      "80 mm/s" in fat.lb_vel.text()
      and (_fase_antes is None
           or abs(fat.resumo.compensacao.fase_mm - _fase_antes * 80.0 / _vel_antes) < 1e-9),
      fat.lb_vel.text())
checa("a pagina Arquivos mostra a velocidade em uso",
      "80 mm/s" in janela.aba_arquivos.lb_vel.text())
_pmv.sp_vel_imp.blockSignals(True)
_pmv.sp_vel_imp.setValue(_vel_antes)
_pmv.sp_vel_imp.blockSignals(False)
perfil.processo.velocidade_mm_s = _vel_antes
janela._velocidade_mudou(_vel_antes)

print("11. CANCELAR IMPRESSAO E RECOLHER (sem parada de emergencia)")
pm = janela.aba_manual
pm.conectar()
servir(5)
checa("o botao de cancelar fica na barra superior, habilitado conectado",
      barra.bt_cancelar.isEnabled() and barra.bt_cancelar.parent() is barra
      and barra.parent() is topo)
fwc = pm.firmware_sim
pm.home("xyz")
servir(30)
s_ = pm.sessao
s_.iniciar_job(job_id=9, total_swaths=6)
s_.cabecote(True)
for k in range(6):
    s_.enviar_swath(k, 400, 1000, 3, bytes(16 * 400), fire_delay_us=0,
                    vel_passos_s=2000)
for _ in range(6):            # parte do job sai, a maquina esta imprimindo
    pm._bombear()
    fwc.avancar(1)
    fwc.enviar_status()
    app.processEvents()
checa("antes: maquina imprimindo", fwc.estado == pvv.Estado.JOB)
_estops = fwc.estops
_o = _QMB.question
_QMB.question = staticmethod(lambda *a, **k: _QMB.StandardButton.Yes)
try:
    barra.bt_cancelar.click()
finally:
    _QMB.question = _o
for _ in range(300):
    pm._bombear()
    fwc.avancar(4)
    fwc.enviar_status()
    app.processEvents()
ha_ = s_.hello_ack
checa("cancelar volta a IDLE sem ESTOP, com o cabecote desligado",
      fwc.estado == pvv.Estado.IDLE and fwc.estops == _estops
      and fwc.last_error == int(pvv.Erro.CANCELLED) and not fwc.vpp,
      f"{fwc.status().nome_estado}, estops {fwc.estops - _estops}")
checa("os eixos continuam referenciados", all(fwc.referenciado))
_z = round(max(25.0, 0.0) * ha_.passos_por_mm(pvv.EIXO_Z))
_y = min(round(perfil.processo.apresentar_y_mm
               * ha_.passos_por_mm(pvv.EIXO_Y)), ha_.travel[pvv.EIXO_Y])
checa("a maquina recolhe: Z 25 mm, X 0, Y de apresentacao",
      fwc.pos == [0, _y, _z], f"{fwc.pos} passos, esperado [0, {_y}, {_z}]")
pm.desconectar()
app.processEvents()

print()
print("=" * 72)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
