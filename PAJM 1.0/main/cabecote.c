// cabecote.c - CLK, SPI e disparo do XJ128
//
// Tempos da tabela 6.12 do manual (XJ128 Guide to Operation):
//   T1  nFIRE ativo: 1 a 120 µs
//   T2  ciclo mínimo: 180 µs
//   T3  nFIRE ativo -> READY cai: 2,35 a 3,35 µs
//   T4  READY baixo: >= 121,5 µs (os ciclos A, B e C disparam aqui).
//       No nosso cabeçote (cinza-claro, fora da tabela 3.1) mede 78 a 80 µs.
//   T7  nSS ativo: 32 µs (64 bits a 0,5 µs: SCK de 2 MHz)
// READY alto = pronto para disparar. A próxima coluna é carregada com o
// READY baixo, enquanto a anterior dispara (o registrador é duplo).
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "hal/gpio_ll.h"
#include "soc/gpio_struct.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "cabecote.h"
#include "movimento.h"
#include "placa.h"
#include "pinos.h"
#include "protocolo.h"

static const char *TAG = "cab";

#define CLK_HZ           1000000   // tabela 6.5: 0,95 a 1,05 MHz
#define SPI_HZ           2000000   // T11 típico de 0,5 µs
#define NFIRE_US         3         // T1 >= 1 µs; 3 µs pega ao menos duas bordas do CLK
#define T3_LIMITE_US     10        // T3 máximo é 3,35 µs; o resto é folga de medida
#define CARGA_APOS_US    5         // sem conferir o READY, espera ele cair antes de carregar
#define READY_ESPERA_US  1000      // READY tem de estar alto antes do disparo
#define FIM_SWATH_MS     20        // folga para o último disparo depois de o X parar

static spi_device_handle_t dev_nss2, dev_nss1;
static TaskHandle_t tarefa;
static SemaphoreHandle_t fim_disparo;
static volatile bool abortar;
static DMA_ATTR uint8_t buf_spi[BYTES_POR_COLUNA];
// Só o disparo e a espera do T3: uma interrupção ali atrasa o nFIRE ou a
// leitura do READY (READY_TIMEOUT falso, visto na placa em 27/10/2026)
static portMUX_TYPE trava_disparo = portMUX_INITIALIZER_UNLOCKED;

static struct {
    bool swath;
    uint16_t n, periodo_us;             // disparo parado (SPIT)
    uint8_t coluna[BYTES_POR_COLUNA];
    cab_swath_t sw;                     // swath
    bool checar_ready;
    cab_resultado_t resultado;
    uint32_t overrun_col;               // diagnóstico do FIRE_OVERRUN
    int32_t overrun_x;
} pedido;

// ---- sinais -------------------------------------------------------------

// Coluna na ordem da seção 6.1 do PROTOCOL.md: bytes 0-7 vão para o chip do
// nSS2 (CH128 a CH65), carregado primeiro; bytes 8-15 para o do nSS1.
static void carregar(const uint8_t *coluna)
{
    memcpy(buf_spi, coluna, BYTES_POR_COLUNA);
    spi_transaction_t t2 = { .length = 64, .tx_buffer = buf_spi };
    spi_transaction_t t1 = { .length = 64, .tx_buffer = buf_spi + 8 };
    spi_device_polling_transmit(dev_nss2, &t2);
    spi_device_polling_transmit(dev_nss1, &t1);
}

static inline int ready(void)
{
    return gpio_ll_get_level(&GPIO, PINO_READY);
}

// Espera o READY chegar a `nivel` até o instante `limite` (µs). Relê o
// pino antes de desistir: entre a leitura e a do relógio pode ter passado
// tempo (interrupção), e o READY pode ter mudado nesse meio.
static bool esperar_ready(int nivel, int64_t limite)
{
    while (ready() != nivel) {
        if (abortar) {
            return false;
        }
        if (esp_timer_get_time() > limite) {
            return ready() == nivel;
        }
    }
    return true;
}

// Pulso de nFIRE e, com checar_ready, a queda do READY em T3. Sem
// checar_ready, espera o READY cair antes de a próxima carga começar.
static cab_resultado_t disparar(bool checar_ready)
{
    taskENTER_CRITICAL(&trava_disparo);
    int64_t t0 = esp_timer_get_time();
    gpio_ll_set_level(&GPIO, PINO_NFIRE, 0);
    esp_rom_delay_us(NFIRE_US);
    gpio_ll_set_level(&GPIO, PINO_NFIRE, 1);
    bool caiu = !checar_ready || esperar_ready(0, t0 + T3_LIMITE_US);
    taskEXIT_CRITICAL(&trava_disparo);

    if (!caiu) {
        return abortar ? CAB_PARADO : CAB_READY_TIMEOUT;
    }
    if (checar_ready) {
        esp_rom_delay_us(1);                // T6 depois da queda do READY
    } else {
        while (esp_timer_get_time() < t0 + CARGA_APOS_US) {
        }
    }
    return CAB_OK;
}

