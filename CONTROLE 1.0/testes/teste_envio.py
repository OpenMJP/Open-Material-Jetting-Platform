"""
Ponta a ponta: STL -> fatiamento -> swaths -> protocolo -> firmware.

E o teste que fecha o circuito. Se as gotas que o firmware simulado recebe
batem com as gotas das camadas fatiadas, entao fatiador, compensacao,
codificador, conversao de unidades e protocolo concordam entre si.
"""
import sys
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pajm.core import envio as env
from pajm.core import job as cjob
from pajm.core import mosaico as mos
from pajm.machine import protocolo as pv
from pajm.machine.sessao import Evento, Sessao
from pajm.machine.simulador import (
    FirmwareSimulado, TransporteSimulado,
)
from pajm.profile import Perfil

todos_ok = True


def checa(nome, cond, extra=""):
    global todos_ok
    todos_ok &= bool(cond)
    print(f"  [{'OK ' if cond else 'FALHA'}] {nome}" + (f"  {extra}" if extra else ""))


class Relogio:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def avancar(self, s):
        self.t += s


print("=" * 72)
print("1. O HELLO_ACK PREENCHE AS PENDENCIAS DO PERFIL")
perfil = Perfil()
# O simulador tem curso de 200 mm e estes testes posicionam em coordenadas de
# maquina. A origem da mesa tem teste proprio, na secao 8.
perfil.movimento.origem_mesa_x_mm = 0.0
perfil.movimento.origem_mesa_y_mm = 0.0
# O perfil ja nasce com os 80 passos/mm medidos; o simulador tem 40, e e
# isso que tem de prevalecer depois de conectar.
perfil.movimento.passos_por_mm_x = None
checa("antes de conectar, passos por mm e pendencia",
      any("passos_por_mm_x" in p.chave for p in perfil.pendencias()))

fw = FirmwareSimulado()
rel = Relogio()
s = Sessao(TransporteSimulado(fw), relogio=rel)
ha = s.abrir()
mudancas = env.aplicar_hello_ack(perfil, ha)
print("   " + "; ".join(mudancas[:3]))
checa("passos por mm vem da maquina",
      perfil.movimento.passos_por_mm_x == 40.0,
      f"{perfil.movimento.passos_por_mm_x} passos/mm")
checa("curso vem da maquina",
      perfil.movimento.curso_x_mm == 200.0,
      f"{perfil.movimento.curso_x_mm} mm")
checa("a pendencia de passos por mm sumiu",
      not any("passos_por_mm_x" in p.chave for p in perfil.pendencias()))

print()
print("2. O DPI EM X PASSA A SAIR DO PITCH")
perfil.processo.micropassos_por_disparo = 3
checa("pitch 3 a 40 passos/mm da 338,7 dpi",
      abs(perfil.dpi_x_efetivo - 338.667) < 0.01,
      f"{perfil.dpi_x_efetivo:.3f} dpi")
tabela = dict(perfil.dpis_alcancaveis(6))
print("   pitch -> dpi:", ", ".join(f"{k}:{v:.0f}" for k, v in tabela.items()))
checa("360 dpi continua inatingivel",
      perfil.micropassos_para_dpi(360) == 3
      and abs(tabela[3] - 360) > 20)

print()
print("3. FATIAR UMA PECA E PLANEJAR O ENVIO")
perfil.cabecote.sentido_indice = 1
from pajm.core import malha as cmalha
peca = trimesh.creation.box(extents=[12, 8, 4])
peca.apply_translation([40.0, 30.0, 0.0])      # longe da origem em X e Y
peca = cmalha.assentar(peca)                   # e apoiada em Z = 0
r = cjob.preparar(perfil, peca, dpi_x=perfil.dpi_x_efetivo,
                  velocidade_mm_s=50.0, espessura_camada_um=500.0)
