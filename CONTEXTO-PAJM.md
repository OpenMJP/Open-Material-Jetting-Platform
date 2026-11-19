# PAJM: contexto para desenvolvimento de software e firmware

> Briefing para manter como `CLAUDE.md` na raiz dos repositórios de firmware e de software.
> Versão 2 · 22/09/2026. Substitui a versão de 02/09/2026, que tinha valores do XJ128/200 e decisões hoje superadas.
> O protocolo de comunicação está em `PROTOCOL.md` (v1.1), que é o contrato entre os dois lados. Em caso de conflito entre este arquivo e o PROTOCOL.md, vale o PROTOCOL.md.
> **[EM ABERTO]** = ainda não definido. **[VERIFICAR]** = confirmar na máquina.

---

## 1. O projeto

TCC de Engenharia Mecatrônica na UTFPR (Campus Curitiba), desenvolvido no NUFER por Arthur e Gabriela, com orientação do Prof. Dr. Neri Volpato. Objetivo: um sistema aberto de controle e posicionamento de cabeçote de jateamento de material (MJT), para pesquisa em manufatura aditiva. O que se busca é controle total da cadeia (padrão, dados e deposição), não apenas obter peças.

Requisitos do TCC que afetam o código:

- jatear fluido de maneira consistente e repetível;
- permitir que o usuário determine os padrões jateados;
- possibilitar expansão para mais cabeçotes;
- permitir alterar parâmetros de processo, explicitamente a **velocidade do carro** e o **atraso entre o pulso de posição e o disparo** (no protocolo: `speed` e `fire_delay_us` do SWATH_BEGIN);
- documentação concisa que permita a terceiros continuar o trabalho. O código é entregável de pesquisa.

Escopo do TCC2: **jateamento 2D de padrões sobre substrato**, com tinta solvente. Fora do escopo: cura UV, construção de peça por camadas, suportes e balanços. O XJ128 não é compatível com resinas fotopolimerizáveis. O protocolo já prevê movimento em Z para quando o escopo crescer.

Divisão: Arthur cuida do firmware e da eletrônica; Gabriela, do software desktop (Python, Windows).

---

## 2. Cabeçote: Xaar XJ128/360

Valores da tabela 3.1 do manual (XJ128 Guide to Operation, Xaar), coluna do modelo /360:

| Parâmetro | Valor |
|---|---|
| Bicos ativos | 128, em fila única |
| Passo entre bicos | 137,1 µm (185 npi) |
| Diâmetro do bico | 35 µm |
| Volume nominal de gota | 40 pL |
| Velocidade da gota | 6,0 m/s |
| **Frequência máxima** | **5,55 kHz** (ciclo mínimo T2 = 180 µs) |
| Velocidade linear máxima | 390 mm/s (a 360 dpi) |
| Resolução em passada única | 360 × 360 dpi, montado a 31° |
| **Largura da swath** | **9,0 mm** |
| Distância de impressão recomendada | 1,0 mm |
| Aceleração máxima do cabeçote | 4 g |
| Modo | binário (uma gota fixa ou nenhuma), piezo em modo de cisalhamento |
| Alimentação | VDD 5 V; VPPH e VPPL 35 V |

### Disparo em três ciclos (A-B-C)

Canais vizinhos compartilham a parede piezoelétrica e não disparam juntos. A cada nFIRE, o cabeçote dispara sozinho três grupos em sequência, separados por **60 µs fixos**:

| Ciclo | Bicos | Atraso relativo (PHO baixo) |
|---|---|---|
| A | 1, 4, 7, … (1 + 3k) | 0 µs |
| B | 2, 5, 8, … (2 + 3k) | 60 µs |
| C | 3, 6, 9, … (3 + 3k) | 120 µs |

Com PHO alto a ordem vira C-B-A (usado no retorno, em impressão bidirecional). Com o carro em movimento, cada ciclo cai deslocado de `v × atraso`: 7,5 µm e 15 µm a 125 mm/s. Sem compensação, o resultado é o serrilhado que o manual descreve. A compensação é feita no software (seção 5).

### Sequência de energização (manual, seção 6.1.2)

- **Ligar:** nRESET ativo; VDD estável; liga VPPH/VPPL; espera **≥ 100 ms**; libera nRESET.
- **Desligar:** ativa nRESET; desliga VPPH/VPPL; VDD só pode cair depois que VPP estiver no nível de VDD ou abaixo.

---

