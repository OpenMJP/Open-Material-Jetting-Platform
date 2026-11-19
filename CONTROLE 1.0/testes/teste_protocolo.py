"""
Conformidade com PROTOCOL.md v1.1.

A secao 10 do documento publica vetores de teste gerados por implementacao de
referencia, e exige que as implementacoes em C e em Python os reproduzam byte
a byte. Este arquivo faz exatamente isso: se passar, os dois lados concordam
no enlace, antes de existir hardware para descobrir o contrario.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pajm.machine import protocolo as pv

todos_ok = True


def checa(nome, cond, extra=""):
    global todos_ok
    todos_ok &= bool(cond)
    print(f"  [{'OK ' if cond else 'FALHA'}] {nome}" + (f"  {extra}" if extra else ""))


def hx(b) -> str:
    return " ".join(f"{x:02X}" for x in b)


print("=" * 72)
print("1. VALORES DE CONFERENCIA DOS CRC (secao 3.1 e 7.3)")
checa("CRC-16/CCITT-FALSE('123456789') == 0x29B1",
      pv.crc16(b"123456789") == 0x29B1, f"0x{pv.crc16(b'123456789'):04X}")
checa("CRC-32('123456789') == 0xCBF43926",
      pv.crc32(b"123456789") == 0xCBF43926, f"0x{pv.crc32(b'123456789'):08X}")

print()
print("2. COBS: ida e volta")
for caso in (b"", b"\x00", b"\x01\x02\x03", b"\x00\x00\x00",
             bytes(range(256)), b"\x11" * 300, bytes(600)):
    cod = pv.cobs_codifica(caso)
    ok = pv.cobs_decodifica(cod) == caso and 0 not in cod
    checa(f"ida e volta de {len(caso)} bytes", ok,
          f"-> {len(cod)} bytes, sem zeros")

# O overhead tem que ficar no limite prometido: 1 byte a cada 254
grande = b"\x42" * 1016
checa("overhead de no maximo 1 byte a cada 254",
      len(pv.cobs_codifica(grande)) - len(grande) <= 1 + len(grande) // 254,
      f"+{len(pv.cobs_codifica(grande)) - len(grande)} bytes em 1016")

print()
print("3. VETORES DE TESTE DA SECAO 10")

vetores = [
    ("HELLO v1.1 (seq 0)",
     pv.Quadro(pv.Op.HELLO, 0, pv.p_hello(1, 1)),
     "01 00 01 01 64 D1",
     "02 01 05 01 01 64 D1 00"),
    ("PING (seq 1)",
     pv.Quadro(pv.Op.PING, 1),
     "02 01 4C 6B",
     "05 02 01 4C 6B 00"),
]

for nome, quadro, esp_bruto, esp_fio in vetores:
    bruto, fio = quadro.bruto(), quadro.no_fio()
    ok_b = hx(bruto) == esp_bruto
    ok_f = hx(fio) == esp_fio
    checa(f"{nome}: quadro bruto", ok_b, hx(bruto))
    if not ok_b:
        print(f"        esperado: {esp_bruto}")
    checa(f"{nome}: no fio", ok_f, hx(fio))
    if not ok_f:
        print(f"        esperado: {esp_fio}")

# ACK do ESP: montado na mao, porque o PC so recebe este quadro
ack = pv.Quadro(pv.Op.ACK, 7, struct.pack("<BBBB", 1, 4, 32, 0))
checa("ACK do ESP: quadro bruto", hx(ack.bruto()) == "81 07 01 04 20 00 D6 54",
      hx(ack.bruto()))
checa("ACK do ESP: no fio",
      hx(ack.no_fio()) == "06 81 07 01 04 20 03 D6 54 00", hx(ack.no_fio()))

# SWATH_DATA com uma coluna acionando CH128 e CH1
coluna = bytes([0x80] + [0x00] * 14 + [0x01])
sd = pv.Quadro(pv.Op.SWATH_DATA, 5, pv.p_swath_data(1, 0, coluna))
esp_bruto = ("31 05 01 00 00 00 80 00 00 00 00 00 00 00 00 00 "
             "00 00 00 00 00 01 29 BA")
esp_fio = ("04 31 05 01 01 01 02 80 01 01 01 01 01 01 01 01 "
           "01 01 01 01 01 04 01 29 BA 00")
checa("SWATH_DATA: quadro bruto", hx(sd.bruto()) == esp_bruto, hx(sd.bruto()))
if hx(sd.bruto()) != esp_bruto:
    print(f"        esperado: {esp_bruto}")
checa("SWATH_DATA: no fio", hx(sd.no_fio()) == esp_fio, hx(sd.no_fio()))
if hx(sd.no_fio()) != esp_fio:
    print(f"        esperado: {esp_fio}")

checa("CRC-32 da swath de uma coluna == 0xC7419637",
      pv.crc32(coluna) == 0xC7419637, f"0x{pv.crc32(coluna):08X}")

print()
print("4. A COLUNA DE 16 BYTES BATE COM A SECAO 6.1")
# "acionar so CH128 e CH1 resulta em byte 0 = 0x80, byte 15 = 0x01"
# Esta e a mesma ordem que o codificador do fatiador ja produzia.
import numpy as np
from pajm.core.codificador import MatrizJateamento, empacotar
from pajm.profile import Cabecote

cab = Cabecote(sentido_indice=1)
log = np.zeros((1, 128), dtype=bool)
log[0, 127] = True          # canal 128
log[0, 0] = True            # canal 1
saida = empacotar(MatrizJateamento(log, 360, 360), cab)[0]
checa("CH128 e CH1 -> byte 0 = 0x80 e byte 15 = 0x01",
      saida[0] == 0x80 and saida[15] == 0x01 and saida.sum() == 0x81,
      hx(saida))

# Formula geral da secao 6.1: o bit j do byte b aciona 128 - 8*b - (7 - j)
ok_formula = True
for canal in (1, 8, 9, 64, 65, 72, 121, 128):
    log = np.zeros((1, 128), dtype=bool)
    log[0, canal - 1] = True
    s = empacotar(MatrizJateamento(log, 360, 360), cab)[0]
    b = (128 - canal) // 8
    j = 7 - ((128 - canal) % 8)
    if s[b] != (1 << j) or int(s.sum()) != (1 << j):
        ok_formula = False
        print(f"        canal {canal}: esperava byte {b} bit {j}, veio {hx(s)}")
checa("formula bit/byte da secao 6.1 confere em 8 canais", ok_formula)

# coluna_de_canais (padroes do SPIT) tem de dar os mesmos bytes que o
# codificador do fatiador, canal a canal
c = pv.coluna_de_canais((1, 128))
checa("coluna_de_canais: CH128 e CH1 -> 0x80 ... 0x01",
      c == bytes([0x80] + [0] * 14 + [0x01]), hx(c))
ok_igual = True
for canal in range(1, 129):
    log = np.zeros((1, 128), dtype=bool)
    log[0, canal - 1] = True
    if bytes(empacotar(MatrizJateamento(log, 360, 360), cab)[0]) \
            != pv.coluna_de_canais((canal,)):
        ok_igual = False
checa("coluna_de_canais igual ao codificador nos 128 canais", ok_igual)
checa("todos os bicos -> 16 bytes 0xFF",
      pv.coluna_de_canais(range(1, 129)) == b"\xff" * 16)

print()
print("5. DECODIFICADOR: fluxo, fragmentacao e lixo")
d = pv.Decodificador()
juntos = b"".join(
    pv.Quadro(pv.Op.PING, i).no_fio() for i in range(5))
checa("5 quadros colados", len(d.alimentar(juntos)) == 5)

d = pv.Decodificador()
saida = []
for b in pv.Quadro(pv.Op.HELLO, 0, pv.p_hello()).no_fio():
    saida += d.alimentar(bytes([b]))
checa("quadro remontado byte a byte", len(saida) == 1)

# Lixo de boot do ESP antes do primeiro quadro valido
d = pv.Decodificador()
lixo = b"ets Jun  8 2016 00:22:57\r\nrst:0x1 (POWERON_RESET)\r\n"
r = d.alimentar(lixo + pv.Quadro(pv.Op.PING, 1).no_fio())
# O quadro colado no lixo E PERDIDO, e isso esta correto: sem um 0x00 entre
# eles, o receptor nao tem como saber onde o lixo acaba. E por isso que a
# secao 5.1 permite mandar um 0x00 antes do HELLO e que o exemplo da secao 9
# comeca com "0x00, HELLO". O que importa e recuperar no quadro seguinte.
checa("quadro colado no lixo de boot e descartado",
      len(r) == 0 and d.frames_dropped == 1, f"dropped={d.frames_dropped}")
checa("o quadro SEGUINTE ao lixo chega inteiro",
      len(d.alimentar(pv.Quadro(pv.Op.PING, 2).no_fio())) == 1)

d = pv.Decodificador()
r = d.alimentar(lixo + bytes([0x00])
                + pv.Quadro(pv.Op.HELLO, 0, pv.p_hello()).no_fio())
checa("o 0x00 de sincronismo da secao 5.1 salva ate o primeiro quadro",
      len(r) == 1 and r[0].tipo == pv.Op.HELLO)

# CRC corrompido tem que ser descartado e contado
bom = bytearray(pv.Quadro(pv.Op.PING, 2).no_fio())
bom[-2] ^= 0xFF
d = pv.Decodificador()
r = d.alimentar(bytes(bom) + pv.Quadro(pv.Op.PING, 3).no_fio())
checa("CRC ruim descartado e contado",
      len(r) == 1 and d.frames_dropped == 1, f"dropped={d.frames_dropped}")

# COBS invalido
d = pv.Decodificador()
r = d.alimentar(b"\x05\x01\x02\x00")
checa("bloco COBS invalido descartado",
      len(r) == 0 and d.frames_dropped == 1)

# 0x00 isolado e quadro vazio: ignorado sem contar como descarte
d = pv.Decodificador()
r = d.alimentar(b"\x00\x00" + pv.Quadro(pv.Op.PING, 4).no_fio())
checa("0x00 isolado ignorado",
      len(r) == 1 and d.frames_dropped == 0, f"dropped={d.frames_dropped}")

# Acumulo sem delimitador tem que ressincronizar
d = pv.Decodificador()
r = d.alimentar(b"\x42" * 2000 + pv.Quadro(pv.Op.PING, 5).no_fio())
checa("acumulo sem delimitador entra em modo de descarte",
      len(r) == 0 and d.frames_dropped >= 1, f"dropped={d.frames_dropped}")
checa("volta a decodificar no quadro seguinte",
      len(d.alimentar(pv.Quadro(pv.Op.PING, 6).no_fio())) == 1)

print()
print("6. PAYLOADS COM OS TAMANHOS DA SECAO 7.3")
casos = [
    (pv.Op.HELLO, pv.p_hello()),
    (pv.Op.JOB_BEGIN, pv.p_job_begin(1, 23)),
    (pv.Op.HOME, pv.p_home(0b111)),
    (pv.Op.HEAD_POWER, pv.p_head_power(True)),
    (pv.Op.MOVE, pv.p_move(pv.EIXO_Z, 1200, 4000)),
    (pv.Op.DWELL, pv.p_dwell(2000)),
    (pv.Op.SPIT, pv.p_spit(200, 500, bytes(16))),
    (pv.Op.SWATH_BEGIN,
     pv.p_swath_begin(0, False, False, 3, 400, 100, 1500, 0, 5000)),
    (pv.Op.SWATH_END, pv.p_swath_end(0, 0xC7419637)),
    (pv.Op.JOG, pv.p_jog(pv.EIXO_X, -400, 2000)),
    (pv.Op.SET_PARAM, pv.p_set_param(pv.Param.VMAX_X, 8000)),
    (pv.Op.GET_PARAM, pv.p_get_param(pv.Param.ACCEL_X)),
]
for op, payload in casos:
    esperado = pv.TAMANHOS[op]
    checa(f"{op.name}: {esperado} bytes", len(payload) == esperado,
          f"{len(payload)} bytes")

# SWATH_DATA variavel: 4 + 16n, de 20 a 1028
checa("SWATH_DATA com 1 coluna = 20 bytes",
      len(pv.p_swath_data(1, 0, bytes(16))) == 20)
checa("SWATH_DATA com 64 colunas = 1028 bytes",
      len(pv.p_swath_data(1, 0, bytes(64 * 16))) == 1028)
checa("SWATH_DATA com 64 colunas cabe em MAX_PAYLOAD",
      len(pv.p_swath_data(1, 0, bytes(64 * 16))) == pv.MAX_PAYLOAD)
try:
    pv.p_swath_data(1, 0, bytes(65 * 16))
    checa("65 colunas recusadas", False)
except ValueError:
    checa("65 colunas recusadas", True)

# O maior quadro no fio tem que bater com a secao 8.1
# O pior caso do COBS e um payload SEM nenhum zero, que e quando ele gasta
# um byte de codigo a cada 254. Com dados zerados o quadro sai menor.
pior = pv.Quadro(pv.Op.SWATH_DATA, 0,
                 pv.p_swath_data(1, 0, bytes([0xAB]) * (64 * 16)))
checa(f"pior caso no fio = {pv.MAIOR_QUADRO_NO_FIO} bytes",
      len(pior.no_fio()) == pv.MAIOR_QUADRO_NO_FIO,
      f"{len(pior.no_fio())} bytes")

print()
print("7. ESTRUTURAS DO ESP -> PC")
ha = pv.HelloAck(1, 1, 1, 0, 0, 8, 4, 32, 1028, 8192, 200,
                 (40000, 40000, 160000), (8000, 8000, 6000))
checa("HELLO_ACK tem 40 bytes", pv.TAMANHO_HELLO_ACK == 40,
      f"{pv.TAMANHO_HELLO_ACK} bytes")
checa("passos por mm derivado do steps_per_m",
      ha.passos_por_mm(pv.EIXO_X) == 40.0, f"{ha.passos_por_mm(0)} passos/mm")

st = pv.Status(1, 0b101111, 7, 12, 3, 30, (1000, 2000, 300), 0, 0, 5)
checa("STATUS tem 24 bytes", pv.TAMANHO_STATUS == 24,
      f"{pv.TAMANHO_STATUS} bytes")
checa("STATUS ida e volta", pv.Status.de_bytes(st.bytes()) == st)
# 0b101111 = bits 0,1,2,3,5: X, Y e Z referenciados, VPP ligado, nFAULT
# inativo (bit 4 = 0), swath em execucao.
checa("flags do STATUS",
      st.referenciado_x and st.referenciado_y and st.referenciado_z
      and st.vpp_ligado and not st.nfault and st.swath_em_execucao,
      f"flags=0b{st.flags:06b}")
checa("estado com nome", st.nome_estado == "JOB", st.nome_estado)

e = pv.Err.de_bytes(struct.pack("<BBHi", 9, pv.Erro.OUT_OF_TRAVEL, 0, -40))
checa("ERR decodificado", e.code == pv.Erro.OUT_OF_TRAVEL and e.detail == -40,
      str(e))

print()
print("=" * 72)
print("RESULTADO:", "TODOS OS TESTES PASSARAM" if todos_ok else "HOUVE FALHA")
sys.exit(0 if todos_ok else 1)
