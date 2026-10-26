// crc.c - CRCs por tabela de 256 entradas
#include "crc.h"

static uint16_t tab16[256];
static uint32_t tab32[256];

void crc_iniciar(void)
{
    for (int i = 0; i < 256; i++) {
        uint16_t c = i << 8;
        for (int k = 0; k < 8; k++) {
            c = (c & 0x8000) ? (c << 1) ^ 0x1021 : (c << 1);
        }
        tab16[i] = c;

        uint32_t r = i;
        for (int k = 0; k < 8; k++) {
            r = (r & 1) ? (r >> 1) ^ 0xEDB88320u : (r >> 1);
        }
        tab32[i] = r;
    }
}

uint16_t crc16(const uint8_t *dados, size_t n)
{
    uint16_t c = 0xFFFF;
    while (n--) {
        c = (c << 8) ^ tab16[((c >> 8) ^ *dados++) & 0xFF];
    }
    return c;
}

uint32_t crc32(uint32_t crc, const uint8_t *dados, size_t n)
{
    uint32_t c = ~crc;
    while (n--) {
        c = (c >> 8) ^ tab32[(c ^ *dados++) & 0xFF];
    }
    return ~c;
}