## 3. Hardware

### Controlador

Clone do **YD-ESP32-S3** (VCC-GND), N16R8 (16 MB de flash, 8 MB de PSRAM octal). Pinagem idêntica à do DevKitC-1, dimensões mecânicas diferentes. Regulador 1117: margem de ~650 mA no 3V3. Framework **ESP-IDF 5.5.5** (a 6.0.x causou laço de reconfiguração do CMake). Projeto em caminho curto, sem espaços, fora do OneDrive. `CONFIG_SPIRAM=y`, `CONFIG_SPIRAM_MODE_OCT=y`.

### Pinagem

| Função | GPIO |
|---|---|
| CLK do cabeçote (LEDC, 1 MHz) | 4 |
| nFIRE | 5 |
| PHO / nRESET | 6 / 7 |
| nSS2 / nSS1 | 9 / 10 |
| MOSI / SCK (SPI2 + DMA) | 11 / 12 |
| r_vpp (relés do VPP) | 13 |
| READY / nFAULT | 15 / 16 |
| SDA / SCL (reserva) | 17 / 18 |
| Heartbeat do watchdog (TLC555) | 38 |
| X_STEP / X_DIR | 47 / 21 |
| Y_STEP / Y_DIR | 1 / 14 |
| Z_STEP / Z_DIR | 40 / 39 |
| MOTOR_EN | 41 |
| Endstops X / Y / Z | 42 / 2 / 8 |
| LED WS2812 | 48 |

Não usar: 35, 36, 37 (PSRAM); 0, 3, 45, 46 (strapping); 43, 44 (UART0, ligada ao CH343P e reservada ao protocolo); 19, 20 (USB nativa, usada para console e logs).

### Proteção e segurança no hardware

- Níveis lógicos por dois 74HCT244: U5 (ESP → Xaar, 33 Ω em série, pull-down no nRESET) e U6 (Xaar → ESP, com limitação de corrente e clamp Schottky).
- **Watchdog externo:** TLC555 monoestável de ~1 s, com trigger acoplado em AC no GPIO38. O firmware **deve** gerar bordas no GPIO38 com período bem menor que 1 s; se parar (travado em alto ou em baixo), o transistor no retorno dos relés corta o VPP independentemente do firmware.
- Fusível nos 35 V, PTC no 24 V e no buck, diodos contra inversão de polaridade.

### Mecânica

Clone de Prusa I3, cinemática cartesiana, três NEMA 17 com drivers TMC2208 (VIO em 3,3 V). **40 passos/mm em X e Y** (25 µm por passo). Mesa de 200 mm, ou seja, ~8000 passos em X. **[EM ABERTO]** passos/mm do Z, que depende do fuso.

Cabeçote montado a **31°** no carro. Reservatório de tinta abaixo dos bicos (pressão negativa); a altura do reservatório é parâmetro de processo e deve ser registrada com cada experimento.

---

## 4. Comunicação

Resumo; os detalhes estão no `PROTOCOL.md`.

- **Canal físico:** CH343P ↔ UART0 a 2 Mbaud (~200 KB/s). A USB nativa fica para console e logs. No desenvolvimento, dois cabos.
- **Unidades:** tudo trafega como inteiro em **passos** (posição) e passos/s (velocidade). O PC converte mm ↔ passos com os `steps_per_m` informados pelo ESP no HELLO_ACK. Nenhum valor mecânico fica fixo no código do PC.
- **Unidade de dados:** a swath. O PC envia uma swath inteira (SWATH_BEGIN, SWATH_DATA, SWATH_END com CRC-32); o ESP só a executa depois de recebê-la completa. O ESP tem 4 slots de swath e imprime uma enquanto recebe a seguinte.
- **Coluna:** 16 bytes na ordem exata de carga do SPI (PROTOCOL.md, seção 6.1). O ESP não mexe em bits.
- **Perda de enlace:** 1 s sem quadro válido cancela o job (parada controlada, VPP desligado).

---

## 5. Geometria e rasterização (software)

Todo o processamento de imagem fica no PC. O ESP recebe colunas prontas e dispara cada uma numa posição de passo.

### Números do cabeçote a 31°

- passo entre bicos em Y: 137,1 × sen 31° ≈ **70,6 µm** (360 dpi);
- deslocamento em X entre bicos vizinhos: 137,1 × cos 31° ≈ **117,5 µm**;
- extensão do cabeçote em X: 127 × 117,5 µm ≈ **14,9 mm**;
- largura da swath: 127 × 70,6 µm ≈ **9,0 mm**.