camadas = cjob.fatiar(peca, r)
print(f"   {len(camadas)} camadas de {camadas.grade.largura_mm:.1f} x "
      f"{camadas.grade.altura_mm:.1f} mm, origem em X = "
      f"{camadas.grade.x0_mm:.1f} mm")

unidades = [
    env.Unidade(f"camada {i+1}", camadas.bitmap(i), camadas.grade,
                float(camadas.z_mm[i]))
    for i in range(len(camadas))]
gotas_esperadas = sum(int(u.bitmap.sum()) for u in unidades)

plano = env.planejar(s, perfil, unidades, r.compensacao,
                     velocidade_mm_s=50.0, accel_passos_s2=40_000,
                     runup_margin=40, min_fire_period_us=200)
print(f"   {plano.n_swaths} swaths, {plano.bytes_totais/1000:.1f} KB, "
      f"pitch {plano.pitch}, {plano.vel_passos_s} passos/s, "
      f"periodo {plano.periodo_disparo_us} us, "
      f"recuo {plano.recuo_mm:.1f} mm")
for a in plano.avisos:
    print(f"   aviso: {a}")
for e in plano.erros:
    print(f"   ERRO: {e}")
checa("plano viavel", plano.viavel)
checa("periodo de disparo acima do minimo",
      plano.periodo_disparo_us >= 200, f"{plano.periodo_disparo_us} us")

print()
print("4. O PLANEJAMENTO RECUSA O QUE O FIRMWARE RECUSARIA")
# Peca encostada na origem: nao ha espaco para acelerar
perto = trimesh.creation.box(extents=[12, 8, 4])
perto.apply_translation([3.0, 30.0, 0.0])
r2 = cjob.preparar(perfil, perto, dpi_x=perfil.dpi_x_efetivo,
                   velocidade_mm_s=50.0, espessura_camada_um=500.0)
c2 = cjob.fatiar(perto, r2)
u2 = [env.Unidade("c1", c2.bitmap(0), c2.grade, 0.0)]
p2 = env.planejar(s, perfil, u2, r2.compensacao, 50.0, 40_000, 40, 200)
checa("peca sem espaco para acelerar e recusada",
      not p2.viavel and any("acelerar" in e for e in p2.erros),
      p2.erros[0][:70] if p2.erros else "aceitou")
c2.fechar()

# Velocidade alta demais para o pitch
p3 = env.planejar(s, perfil, unidades, r.compensacao, 400.0, 40_000, 40, 200)
checa("velocidade acima do periodo minimo e recusada",
      not p3.viavel and any("periodo de disparo" in e for e in p3.erros),
      p3.erros[0][:70] if p3.erros else "aceitou")

# Acima do VMAX_X: o firmware recusa o SWATH_BEGIN com BAD_PARAM (detalhe
# 16), o erro que apareceu na maquina. O plano tem que pegar antes.
p5 = env.planejar(s, perfil, unidades, r.compensacao, 50.0, 40_000, 40, 200,
                  vmax_x_passos_s=1000)
checa("velocidade acima do VMAX_X da maquina e recusada",
      not p5.viavel and any("maxima do eixo X" in e for e in p5.erros),
      p5.erros[0][:80] if p5.erros else "aceitou")
p6 = env.planejar(s, perfil, unidades, r.compensacao, 50.0, 40_000, 40, 200,
                  vmax_x_passos_s=plano.vel_passos_s)
checa("exatamente no VMAX_X passa", p6.viavel, "; ".join(p6.erros)[:80])

# Z negativo: a peca nao foi assentada
baixa = trimesh.creation.box(extents=[12, 8, 4])
baixa.apply_translation([40.0, 30.0, 0.0])
r4 = cjob.preparar(perfil, baixa, dpi_x=perfil.dpi_x_efetivo,
                   velocidade_mm_s=50.0, espessura_camada_um=500.0)
c4 = cjob.fatiar(baixa, r4)
u4 = [env.Unidade(f"c{i}", c4.bitmap(i), c4.grade, float(c4.z_mm[i]))
      for i in range(len(c4))]
