# PAJM: Protocolo de comunicação PC ↔ ESP32-S3

Versão **1.4** · 29/10/2026 · Status: implementado no firmware e no programa de controle, em uso na máquina (validação na seção 11)

Este documento é o contrato entre o software de controle (PC, Python) e o firmware (ESP32-S3, ESP-IDF 5.5.5). Toda mudança no protocolo começa aqui, antes do código, e altera a versão conforme a seção 12.

Convenções: "PC" é o software de controle e "ESP" é o firmware. DEVE, NÃO DEVE e PODE indicam requisito, proibição e opção. Constantes em MAIÚSCULAS estão na seção 8.

---

## 1. Visão geral

| Camada OSI | Problema que resolve | No PAJM |
|---|---|---|
| 1. Física | bits no fio | CH343P ↔ UART0, 2 Mbaud (§2) |
| 2. Enlace | onde o quadro começa e termina; chegou íntegro? | COBS + CRC-16 (§3) |
| 3. Rede | endereçamento entre muitos nós | não existe (ponto a ponto) |
| 4. Transporte | chegou tudo, em ordem, sem estourar o receptor? | sequência, janela Go-Back-N, créditos (§4) |
| 5. Sessão | conexão aberta? mesma versão dos dois lados? | HELLO, heartbeat, timeout (§5) |
| 6. Apresentação | como os dados são representados | little-endian, passos inteiros, coluna na ordem do SPI (§6) |
| 7. Aplicação | o que cada mensagem significa | estados, comandos, swaths (§7) |

Princípio de divisão de trabalho: o PC faz todo o processamento de imagem (inclinação do cabeçote a 31°, compensação dos ciclos A-B-C, espaçamento das gotas, recorte das swaths). O ESP recebe colunas prontas, na ordem de carga do SPI, e posições em passos. Cabe a ele apenas validar, mover e disparar.

---

## 2. Camada física

A comunicação usa a UART0 do ESP (GPIO43 TX, GPIO44 RX), ligada ao CH343P, a **2 000 000 baud, 8N1**, sem controle de fluxo por hardware. As linhas DTR/RTS do CH343P vão ao circuito de auto-reset, não a controle de fluxo. A vazão nominal é de ~200 KB/s. Os 2 Mbaud foram validados na máquina (seção 11); 921 600 baud fica previsto como alternativa, nos dois lados. A taxa é fixa, sem negociação.

