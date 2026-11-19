// swath.c - slots de swath na PSRAM
//
// A sessão (núcleo 0) escreve as colunas; o cabeçote (núcleo 1) lê. No
// ESP32-S3 a cache de dados é compartilhada pelos dois núcleos, então a
// PSRAM fica coerente sem cuidado extra. Os estados são atômicos porque a
// sessão e a tarefa de movimento mexem neles.
#include <stdatomic.h>
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "swath.h"
#include "protocolo.h"

static const char *TAG = "swath";

enum { LIVRE, RECEBENDO, NA_FILA, EXECUTANDO };

static swath_t slots[SWATH_SLOTS];
static atomic_uchar estado[SWATH_SLOTS];
static atomic_ushort id_atual;

void swath_iniciar(void)
{
    // 4 × 20000 × 16 B = 1,25 MiB: só cabe na PSRAM
    for (int i = 0; i < SWATH_SLOTS; i++) {
        slots[i].dados = heap_caps_malloc(MAX_COLS * BYTES_POR_COLUNA, MALLOC_CAP_SPIRAM);
        if (slots[i].dados == NULL) {
            ESP_LOGE(TAG, "sem PSRAM para o slot %d", i);
            abort();
        }
        atomic_store(&estado[i], LIVRE);
    }
}

uint8_t swath_livres(void)
{
    uint8_t n = 0;
    for (int i = 0; i < SWATH_SLOTS; i++) {
        n += atomic_load(&estado[i]) == LIVRE;
    }
    return n;
}

int swath_reservar(void)
{
    for (int i = 0; i < SWATH_SLOTS; i++) {
        unsigned char livre = LIVRE;
        if (atomic_compare_exchange_strong(&estado[i], &livre, RECEBENDO)) {
            return i;
        }
    }
    return -1;
}

swath_t *swath_slot(int i)
{
    return &slots[i];
}

void swath_enfileirada(int i)
{
    atomic_store(&estado[i], NA_FILA);
}

bool swath_comecar(int i)
{
    unsigned char na_fila = NA_FILA;
    if (!atomic_compare_exchange_strong(&estado[i], &na_fila, EXECUTANDO)) {
        return false;
    }
    atomic_store(&id_atual, slots[i].id);
    return true;
}

void swath_liberar(int i)
{
    atomic_store(&estado[i], LIVRE);
}

void swath_descartar_pendentes(void)
{
    for (int i = 0; i < SWATH_SLOTS; i++) {
        unsigned char e = RECEBENDO;
        if (!atomic_compare_exchange_strong(&estado[i], &e, LIVRE)) {
            e = NA_FILA;
            atomic_compare_exchange_strong(&estado[i], &e, LIVRE);
        }
    }
}

bool swath_em_andamento(void)
{
    for (int i = 0; i < SWATH_SLOTS; i++) {
        unsigned char e = atomic_load(&estado[i]);
        if (e == NA_FILA || e == EXECUTANDO) {
            return true;
        }
    }
    return false;
}

uint16_t swath_id_atual(void)
{
    return atomic_load(&id_atual);
}