p4 = env.planejar(s, perfil, u4, r4.compensacao, 50.0, 40_000, 40, 200)
checa("peca com camadas em Z negativo e recusada",
      not p4.viavel and any("Z negativo" in e for e in p4.erros),
      p4.erros[0][:70] if p4.erros else "aceitou")
c4.fechar()

print()
print("5. IMPRIMIR DE VERDADE NO FIRMWARE SIMULADO")
s.iniciar_job(job_id=1, total_swaths=plano.n_swaths)
s.referenciar("xyz")
s.cabecote(True)

z_atual = None
for sw in env.gerar_swaths(unidades, perfil, r.compensacao, ha, plano.pitch):
    if sw.z_passos != z_atual:
        s.enfileirar(pv.Op.MOVE, pv.p_move(pv.EIXO_Z, sw.z_passos, 2000))
        z_atual = sw.z_passos
    s.enviar_swath(sw.swath_id, sw.y_passos, sw.x_start_passos, sw.pitch,
                   sw.colunas, fire_delay_us=0,
                   vel_passos_s=plano.vel_passos_s)
s.terminar_job()

avisos = []
for _ in range(4000):
    avisos += s.bombear()
    fw.avancar(2)
    fw.enviar_status()
    rel.avancar(0.002)
    avisos += s.bombear()
    if s.pendentes == 0 and not fw._fila:
        break

erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("nenhum erro do firmware", not erros,
      "; ".join(str(e) for e in erros[:2]))
checa("todas as swaths executadas",
      fw.swaths_executadas == plano.n_swaths,
      f"{fw.swaths_executadas} de {plano.n_swaths}")
checa("as gotas recebidas batem com as camadas fatiadas",
      fw.gotas == gotas_esperadas, f"{fw.gotas} de {gotas_esperadas}")
checa("job encerrado em IDLE sem erro",
      fw.estado == pv.Estado.IDLE and fw.last_error == 0,
      f"{fw.status().nome_estado}, last_error={fw.last_error}")
checa("cabecote desligado ao fim", not fw.vpp)

print()
print("6. O Z ANDA ENTRE CAMADAS")
zs = sorted({int(round(float(camadas.z_mm[i]) * 160)) for i in range(len(camadas))})
checa("cada camada tem o seu Z em passos", len(zs) == len(camadas),
      f"{len(zs)} alturas distintas, de {zs[0]} a {zs[-1]} passos")
checa("todo Z e nao negativo e dentro do curso",
      zs[0] >= 0 and zs[-1] <= 6000, f"de {zs[0]} a {zs[-1]} passos")
checa("o firmware terminou num Z de camada", fw.pos[2] in zs,
      f"pos Z = {fw.pos[2]} passos")

print()
print("7. ARREDONDAMENTO DO Y NAO ACUMULA ERRO")
# O passo entre faixas (70,6 um) nao e multiplo do micropasso (25 um).
# Arredondar a posicao ABSOLUTA mantem o erro limitado a meio passo; somar
# incrementos arredondados faria o erro crescer com o numero de faixas.
ha_ = s.hello_ack
pmy = ha_.passos_por_mm(pv.EIXO_Y)
passo_faixa = perfil.cabecote.n_bicos * camadas.grade.passo_y_mm
pior = 0.0
for k in range(60):
    exato = camadas.grade.y0_mm + k * passo_faixa
    pior = max(pior, abs(round(exato * pmy) / pmy - exato))
checa("erro de Y limitado a meio passo em 60 faixas",
      pior <= 0.5 / pmy + 1e-9, f"pior erro {pior*1000:.1f} um "
      f"(meio passo = {0.5/pmy*1000:.1f} um)")

print()
print("8. ORIGEM DA MESA")
# As coordenadas da peca sao da mesa; a swath vai em coordenadas de maquina.
uns = [env.Unidade("u", np.ones((10, 4), dtype=bool),
                   camadas.grade.__class__(5.0, 7.0, camadas.grade.dpi_x,
                                           camadas.grade.dpi_y, 4, 10), 0.0)]
