// cabecote.h - interface com o Xaar XJ128 (manual, seções 6.1 e 6.2)
//
// CLK de 1 MHz pelo LEDC, carga das colunas pelo SPI2 (nSS2 e depois nSS1,
// 64 bits cada) e disparo pelo nFIRE. O disparo roda numa tarefa no núcleo
// 1, que é exclusivo do cabeçote: a espera ativa entre disparos não pode
// atrasar a comunicação nem o movimento, que ficam no núcleo 0.
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    CAB_OK,
    CAB_READY_TIMEOUT,  // READY não subiu antes do disparo ou não caiu em T3
    CAB_PARADO,         // CANCEL, ESTOP ou nFAULT
    CAB_OVERRUN,        // swath: o X passou da coluna seguinte antes do disparo
} cab_resultado_t;

// Swath já recebida (PROTOCOL.md 7.5). Os dados ficam no slot dela.
typedef struct {
    const uint8_t *dados;   // n_cols × 16 bytes
    uint32_t n_cols;
    int32_t x_start;        // passos; a coluna i dispara em x_start + i·pitch
    uint32_t pitch;
    uint16_t fire_delay_us;
    bool pho_cba;           // nível do PHO (flags bit 1)
} cab_swath_t;

// CLK, SPI, interrupção do nFAULT e tarefa no núcleo 1.
// Chamar depois de placa_iniciar().
void cabecote_iniciar(void);

// Carro parado: dispara `n` vezes a mesma coluna (16 bytes, PROTOCOL.md
// 6.1), com `periodo_us` entre disparos. Bloqueia até terminar.
cab_resultado_t cabecote_disparar_parado(uint16_t n, uint16_t periodo_us, const uint8_t coluna[16],
                                         bool checar_ready);

// Começa a vigiar a posição do X para disparar a swath (só em +X, na v1.0
// do firmware). Não bloqueia: quem chama move o X e depois chama
// cabecote_swath_terminar(), com interromper = true se o X não chegou ao fim.
void cabecote_swath_iniciar(const cab_swath_t *s, bool checar_ready);
cab_resultado_t cabecote_swath_terminar(bool interromper);

// Interrompe um disparo parado em andamento (CANCEL, ESTOP). Pode ser chamada de
// qualquer tarefa.
void cabecote_abortar(void);

// Liga ou desliga a vigilância do nFAULT. Só faz sentido com o cabeçote
// energizado e fora de reset: antes disso o pino não significa nada.
void cabecote_vigiar_nfault(bool ligado);
