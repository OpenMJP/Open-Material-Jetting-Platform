"""
Bring-up: primeiro contato entre o PC e o ESP32-S3.

Roda na linha de comando, sem interface grafica no caminho. Quando o primeiro
HELLO falha numa bancada, voce quer ver os BYTES, nao uma caixa de dialogo
dizendo "nao consegui conectar".

Cobre, na ordem, as verificacoes da secao 11 do PROTOCOL.md que dependem do
software. As que dependem de osciloscopio ou analisador logico ficam de fora
e estao listadas no fim.

USO
    python ferramentas/bringup.py                 lista as portas
    python ferramentas/bringup.py COM3            testa a COM3 a 2 Mbaud
    python ferramentas/bringup.py COM3 921600     testa noutra taxa
    python ferramentas/bringup.py COM3 --escuta   so escuta, sem transmitir
    python ferramentas/bringup.py --sim           ensaia contra o simulador

O modo --escuta e util antes de qualquer coisa: liga a placa e ve o que ela
cospe sozinha. Se aparecer texto legivel, o firmware esta logando na UART0 e
vai corromper os quadros (a secao 2 manda o console sair pela USB nativa).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pajm.machine import protocolo as pv
from pajm.machine.sessao import EnlacePerdido, Evento, Sessao, VersaoIncompativel
from pajm.machine.simulador import FirmwareSimulado, TransporteSimulado
from pajm.machine.transporte_serial import SerialIndisponivel, TransporteSerial

LARGURA = 74


def titulo(t):
    print()
    print("=" * LARGURA)
    print(t)
    print("=" * LARGURA)


def item(ok, nome, extra=""):
    marca = "OK " if ok else "FALHA"
    print(f"  [{marca}] {nome}" + (f"   {extra}" if extra else ""))
    return bool(ok)


def hexdump(dados: bytes, prefixo: str = "", maximo: int = 256) -> str:
    corte = dados[:maximo]
    linhas = []
    for i in range(0, len(corte), 16):
        bloco = corte[i:i + 16]
        hx = " ".join(f"{b:02X}" for b in bloco)
        txt = "".join(chr(b) if 32 <= b < 127 else "." for b in bloco)
        linhas.append(f"{prefixo}{i:04X}  {hx:<47}  |{txt}|")
    if len(dados) > maximo:
        linhas.append(f"{prefixo}... mais {len(dados) - maximo} bytes")
    return "\n".join(linhas)


def listar_portas():
    titulo("PORTAS SERIAIS VISIVEIS")
    portas = TransporteSerial.portas_disponiveis()
    if not portas:
        print("  nenhuma porta encontrada.")
        print()
        print("  Se a placa esta ligada, o driver do CH343 provavelmente nao")
        print("  esta instalado. Baixe em wch-ic.com (CH343SER) e reinicie.")
        return
    for dev, desc in portas:
        marca = "  <- parece o CH343" if "CH34" in desc.upper() else ""
        print(f"  {dev:8s}  {desc}{marca}")
    print()
    print("  O ESP tem DUAS portas: a do CH343P (protocolo) e a USB nativa")
    print("  (console do idf.py monitor). Use a do CH343 aqui.")


def escutar(porta: str, baud: int, segundos: float = 10.0):
    titulo(f"ESCUTA PASSIVA EM {porta} A {baud} BAUD")
    print(f"  Ouvindo por {segundos:.0f} s sem transmitir nada.")
    print("  Reinicie a placa agora para ver o que ela cospe no boot.")
    print()
    try:
        t = TransporteSerial(porta, baud)
    except Exception as e:
        print(f"  nao consegui abrir: {e}")
        return
    acumulado = bytearray()
    fim = time.perf_counter() + segundos
    try:
        while time.perf_counter() < fim:
            d = t.ler()
            if d:
                acumulado.extend(d)
            time.sleep(0.01)
    finally:
        t.fechar()

    if not acumulado:
        print("  Nada chegou.")
        print()
        print("  Isso e o ESPERADO se o firmware so fala quando recebe HELLO")
        print("  (secao 5.1: sem sessao, o ESP fica em silencio).")
        return

    print(f"  {len(acumulado)} bytes recebidos:")
    print(hexdump(bytes(acumulado), "    "))
    legiveis = sum(1 for b in acumulado if 32 <= b < 127)
    if legiveis > len(acumulado) * 0.6:
        print()
        print("  ATENCAO: a maior parte e texto legivel. Se isso continuar")
        print("  depois do boot, o console do ESP-IDF esta saindo pela UART0")
        print("  e vai corromper os quadros. A secao 2 manda usar")
        print("  CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y.")

    dec = pv.Decodificador()
    quadros = dec.alimentar(bytes(acumulado))
    print()
    print(f"  quadros validos no meio disso: {len(quadros)}, "
          f"descartados: {dec.frames_dropped}")
    for q in quadros[:5]:
        print(f"    {q.nome} seq={q.seq} payload={len(q.payload)} B")


def conectar(porta: str, baud: int, simulado: bool = False):
    titulo(f"BRING-UP {'(SIMULADO)' if simulado else porta + f' a {baud} baud'}")
    ok_geral = True
    trafego: list[tuple[str, bytes]] = []

    def espiao(direcao, dados):
        trafego.append((direcao, dados))

    # ---- 1. abrir a porta ------------------------------------------------
    print("1. Abertura da porta")
    try:
        if simulado:
            fw = FirmwareSimulado()
            transporte = TransporteSimulado(fw, auto_executar=True)
        else:
            fw = None
            transporte = TransporteSerial(porta, baud, espiao=espiao)
        ok_geral &= item(True, "porta aberta",
                         "DTR e RTS desativados antes de abrir")
    except SerialIndisponivel as e:
        item(False, "pyserial", str(e))
        return False
    except Exception as e:
        item(False, "abrir a porta", f"{type(e).__name__}: {e}")
        print()
        print("  Se a porta existe mas da acesso negado, outro programa esta")
        print("  com ela aberta: o idf.py monitor, ou o proprio PAJM Controle.")
        return False

    sessao = Sessao(transporte)

    # ---- 2. handshake ----------------------------------------------------
    print()
    print("2. Handshake HELLO")
    t0 = time.perf_counter()
    try:
        ha = sessao.abrir()
        dt = time.perf_counter() - t0
        ok_geral &= item(True, "HELLO_ACK recebido", f"em {dt*1000:.0f} ms")
    except VersaoIncompativel as e:
        item(False, "versao do protocolo", str(e))
        return False
    except EnlacePerdido as e:
        item(False, "HELLO", str(e))
        print()
        print("  O que chegou no fio durante as tentativas:")
        recebido = b"".join(d for s, d in trafego if s == "rx")
        if recebido:
            print(hexdump(recebido, "    "))
        else:
            print("    nada. O ESP esta mudo.")
            print()
            print("    Conferir: a placa esta alimentada? e a porta do CH343P")
            print("    e nao a USB nativa? o TX e o RX estao trocados? a taxa")
            print("    bate com a do firmware? Tente --escuta e reinicie a")
            print("    placa para ver se ela cospe o boot nesta porta.")
        return False
    finally:
        pass

    print(f"     protocolo {ha.proto_major}.{ha.proto_minor}, "
          f"firmware {ha.versao_firmware}")
    ok_geral &= item(ha.proto_major == pv.PROTO_MAJOR,
                     "versao maior do protocolo compativel")
    print(f"     janela {ha.window}, {ha.swath_slots} slots de swath, "
          f"{ha.queue_slots} de fila")
    print(f"     payload max {ha.max_payload} B, colunas max {ha.max_cols}")
    print(f"     periodo minimo de disparo {ha.min_fire_period_us} us")
    for eixo, nome in ((pv.EIXO_X, "X"), (pv.EIXO_Y, "Y"), (pv.EIXO_Z, "Z")):
        pm = ha.passos_por_mm(eixo)
        print(f"     {nome}: {pm:g} passos/mm, curso {ha.travel[eixo]} passos "
              f"= {ha.travel[eixo]/pm:.1f} mm")

    ok_geral &= item(ha.max_payload >= pv.MAX_PAYLOAD,
                     "payload maximo compativel",
                     f"{ha.max_payload} B")
    ok_geral &= item(all(ha.passos_por_mm(e) > 0 for e in range(3)),
                     "passos por mm declarados nos tres eixos")
    ok_geral &= item(all(t > 0 for t in ha.travel),
                     "curso declarado nos tres eixos")

    # ---- 3. STATUS periodico ---------------------------------------------
    print()
    print("3. STATUS periodico (secao 5.2: a cada 100 ms)")
    marcas = []
    fim = time.perf_counter() + 1.5
    while time.perf_counter() < fim:
        for aviso in sessao.bombear():
            if aviso.tipo == Evento.STATUS:
                marcas.append(time.perf_counter())
        if fw:
            fw.enviar_status()
            time.sleep(0.05)
        else:
            time.sleep(0.005)
    ok_geral &= item(len(marcas) >= 5, f"{len(marcas)} STATUS em 1,5 s")
    if len(marcas) >= 3:
        periodos = [(b - a) * 1000 for a, b in zip(marcas, marcas[1:])]
        media = sum(periodos) / len(periodos)
        print(f"     periodo medio {media:.0f} ms "
              f"(min {min(periodos):.0f}, max {max(periodos):.0f})")
    s = sessao.status
    if s:
        print(f"     estado {s.nome_estado}, posicao {s.pos} passos")
        refs = "".join(e.upper() if r else "-" for e, r in zip(
            "xyz", (s.referenciado_x, s.referenciado_y, s.referenciado_z)))
        print(f"     referenciados: {refs}, VPP "
              f"{'ligado' if s.vpp_ligado else 'desligado'}, "
              f"nFAULT {'ATIVO' if s.nfault else 'inativo'}")
        ok_geral &= item(not s.nfault, "nFAULT do cabecote inativo")
        ok_geral &= item(s.frames_dropped == 0,
                         "nenhum quadro descartado pelo ESP",
                         f"frames_dropped={s.frames_dropped}")

    # ---- 4. parametros ---------------------------------------------------
    print()
    print("4. Leitura de parametros")
    try:
        ids = [pv.Param.ACCEL_X, pv.Param.ACCEL_Y, pv.Param.VMAX_X,
               pv.Param.VMAX_Y, pv.Param.RUNUP_MARGIN,
               pv.Param.MIN_FIRE_PERIOD, pv.Param.CHECK_READY]
        vals = sessao.obter_parametros(ids, timeout_s=3.0)
        for i in ids:
            print(f"     {pv.Param(i).name:18s} = {vals[int(i)]}")
        ok_geral &= item(True, "todos os parametros responderam")
        recuo = pv.recuo_de_aceleracao(
            vals[int(pv.Param.VMAX_X)], vals[int(pv.Param.ACCEL_X)],
            vals[int(pv.Param.RUNUP_MARGIN)])
        pmx = ha.passos_por_mm(pv.EIXO_X)
        print(f"     recuo de aceleracao na velocidade maxima: {recuo} passos "
              f"= {recuo/pmx:.1f} mm de cada lado")
    except Exception as e:
        ok_geral &= item(False, "leitura de parametros", str(e))

    # ---- 5. vazao --------------------------------------------------------
    print()
    print("5. Vazao do enlace (secao 11: meta de 100 KB/s com janela 8)")
    if simulado:
        print("     pulado no modo simulado: nao ha fio.")
    else:
        antes_tx = transporte.bytes_tx
        t0 = time.perf_counter()
        enviados = 0
        # PING e inofensivo e passa pela mesma janela que os dados
        for _ in range(200):
            sessao.ping()
        while sessao.pendentes and time.perf_counter() - t0 < 10.0:
            sessao.bombear()
            enviados = transporte.bytes_tx - antes_tx
        dt = time.perf_counter() - t0
        taxa = enviados / dt if dt > 0 else 0
        print(f"     {enviados} B em {dt*1000:.0f} ms = {taxa/1000:.1f} KB/s "
              "(quadros pequenos; a vazao real com swaths e maior)")
        ok_geral &= item(sessao.pendentes == 0,
                         "a janela escoou sem travar",
                         f"{sessao.pendentes} pendentes")
        if sessao.status:
            ok_geral &= item(sessao.status.frames_dropped == 0,
                             "sem quadros descartados no trafego",
                             f"frames_dropped={sessao.status.frames_dropped}")

    # ---- 6. parada -------------------------------------------------------
    print()
    print("6. ESTOP e CLEAR_FAULT")
    print("     NAO energiza o cabecote nem move eixo: so exercita o caminho.")
    sessao.parar()
    fim = time.perf_counter() + 1.5
    virou = False
    while time.perf_counter() < fim and not virou:
        for aviso in sessao.bombear():
            pass
        if fw:
            fw.enviar_status()
            time.sleep(0.02)
        else:
            time.sleep(0.005)
        if sessao.status and sessao.status.state == pv.Estado.FAULT:
            virou = True
    ok_geral &= item(virou, "ESTOP leva a maquina para FAULT",
                     sessao.status.nome_estado if sessao.status else "?")
    if virou:
        print(f"     motivo: {pv.nome_da_falha(sessao.status.fault)}")

    sessao.limpar_falha()
    fim = time.perf_counter() + 1.5
    voltou = False
    while time.perf_counter() < fim and not voltou:
        for aviso in sessao.bombear():
            pass
        if fw:
            fw.avancar(2)
            fw.enviar_status()
            time.sleep(0.02)
        else:
            time.sleep(0.005)
        if sessao.status and sessao.status.state == pv.Estado.IDLE:
            voltou = True
    ok_geral &= item(voltou, "CLEAR_FAULT volta para IDLE",
                     sessao.status.nome_estado if sessao.status else "?")

    sessao.fechar()

    # ---- fecho -----------------------------------------------------------
    titulo("RESULTADO: " + ("TUDO PASSOU" if ok_geral else "HOUVE FALHA"))
    if not simulado:
        rx = sum(len(d) for s_, d in trafego if s_ == "rx")
        tx = sum(len(d) for s_, d in trafego if s_ == "tx")
        print(f"  trafego total: {tx} B enviados, {rx} B recebidos")
    print()
    print("  O QUE ESTE TESTE NAO COBRE, e que a secao 11 pede:")
    print("   - 1 MB transmitido com frames_dropped = 0 (so com swaths reais)")
    print("   - se abrir a porta reinicia o ESP (confira se o STATUS zera)")
    print("   - latencia do ESTOP medida no analisador logico, meta < 50 ms")
    print("   - MIN_FIRE_PERIOD contra o cabecote real, pelo sinal READY")
    print("   - grade de disparo com pitch 1 contra pitch 3, escaneada")
    print("   - ordem do HOME conforme a posicao do endstop de Z")
    return ok_geral


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if "--sim" in args:
        return 0 if conectar("", 0, simulado=True) else 1
    if not args:
        listar_portas()
        print()
        print("  Para testar:  python ferramentas/bringup.py COM3")
        return 0

    porta = args[0]
    baud = pv.BAUD
    for a in args[1:]:
        if a.isdigit():
            baud = int(a)
    if "--escuta" in args:
        escutar(porta, baud)
        return 0
    return 0 if conectar(porta, baud) else 1


if __name__ == "__main__":
    sys.exit(main())
