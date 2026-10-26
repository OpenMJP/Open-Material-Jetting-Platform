// quadro.c - COBS, CRC e montagem/desmontagem de quadros
#include <string.h>
#include "quadro.h"
#include "crc.h"

size_t cobs_codificar(const uint8_t *in, size_t n, uint8_t *out)
{
    size_t w = 1;           // próxima posição de escrita
    size_t pos_codigo = 0;  // onde vai o código do bloco atual
    uint8_t codigo = 1;

    for (size_t i = 0; i < n; i++) {
        if (in[i] != 0) {
            out[w++] = in[i];
            codigo++;
        }
        if (in[i] == 0 || codigo == 0xFF) {
            out[pos_codigo] = codigo;
            codigo = 1;
            pos_codigo = w;
            // Bloco de 254 que termina junto com a entrada não abre bloco
            // novo: é a forma canônica, a mesma do pacote `cobs` do Python.
            if (in[i] == 0 || i + 1 < n) {
                w++;
            }
        }
    }
    out[pos_codigo] = codigo;
    return w;
}

int cobs_decodificar(const uint8_t *in, size_t n, uint8_t *out, size_t cap)
{
    size_t i = 0, w = 0;
    while (i < n) {
        uint8_t codigo = in[i++];
        if (codigo == 0) {
            return -1;
        }
        for (int k = 1; k < codigo; k++) {
            if (i >= n || w >= cap) {
                return -1;
            }
            out[w++] = in[i++];
        }
        if (codigo != 0xFF && i < n) {
            if (w >= cap) {
                return -1;
            }
            out[w++] = 0;
        }
    }
    return (int)w;
}

size_t quadro_codificar(uint8_t tipo, uint8_t seq, const void *payload, size_t len,
                        uint8_t *fio)
{
    uint8_t bruto[MAX_QUADRO_BRUTO];

    bruto[0] = tipo;
    bruto[1] = seq;
    if (len) {
        memcpy(bruto + 2, payload, len);
    }
    put_u16(bruto + 2 + len, crc16(bruto, 2 + len));

    size_t n = cobs_codificar(bruto, len + 4, fio);
    fio[n++] = 0x00;
    return n;
}

static void processar(decodificador_t *d, quadro_cb_t cb, void *ctx)
{
    int m = cobs_decodificar(d->acum, d->n, d->bruto, sizeof d->bruto);
    d->n = 0;

    if (m < 4 || m > MAX_QUADRO_BRUTO
        || crc16(d->bruto, m - 2) != get_u16(d->bruto + m - 2)) {
        d->descartados++;
        return;
    }

    quadro_t q = {
        .tipo = d->bruto[0],
        .seq = d->bruto[1],
        .len = (uint16_t)(m - 4),
        .payload = d->bruto + 2,
    };
    cb(&q, ctx);
}

void decodificador_alimentar(decodificador_t *d, const uint8_t *dados, size_t n,
                             quadro_cb_t cb, void *ctx)
{
    for (size_t i = 0; i < n; i++) {
        uint8_t b = dados[i];
        if (b == 0x00) {
            if (d->descartando) {
                d->descartando = false;
                d->n = 0;
            } else if (d->n > 0) {      // 0x00 isolado é ignorado
                processar(d, cb, ctx);
            }
            continue;
        }
        if (d->descartando) {
            continue;
        }
        if (d->n >= sizeof d->acum) {
            d->descartando = true;
            d->n = 0;
            d->descartados++;
            continue;
        }
        d->acum[d->n++] = b;
    }
}
