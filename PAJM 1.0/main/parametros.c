// parametros.c - tabela de parâmetros com persistência na NVS
#include <stdio.h>
#include "esp_log.h"
#include "nvs.h"
#include "parametros.h"
#include "protocolo.h"
#include "maquina.h"

static const char *TAG = "param";

typedef struct {
    uint16_t id;
    int32_t valor;
    int32_t min, max;
} param_t;

static param_t tabela[] = {
    { PARAM_ACCEL_X,         ACCEL_X_INICIAL,      1, INT32_MAX },
    { PARAM_ACCEL_Y,         ACCEL_Y_INICIAL,      1, INT32_MAX },
    { PARAM_ACCEL_Z,         ACCEL_Z_INICIAL,      1, INT32_MAX },
    { PARAM_VMAX_X,          VMAX_X_INICIAL,       1, INT32_MAX },
    { PARAM_VMAX_Y,          VMAX_Y_INICIAL,       1, INT32_MAX },
    { PARAM_VMAX_Z,          VMAX_Z_INICIAL,       1, INT32_MAX },
    { PARAM_RUNUP_MARGIN,    RUNUP_MARGIN_INICIAL, 0, INT32_MAX },
    { PARAM_CHECK_READY,     1,                    0, 1 },
    { PARAM_LOG_ENABLE,      0,                    0, 1 },
    // vai num u16 do HELLO_ACK
    { PARAM_MIN_FIRE_PERIOD, 200, MIN_FIRE_PERIOD_PISO_US, 0xFFFF },
};
#define N_PARAMS (sizeof tabela / sizeof tabela[0])

static param_t *achar(uint16_t id)
{
    for (size_t i = 0; i < N_PARAMS; i++) {
        if (tabela[i].id == id) {
            return &tabela[i];
        }
    }
    return NULL;
}

static void chave(char *buf, uint16_t id)
{
    snprintf(buf, 8, "p%04x", id);
}

void parametros_carregar(void)
{
    nvs_handle_t h;
    if (nvs_open("pajm", NVS_READONLY, &h) != ESP_OK) {
        ESP_LOGI(TAG, "nenhum parâmetro salvo, usando os iniciais");
        return;
    }
    for (size_t i = 0; i < N_PARAMS; i++) {
        char k[8];
        int32_t v;
        chave(k, tabela[i].id);
        if (nvs_get_i32(h, k, &v) == ESP_OK) {
            if (v >= tabela[i].min && v <= tabela[i].max) {
                tabela[i].valor = v;
            } else {
                ESP_LOGW(TAG, "0x%04x salvo fora da faixa (%ld), ignorado",
                         tabela[i].id, (long)v);
            }
        }
    }
    nvs_close(h);
}

esp_err_t parametros_salvar(void)
{
    nvs_handle_t h;
    esp_err_t e = nvs_open("pajm", NVS_READWRITE, &h);
    if (e != ESP_OK) {
        return e;
    }
    for (size_t i = 0; i < N_PARAMS && e == ESP_OK; i++) {
        char k[8];
        chave(k, tabela[i].id);
        e = nvs_set_i32(h, k, tabela[i].valor);
    }
    if (e == ESP_OK) {
        e = nvs_commit(h);
    }
    nvs_close(h);
    return e;
}

bool parametros_obter(uint16_t id, int32_t *valor)
{
    param_t *p = achar(id);
    if (!p) {
        return false;
    }
    *valor = p->valor;
    return true;
}

uint8_t parametros_definir(uint16_t id, int32_t valor, int32_t *detalhe)
{
    param_t *p = achar(id);
    if (!p) {
        *detalhe = 0;           // offset do param_id
        return ERR_BAD_PARAM;
    }
    if (valor < p->min || valor > p->max) {
        *detalhe = 4;           // offset do value
        return ERR_BAD_PARAM;
    }
    p->valor = valor;
    return ERR_OK;
}

int32_t parametro(uint16_t id)
{
    param_t *p = achar(id);
    return p ? p->valor : 0;
}