perfil.movimento.origem_mesa_x_mm = 35.0
perfil.movimento.origem_mesa_y_mm = 30.0
sw = next(env.gerar_swaths(uns, perfil, r.compensacao, ha_, 1))
pmx = ha_.passos_por_mm(pv.EIXO_X)
checa("x_start soma a origem da mesa",
      sw.x_start_passos == round(40.0 * pmx), f"{sw.x_start_passos} passos")
checa("y soma a origem da mesa",
      sw.y_passos == round(37.0 * pmy), f"{sw.y_passos} passos")
perfil.movimento.origem_mesa_x_mm = 0.0
perfil.movimento.origem_mesa_y_mm = 0.0

print()
print("9. CORRECAO DO AVANCO ENTRE FAIXAS")
g9 = camadas.grade.__class__(5.0, 7.0, camadas.grade.dpi_x,
                             camadas.grade.dpi_y, 4, 300)
uns9 = [env.Unidade("u", np.ones((300, 4), dtype=bool), g9, 0.0)]
nominal = perfil.cabecote.n_bicos * g9.passo_y_mm
perfil.processo.correcao_passo_faixa_um = 50.0
sws = list(env.gerar_swaths(uns9, perfil, r.compensacao, ha_, 1))
checa("a correcao entra no avanco de cada faixa",
      [sw.y_passos for sw in sws]
      == [round((7.0 + k * (nominal + 0.050)) * pmy) for k in range(3)],
      f"{[sw.y_passos for sw in sws]}")
perfil.processo.correcao_passo_faixa_um = 0.0

print()
print("10. PURGA ANTES E APRESENTACAO DEPOIS")
fw10 = FirmwareSimulado()
rel10 = Relogio()
s10 = Sessao(TransporteSimulado(fw10, auto_executar=True), relogio=rel10)
ha10 = s10.abrir()
perfil.processo.purga_disparos = 300
perfil.processo.purga_periodo_us = 500
perfil.processo.apresentar_ao_fim = True
perfil.processo.apresentar_y_mm = 150.0
p10 = s10.obter_parametros([pv.Param.ACCEL_X, pv.Param.RUNUP_MARGIN,
                            pv.Param.MIN_FIRE_PERIOD])
g10 = camadas.grade.__class__(30.0, 10.0, camadas.grade.dpi_x,
                              camadas.grade.dpi_y, 20, 200)
uns10 = [env.Unidade("u", np.ones((200, 20), dtype=bool), g10, 0.0)]
plano10 = env.planejar(s10, perfil, uns10, r.compensacao,
                       velocidade_mm_s=perfil.processo.velocidade_mm_s,
                       accel_passos_s2=p10[int(pv.Param.ACCEL_X)],
                       runup_margin=p10[int(pv.Param.RUNUP_MARGIN)],
                       min_fire_period_us=p10[int(pv.Param.MIN_FIRE_PERIOD)])
checa("o plano com duas faixas e viavel", plano10.viavel,
      "; ".join(plano10.erros))
res10 = env.enviar_unidades(s10, perfil, uns10, r.compensacao, plano10)
for _ in range(2000):
    s10.bombear()
    fw10.enviar_status()
    rel10.avancar(0.002)
    if s10.pendentes == 0 and not fw10._fila:
        break
gotas_purga = 300 * 128
checa("job sem erro", not res10["erros"] and fw10.last_error == 0,
      "; ".join(res10["erros"][:2]))
checa("a purga disparou todos os bicos, alem das gotas da peca",
      fw10.gotas == gotas_purga + 200 * 20,
      f"{fw10.gotas} gotas, {gotas_purga} da purga")
