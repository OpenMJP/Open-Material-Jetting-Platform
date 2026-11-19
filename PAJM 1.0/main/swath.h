// swath.h - slots de swath na PSRAM (PROTOCOL.md 4.3 e 7.5)
//
// Ciclo de um slot: LIVRE -> RECEBENDO (SWATH_BEGIN, sessão) -> NA_FILA
// (SWATH_END validado) -> EXECUTANDO (tarefa de movimento) -> LIVRE.
// CANCEL, ESTOP e falhas descartam os slots RECEBENDO e NA_FILA; o que está
// EXECUTANDO é liberado pela própria tarefa de movimento quando ela para.
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint16_t id;
    uint8_t flags;          // bit 0 = dir, bit 1 = PHO
    uint8_t pitch;
    int32_t y, x_start;
    uint16_t n_cols, fire_delay_us;
    uint32_t vel;           // passos/s
    int32_t recuo;          // d da seção 7.5, em passos
    uint16_t recebidas;
    uint32_t crc;           // CRC-32 acumulado das colunas recebidas
    uint8_t *dados;         // n_cols × 16 bytes, na PSRAM
} swath_t;

// Reserva os SWATH_SLOTS buffers na PSRAM.
void swath_iniciar(void);

uint8_t swath_livres(void);

// Slot reservado para recepção, ou -1 se não houver.
int swath_reservar(void);
swath_t *swath_slot(int i);

void swath_enfileirada(int i);
// NA_FILA -> EXECUTANDO. false se o slot foi descartado nesse meio tempo.
bool swath_comecar(int i);
void swath_liberar(int i);
void swath_descartar_pendentes(void);

// Para o STATUS: há swath na fila ou em execução; id da última que começou.
bool swath_em_andamento(void);
uint16_t swath_id_atual(void);
