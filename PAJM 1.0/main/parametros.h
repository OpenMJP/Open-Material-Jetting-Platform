// parametros.h - parâmetros configuráveis por SET_PARAM (PROTOCOL.md, seção 8.2)
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

// Lê da NVS os valores salvos com SAVE_PARAMS; os que não existirem ficam
// com o valor inicial. Exige nvs_flash_init() antes.
void parametros_carregar(void);
esp_err_t parametros_salvar(void);

// false se o id não existir
bool parametros_obter(uint16_t id, int32_t *valor);

// Devolve ERR_OK ou o código de ERR, com o detalhe da seção 7.9 (offset do
// campo ofensivo no payload do SET_PARAM).
uint8_t parametros_definir(uint16_t id, int32_t valor, int32_t *detalhe);

// Leitura direta, para uso interno do firmware (id precisa existir)
int32_t parametro(uint16_t id);
