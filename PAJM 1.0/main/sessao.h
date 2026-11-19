// sessao.h - transporte, sessão e aplicação (PROTOCOL.md, seções 4, 5 e 7)
//
// Implementado: HELLO, Go-Back-N, PING, STATUS periódico, perda de enlace,
// ESTOP, CANCEL, CLEAR_FAULT, parâmetros, HOME, JOG, MOVE, DWELL,
// HEAD_POWER, SPIT, JOB_BEGIN, JOB_END, SWATH_BEGIN/DATA/END e MOTORS_OFF.
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