### Grade de disparo

Cada coluna é disparada numa posição múltipla de `pitch` passos (`pitch` é campo do SWATH_BEGIN). Todos os 128 bicos disparam juntos a cada nFIRE, então cada bico só consegue depositar em posições da grade deslocadas pelo seu próprio offset. O erro de arredondamento máximo por bico é metade do passo da grade:

| pitch | Grade | Erro máx. por bico | Velocidade máx. (período mínimo de 200 µs) |
|---|---|---|---|
| 1 | 25 µm | ±12,5 µm | 125 mm/s |
| 2 | 50 µm | ±25 µm | 250 mm/s |
| 3 | 75 µm | ±37,5 µm | 375 mm/s |

Com cabeçote inclinado, um preenchimento cheio acaba disparando em praticamente todas as colunas, então o limite de velocidade vale para o pior caso. **[EM ABERTO]** pitch final: começar com 3 (timing já validado) e comparar com 1 no teste de linhas verticais.

### Posição de disparo de cada bico

Para o bico *n* (1 a 128) depositar na posição X alvo `x`, o carro deve estar em:

```
c(n, x) = x + (n − 1) · 117,5 µm − v · Δt_ciclo(n) + offset_calibração
```

- `Δt_ciclo(n)` = 0, 60 ou 120 µs conforme o ciclo do bico (tabela da seção 2).
- `offset_calibração` absorve tudo o que é igual para todos os bicos: tempo de voo da gota (~1 mm / 6 m/s ≈ 167 µs, ou ~21 µm a 125 mm/s), latência STEP → nFIRE medida (4,21 µs), `fire_delay_us` e a distância entre o bico 1 e a referência de passos. É determinado por padrão de teste.
- A coluna do bico é `round((c − x_start) / (pitch · 25 µm))`.
- **[VERIFICAR]** o sinal do termo `(n − 1) · 117,5 µm` depende de qual lado da montagem fica o bico 1. O manual define a direção natural de impressão como aquela em que o bico 1 vai à frente. Confirmar com uma linha vertical antes de confiar na fórmula.

### Velocidade constante

Os termos `v · Δt_ciclo` e `v · t_voo` dependem da velocidade. Por isso a velocidade precisa ser **constante durante toda a swath** e igual à usada no cálculo. O ESP acelera fora da região impressa. O recuo necessário em cada lado é `v² / (2a) + margem`: a 125 mm/s e 1000 mm/s², ~7,8 mm + 1 mm.

**Área útil em X:** 200 mm − 2 × recuo − 14,9 mm (extensão do cabeçote) ≈ **167 mm** a 125 mm/s. O software deve posicionar as peças dentro dela.

### Direção

Na v1.x, só impressão em +X (dir = 0, PHO baixo); o retorno é feito sem jatear. A impressão bidirecional inverte a ordem dos ciclos (PHO alto) e o sinal dos termos de velocidade; fica para depois do TCC2.

### Recomendações para o código do PC

- Núcleo de rasterização separado da interface e da comunicação, testável isoladamente.
- Geração determinística: mesma entrada e mesmos parâmetros produzem os mesmos bytes.
- Internamente, tudo em mm; conversão para passos só na borda, com os valores do HELLO_ACK. Trabalhos salvos em mm continuam válidos se a mecânica mudar.
- Implementar cedo o **teste de bicos** e os padrões de validação do TCC (quadriculados e linhas paralelas de dimensões conhecidas, depois escaneados), gerando junto o arquivo de referência para a comparação.
- Preview do bitmap e modo dry-run, para desenvolver sem a máquina.

---

## 6. Firmware

### Fronteira de responsabilidade

O PC não faz timing; o firmware não faz geometria. O firmware valida, move e dispara.

### Decisões de implementação