O console do ESP-IDF DEVE sair pela porta USB nativa (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`, GPIO19/20). Depois do boot, nenhum log pode sair pela UART0, porque ele corromperia o fluxo binário. Durante o desenvolvimento usam-se dois cabos: o do CH343P para o protocolo e o da USB nativa para `idf.py monitor`.

As mensagens da ROM e do bootloader que saem na UART0 durante o boot chegam ao PC como lixo e são descartadas pela ressincronização do enlace (§3.2).

---

## 3. Enlace

### 3.1 Formato do quadro

```
quadro bruto:  type[u8] | seq[u8] | payload[0..MAX_PAYLOAD] | crc16[u16 LE]
no fio:        COBS(quadro bruto) | 0x00
```

O **CRC-16/CCITT-FALSE** usa polinômio 0x1021, valor inicial 0xFFFF, sem reflexão e sem XOR final. O valor de conferência para a string ASCII `123456789` é `0x29B1`. Ele é calculado sobre `type | seq | payload` e transmitido em little-endian. Em Python: `binascii.crc_hqx(dados, 0xFFFF)`. Em C: tabela de 256 entradas, testada contra o valor de conferência.

O **COBS** (Consistent Overhead Byte Stuffing) reescreve o quadro bruto de forma que ele não contenha nenhum byte 0x00. O 0x00 passa a marcar exclusivamente o fim do quadro. O overhead é de no máximo 1 byte a cada 254. Em Python há o pacote `cobs` (`from cobs import cobs; cobs.encode(...)`).

Não existe campo de comprimento: o tamanho do payload é deduzido do quadro decodificado. O maior quadro possível ocupa 1038 bytes no fio.

### 3.2 Recepção

O receptor acumula bytes até encontrar 0x00 e então decodifica. O quadro é descartado silenciosamente quando o COBS é inválido, o quadro bruto tem menos de 4 bytes ou mais de MAX_PAYLOAD + 4, ou o CRC não confere. Se o acúmulo passar de 1038 bytes sem encontrar 0x00, o receptor descarta tudo até o próximo 0x00.

Cada descarte incrementa o contador `frames_dropped`, reportado no STATUS. Um 0x00 isolado (quadro vazio) é ignorado. O PC PODE enviar um 0x00 antes do HELLO para garantir o sincronismo.

---

## 4. Transporte

### 4.1 Classes de quadro

| Classe | Quadros | Tratamento |
|---|---|---|
| Sequenciados | todos do PC → ESP, exceto os prioritários | confiáveis: seq, ACK, retransmissão |
| Prioritários | ESTOP, CANCEL | fora da janela; seq = 0 e ignorado; sem ACK; tratados assim que chegam, antes de qualquer outro quadro; idempotentes |
| ESP → PC | todos | sem confirmação; seq = contador próprio do ESP (circular), usado só para o PC detectar perdas |

Mensagens do ESP perdidas não são retransmitidas. O STATUS periódico (§5.2) carrega o estado completo e reconcilia qualquer evento perdido.

### 4.2 Janela Go-Back-N

O seq tem 8 bits e é circular. Depois do HELLO (seq 0), o próximo quadro sequenciado leva seq 1. O PC mantém no máximo WINDOW quadros sequenciados sem confirmação.

O ESP confirma um quadro depois de **consumi-lo**, isto é, depois de validá-lo e armazená-lo ou enfileirá-lo, ou então rejeitá-lo com ERR. O ACK significa "recebido e tratado", não "executado"; a execução é reportada pelo STATUS. Quando rejeita um quadro, o ESP envia o ERR antes do ACK. O ACK é cumulativo: ACK(n) confirma todos os quadros até n.

Regras do ESP, sendo E o próximo seq esperado:

| seq recebido | Ação |
|---|---|
| igual a E | trata o quadro, faz E ← E+1, envia ACK(seq) |
| de E−8 a E−1 (duplicata) | descarta e reenvia ACK(E−1) |
| outro valor (lacuna) | descarta; envia NACK(E, SEQ_GAP) uma única vez por lacuna, depois descarta em silêncio até E chegar |

Regras do PC:

- Ao receber ACK(n), libera todos os quadros até n.
- Ao receber NACK(e), retransmite a partir de e, no máximo uma vez por intervalo de RTO.
- Se o quadro não confirmado mais antigo ficar RTO sem ACK, retransmite a partir dele.
- Após MAX_RETRIES estouros seguidos de RTO sem progresso, declara falha de enlace: para de transmitir e avisa o usuário. O ESP cancela sozinho por LINK_TIMEOUT.

### 4.3 Créditos

O ESP tem dois recursos finitos na camada de aplicação:

| Recurso | Quantidade | Consumido por | Liberado quando |
|---|---|---|---|
| slot de swath | SWATH_SLOTS | SWATH_BEGIN | a swath termina de executar ou é descartada |
| entrada da fila de execução | QUEUE_SLOTS | HOME, HEAD_POWER, MOVE, DWELL, SPIT, JOG, MOTORS_OFF, JOB_END, SWATH_END | o comando termina de executar ou é descartado |

O ACK e o STATUS informam `swath_free` e `queue_free` já considerando o quadro tratado. O PC calcula `disponível = último valor informado − consumos enviados e ainda não confirmados` e NÃO DEVE enviar um quadro consumidor quando `disponível = 0`. Se enviar, o ESP rejeita com NO_SWATH_SLOT ou NO_QUEUE_SLOT.

### 4.4 Quadros prioritários e latência

No pior caso, um ESTOP chega atrás de WINDOW quadros máximos: 8 × 1038 B ≈ 8,3 KB, cerca de 42 ms a 2 Mbaud. Esse é o motivo da janela pequena e dos quadros de ~1 KB.

O PC repete o quadro prioritário a cada PRIORITY_REPEAT até que o STATUS mostre o estado esperado (FAULT após ESTOP; STOPPING ou IDLE após CANCEL), por no máximo 1 s. Quadros de trabalho que já estavam em trânsito serão rejeitados com BAD_STATE, e o PC deve ignorar esses ERR após um CANCEL.

O ESTOP do protocolo **complementa** o botão físico de emergência. Ele não o substitui.

O buffer de recepção da UART no ESP DEVE ter pelo menos UART_RX_BUF bytes, o que cobre a janela cheia mais os prioritários.

---

## 5. Sessão

### 5.1 Abertura

1. O PC configura DTR e RTS como desativados **antes** de abrir a porta (pyserial: `ser.dtr = False; ser.rts = False` antes de `ser.open()`), para não acionar o auto-reset. Mesmo assim, o PC DEVE tolerar um reset do ESP na abertura.
2. O PC envia um 0x00 e em seguida HELLO a cada HELLO_RETRY, até HELLO_TRIES tentativas, até receber HELLO_ACK.
3. Ao receber HELLO, o ESP zera o transporte (E ← 1; contador próprio ← 0) e responde HELLO_ACK. Se houver job ativo, executa antes a rotina de desligamento (§7.7) com `last_error = LINK_LOST`.
4. O PC confere `proto_major`: se for diferente, fecha a sessão e avisa o usuário. `proto_minor` diferente é compatível.
5. Sem sessão ativa, o ESP fica em silêncio e ignora tudo que não seja HELLO.

### 5.2 Manutenção e perda de enlace

Enquanto a sessão existir, o ESP envia STATUS a cada STATUS_PERIOD. O PC envia PING sempre que tiver passado PING_PERIOD sem transmitir nada.

Quando um dos lados passa LINK_TIMEOUT sem receber quadro válido:

- **ESP:** encerra a sessão. Se houver job ativo, executa a rotina de desligamento com `last_error = LINK_LOST`. Se houver movimento manual em curso, para com rampa. Em seguida, fora de FAULT, desliga o VPP e, depois que os eixos param, os drivers dos motores, como num MOTORS_OFF: todos os eixos perdem a referência (v1.4). Sem o PC ninguém comanda a máquina, e deixá-la energizada não tem utilidade.
- **PC:** considera o enlace perdido e reabre a sessão com HELLO.

Não há mensagem de encerramento de sessão. Fechar a porta leva ao timeout, e com job ativo isso cancela o job, que é o comportamento desejado.

---

## 6. Apresentação

- Todos os campos são little-endian e empacotados, sem padding implícito. Campos reservados explícitos mantêm o alinhamento natural.
- Os tipos usados são `u8`, `u16`, `u32` e `i32`. Campos reservados DEVEM ser enviados como 0 e DEVEM ser ignorados na recepção.
- **Unidades:**
  - posições: passos absolutos em `i32` (0 = posição do endstop após HOME);
  - velocidades: passos/s em `u32`;
  - acelerações: passos/s²;
  - tempos: ms ou µs, conforme o nome do campo.
- Não existe ponto flutuante no protocolo. A conversão entre mm e passos é feita só no PC, com os `steps_per_m` do HELLO_ACK.
- Eixos: 0 = X, 1 = Y, 2 = Z. Nas máscaras de eixos: bit 0 = X, bit 1 = Y, bit 2 = Z.

### 6.1 Coluna de jateamento (16 bytes)

A coluna vem na ordem exata de carga no SPI (manual XJ128, tabela 6.11), com MSB primeiro. Bit 1 significa ejetar. O ESP envia os 16 bytes ao SPI sem nenhuma manipulação.

| Bytes | Registrador | Conteúdo |
|---|---|---|
| 0-7 | nSS2 (carregado primeiro) | byte 0 = CH128…CH121, …, byte 7 = CH72…CH65 |
| 8-15 | nSS1 | byte 8 = CH64…CH57, …, byte 15 = CH8…CH1 |

Fórmula geral: o bit *j* (7 = MSB) do byte *b* aciona o canal `128 − 8·b − (7 − j)`. A numeração dos canais segue o manual (bico 1 = primeiro bico na direção natural de impressão).

Exemplo: acionar só CH128 e CH1 resulta em byte 0 = `0x80`, byte 15 = `0x01` e os demais bytes em `0x00`.

---

## 7. Aplicação

### 7.1 Estados

| Estado | Significado | Aceita |
|---|---|---|
| IDLE (0) | sem job | sessão, prioritários, JOB_BEGIN, comandos de fila (modo manual), JOG, MOTORS_OFF, SET/GET/SAVE_PARAM |
| JOB (1) | job ativo | sessão, prioritários, comandos de fila, SWATH_*, JOB_END |
| STOPPING (2) | rotina de desligamento em curso | sessão, prioritários, GET_PARAM |
| FAULT (3) | travado após falha | sessão, ESTOP, GET_PARAM, CLEAR_FAULT |

Mensagens não aceitas no estado atual recebem ERR BAD_STATE.

Transições:

| De | Para | Gatilho |
|---|---|---|
| IDLE | JOB | JOB_BEGIN |
| JOB | STOPPING | JOB_END alcançado na fila, CANCEL, erro em mensagem de trabalho ou de dados, LINK_LOST, HELLO |
| STOPPING | IDLE | fim da rotina de desligamento |
| qualquer | FAULT | ESTOP, nFAULT do cabeçote, READY_TIMEOUT, FIRE_OVERRUN, endstop inesperado |
| FAULT | IDLE | CLEAR_FAULT (todos os eixos ficam não referenciados e exigem HOME) |

**Regra de erros:** em JOB, qualquer ERR causado por uma mensagem de trabalho (0x10-0x2F) ou de dados (0x30-0x3F) aborta o job. Uma mensagem manual enviada durante JOB é apenas rejeitada com BAD_STATE, sem abortar. Em IDLE, o comando com erro é só rejeitado.

**Estado planejado:** as validações de comandos enfileirados (referenciamento, VPP, posições) são feitas contra o estado que a máquina terá depois de executar tudo o que já está na fila, e não contra o estado atual. Por exemplo, um SWATH_BEGIN que chega enquanto o HOME ainda está na fila é válido. O ESP mantém uma cópia desse estado planejado, atualizada a cada comando enfileirado, e o ressincroniza com o estado real sempre que a fila é esvaziada.

### 7.2 Mapa de opcodes

| Opcode | Nome | Sentido | Classe | Consome |
|---|---|---|---|---|
| 0x01 | HELLO | PC → ESP | sessão (seq = 0) | - |
| 0x02 | PING | PC → ESP | sequenciado | - |
| 0x03 | CLEAR_FAULT | PC → ESP | sequenciado | - |
| 0x08 | ESTOP | PC → ESP | prioritário | - |
| 0x09 | CANCEL | PC → ESP | prioritário | - |
| 0x10 | JOB_BEGIN | PC → ESP | sequenciado | - |
| 0x11 | JOB_END | PC → ESP | sequenciado | fila |
| 0x12 | HOME | PC → ESP | sequenciado | fila |
| 0x13 | HEAD_POWER | PC → ESP | sequenciado | fila |
| 0x14 | MOVE | PC → ESP | sequenciado | fila |
| 0x15 | DWELL | PC → ESP | sequenciado | fila |
| 0x16 | SPIT | PC → ESP | sequenciado | fila |
| 0x30 | SWATH_BEGIN | PC → ESP | sequenciado | slot de swath |
| 0x31 | SWATH_DATA | PC → ESP | sequenciado | - |
| 0x32 | SWATH_END | PC → ESP | sequenciado | fila |
| 0x40 | JOG | PC → ESP | sequenciado | fila |
| 0x41 | SET_PARAM | PC → ESP | sequenciado | - |
| 0x42 | GET_PARAM | PC → ESP | sequenciado | - |
| 0x43 | SAVE_PARAMS | PC → ESP | sequenciado | - |
| 0x44 | MOTORS_OFF | PC → ESP | sequenciado | fila |
| 0x80 | HELLO_ACK | ESP → PC | - | - |
| 0x81 | ACK | ESP → PC | - | - |
| 0x82 | NACK | ESP → PC | - | - |
| 0x83 | STATUS | ESP → PC | - | - |
| 0x84 | ERR | ESP → PC | - | - |
| 0x85 | PARAM_VALUE | ESP → PC | - | - |
| 0x86 | LOG | ESP → PC | - | - |

Faixas reservadas para versões futuras: 0x04-0x07, 0x0A-0x0F, 0x17-0x2F, 0x33-0x3F, 0x45-0x7F e 0x87-0xFF. Opcode desconhecido gera ERR UNKNOWN_OPCODE, seguido do ACK normal.

### 7.3 Mensagens PC → ESP

Payloads com offset em bytes. Payload com tamanho diferente do especificado gera ERR BAD_LENGTH.

**HELLO (0x01)**: 2 bytes, sempre enviado com seq = 0.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | proto_major | u8 | 1 |
| 1 | proto_minor | u8 | 1 |

**PING (0x02)**: 0 bytes. Serve de heartbeat.

**CLEAR_FAULT (0x03)**: 0 bytes. Leva de FAULT para IDLE; em outro estado gera BAD_STATE.

**ESTOP (0x08)**: 0 bytes. Ver §7.8.

**CANCEL (0x09)**: 0 bytes.
- Em JOB: rotina de desligamento com `last_error = CANCELLED`.
- Em IDLE: para o movimento com rampa e esvazia a fila.
- Nos demais estados: sem efeito.

**JOB_BEGIN (0x10)**: 4 bytes. Aceito só em IDLE.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | job_id | u16 | identificador do trabalho |
| 2 | total_swaths | u16 | total previsto (0 = desconhecido); usado só para indicar progresso |

**JOB_END (0x11)**: 0 bytes. Quando alcançado na fila, executa a rotina de desligamento e volta a IDLE com `last_error = 0`.

**HOME (0x12)**: 1 byte.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | axes | u8 | máscara dos eixos a referenciar |

A ordem de referenciamento é definida pelo firmware, conforme a mecânica (seção 11).

**HEAD_POWER (0x13)**: 1 byte.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | on | u8 | 1 = executa a sequência de energização do XJ128 e liga VPP; 0 = desliga VPP |

**MOVE (0x14)**: 12 bytes. Movimento absoluto de um eixo, com rampa trapezoidal. Exige o eixo referenciado (estado planejado), `pos` dentro de [0, travel] e `speed ≤ VMAX` do eixo.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | axis | u8 | eixo |
| 1 | reservado | u8 | 0 |
| 2 | reservado | u16 | 0 |
| 4 | pos | i32 | posição de destino (passos) |
| 8 | speed | u32 | velocidade de cruzeiro (passos/s) |

**DWELL (0x15)**: 4 bytes. Espera parada, útil para secagem entre camadas.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | ms | u32 | duração |

**SPIT (0x16)**: 20 bytes. Dispara um padrão repetidas vezes na posição atual, com o carro parado. Exige VPP ligado (estado planejado) e `period_us ≥ MIN_FIRE_PERIOD`. Usos: disparo em recipiente antes da impressão (purga) e testes de bico. A purga por pressão fica fora do firmware, porque o hardware atual não tem atuador para ela.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | n_fires | u16 | número de disparos |
| 2 | period_us | u16 | período entre disparos |
| 4 | pattern | 16 B | coluna no formato da §6.1 |

**SWATH_BEGIN (0x30)**: 20 bytes. Abre a recepção de uma swath e consome um slot. As validações estão na §7.5.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | swath_id | u16 | identificador (recomenda-se sequencial dentro do job) |
| 2 | flags | u8 | bit 0 = dir (0 = +X, 1 = −X); bit 1 = nível do pino PHO (0 = A-B-C, 1 = C-B-A); bits 2-7 = 0 |
| 3 | pitch | u8 | passos entre colunas (≥ 1) |
| 4 | y | i32 | posição de Y durante a swath |
| 8 | x_start | i32 | posição do carro no disparo da coluna 0 |
| 12 | n_cols | u16 | de 1 a MAX_COLS |
| 14 | fire_delay_us | u16 | atraso entre a posição da coluna e o nFIRE (µs); 0 = sem atraso. Requisito do TCC: parâmetro de processo ajustável |
| 16 | speed | u32 | velocidade de X (passos/s), constante durante toda a swath |

**SWATH_DATA (0x31)**: de 20 a 1028 bytes (4 + 16·n).

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | swath_id | u16 | igual ao do SWATH_BEGIN aberto |
| 2 | col_start | u16 | índice da primeira coluna deste bloco |
| 4 | dados | n × 16 B | de 1 a 64 colunas no formato da §6.1 |

As colunas DEVEM chegar em ordem, sem lacunas nem sobreposição: `col_start` tem de ser igual à próxima coluna esperada.

**SWATH_END (0x32)**: 8 bytes. O ESP confere se chegaram n_cols colunas e se o CRC-32 bate. Só então a swath entra na fila de execução.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | swath_id | u16 | igual ao do SWATH_BEGIN aberto |
| 2 | reservado | u16 | 0 |
| 4 | crc32 | u32 | CRC-32 IEEE/zlib (conferência: `0xCBF43926`) sobre as n_cols × 16 bytes, em ordem de coluna. Em Python: `zlib.crc32` |

Entre SWATH_BEGIN e SWATH_END, o PC só PODE enviar SWATH_DATA da mesma swath, PING e quadros prioritários.

**JOG (0x40)**: 12 bytes. Aceito só em IDLE. Movimento relativo que não exige HOME. O eixo para se o endstop for acionado e, se estiver referenciado, respeita os limites [0, travel].

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | axis | u8 | eixo |
| 1 | reservado | u8 | 0 |
| 2 | reservado | u16 | 0 |
| 4 | delta | i32 | deslocamento (passos) |
| 8 | speed | u32 | passos/s |

**SET_PARAM (0x41)**: 8 bytes. Aceito em IDLE com a fila vazia. O valor vale até o ESP reiniciar, a menos que seja salvo com SAVE_PARAMS.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | param_id | u16 | ver §8.2 |
| 2 | reservado | u16 | 0 |
| 4 | value | i32 | novo valor |

**GET_PARAM (0x42)**: 2 bytes (`param_id u16`). O ESP responde com PARAM_VALUE.

**SAVE_PARAMS (0x43)**: 0 bytes. Grava os parâmetros atuais na NVS. Aceito só em IDLE.

**MOTORS_OFF (0x44)**: 0 bytes. Aceito só em IDLE. Quando alcançado na fila, desliga os drivers dos três eixos (MOTOR_EN inativo), para os eixos poderem ser movidos à mão. Todos os eixos ficam não referenciados e exigem HOME. O próximo movimento religa os drivers.

### 7.4 Mensagens ESP → PC

**HELLO_ACK (0x80)**: 40 bytes.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | proto_major | u8 | 1 |
| 1 | proto_minor | u8 | 1 |
| 2 | fw_major | u8 | versão do firmware |
| 3 | fw_minor | u8 | |
| 4 | fw_patch | u8 | |
| 5 | window | u8 | WINDOW |
| 6 | swath_slots | u8 | SWATH_SLOTS |
| 7 | queue_slots | u8 | QUEUE_SLOTS |
| 8 | max_payload | u16 | MAX_PAYLOAD |
| 10 | max_cols | u16 | MAX_COLS |
| 12 | min_fire_period_us | u16 | valor atual do parâmetro MIN_FIRE_PERIOD |
| 14 | reservado | u16 | 0 |
| 16 | steps_per_m_x | u32 | passos por metro (40 passos/mm = 40 000) |
| 20 | steps_per_m_y | u32 | |
| 24 | steps_per_m_z | u32 | |
| 28 | travel_x | i32 | curso útil em passos |
| 32 | travel_y | i32 | |
| 36 | travel_z | i32 | |

**ACK (0x81)**: 4 bytes.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | ack_seq | u8 | último seq tratado (cumulativo) |
| 1 | swath_free | u8 | slots de swath livres |
| 2 | queue_free | u8 | entradas livres na fila |
| 3 | reservado | u8 | 0 |

**NACK (0x82)**: 2 bytes.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | expected_seq | u8 | seq que o ESP espera |
| 1 | reason | u8 | 0x01 = SEQ_GAP |

**STATUS (0x83)**: 24 bytes, enviado a cada STATUS_PERIOD.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | state | u8 | 0 IDLE, 1 JOB, 2 STOPPING, 3 FAULT |
| 1 | flags | u8 | bits 0-2 = X, Y, Z referenciados; bit 3 = VPP ligado; bit 4 = nFAULT ativo; bit 5 = swath em execução |
| 2 | job_id | u16 | job atual ou último job |
| 4 | swath_id | u16 | swath em execução ou última concluída |
| 6 | swath_free | u8 | |
| 7 | queue_free | u8 | |
| 8 | pos_x | i32 | posição atual (passos) |
| 12 | pos_y | i32 | |
| 16 | pos_z | i32 | |
| 20 | last_error | u8 | último código de ERR ou motivo de término do job (0 = nenhum) |
| 21 | fault | u8 | motivo da falha (válido em FAULT) |
| 22 | frames_dropped | u16 | quadros descartados no enlace; satura em 65 535 |

**ERR (0x84)**: 8 bytes, enviado antes do ACK do quadro que causou o erro.

| Off | Campo | Tipo | Descrição |
|---|---|---|---|
| 0 | seq | u8 | seq do quadro que causou o erro |
| 1 | code | u8 | ver §7.9 |
| 2 | reservado | u16 | 0 |
| 4 | detail | i32 | valor ofensivo ou esperado, conforme o código |

**PARAM_VALUE (0x85)**: 8 bytes (`param_id u16`, `reservado u16`, `value i32`). Um `param_id` desconhecido gera ERR BAD_PARAM.

**LOG (0x86)**: de 1 a MAX_PAYLOAD bytes de texto ASCII, sem terminador. É opcional, serve para exibição no PC, e só é enviado com `LOG_ENABLE = 1`.

### 7.5 Execução de uma swath

Definições:

- `s = +1` se dir = 0, `s = −1` se dir = 1.
- A coluna *i* é disparada quando `X = x_start + s·i·pitch`.
- A última coluna é disparada em `x_end = x_start + s·(n_cols − 1)·pitch`.
- O recuo para aceleração é `d = ⌈speed² / (2·ACCEL_X)⌉ + RUNUP_MARGIN`, em passos.

Sequência de execução:

1. Move Y até `y` e X até `x_start − s·d`.
2. Ajusta o pino PHO conforme `flags` e carrega a coluna 0 no cabeçote.
3. Acelera X até `speed`. Ao atingir a posição de cada coluna (watchpoint do PCNT a cada `pitch` passos), o ESP aciona o nFIRE depois de `fire_delay_us`. A coluna seguinte é carregada depois do READY. Colunas totalmente zeradas não geram nFIRE.
4. Depois de `x_end`, desacelera até parar, perto de `x_end + s·d`.
5. Libera o slot e a entrada da fila.

A swath só entra na fila depois do SWATH_END validado. Com isso, o ESP nunca inicia uma swath incompleta. Se o PC atrasar o envio, a máquina espera **entre** swaths, nunca no meio de uma.

Validações feitas no SWATH_BEGIN, contra o estado planejado. Qualquer falha gera ERR e aborta o job.

| Condição | Erro |
|---|---|
| estado JOB | BAD_STATE |
| slot livre | NO_SWATH_SLOT |
| X e Y referenciados | NOT_HOMED |
| VPP ligado | HEAD_OFF |
| `1 ≤ n_cols ≤ MAX_COLS`, `pitch ≥ 1`, bits reservados de `flags` = 0, `speed ≤ VMAX_X` | BAD_PARAM |
| na v1.0 do firmware, dir = 0 (dir = 1 fica reservado para impressão bidirecional) | BAD_PARAM |
| `10⁶ · pitch / speed ≥ MIN_FIRE_PERIOD` | FIRE_TOO_FAST |
| `fire_delay_us < 10⁶ · pitch / speed` (o disparo atrasado precisa acontecer antes da posição da coluna seguinte) | BAD_PARAM |
| `x_start − s·d`, `x_end + s·d` e `y` dentro de [0, travel] | OUT_OF_TRAVEL |

### 7.6 Execução de comandos em IDLE

Em IDLE, os comandos de fila (HOME, HEAD_POWER, MOVE, DWELL, SPIT, JOG, MOTORS_OFF) passam pela mesma fila e executam em sequência. Não existe um caminho separado para o modo manual.

### 7.7 Rotina de desligamento

Acionada por JOB_END, CANCEL, erro de job, LINK_LOST e HELLO recebido com job ativo.

1. Interrompe os disparos: nFIRE inativo, colunas restantes descartadas.
2. Desliga VPP.
3. Desacelera todos os eixos em movimento com a aceleração configurada de cada um.
4. Esvazia a fila e libera todos os slots.
5. Os motores continuam energizados e os eixos continuam referenciados, exceto no LINK_LOST por timeout, em que os drivers desligam depois da rampa (§5.2).
6. Vai para IDLE, com o motivo registrado em `last_error` (0 no JOB_END normal).

### 7.8 ESTOP

1. nFIRE inativo e VPP desligado imediatamente.
2. A geração de passos é interrompida sem rampa e MOTOR_EN é desativado.
3. A fila é esvaziada, os slots são liberados e todos os eixos são marcados como não referenciados.
4. Vai para FAULT, com `fault = ESTOP`.

Para sair: CLEAR_FAULT e depois HOME.

### 7.9 Códigos

**ERR.code:**

| Código | Nome | Significado | detail |
|---|---|---|---|
| 0x01 | BAD_STATE | mensagem não aceita no estado atual | estado |
| 0x02 | BAD_LENGTH | tamanho de payload incompatível com o opcode | tamanho recebido |
| 0x03 | UNKNOWN_OPCODE | opcode desconhecido | opcode |
| 0x04 | BAD_PARAM | campo fora de faixa ou reservado ≠ 0 | offset do campo |
| 0x05 | NOT_HOMED | eixo não referenciado | máscara de eixos |
| 0x06 | OUT_OF_TRAVEL | posição ou recuo fora do curso | posição ofensiva |
| 0x07 | FIRE_TOO_FAST | período de disparo abaixo do mínimo | período calculado (µs) |
| 0x08 | NO_SWATH_SLOT | sem slot de swath livre | - |
| 0x09 | NO_QUEUE_SLOT | fila de execução cheia | - |
| 0x0A | SWATH_SEQUENCE | swath_id diferente do aberto, col_start fora de ordem ou SWATH_* sem BEGIN | coluna esperada |
| 0x0B | SWATH_INCOMPLETE | SWATH_END com colunas faltando | colunas recebidas |
| 0x0C | SWATH_CRC | CRC-32 da swath não confere | CRC calculado pelo ESP |
| 0x0D | HEAD_OFF | SPIT ou swath com VPP desligado | - |
| 0x0E | LINK_LOST | aparece só em `last_error` | - |
| 0x0F | CANCELLED | aparece só em `last_error` | - |

**STATUS.fault:**

| Código | Nome | Significado |
|---|---|---|
| 0x01 | ESTOP | ESTOP recebido |
| 0x02 | HEAD_NFAULT | nFAULT do cabeçote ativo |
| 0x03 | READY_TIMEOUT | READY não caiu dentro de T3 após o nFIRE (desativável com `CHECK_READY = 0`) |
| 0x04 | FIRE_OVERRUN | a posição da coluna seguinte foi alcançada antes de ela estar carregada |
| 0x05 | ENDSTOP | endstop acionado fora de HOME ou JOG |
| 0x06 | INTERNAL | falha interna do firmware |

**NACK.reason:** 0x01 = SEQ_GAP.

---

## 8. Parâmetros

### 8.1 Constantes do protocolo

| Constante | Valor | Justificativa |
|---|---|---|
| BAUD | 2 000 000 | ~200 KB/s; fallback de 921 600 (§11) |
| MAX_PAYLOAD | 1028 B | SWATH_DATA com 64 colunas |
| Maior quadro no fio | 1038 B | COBS + delimitador |
| WINDOW | 8 quadros | ~8,3 KB em trânsito, ~42 ms de latência máxima para prioritários |
| UART_RX_BUF | ≥ 16 KB | janela cheia + prioritários |
| RTO | 200 ms | serialização da janela cheia (~42 ms) + latência USB, com folga |
| MAX_RETRIES | 5 | |
| PING_PERIOD | 200 ms | |
| STATUS_PERIOD | 100 ms | |
| LINK_TIMEOUT | 1000 ms | igual nos dois lados |
| HELLO_RETRY / HELLO_TRIES | 250 ms / 10 | cobre um reset do ESP na abertura da porta |
| PRIORITY_REPEAT | 100 ms, até 1 s | |
| SWATH_SLOTS | 4 | 4 × 312,5 KiB = 1,25 MiB de PSRAM (a placa tem 8 MiB) |
| MAX_COLS | 20000 | curso de X de 245 mm a 80 passos/mm (19 600 passos) com pitch = 1 |
| QUEUE_SLOTS | 32 | |

### 8.2 Parâmetros configuráveis (SET_PARAM)

Valores iniciais do firmware (`maquina.h`), em passos para 80 passos/mm em X e Y e 400 em Z. Revistos em 03/11/2026: teto de 250 mm/s em X e Y; aceleração e velocidade do Z mantidas.

| ID | Nome | Unidade | Inicial |
|---|---|---|---|
| 0x0001 | ACCEL_X | passos/s² | 80 000 (1 000 mm/s²) |
| 0x0002 | ACCEL_Y | passos/s² | 40 000 (500 mm/s²) |
| 0x0003 | ACCEL_Z | passos/s² | 20 000 (50 mm/s²) |
| 0x0011 | VMAX_X | passos/s | 20 000 (250 mm/s) |
| 0x0012 | VMAX_Y | passos/s | 20 000 (250 mm/s) |
| 0x0013 | VMAX_Z | passos/s | 2 000 (5 mm/s) |
| 0x0020 | RUNUP_MARGIN | passos | 80 (1 mm) |
| 0x0030 | CHECK_READY | 0/1 | 1 (0 só para testes sem cabeçote) |
| 0x0031 | LOG_ENABLE | 0/1 | 0 |
| 0x0032 | MIN_FIRE_PERIOD | µs | 200. Piso fixo de 180 µs, que é o ciclo mínimo T2 do manual (5,55 kHz); valores abaixo geram BAD_PARAM. Com 200 µs e pitch = 1, X fica limitado a 5000 passos/s (125 mm/s) |

`steps_per_m` e `travel` são constantes de compilação do firmware na v1.0 e são informados ao PC no HELLO_ACK.

---

## 9. Exemplo de trabalho

```
PC                                            ESP
0x00, HELLO(seq 0)                      →
                                        ←     HELLO_ACK
                                        ←     STATUS (a cada 100 ms, até o fim da sessão)
JOB_BEGIN(job 1, 23 swaths)      seq 1  →
HOME(X|Y|Z)                      seq 2  →
HEAD_POWER(1)                    seq 3  →
MOVE(Z, z0, v)                   seq 4  →
MOVE(X, recipiente, v)           seq 5  →
SPIT(200, 500 µs, todos os bicos) seq 6 →
                                        ←     ACK 1..6 (à medida que são tratados)
SWATH_BEGIN(0, …)                seq 7  →
SWATH_DATA(0, col 0..63)         seq 8  →
SWATH_DATA(0, col 64..127)       seq 9  →     (janela de 8; um ACK por quadro)
…
SWATH_END(0, crc32)                     →     swath 0 entra na fila
SWATH_BEGIN(1, …)                       →     transmitida enquanto a swath 0 imprime
…
MOVE(Z, z1, v)                          →     próxima camada
DWELL(2000)                             →     secagem
…
JOB_END                                 →
                                        ←     STATUS: STOPPING → IDLE, last_error = 0
```

---

## 10. Vetores de teste

Os vetores foram gerados por implementação de referência e conferidos contra a biblioteca `cobs`. As duas implementações (C e Python) DEVEM reproduzi-los byte a byte.

Valores de conferência: CRC-16/CCITT-FALSE(`"123456789"`) = `0x29B1` e CRC-32(`"123456789"`) = `0xCBF43926`.

HELLO v1.1 (seq 0):
```
bruto: 01 00 01 01 64 D1
fio:   02 01 05 01 01 64 D1 00
```

PING (seq 1):
```
bruto: 02 01 4C 6B
fio:   05 02 01 4C 6B 00
```

ACK do ESP (ack_seq = 1, swath_free = 4, queue_free = 32; seq do ESP = 7):
```
bruto: 81 07 01 04 20 00 D6 54
fio:   06 81 07 01 04 20 03 D6 54 00
```

SWATH_DATA (seq 5; swath_id = 1; col_start = 0; uma coluna com CH128 e CH1):
```
bruto: 31 05 01 00 00 00 80 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 29 BA
fio:   04 31 05 01 01 01 02 80 01 01 01 01 01 01 01 01 01 01 01 01 01 04 01 29 BA 00
```
O CRC-32 dessa swath de uma coluna (para o SWATH_END) é `0xC7419637`.

---

## 11. Validação na máquina

| Item | Critério | Situação |
|---|---|---|
| 2 Mbaud pelo CH343P | `frames_dropped = 0` | validado em 26/10/2026; 921 600 baud fica como alternativa |
| Vazão sustentada PC → ESP com janela 8 | ≥ 100 KB/s | não medida isoladamente |
| DTR/RTS no Windows com o driver do CH343 | abrir a porta não reseta o ESP | não medido; o PC tolera um reset na abertura (§5.1) |
| Latência do ESTOP | da borda final do quadro em GPIO44 ao r_vpp (GPIO13), meta < 50 ms | não medida |
| MIN_FIRE_PERIOD | cabeçote real com o READY | 200 µs validado em 27/10/2026 (SPIT de 1000 pulsos); o limite de 180 µs do manual não foi testado |
| Grade de disparo | pitch | definida: pitch = micropassos por disparo, 6 por padrão (339 dpi a 80 passos/mm) |
| Ordem do HOME | endstop de Z embaixo | implementada: Z sobe 10 mm, X e Y referenciam, e o Z desce com o carro na zona morta |
| ACCEL/VMAX por eixo | §8.2 | definidos em 03/11/2026; falta conferir que X e Y não perdem passo a 250 mm/s |

---

## 12. Versionamento

A versão é `proto_major.proto_minor`.

- **Minor:** novos opcodes, novos `param_id`, ou uso de campos reservados em que o valor 0 preserva o comportamento anterior.
- **Major:** qualquer mudança incompatível.

| Versão | Data | Mudança |
|---|---|---|
| 1.0 | 22/09/2026 | versão inicial |
| 1.1 | 22/09/2026 | `fire_delay_us` no SWATH_BEGIN (campo antes reservado); período mínimo de disparo passa a ser o parâmetro MIN_FIRE_PERIOD, com piso de 180 µs do manual; remoção de travessões |
| 1.2 | 27/10/2026 | MAX_COLS de 8192 para 20000, para o curso real de X (245 mm) com pitch = 1. O PC usa o `max_cols` do HELLO_ACK, então um lado 1.1 e outro 1.2 continuam conversando |
| 1.3 | 29/10/2026 | MOTORS_OFF (0x44): desliga os drivers dos motores. Um firmware 1.2 responde UNKNOWN_OPCODE; o PC só oferece o comando com `proto_minor` ≥ 3 |
| 1.4 | 29/10/2026 | Perda de enlace por timeout desliga o VPP e os drivers dos motores (§5.2, §7.7). Sem opcode novo: só muda o comportamento do ESP |
