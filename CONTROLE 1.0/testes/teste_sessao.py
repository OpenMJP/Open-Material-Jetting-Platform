"""
Sessao e transporte v1.1 contra o firmware simulado.

O relogio e falso: os testes avancam o tempo em vez de esperar, entao RTO,
heartbeat e timeout de enlace sao verificados em milissegundos de execucao.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pajm.machine import protocolo as pv
from pajm.machine.sessao import (
    Aviso, EnlacePerdido, Evento, Sessao, VersaoIncompativel,
)
from pajm.machine.simulador import (
    FirmwareSimulado, TransporteSimulado,
)

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


def monta(**kw):
    """Sessao aberta contra um firmware simulado, com relogio controlado."""
    fw = FirmwareSimulado(**kw)
    rel = Relogio()
    s = Sessao(TransporteSimulado(fw), relogio=rel)
    s.abrir()
    return s, fw, rel


def rodar(s, fw, rel, passos=40, dt=0.01, executar=True):
    """Bombeia a sessao e deixa o firmware executar, coletando avisos."""
    avisos = []
    for _ in range(passos):
        avisos += s.bombear()
        if executar:
            fw.avancar(2)
        fw.enviar_status()
        rel.avancar(dt)
        avisos += s.bombear()
    return avisos


print("=" * 72)
print("1. ABERTURA DE SESSAO")
fw = FirmwareSimulado()
rel = Relogio()
s = Sessao(TransporteSimulado(fw), relogio=rel)
ha = s.abrir()
checa("HELLO_ACK recebido", s.aberta and ha is not None)
checa("versao do protocolo", (ha.proto_major, ha.proto_minor) == (1, 4),
      f"{ha.proto_major}.{ha.proto_minor}")
checa("steps_per_m chega do firmware", s.passos_por_mm(pv.EIXO_X) == 40.0,
      f"{s.passos_por_mm(0)} passos/mm em X")
checa("creditos iniciais", s.swath_disponivel == 4 and s.fila_disponivel == 32,
      f"swath={s.swath_disponivel} fila={s.fila_disponivel}")
checa("o ESP espera seq 1 depois do HELLO", fw._esperado == 1)

print()
print("2. VERSAO MAIOR DIFERENTE TEM QUE RECUSAR")
fwx = FirmwareSimulado()
orig = fwx._hello


def hello_falso(q):
    orig(q)
    # troca o proto_major da resposta ja montada
    fwx._saida.clear()
    import struct
    fwx._enviar(pv.Op.HELLO_ACK, struct.pack(
        pv.HelloAck.FORMATO, 2, 0, 1, 0, 0, 8, 4, 32, 1028, 8192, 200, 0,
        40000, 40000, 160000, 8000, 8000, 6000))


fwx._hello = hello_falso
try:
    Sessao(TransporteSimulado(fwx), relogio=Relogio()).abrir()
    checa("protocolo 2.x recusado", False)
except VersaoIncompativel as e:
    checa("protocolo 2.x recusado", True, str(e)[:48])

print()
print("3. CONVERSAO mm <-> passos")
s, fw, rel = monta()
checa("25 mm viram 1000 passos em X", s.para_passos(pv.EIXO_X, 25.0) == 1000)
checa("1000 passos viram 25 mm", s.para_mm(pv.EIXO_X, 1000) == 25.0)
checa("Z usa o proprio steps_per_m",
      s.para_passos(pv.EIXO_Z, 1.0) == 160, f"{s.para_passos(2, 1.0)} passos")

print()
print("4. COMANDOS MANUAIS")
s, fw, rel = monta()
s.referenciar("xyz")
rodar(s, fw, rel, passos=5)
checa("HOME referencia os tres eixos", all(fw.referenciado),
      f"{fw.referenciado}")

s.jog_mm(pv.EIXO_X, 25.0, 50.0)
rodar(s, fw, rel, passos=5)
checa("jog de 25 mm anda 1000 passos", fw.pos[0] == 1000, f"{fw.pos}")

s.mover_mm(pv.EIXO_X, 100.0, 50.0)
rodar(s, fw, rel, passos=5)
checa("MOVE absoluto para 100 mm", fw.pos[0] == 4000, f"{fw.pos}")

s.cabecote(True)
rodar(s, fw, rel, passos=5)
checa("cabecote liga", fw.vpp)

print()
print("5. VALIDACOES DO FIRMWARE VIRAM ERR")
# A secao 7.3 diz que o JOG nao exige HOME e so respeita [0, travel] se o
# eixo estiver referenciado. Entao sem HOME ele passa, e COM HOME e recusado.
s, fw, rel = monta()
s.jog_mm(pv.EIXO_X, 10_000.0, 50.0)
avisos = rodar(s, fw, rel, passos=5)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("jog sem referenciar nao checa curso (secao 7.3)", not erros,
      str(erros[0]) if erros else "sem erro, correto")

s, fw, rel = monta()
s.referenciar("x")
rodar(s, fw, rel, passos=5)
s.jog_mm(pv.EIXO_X, 10_000.0, 50.0)
avisos = rodar(s, fw, rel, passos=5)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("jog fora do curso recusado DEPOIS de referenciar",
      any(e.code == pv.Erro.OUT_OF_TRAVEL for e in erros),
      str(erros[0]) if erros else "nenhum erro")

s, fw, rel = monta()
s.mover_mm(pv.EIXO_X, 50.0, 50.0)        # sem HOME antes
avisos = rodar(s, fw, rel, passos=5)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("MOVE sem referenciar recusado",
      any(e.code == pv.Erro.NOT_HOMED for e in erros),
      str(erros[0]) if erros else "nenhum erro")

s, fw, rel = monta()
s.mover_mm(pv.EIXO_X, 50.0, 10_000.0)    # acima de VMAX
avisos = rodar(s, fw, rel, passos=5)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("velocidade acima de VMAX recusada",
      any(e.code in (pv.Erro.BAD_PARAM, pv.Erro.NOT_HOMED) for e in erros))

print()
print("6. ESTADO PLANEJADO (secao 7.1)")
# MOVE enfileirado logo depois de HOME tem que ser aceito, mesmo com o HOME
# ainda nao executado: a validacao olha o estado PLANEJADO.
s, fw, rel = monta()
s.referenciar("xyz")
s.mover_mm(pv.EIXO_X, 50.0, 50.0)
avisos = []
for _ in range(6):
    avisos += s.bombear()
    rel.avancar(0.005)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("MOVE aceito com HOME ainda na fila", not erros,
      str(erros[0]) if erros else "sem erro, correto")
fw.executar_tudo()
checa("e executa na ordem certa", fw.pos[0] == 2000 and fw.referenciado[0],
      f"pos={fw.pos}")

print()
print("7. JANELA GO-BACK-N E CREDITOS")
s, fw, rel = monta()
for i in range(20):
    s.esperar(1)                          # 20 DWELL, consomem fila
s.bombear()
checa("nunca mais que WINDOW quadros em voo",
      len(s._em_voo) <= pv.WINDOW, f"{len(s._em_voo)} em voo")
checa("o resto fica enfileirado no PC", len(s._fila) == 20 - len(s._em_voo))
rodar(s, fw, rel, passos=30)
checa("todos os 20 chegaram", s.pendentes == 0, f"{s.pendentes} pendentes")

# Credito de fila esgotado trava o envio, sem estourar o firmware
s, fw, rel = monta(queue_slots=3)
for i in range(10):
    s.esperar(1)
for _ in range(6):
    s.bombear()
    rel.avancar(0.005)
checa("respeita o credito de fila do firmware",
      len([e for e in s._em_voo if e.consome_fila]) <= 3,
      f"{len(s._em_voo)} em voo, fila_disponivel={s.fila_disponivel}")
avisos = rodar(s, fw, rel, passos=40)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("nenhum NO_QUEUE_SLOT: o PC segurou",
      not any(e.code == pv.Erro.NO_QUEUE_SLOT for e in erros))

print()
print("8. RETRANSMISSAO POR RTO")


class TransporteFurado(TransporteSimulado):
    """Engole os primeiros N quadros que o PC manda, para forcar o RTO."""

    def __init__(self, fw, engolir=1):
        super().__init__(fw)
        self.engolir = engolir
        self.engolidos = 0

    def escrever(self, dados):
        if self.engolidos < self.engolir and dados and dados[0] != 0x00:
            self.engolidos += 1
            return
        super().escrever(dados)


fw = FirmwareSimulado()
rel = Relogio()
t = TransporteFurado(fw, engolir=0)
s = Sessao(t, relogio=rel)
s.abrir()
t.engolir = 1
t.engolidos = 0
s.referenciar("x")
s.bombear()
checa("quadro perdido fica em voo", len(s._em_voo) == 1)
checa("o firmware nao recebeu", not fw.referenciado[0])
rel.avancar(pv.RTO_S + 0.01)
s.bombear()
fw.executar_tudo()
checa("retransmitido depois do RTO", fw.referenciado[0],
      f"engolidos={t.engolidos}")

print()
print("9. FALHA DE ENLACE APOS MAX_RETRIES")
fw = FirmwareSimulado()
rel = Relogio()
t = TransporteFurado(fw, engolir=0)
s = Sessao(t, relogio=rel)
s.abrir()
# O enlace tem que cair por MAX_RETRIES, e nao por silencio: o firmware
# segue mandando STATUS, entao so o caminho de IDA esta quebrado.
t.engolir = 999
s.referenciar("x")
avisos = []
for _ in range(pv.MAX_RETRIES + 3):
    fw.enviar_status()                    # a volta continua viva
    avisos += s.bombear()
    rel.avancar(pv.RTO_S + 0.001)
avisos += s.bombear()
perdas = [a for a in avisos if a.tipo == Evento.ENLACE_PERDIDO]
checa("enlace cai por MAX_RETRIES, com a volta viva",
      bool(perdas) and not s.aberta
      and "retransmissoes" in str(perdas[0].dado),
      str(perdas[0].dado) if perdas else "nao caiu")

print()
print("10. TIMEOUT DE ENLACE POR SILENCIO")
s, fw, rel = monta()
avisos = []
rel.avancar(pv.LINK_TIMEOUT_S + 0.01)
avisos += s.bombear()
perdas = [a for a in avisos if a.tipo == Evento.ENLACE_PERDIDO]
checa("silencio de 1 s derruba a sessao", bool(perdas) and not s.aberta,
      perdas[0].dado if perdas else "")

print()
print("11. HEARTBEAT")
s, fw, rel = monta()
s.bombear()
antes = fw._esperado
rel.avancar(pv.PING_PERIOD_S + 0.01)
s.bombear()
checa("PING sai quando nao ha nada a enviar", fw._esperado > antes,
      f"seq esperado foi de {antes} para {fw._esperado}")

print()
print("12. ESTOP E PRIORITARIOS")
s, fw, rel = monta()
s.referenciar("xyz")
rodar(s, fw, rel, passos=5)
for i in range(10):
    s.esperar(100)
s.bombear()
antes_fila = len(fw._fila)
s.parar()
checa("ESTOP chega direto, sem esperar a janela", fw.estops == 1)
checa("maquina vai para FAULT", fw.estado == pv.Estado.FAULT)
checa("fila do firmware esvaziada", len(fw._fila) == 0,
      f"tinha {antes_fila}")
checa("eixos ficam nao referenciados", not any(fw.referenciado))

# e o prioritario para de repetir quando o STATUS confirma
fw.enviar_status()
s.bombear()
checa("prioritario encerra ao ver o estado esperado", s._prioritario is None)

# CLEAR_FAULT volta para IDLE
s, fw, rel = monta()
s.parar()
fw.enviar_status()
s.bombear()
s.limpar_falha()
rodar(s, fw, rel, passos=5)
checa("CLEAR_FAULT volta para IDLE", fw.estado == pv.Estado.IDLE,
      fw.status().nome_estado)

print()
print("13. PARAMETROS")
s, fw, rel = monta()
s.ler_parametro(pv.Param.VMAX_X)
avisos = rodar(s, fw, rel, passos=5)
vals = [a.dado for a in avisos if a.tipo == Evento.PARAMETRO]
checa("GET_PARAM devolve o valor", vals and vals[0].value == 8000,
      str(vals[0]) if vals else "nada")

s.definir_parametro(pv.Param.MIN_FIRE_PERIOD, 150)   # abaixo do piso de 180
avisos = rodar(s, fw, rel, passos=5)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("MIN_FIRE_PERIOD abaixo de 180 us recusado",
      any(e.code == pv.Erro.BAD_PARAM for e in erros))

print()
print("14. JOB COM SWATHS")
import numpy as np

rng = np.random.default_rng(7)


def prepara_job(s, fw, rel):
    s.iniciar_job(job_id=1, total_swaths=3)
    s.referenciar("xyz")
    s.cabecote(True)
    rodar(s, fw, rel, passos=8)


s, fw, rel = monta()
prepara_job(s, fw, rel)
checa("JOB_BEGIN leva o firmware para JOB", fw.estado == pv.Estado.JOB,
      fw.status().nome_estado)

colunas = rng.integers(0, 256, size=200 * 16, dtype=np.uint8).tobytes()
gotas_enviadas = sum(bin(b).count("1") for b in colunas)
n = s.enviar_swath(swath_id=0, y_passos=400, x_start_passos=1000, pitch=3,
                   colunas=colunas, fire_delay_us=0, vel_passos_s=5000)
checa("200 colunas viram 1 BEGIN + 4 DATA + 1 END",
      n == 200 and len(s._fila) == 6, f"{len(s._fila)} quadros na fila")

avisos = rodar(s, fw, rel, passos=30)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("swath aceita sem erro", not erros,
      str(erros[0]) if erros else "")
checa("o firmware jateou as gotas certas", fw.gotas == gotas_enviadas,
      f"{fw.gotas} de {gotas_enviadas}")
checa("uma swath executada", fw.swaths_executadas == 1)

print()
print("15. O FIRMWARE RECUSA SWATH CORROMPIDA")
s, fw, rel = monta()
prepara_job(s, fw, rel)
col = bytes(16 * 10)
s.enfileirar(pv.Op.SWATH_BEGIN, pv.p_swath_begin(
    0, False, False, 3, 400, 1000, 10, 0, 5000))
s._fila.append((int(pv.Op.SWATH_DATA), pv.p_swath_data(0, 0, col)))
s.enfileirar(pv.Op.SWATH_END, pv.p_swath_end(0, 0xDEADBEEF))   # CRC errado
avisos = rodar(s, fw, rel, passos=20)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("CRC-32 errado recusado",
      any(e.code == pv.Erro.SWATH_CRC for e in erros),
      str(erros[0]) if erros else "nenhum erro")
checa("e o erro aborta o job (secao 7.1)", fw.estado == pv.Estado.IDLE,
      fw.status().nome_estado)

s, fw, rel = monta()
prepara_job(s, fw, rel)
s.enfileirar(pv.Op.SWATH_BEGIN, pv.p_swath_begin(
    0, False, False, 3, 400, 1000, 10, 0, 5000))
s._fila.append((int(pv.Op.SWATH_DATA), pv.p_swath_data(0, 0, bytes(16 * 4))))
s.enfileirar(pv.Op.SWATH_END, pv.p_swath_end(0, pv.crc32(bytes(16 * 4))))
avisos = rodar(s, fw, rel, passos=20)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("swath incompleta recusada",
      any(e.code == pv.Erro.SWATH_INCOMPLETE for e in erros),
      str(erros[0]) if erros else "nenhum erro")

print()
print("16. VALIDACOES DA SECAO 7.5")
s, fw, rel = monta()
s.iniciar_job(1)
s.referenciar("xy")
rodar(s, fw, rel, passos=6)
s.enviar_swath(0, 400, 1000, 3, bytes(16 * 10), 0, 5000)   # VPP desligado
avisos = rodar(s, fw, rel, passos=20)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("swath com cabecote desligado recusada",
      any(e.code == pv.Erro.HEAD_OFF for e in erros),
      str(erros[0]) if erros else "nenhum erro")

# Disparo rapido demais: pitch 1 a 8000 passos/s da 125 us, abaixo dos 200
s, fw, rel = monta()
prepara_job(s, fw, rel)
s.enviar_swath(0, 400, 1000, 1, bytes(16 * 10), 0, 8000)
avisos = rodar(s, fw, rel, passos=20)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("FIRE_TOO_FAST quando o periodo cai abaixo do minimo",
      any(e.code == pv.Erro.FIRE_TOO_FAST for e in erros),
      str(erros[0]) if erros else "nenhum erro")

# Bidirecional nao existe no firmware v1.0
s, fw, rel = monta()
prepara_job(s, fw, rel)
s.enviar_swath(0, 400, 1000, 3, bytes(16 * 10), 0, 5000, dir_menos_x=True)
avisos = rodar(s, fw, rel, passos=20)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("dir = 1 recusado pelo firmware v1.0",
      any(e.code == pv.Erro.BAD_PARAM for e in erros),
      str(erros[0]) if erros else "nenhum erro")

print()
print("17. CREDITO DE SWATH SEGURA O ENVIO")
s, fw, rel = monta(swath_slots=2)
prepara_job(s, fw, rel)
for i in range(6):
    s.enviar_swath(i, 400 + i * 10, 1000, 3, bytes(16 * 20), 0, 5000)
avisos = []
for _ in range(10):
    avisos += s.bombear()
    rel.avancar(0.005)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("nenhum NO_SWATH_SLOT com so 2 slots",
      not any(e.code == pv.Erro.NO_SWATH_SLOT for e in erros),
      f"swath_disponivel={s.swath_disponivel}")
avisos += rodar(s, fw, rel, passos=80)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("as 6 swaths passaram pelo gargalo de 2 slots",
      fw.swaths_executadas == 6 and not erros,
      f"{fw.swaths_executadas} executadas"
      + (f", erro: {erros[0]}" if erros else ""))

print()
print("18. JOB_END FECHA O TRABALHO")
s, fw, rel = monta()
prepara_job(s, fw, rel)
s.enviar_swath(0, 400, 1000, 3, bytes(16 * 20), 0, 5000)
s.terminar_job()
rodar(s, fw, rel, passos=30)
checa("volta para IDLE", fw.estado == pv.Estado.IDLE, fw.status().nome_estado)
checa("sem erro registrado", fw.last_error == 0, f"last_error={fw.last_error}")
checa("cabecote desligado na rotina de desligamento", not fw.vpp)

print()
print("19. CANCEL NO MEIO DO JOB")
s, fw, rel = monta()
prepara_job(s, fw, rel)
for i in range(3):
    s.enviar_swath(i, 400, 1000, 3, bytes(16 * 20), 0, 5000)
for _ in range(4):
    s.bombear()
    rel.avancar(0.005)
s.cancelar()
checa("CANCEL chega na frente", fw.cancels == 1)
checa("job abortado, de volta para IDLE", fw.estado == pv.Estado.IDLE,
      fw.status().nome_estado)
checa("motivo registrado como CANCELLED",
      fw.last_error == pv.Erro.CANCELLED,
      pv.nome_do_erro(fw.last_error))
checa("cabecote desligado", not fw.vpp)

print()
print("20. ESPERANDO SLOT DE SWATH, O PC CONTINUA FALANDO")
# Mais swaths do que SWATH_SLOTS: a quinta espera a primeira terminar de
# imprimir, o que leva segundos. O ESP derruba a sessao depois de 1 s sem
# receber nada (LINK_TIMEOUT), entao o heartbeat nao pode depender de a
# fila do PC estar vazia. Visto na maquina em 27/10/2026 com 5 faixas.
s, fw, rel = monta()
prepara_job(s, fw, rel)
for i in range(pv.SWATH_SLOTS + 1):
    s.enviar_swath(i, 400, 1000, 3, bytes(16 * 20), 0, 5000)
envios = []
original = s._t.escrever
s._t.escrever = lambda d: (envios.append(rel()), original(d))
inicio = rel()
for _ in range(150):                  # 1,5 s com a maquina "imprimindo"
    s.bombear()
    fw.enviar_status()
    rel.avancar(0.01)
checa("a quinta swath ficou esperando slot",
      fw.swaths_executadas == 0 and s.pendentes > 0, f"{s.pendentes} pendentes")
tempos = [inicio] + envios + [rel()]
maior = max(b - a for a, b in zip(tempos, tempos[1:]))
checa("nenhum silencio do PC chega perto do LINK_TIMEOUT",
      maior < pv.LINK_TIMEOUT_S / 2, f"maior silencio = {maior*1000:.0f} ms")

print()
print("21. DESLIGAR OS MOTORES (protocolo 1.3)")
s, fw, rel = monta()
checa("o firmware anuncia MOTORS_OFF", s.tem_motors_off)
s.referenciar("xyz")
rodar(s, fw, rel, passos=10)
checa("eixos referenciados antes", all(fw.referenciado))
s.desligar_motores()
avisos = rodar(s, fw, rel, passos=10)
checa("MOTORS_OFF aceito em IDLE",
      not [a for a in avisos if a.tipo == Evento.ERRO])
checa("todos os eixos perdem a referencia", not any(fw.referenciado))
prepara_job(s, fw, rel)
s.desligar_motores()
avisos = rodar(s, fw, rel, passos=10)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("em JOB e recusado com BAD_STATE",
      any("BAD_STATE" in str(e) for e in erros), "; ".join(map(str, erros)))

print()
print("22. DESLIGAR AO FECHAR E AO PERDER O ENLACE (protocolo 1.4)")
s, fw, rel = monta()
s.referenciar("xyz")
s.cabecote(True)
rodar(s, fw, rel, passos=10)
checa("antes: cabecote ligado e eixos referenciados",
      fw.vpp and all(fw.referenciado))
ok = s.desligar_para_fechar()
rodar(s, fw, rel, passos=10)
checa("desconectar em IDLE desliga o cabecote e os motores",
      ok and not fw.vpp and not any(fw.referenciado),
      f"vpp={fw.vpp}, ref={fw.referenciado}")

s, fw, rel = monta()
prepara_job(s, fw, rel)
checa("em JOB o PC nao tenta desligar (quem cancela e o ESP)",
      not s.desligar_para_fechar() and fw.estado == pv.Estado.JOB)
fw.perder_enlace()
checa("enlace perdido em JOB: job cancelado com LINK_LOST",
      fw.estado == pv.Estado.IDLE and fw.last_error == int(pv.Erro.LINK_LOST))
checa("enlace perdido: cabecote e motores desligados",
      not fw.vpp and not any(fw.referenciado)
      and fw.motores_desligados_por_enlace == 1)

s, fw, rel = monta()
s.referenciar("xyz")
s.cabecote(True)
rodar(s, fw, rel, passos=10)
fw.perder_enlace()
checa("enlace perdido em IDLE tambem desliga tudo",
      not fw.vpp and not any(fw.referenciado))

print()
print("23. SWATH ACIMA DO VMAX_X E CANCELAMENTO")
s, fw, rel = monta()
prepara_job(s, fw, rel)
vmax = fw.parametros[pv.Param.VMAX_X]
for k in range(3):
    s.enviar_swath(swath_id=k, y_passos=400, x_start_passos=1000, pitch=3,
                   colunas=bytes(16 * 3000), fire_delay_us=0,
                   vel_passos_s=vmax + 1)
avisos = rodar(s, fw, rel, passos=3, executar=False)
erros = [a.dado for a in avisos if a.tipo == Evento.ERRO]
checa("swath acima do VMAX_X recusada com BAD_PARAM detalhe 16",
      erros and int(erros[0].code) == int(pv.Erro.BAD_PARAM)
      and erros[0].detail == 16, str(erros[:1]))
na_fila = len(s._fila)
s.cancelar()
checa("cancelar descarta o que ainda nao saiu do PC",
      na_fila > 0 and not s._fila, f"{na_fila} quadros descartados")
avisos = rodar(s, fw, rel, passos=30)
seq = [a for a in avisos if a.tipo == Evento.ERRO
       and int(a.dado.code) == int(pv.Erro.SWATH_SEQUENCE)]
checa("depois do cancelamento, no maximo a janela em voo e recusada",
      len(seq) <= pv.WINDOW, f"{len(seq)} SWATH_SEQUENCE")
s.ping()
avisos = rodar(s, fw, rel, passos=10)
checa("a sessao continua utilizavel depois de descartar",
      s.aberta and not s.pendentes
      and not [a for a in avisos if a.tipo == Evento.ERRO])

print()
print("=" * 72)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