- **Núcleos:** núcleo 0 para movimento dos motores e comunicação; núcleo 1 exclusivo para a interface com o Xaar.
- **CLK de 1 MHz** pelo LEDC.
- **Contagem de posição em X** pelo PCNT, lendo o próprio X_STEP por loopback interno. Disparo por watchpoint a cada `pitch` passos, com `fire_delay_us` opcional.
- **Armadilha já encontrada:** chamar `gpio_set_direction()` depois do `ledc_channel_config()` quebra o LEDC (o pino fica estático). O driver do PCNT monta o caminho de entrada sozinho; não é preciso mexer na direção do pino.
- **SPI2 com DMA, modo 0, MSB primeiro**, que casa com o cabeçote. Sugestão: nSS1 e nSS2 como dois devices no mesmo barramento, cada um com seu chip select, e cada metade de 64 bits como uma transação de 8 bytes (nSS2 primeiro).
- **ISR de disparo em IRAM** (`IRAM_ATTR`), sem chamar código em flash. Cache miss vira gota fora do lugar.
- **Buffers:** slots de swath na PSRAM; a coluna consumida pela ISR/DMA deve estar em SRAM interna, com cópia antecipada.
- **Console do ESP-IDF na USB nativa** (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`). Nenhum log na UART0.
- **nFAULT** tratado como interrupção dedicada: leva ao estado FAULT.
- Heartbeat do watchdog no GPIO38 (seção 3).

### Validação já medida (ESP solto, sem cabeçote)

LA1010 a 100 MS/s: latência STEP → nFIRE de **4,21 µs** (4,07 a 4,26 µs), jitter de ~190 ns. Margem de ~14× sobre a janela do READY (58,5 µs). A 150 mm/s: 0,63 µm de offset constante e 28 nm de jitter.

Antes disso, uma validação preliminar com Arduino Mega e script Python já fez o cabeçote jatear com a sequência de sinais correta.

---

## 7. Orçamento de dados

- Coluna: 16 bytes. Swath de 200 mm com pitch 1: 8000 colunas, ~128 KB. Com pitch 3: ~43 KB.
- Vazão durante a impressão a 5 kHz de disparo: 80 KB/s, contra ~200 KB/s do enlace.
- Na prática, uma swath de 128 KB é transmitida em ~0,7 s, enquanto a anterior leva ~1,6 s para imprimir a 125 mm/s. O enlace não é o gargalo.
- PSRAM: 4 slots × 128 KiB = 512 KiB.

---

## 8. Estado atual

Atualizado em 03/11/2026.

- **Máquina imprimindo.** Imagens, padrões de teste e peças fatiadas saem na PAJM; alguns bicos estão entupidos, o que vai para as conclusões.
- **Protocolo** `PROTOCOL.md` v1.4: swaths (etapa 3), MOTORS_OFF (1.3) e desligamento do cabeçote e dos motores ao perder o enlace (1.4).
- **Calibração feita na máquina:** ângulo do cabeçote 31° (teste visual, resolução de ~0,45°), avanço de Y entre faixas sem correção, sentido do índice +1 (bico 1 no Y menor), 80 passos/mm em X e Y, 400 em Z, origem da mesa em (35, 35) mm.
- **Programa de controle** `PAJM Controle` (antes "PAJM Slicer"): painel de comando manual, fatiador de STL, impressão de imagem, importação de pacotes `.pajm` de um CAM externo (`IMPORTACAO.md`), calibração e configuração da máquina. Interface no estilo do Mainsail.
- Cronograma em `cronograma_TCC2_v3.xlsx`.

---

## 9. Convenções de trabalho

- Documentar e responder em **português**.
- **Não usar travessão** em textos gerados.
- Mudanças de protocolo começam no `PROTOCOL.md`, antes do código, com incremento de versão.
- Código testável sem a máquina: vetores de teste do protocolo, mock da comunicação, dry-run.
- Nenhum parâmetro mecânico fixo no código do PC: tudo vem do HELLO_ACK ou dos parâmetros.

---

## 10. Lacunas

Resolvidas desde a primeira versão deste documento: o pipeline de rasterização (implementado e testado no fatiador), o bring-up a 2 Mbaud, a grade de disparo (pitch = micropassos por disparo, 6 por padrão), o sinal do deslocamento por bico (conferido nas impressões), a ordem do HOME e a calibração de X e Y.

Decididas em 03/11/2026:

- **Z:** 400 passos/mm (medido), 5 mm/s e 50 mm/s² (mantidos).
- **X e Y:** teto de 250 mm/s.
- **Impressão monodirecional** (só em +X). O teste de alinhamento em X fica disponível na Calibração, mas sem onde aplicar.
- **Computadores do laboratório:** Windows.

O que continua em aberto:

1. Formato `.pajm` (`IMPORTACAO.md` v1.0): revisar com quem mantém o RP3.
2. Conferir na máquina que X e Y não perdem passo no novo teto de 250 mm/s.
