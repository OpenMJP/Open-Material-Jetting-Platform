// autoteste.h - vetores da seção 10 do PROTOCOL.md, conferidos no boot
#pragma once

#include <stdbool.h>

// Confere CRCs, COBS e montagem/desmontagem de quadros contra os vetores
// que o lado do PC também reproduz. Loga cada falha no console (USB).
bool autoteste_executar(void);
