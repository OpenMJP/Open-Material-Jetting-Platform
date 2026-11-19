// placa.c - GPIOs da placa pajm_V2.1
#include "hal/gpio_ll.h"
#include "soc/gpio_struct.h"
#include "esp_attr.h"
#include "placa.h"
#include "pinos.h"

static volatile bool vpp_ligado;
static bool motores_ligados;
static int nivel_heartbeat;

void placa_iniciar(void)
{
    // Níveis definidos ANTES de configurar como saída: o registrador já está
    // carregado e o pino nasce no nível certo (mesma técnica do teste_motores).
    gpio_set_level(PINO_RELE_VPP, 0);
    gpio_set_level(PINO_NRESET, 0);
    gpio_set_level(PINO_NFIRE, 1);
    gpio_set_level(PINO_PHO, 0);
    gpio_set_level(PINO_NSS1, 1);
    gpio_set_level(PINO_NSS2, 1);
    gpio_set_level(PINO_HEAD_CLK, 0);
    gpio_set_level(PINO_MOSI, 0);
    gpio_set_level(PINO_SCK, 0);
    gpio_set_level(PINO_MOTOR_EN, 1);
    gpio_set_level(PINO_X_STEP, 0);
    gpio_set_level(PINO_X_DIR, 0);
    gpio_set_level(PINO_Y_STEP, 0);
    gpio_set_level(PINO_Y_DIR, 0);
    gpio_set_level(PINO_Z_STEP, 0);
    gpio_set_level(PINO_Z_DIR, 0);
    gpio_set_level(PINO_HEARTBEAT, 0);

    // CLK, MOSI e SCK ficam como GPIO só até o LEDC e o SPI assumirem os
    // pinos. Cuidado: depois do ledc_channel_config(), não mexer mais na
    // direção do pino do CLK (CONTEXTO-PAJM.md, seção 6).
    const gpio_config_t saidas = {
        .pin_bit_mask = (1ULL << PINO_RELE_VPP) | (1ULL << PINO_NRESET)
                      | (1ULL << PINO_NFIRE) | (1ULL << PINO_PHO)
                      | (1ULL << PINO_NSS1) | (1ULL << PINO_NSS2)
                      | (1ULL << PINO_HEAD_CLK) | (1ULL << PINO_MOSI) | (1ULL << PINO_SCK)
                      | (1ULL << PINO_MOTOR_EN)
                      | (1ULL << PINO_X_STEP) | (1ULL << PINO_X_DIR)
                      | (1ULL << PINO_Y_STEP) | (1ULL << PINO_Y_DIR)
                      | (1ULL << PINO_Z_STEP) | (1ULL << PINO_Z_DIR)
                      | (1ULL << PINO_HEARTBEAT),
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&saidas);

    // Endstops sem resistor na placa: pull-down interno (ver teste_motores.c)
    const gpio_config_t fins = {
        .pin_bit_mask = (1ULL << PINO_FIM_X) | (1ULL << PINO_FIM_Y) | (1ULL << PINO_FIM_Z),
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
    };
    gpio_config(&fins);

    // READY e nFAULT vêm do U6, que já tem saída definida
    const gpio_config_t cabecote = {
        .pin_bit_mask = (1ULL << PINO_READY) | (1ULL << PINO_NFAULT),
        .mode = GPIO_MODE_INPUT,
    };
    gpio_config(&cabecote);

    vpp_ligado = false;
}

void placa_estado_seguro(void)
{
    gpio_set_level(PINO_NFIRE, 1);
    gpio_set_level(PINO_NRESET, 0);     // seção 6.1.2 do manual: nRESET antes de tirar o VPP
    gpio_set_level(PINO_RELE_VPP, 0);
    gpio_set_level(PINO_MOTOR_EN, 1);
    vpp_ligado = false;
    motores_ligados = false;
}

bool placa_motores_ligar(void)
{
    if (motores_ligados) {
        return false;
    }
    gpio_set_level(PINO_MOTOR_EN, 0);
    motores_ligados = true;
    return true;
}

void placa_motores_desligar(void)
{
    gpio_set_level(PINO_MOTOR_EN, 1);
    motores_ligados = false;
}

void placa_vpp_ligar(void)
{
    gpio_set_level(PINO_NFIRE, 1);
    gpio_set_level(PINO_NRESET, 0);
    gpio_set_level(PINO_RELE_VPP, 1);
    vpp_ligado = true;
}

void placa_cabecote_liberar(void)
{
    gpio_set_level(PINO_NRESET, 1);
}

void placa_cabecote_resetar(void)
{
    gpio_set_level(PINO_NRESET, 0);
}

void placa_vpp_desligar(void)
{
    gpio_set_level(PINO_NFIRE, 1);
    gpio_set_level(PINO_NRESET, 0);
    gpio_set_level(PINO_RELE_VPP, 0);
    vpp_ligado = false;
}

void IRAM_ATTR placa_corte_isr(void)
{
    gpio_ll_set_level(&GPIO, PINO_NFIRE, 1);
    gpio_ll_set_level(&GPIO, PINO_NRESET, 0);
    gpio_ll_set_level(&GPIO, PINO_RELE_VPP, 0);
    vpp_ligado = false;
}

bool placa_vpp_ligado(void)
{
    return vpp_ligado;
}

void placa_heartbeat(void)
{
    nivel_heartbeat = !nivel_heartbeat;
    gpio_set_level(PINO_HEARTBEAT, nivel_heartbeat);
}

bool placa_nfault_ativo(void)
{
    // Com o cabeçote sem alimentação o nFAULT não significa nada (e pode
    // flutuar sem o cabeçote ligado à placa). Só vale com o VPP ligado.
    return vpp_ligado && gpio_get_level(PINO_NFAULT) == 0;
}
