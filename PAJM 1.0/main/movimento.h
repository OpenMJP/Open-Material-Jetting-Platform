// movimento.h - fila de execução e geração de passos (PROTOCOL.md 4.3, 7.3, 7.6)
//
// A sessão valida e enfileira; a tarefa de movimento executa em ordem. Os
// passos saem de um timer em interrupção, um eixo por vez, com rampa
// trapezoidal (ou velocidade constante, no HOME).
#pragma once

#include <stdbool.h>
#include <stdint.h>

enum {
    CMD_HOME,
    CMD_JOG,
    CMD_MOVE,
    CMD_DWELL,
};

typedef struct {
    uint8_t tipo;
    uint8_t eixo;       // JOG e MOVE: eixo; HOME: máscara de eixos
    int32_t valor;      // JOG: delta; MOVE: posição; DWELL: ms
    uint32_t vel;       // passos/s (JOG e MOVE)
    uint32_t geracao;   // preenchido por movimento_enfileirar
} comando_t;

void movimento_iniciar(void);

// false se a fila estiver cheia (NO_QUEUE_SLOT)
bool movimento_enfileirar(comando_t c);

// Entradas livres: a fila conta o comando em execução até ele terminar.
uint8_t movimento_fila_livre(void);
bool movimento_ocioso(void);

// CANCEL: para com rampa e descarta a fila.
void movimento_cancelar(void);

// ESTOP e falhas: para sem rampa, descarta a fila e tira a referência de
// todos os eixos. Não mexe no MOTOR_EN: isso é com placa_estado_seguro().
void movimento_parar_ja(void);

uint8_t movimento_referenciados(void);     // bits 0-2 = X, Y, Z
int32_t movimento_posicao(int eixo);

// Falha detectada na execução (STATUS.fault), ou 0. Ler zera.
uint8_t movimento_falha_pendente(void);
