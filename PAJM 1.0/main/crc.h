// crc.h - CRC-16/CCITT-FALSE do quadro e CRC-32 IEEE da swath (PROTOCOL.md 3.1 e 7.3)
#pragma once

#include <stddef.h>
#include <stdint.h>

// Gera as tabelas. Chamar uma vez no boot, antes de qualquer outro uso.
void crc_iniciar(void);

// Polinômio 0x1021, início 0xFFFF, sem reflexão, sem XOR final.
// Conferência: "123456789" -> 0x29B1.
uint16_t crc16(const uint8_t *dados, size_t n);

// IEEE/zlib. Encadeável como o zlib.crc32: crc32(crc32(0, a), b) = crc32(0, a + b).
// Conferência: "123456789" -> 0xCBF43926.
uint32_t crc32(uint32_t crc, const uint8_t *dados, size_t n);
