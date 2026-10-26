// enlace.h - UART0 (CH343P) a 2 Mbaud com enquadramento COBS
#pragma once

#include <stddef.h>
#include <stdint.h>
#include "quadro.h"

void enlace_iniciar(void);

// Lê o que já estiver no buffer da UART; se não houver nada, espera até
// `espera_ms` pelo primeiro byte. Cada quadro válido vai para `cb`.
void enlace_receber(uint32_t espera_ms, quadro_cb_t cb, void *ctx);

// Monta e envia um quadro com o próximo seq do ESP (seção 4.1: contador
// próprio, circular). Pode ser chamada de qualquer tarefa.
void enlace_enviar(uint8_t tipo, const void *payload, size_t len);

// Zera o contador próprio (HELLO, seção 5.1)
void enlace_zerar_seq(void);

// Quadros descartados na recepção, saturado em 65 535 (STATUS.frames_dropped)
uint16_t enlace_descartados(void);
