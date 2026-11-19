// app_main.c - firmware do PAJM (ESP32-S3, ESP-IDF 5.5.5)
//
// Fronteira de responsabilidade: o PC não faz timing; o firmware não faz
// geometria. O firmware valida, move e dispara. Contrato: PROTOCOL.md v1.4.
//
// Núcleo 0: comunicação (prioridade 10) e movimento (12).
// Núcleo 1: exclusivo da interface com o Xaar (cabecote.c).
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "autoteste.h"
#include "cabecote.h"
#include "crc.h"
#include "enlace.h"
#include "movimento.h"
#include "parametros.h"
#include "placa.h"
#include "protocolo.h"
#include "sessao.h"
#include "swath.h"

static const char *TAG = "pajm";

// Onda quadrada de 20 Hz (borda a cada 25 ms). O monoestável do TLC555 é de
// ~1 s, mas na placa atual os relés só ficam estáveis a partir de ~10 Hz
// (medido em 27/10/2026; suspeita de erro na plaquinha do watchdog). O laço
// de comunicação roda a cada 5 ms no máximo, então 25 ms tem folga.
#define HEARTBEAT_MEIO_PERIODO_US  25000

// O heartbeat fica neste laço de propósito: se a comunicação travar, ele
// para e o watchdog externo corta o VPP.
static void tarefa_comunicacao(void *arg)
{
    int64_t t_heartbeat = 0;

    for (;;) {
        enlace_receber(5, sessao_receber, NULL);

        int64_t agora = esp_timer_get_time();
        sessao_periodico(agora);
        if (agora - t_heartbeat >= HEARTBEAT_MEIO_PERIODO_US) {
            t_heartbeat = agora;
            placa_heartbeat();
        }
    }
}

void app_main(void)
{
    placa_iniciar();
    crc_iniciar();

    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        e = nvs_flash_init();
    }
    ESP_ERROR_CHECK(e);
    parametros_carregar();

    bool ok = autoteste_executar();
    if (!ok) {
        ESP_LOGE(TAG, "autoteste falhou: a máquina fica em FAULT (INTERNAL)");
    }

    swath_iniciar();
    enlace_iniciar();
    cabecote_iniciar();
    movimento_iniciar();
    sessao_iniciar(ok);

    ESP_LOGI(TAG, "firmware %d.%d.%d, protocolo %d.%d, UART0 a %d baud",
             FW_MAJOR, FW_MINOR, FW_PATCH, PROTO_MAJOR, PROTO_MINOR, BAUD);

    xTaskCreatePinnedToCore(tarefa_comunicacao, "com", 6144, NULL, 10, NULL, 0);
}
