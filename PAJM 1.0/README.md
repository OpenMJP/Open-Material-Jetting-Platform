# Firmware do PAJM

Firmware do ESP32-S3 (YD-ESP32-S3 N16R8) que controla o posicionamento e o
disparo do cabeçote Xaar XJ128/360. Fala com o PAJM Slicer pelo protocolo
descrito em `../PROTOCOL.md` (v1.1), que é o contrato entre os dois lados.

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
cd "../SLICER 1.0"
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
| `main/sessao.c` | Go-Back-N, sessão, estados e tratamento dos opcodes |
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

### Etapa 2: movimento (próxima)

Fila de execução com créditos; HOME, JOG e MOVE com geração de passos por
timer; CANCEL com rampa. Critério: os botões da aba de comandos manuais do
slicer movem a máquina.

### Em aberto

- Passos/mm de X e Y (80, supondo polia de 20 dentes): confirmar medindo.
- Passos/mm, VMAX e ACCEL do Z: dependem do fuso.
- `MAX_COLS = 8192` não cobre 200 mm com pitch 1 a 80 passos/mm (16 000
  colunas): decidir entre pitch ≥ 2 e mudar o protocolo, ao chegar nas swaths.
- Menu de configuração da máquina no slicer (driver, micropasso,
  transmissão), com passos/mm guardados no ESP: exige PROTOCOL.md v1.2.
