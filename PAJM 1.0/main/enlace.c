// enlace.c - camada física e de enlace sobre a UART0
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "driver/uart.h"
#include "enlace.h"
#include "pinos.h"

#define UART_PROTO    UART_NUM_0
#define UART_TX_BUF   4096

static decodificador_t dec;
static SemaphoreHandle_t mutex_tx;
static uint8_t fio_tx[MAIOR_QUADRO_NO_FIO];
static uint8_t seq_tx;
static uint8_t buf_rx[512];

void enlace_iniciar(void)
{
    const uart_config_t cfg = {
        .baud_rate = BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(UART_PROTO, UART_RX_BUF, UART_TX_BUF, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_PROTO, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(UART_PROTO, PINO_UART_TX, PINO_UART_RX,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    // Lixo da ROM e do bootloader que chegou antes daqui não interessa.
    uart_flush_input(UART_PROTO);

    mutex_tx = xSemaphoreCreateMutex();
}

void enlace_receber(uint32_t espera_ms, quadro_cb_t cb, void *ctx)
{
    size_t disponivel = 0;
    uart_get_buffered_data_len(UART_PROTO, &disponivel);

    int n;
    if (disponivel == 0) {
        n = uart_read_bytes(UART_PROTO, buf_rx, 1, pdMS_TO_TICKS(espera_ms));
    } else {
        if (disponivel > sizeof buf_rx) {
            disponivel = sizeof buf_rx;
        }
        n = uart_read_bytes(UART_PROTO, buf_rx, disponivel, 0);
    }
    if (n > 0) {
        decodificador_alimentar(&dec, buf_rx, n, cb, ctx);
    }
}

void enlace_enviar(uint8_t tipo, const void *payload, size_t len)
{
    xSemaphoreTake(mutex_tx, portMAX_DELAY);
    size_t n = quadro_codificar(tipo, seq_tx++, payload, len, fio_tx);
    uart_write_bytes(UART_PROTO, fio_tx, n);
    xSemaphoreGive(mutex_tx);
}

void enlace_zerar_seq(void)
{
    xSemaphoreTake(mutex_tx, portMAX_DELAY);
    seq_tx = 0;
    xSemaphoreGive(mutex_tx);
}

uint16_t enlace_descartados(void)
{
    return dec.descartados > 0xFFFF ? 0xFFFF : (uint16_t)dec.descartados;
}