static cab_resultado_t disparar_parado(void)
{
    // PHO baixo = ordem A-B-C; estável antes do nFIRE (T18)
    gpio_ll_set_level(&GPIO, PINO_PHO, 0);
    carregar(pedido.coluna);

    int64_t proximo = esp_timer_get_time();
    for (uint32_t i = 0; i < pedido.n; i++) {
        // Espera longa cede o núcleo; curta é ativa, para não perder o instante
        int64_t falta;
        while ((falta = proximo - esp_timer_get_time()) > 0) {
            if (abortar) {
                return CAB_PARADO;
            }
            if (falta > 2000) {
                vTaskDelay(1);
            }
        }
        if (abortar) {
            return CAB_PARADO;
        }
        if (pedido.checar_ready
            && !esperar_ready(1, esp_timer_get_time() + READY_ESPERA_US)) {
            return abortar ? CAB_PARADO : CAB_READY_TIMEOUT;
        }

        proximo = esp_timer_get_time() + pedido.periodo_us;
        cab_resultado_t r = disparar(pedido.checar_ready);
        if (r != CAB_OK) {
            return r;
        }
        if (i + 1 < pedido.n) {
            carregar(pedido.coluna);
        }
    }
    return CAB_OK;
}

static bool coluna_vazia(const uint8_t *c)
{
    for (int i = 0; i < BYTES_POR_COLUNA; i++) {
        if (c[i]) {
            return false;
        }
    }
    return true;
}

// Próxima coluna com algum bico ligado a partir de `i`; n_cols se não houver
static uint32_t proxima_coluna(uint32_t i)
{
    const cab_swath_t *s = &pedido.sw;
    while (i < s->n_cols && coluna_vazia(s->dados + i * BYTES_POR_COLUNA)) {
        i++;
    }
    return i;
}

// Seção 7.5 do PROTOCOL.md, passo 3: o X anda (núcleo 0) e aqui se vigia a
// posição dele. A coluna i dispara ao passar por x_start + i·pitch, depois
// de fire_delay_us. Se o X já passou da posição da coluna seguinte quando a
// coluna i ficou pronta, o disparo sairia fora do lugar: FIRE_OVERRUN.
static cab_resultado_t executar_swath(void)
{
    const cab_swath_t *s = &pedido.sw;
    gpio_ll_set_level(&GPIO, PINO_PHO, s->pho_cba);

    uint32_t i = proxima_coluna(0);
    if (i < s->n_cols) {
        carregar(s->dados + i * BYTES_POR_COLUNA);
    }
    while (i < s->n_cols) {
        int32_t alvo = s->x_start + (int32_t)(i * s->pitch);
        int32_t x;
        while ((x = movimento_posicao(0)) < alvo) {
            if (abortar) {
                return CAB_PARADO;
            }
        }
        if (x >= alvo + s->pitch) {
            pedido.overrun_col = i;
            pedido.overrun_x = x;
            return CAB_OVERRUN;
        }
        if (s->fire_delay_us) {
            int64_t t = esp_timer_get_time() + s->fire_delay_us;
            while (esp_timer_get_time() < t) {
            }
        }
        if (pedido.checar_ready
            && !esperar_ready(1, esp_timer_get_time() + READY_ESPERA_US)) {
            return abortar ? CAB_PARADO : CAB_READY_TIMEOUT;
        }
        cab_resultado_t r = disparar(pedido.checar_ready);
        if (r != CAB_OK) {
            return r;
        }
        i = proxima_coluna(i + 1);
        if (i < s->n_cols) {
            carregar(s->dados + i * BYTES_POR_COLUNA);
        }
    }
    return CAB_OK;
}

static void IRAM_ATTR ao_nfault(void *arg)
{
    placa_corte_isr();
    abortar = true;
    movimento_sinalizar_falha(FALHA_HEAD_NFAULT);
}

// ---- tarefa no núcleo 1 -------------------------------------------------

