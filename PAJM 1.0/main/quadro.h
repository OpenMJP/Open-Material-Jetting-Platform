// quadro.h - enquadramento COBS + CRC-16 (PROTOCOL.md, seção 3)
//
// Funções puras, sem UART: o autoteste usa as mesmas que o enlace.
//
//   quadro bruto:  type | seq | payload | crc16 (LE)
//   no fio:        COBS(quadro bruto) | 0x00
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "protocolo.h"

typedef struct {
    uint8_t tipo;
    uint8_t seq;
    uint16_t len;
    const uint8_t *payload;   // válido só durante o callback
} quadro_t;

typedef void (*quadro_cb_t)(const quadro_t *q, void *ctx);

// Monta o quadro no fio, já com o 0x00 final. `fio` precisa de
// MAIOR_QUADRO_NO_FIO bytes. Devolve o número de bytes escritos.
size_t quadro_codificar(uint8_t tipo, uint8_t seq, const void *payload, size_t len,
                        uint8_t *fio);

// Recepção (seção 3.2): acumula bytes até o 0x00, decodifica e entrega ao
// callback os quadros válidos. Os inválidos só incrementam `descartados`.
typedef struct {
    uint8_t acum[MAIOR_QUADRO_NO_FIO];
    size_t n;
    bool descartando;           // estourou o acúmulo; espera o próximo 0x00
    uint32_t descartados;
    uint8_t bruto[MAX_QUADRO_BRUTO];
} decodificador_t;

void decodificador_alimentar(decodificador_t *d, const uint8_t *dados, size_t n,
                             quadro_cb_t cb, void *ctx);

// COBS sem o delimitador. A decodificação devolve -1 se a entrada for
// inválida ou não couber em `cap`.
size_t cobs_codificar(const uint8_t *in, size_t n, uint8_t *out);
int cobs_decodificar(const uint8_t *in, size_t n, uint8_t *out, size_t cap);