pmy10 = ha10.passos_por_mm(pv.EIXO_Y)
checa("ao fim, carro no zero e mesa em Y = 150 mm",
      fw10.pos[0] == 0 and fw10.pos[1] == round(150.0 * pmy10),
      f"pos = {fw10.pos[:2]}")
checa("job encerrado em IDLE", fw10.estado == pv.Estado.IDLE)

perfil.processo.purga_disparos = 0
perfil.processo.apresentar_ao_fim = False
fw10b = FirmwareSimulado()
s10b = Sessao(TransporteSimulado(fw10b, auto_executar=True),
              relogio=Relogio())
s10b.abrir()
env.enviar_unidades(s10b, perfil, uns10, r.compensacao, plano10)
for _ in range(2000):
    s10b.bombear()
    if s10b.pendentes == 0 and not fw10b._fila:
        break
checa("purga 0 e sem apresentacao: so as gotas da peca, carro onde parou",
      fw10b.gotas == 200 * 20 and fw10b.pos[0] != 0,
      f"{fw10b.gotas} gotas, X = {fw10b.pos[0]}")

print()
print("10b. CANCELAMENTO VINDO DE FORA DO ENVIO")
# A barra superior manda o CANCEL pela sessao, nao pelo painel que esta
# enviando: o laco do envio tem que parar de mandar passadas mesmo assim
fw10c = FirmwareSimulado()
rel10c = Relogio()
s10c = Sessao(TransporteSimulado(fw10c, auto_executar=True), relogio=rel10c)
s10c.abrir()
perfil.processo.purga_disparos = 0
uns10c = [env.Unidade(f"u{k}", np.ones((128, 20), dtype=bool), g10, 0.0)
          for k in range(12)]
plano10c = env.planejar(s10c, perfil, uns10c, r.compensacao,
                        velocidade_mm_s=perfil.processo.velocidade_mm_s,
                        accel_passos_s2=p10[int(pv.Param.ACCEL_X)],
                        runup_margin=p10[int(pv.Param.RUNUP_MARGIN)],
                        min_fire_period_us=p10[int(pv.Param.MIN_FIRE_PERIOD)])
_cancels_antes = []


def _cancela_cedo(n, total, rotulo):
    if n == 2 and not s10c.cancelamento_pedido:
        _cancels_antes.append(fw10c.cancels)
        s10c.cancelar()


res10c = env.enviar_unidades(s10c, perfil, uns10c, r.compensacao, plano10c,
                             progresso=_cancela_cedo)
for _ in range(400):
    s10c.bombear()
    fw10c.enviar_status()
    rel10c.avancar(0.01)
checa("cancelar pela sessao interrompe o envio",
      res10c["cancelado"] and res10c["enviadas"] < len(uns10c),
      f"{res10c['enviadas']} de {len(uns10c)} passadas enviadas")
checa("e o job termina cancelado na maquina, sem cancelamento duplo do envio",
      fw10c.estado == pv.Estado.IDLE
      and fw10c.last_error == int(pv.Erro.CANCELLED))
checa("um job novo comeca sem o pedido de cancelamento anterior",
      (s10c.iniciar_job(2), not s10c.cancelamento_pedido)[1])

print()
print("11. AJUSTES SOBREVIVEM AO FECHAR O PROGRAMA")
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    arq = Path(tmp) / "ajustes.json"
    pa = Perfil()
    pa.processo.correcao_passo_faixa_um = -37.5
    pa.processo.purga_disparos = 1234
    pa.processo.apresentar_ao_fim = False
    pa.salvar_ajustes(arq)
    pb = Perfil()
    checa("sem arquivo, carregar devolve False",
          not pb.carregar_ajustes(Path(tmp) / "nao_existe.json"))
    checa("os ajustes voltam iguais",
          pb.carregar_ajustes(arq)
          and pb.processo.correcao_passo_faixa_um == -37.5
          and pb.processo.purga_disparos == 1234
          and pb.processo.apresentar_ao_fim is False)

camadas.fechar()

print()
print("=" * 72)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