static void tarefa_cabecote(void *arg)
{
    // O SPI é criado aqui para ficar no núcleo do cabeçote
    const spi_bus_config_t bus = {
        .mosi_io_num = PINO_MOSI,
        .miso_io_num = -1,              // o MISO do cabeçote não volta ao ESP
        .sclk_io_num = PINO_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = BYTES_POR_COLUNA,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &bus, SPI_DMA_CH_AUTO));

    // Modo 0, MSB primeiro: o cabeçote amostra na borda de subida do SCK.
    // Um ciclo de SCK antes e depois do nSS cobre T9 e T10 (250 ns).
    spi_device_interface_config_t dev = {
        .mode = 0,
        .clock_speed_hz = SPI_HZ,
        .spics_io_num = PINO_NSS2,
        .queue_size = 1,
        .cs_ena_pretrans = 1,
        .cs_ena_posttrans = 1,
        .flags = SPI_DEVICE_HALFDUPLEX,
    };
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &dev_nss2));
    dev.spics_io_num = PINO_NSS1;
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &dev_nss1));

    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        pedido.resultado = pedido.swath ? executar_swath() : disparar_parado();
        gpio_ll_set_level(&GPIO, PINO_NFIRE, 1);
        xSemaphoreGive(fim_disparo);
    }
}

// ---- interface ----------------------------------------------------------

void cabecote_iniciar(void)
{
    // CLK livre de 1 MHz. O pino já é saída desde o placa_iniciar(); depois
    // daqui não se mexe mais na direção dele (CONTEXTO-PAJM.md, seção 6).
    const ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .timer_num = LEDC_TIMER_0,
        .duty_resolution = LEDC_TIMER_1_BIT,
        .freq_hz = CLK_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&t));
    const ledc_channel_config_t c = {
        .gpio_num = PINO_HEAD_CLK,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 1,                      // 50 % com 1 bit de resolução
    };
    ESP_ERROR_CHECK(ledc_channel_config(&c));

    // nFAULT: borda de descida, só habilitada com o cabeçote energizado
    ESP_ERROR_CHECK(gpio_install_isr_service(ESP_INTR_FLAG_IRAM));
    ESP_ERROR_CHECK(gpio_set_intr_type(PINO_NFAULT, GPIO_INTR_NEGEDGE));
    ESP_ERROR_CHECK(gpio_isr_handler_add(PINO_NFAULT, ao_nfault, NULL));
    gpio_intr_disable(PINO_NFAULT);

    fim_disparo = xSemaphoreCreateBinary();
    xTaskCreatePinnedToCore(tarefa_cabecote, "cab", 4096, NULL, 20, &tarefa, 1);
}

cab_resultado_t cabecote_disparar_parado(uint16_t n, uint16_t periodo_us, const uint8_t coluna[16],
                                         bool checar_ready)
{
    pedido.swath = false;
    pedido.n = n;
    pedido.periodo_us = periodo_us;
    memcpy(pedido.coluna, coluna, BYTES_POR_COLUNA);
    pedido.checar_ready = checar_ready;
    abortar = false;
    xTaskNotifyGive(tarefa);
    xSemaphoreTake(fim_disparo, portMAX_DELAY);
    if (pedido.resultado == CAB_READY_TIMEOUT) {
        ESP_LOGE(TAG, "READY fora do tempo (nível atual %d)", ready());
    }
    return pedido.resultado;
}

void cabecote_swath_iniciar(const cab_swath_t *s, bool checar_ready)
{
    pedido.swath = true;
    pedido.sw = *s;
    pedido.checar_ready = checar_ready;
    abortar = false;
    xTaskNotifyGive(tarefa);
}

cab_resultado_t cabecote_swath_terminar(bool interromper)
{
    if (interromper) {
        abortar = true;
    }
    if (xSemaphoreTake(fim_disparo, pdMS_TO_TICKS(FIM_SWATH_MS)) != pdTRUE) {
        // O X parou e ainda faltam colunas: elas ficaram para trás
        abortar = true;
        xSemaphoreTake(fim_disparo, portMAX_DELAY);
        if (!interromper) {
            ESP_LOGE(TAG, "swath: o X terminou antes do último disparo");
            return CAB_OVERRUN;
        }
    }
    if (pedido.resultado == CAB_READY_TIMEOUT) {
        ESP_LOGE(TAG, "READY fora do tempo na swath (nível atual %d)", ready());
    } else if (pedido.resultado == CAB_OVERRUN) {
        ESP_LOGE(TAG, "swath: coluna %lu pronta com o X já em %ld (passou da seguinte)",
                 (unsigned long)pedido.overrun_col, (long)pedido.overrun_x);
    }
    return pedido.resultado;
}

void cabecote_abortar(void)
{
    abortar = true;
}

void cabecote_vigiar_nfault(bool ligado)
{
    if (!ligado) {
        gpio_intr_disable(PINO_NFAULT);
        return;
    }
    gpio_intr_enable(PINO_NFAULT);
    // A interrupção é por borda: um nFAULT que já estava ativo não gera borda
    if (gpio_get_level(PINO_NFAULT) == 0) {
        ao_nfault(NULL);
    }
}
