// sessao.h - transporte, sessão e aplicação (PROTOCOL.md, seções 4, 5 e 7)
//
// Etapa 1: HELLO, Go-Back-N, PING, STATUS periódico, perda de enlace,
// ESTOP, CANCEL, CLEAR_FAULT e parâmetros. Os comandos de fila (HOME, JOG,
// MOVE...) e as swaths ainda respondem ERR UNKNOWN_OPCODE.
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "quadro.h"

// Com autoteste_ok = false, a máquina nasce em FAULT com fault = INTERNAL.
void sessao_iniciar(bool autoteste_ok);

// Callback do enlace: um quadro válido recebido.
void sessao_receber(const quadro_t *q, void *ctx);

// Chamar a cada volta do laço de comunicação: STATUS e timeout do enlace.
void sessao_periodico(int64_t agora_us);
