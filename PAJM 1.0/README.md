# Firmware do PAJM

Firmware do ESP32-S3 (YD-ESP32-S3 N16R8) que controla o posicionamento e o
disparo do cabeçote Xaar XJ128/360. Fala com o PAJM Controle pelo protocolo
descrito em `../PROTOCOL.md` (v1.4), que é o contrato entre os dois lados.

Fronteira: o PC não faz timing; o firmware não faz geometria.

## Compilar e gravar

ESP-IDF 5.5.5. No PowerShell com o ambiente ativado:

```
idf.py build
idf.py -p COMx flash monitor
```

A placa expõe duas portas: a **USB nativa** é o console (`idf.py monitor`,
logs) e a do **CH343P** é o protocolo (UART0 a 2 Mbaud). Nenhum log sai
pela UART0.

Para testar o protocolo, com o monitor fechado na porta do CH343P:

```
cd "../CONTROLE 1.0"
python ferramentas/bringup.py COMy
```

## Organização

| Arquivo | Papel |
|---|---|
| `main/app_main.c` | boot e laço de comunicação (núcleo 0), heartbeat do watchdog |
| `main/placa.c` | níveis seguros no boot, corte de emergência, GPIO38 |
| `main/pinos.h` | pinagem da placa pajm_V2.1 |
| `main/maquina.h` | passos/m, curso e valores iniciais; vão para o HELLO_ACK |
| `main/protocolo.h` | constantes e códigos do PROTOCOL.md |
| `main/crc.c`, `main/quadro.c` | CRC-16, CRC-32, COBS e montagem de quadros (funções puras) |
| `main/enlace.c` | UART0 e envio/recepção de quadros |
| `main/sessao.c` | Go-Back-N, sessão, estados, estado planejado e tratamento dos opcodes |
| `main/cabecote.c` | CLK de 1 MHz, SPI2 (nSS2 e nSS1), nFIRE, READY e nFAULT; tarefa no núcleo 1 |
| `main/movimento.c` | fila de execução, HOME, JOG, MOVE, DWELL; passos por timer com rampa (AVR446) |
| `main/parametros.c` | SET/GET/SAVE_PARAM, com persistência na NVS |
| `main/autoteste.c` | confere os vetores da seção 10 a cada boot |
| `teste_motores/` | projeto separado: teste manual dos eixos pelo monitor |

## Estado

### Etapa 1: enlace e sessão (concluída)

Implementado: HELLO/HELLO_ACK, janela Go-Back-N (ACK, NACK, duplicatas),
PING, STATUS a cada 100 ms, perda de enlace em 1 s, ESTOP, CANCEL,
CLEAR_FAULT, SET/GET/SAVE_PARAM e autoteste dos vetores no boot. HOME, JOG,
MOVE, cabeçote e swaths respondem `ERR UNKNOWN_OPCODE`.

Validado na placa em 26/10/2026 com `bringup.py`, tudo aprovado:
HELLO_ACK em 2 ms, STATUS a 100 ms (96 a 103 ms), `frames_dropped = 0` a
2 Mbaud pelo CH343P, leitura de parâmetros, ESTOP → FAULT e
CLEAR_FAULT → IDLE. Primeira conversa real do `transporte_serial.py` do
slicer com o hardware.

Decisões que se afastam da letra do PROTOCOL.md, de propósito:

- ESTOP é atendido mesmo sem sessão aberta: parar não deve depender dela
  (o simulador do slicer faz o mesmo).
- O bit nFAULT do STATUS só é reportado com VPP ligado; sem cabeçote o
  pino pode flutuar.
- `CHECK_READY = 0`, que o protocolo reserva para testes sem cabeçote,
  também desliga a vigilância do nFAULT, pelo mesmo motivo.

### Etapa 2: movimento (concluída)

Fila de execução com créditos (32 entradas); HOME, JOG, MOVE e DWELL;
CANCEL para com rampa; ESTOP e falhas cortam sem rampa e desligam os
drivers. Os passos saem de um gptimer em interrupção (núcleo 0), um eixo
por vez, com rampa trapezoidal calculada só com inteiros (AVR446).

HOME: o Z sobe 10 mm, X e Y vão aos endstops, e só então o Z desce até o
seu endstop. HOME com Z sempre referencia X junto, para o Z descer com o
carro na zona morta, fora da mesa. Cada eixo: aproximação rápida, recuo
e reaproximação lenta, como no teste_motores.

Critério: os botões da aba de comandos manuais do slicer movem a máquina.

Validado na máquina em 26/10/2026, pela aba de comandos manuais do slicer:
HOME dos três eixos, JOG e MOVE em X, Y e Z, CANCEL com rampa, ESTOP com
bloqueio de movimento até o CLEAR_FAULT. JOG de 100 mm em X e Y mede 100 mm
(80 passos/mm e polia de 20 dentes confirmados).

### Etapa 3: cabeçote parado (implementada, a conferir no analisador)

HEAD_POWER e SPIT, pela fila de execução. Energização conforme a seção
6.1.2 do manual: nRESET ativo, liga o VPP, espera 300 ms e libera o nRESET;
para desligar, nRESET ativo antes de cortar o VPP. CLK livre de 1 MHz pelo
LEDC. Coluna pelo SPI2 a 2 MHz, modo 0, nSS2 e depois nSS1 (64 bits cada).
Disparo: espera o READY alto, pulso de 3 µs no nFIRE, confere a queda do
READY em até 10 µs (T3 é de até 3,35 µs) e carrega a próxima coluna com o
READY baixo. nFAULT por interrupção leva a FAULT (HEAD_NFAULT). O disparo
roda numa tarefa no núcleo 1, exclusivo do cabeçote.

Ordem de validação: primeiro os sinais no analisador lógico, sem o cabeçote
(`CHECK_READY = 0`); só depois o cabeçote real, primeiro sem tinta.

### Em aberto

- Passos/mm, VMAX e ACCEL do Z: dependem do fuso.
- `MAX_COLS = 8192` não cobre 200 mm com pitch 1 a 80 passos/mm (16 000
  colunas): decidir entre pitch ≥ 2 e mudar o protocolo, ao chegar nas swaths.
- Menu de configuração da máquina no slicer (driver, micropasso,
  transmissão), com passos/mm guardados no ESP: exige PROTOCOL.md v1.2.
