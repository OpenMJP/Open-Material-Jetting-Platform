// autoteste.c - conferência do enlace contra os vetores do PROTOCOL.md
//
// Se isto falhar, o firmware não conversa com o PC: melhor saber no boot,
// pelo console, do que pela primeira vez na bancada com o bringup.py.
#include <string.h>
#include "esp_log.h"
#include "autoteste.h"
#include "crc.h"
#include "quadro.h"

static const char *TAG = "autoteste";

typedef struct {
    const char *nome;
    uint8_t tipo, seq;
    const uint8_t *payload;
    size_t len;
    const uint8_t *fio;
    size_t len_fio;
} vetor_t;

static const uint8_t HELLO_P[] = { 0x01, 0x01 };
static const uint8_t HELLO_F[] = { 0x02, 0x01, 0x05, 0x01, 0x01, 0x64, 0xD1, 0x00 };

static const uint8_t PING_F[] = { 0x05, 0x02, 0x01, 0x4C, 0x6B, 0x00 };

static const uint8_t ACK_P[] = { 0x01, 0x04, 0x20, 0x00 };
static const uint8_t ACK_F[] = { 0x06, 0x81, 0x07, 0x01, 0x04, 0x20, 0x03, 0xD6, 0x54, 0x00 };

// swath_id = 1, col_start = 0, uma coluna com CH128 e CH1
static const uint8_t SD_P[] = {
    0x01, 0x00, 0x00, 0x00,
    0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
};
static const uint8_t SD_F[] = {
    0x04, 0x31, 0x05, 0x01, 0x01, 0x01, 0x02, 0x80, 0x01, 0x01, 0x01, 0x01, 0x01,
    0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x04, 0x01, 0x29, 0xBA, 0x00,
};

static const vetor_t VETORES[] = {
    { "HELLO",      0x01, 0, HELLO_P, sizeof HELLO_P, HELLO_F, sizeof HELLO_F },
    { "PING",       0x02, 1, NULL,    0,              PING_F,  sizeof PING_F  },
    { "ACK",        0x81, 7, ACK_P,   sizeof ACK_P,   ACK_F,   sizeof ACK_F   },
    { "SWATH_DATA", 0x31, 5, SD_P,    sizeof SD_P,    SD_F,    sizeof SD_F    },
};

typedef struct {
    const vetor_t *v;
    int recebidos;
    bool igual;
} contexto_t;

static void conferir(const quadro_t *q, void *arg)
{
    contexto_t *c = arg;
    c->recebidos++;
    c->igual = q->tipo == c->v->tipo && q->seq == c->v->seq && q->len == c->v->len
            && (q->len == 0 || memcmp(q->payload, c->v->payload, q->len) == 0);
}

// decodificador_t tem ~2 KB: fica fora da pilha.
static decodificador_t dec;
static uint8_t fio[MAIOR_QUADRO_NO_FIO];

bool autoteste_executar(void)
{
    bool ok = true;
    static const uint8_t TEXTO[] = "123456789";

    uint16_t c16 = crc16(TEXTO, 9);
    if (c16 != 0x29B1) {
        ESP_LOGE(TAG, "CRC-16 deu 0x%04X, esperado 0x29B1", c16);
        ok = false;
    }
    uint32_t c32 = crc32(0, TEXTO, 9);
    if (c32 != 0xCBF43926u) {
        ESP_LOGE(TAG, "CRC-32 deu 0x%08lX, esperado 0xCBF43926", (unsigned long)c32);
        ok = false;
    }
    c32 = crc32(0, SD_P + 4, BYTES_POR_COLUNA);
    if (c32 != 0xC7419637u) {
        ESP_LOGE(TAG, "CRC-32 da coluna deu 0x%08lX, esperado 0xC7419637", (unsigned long)c32);
        ok = false;
    }

    for (size_t i = 0; i < sizeof VETORES / sizeof VETORES[0]; i++) {
        const vetor_t *v = &VETORES[i];

        size_t n = quadro_codificar(v->tipo, v->seq, v->payload, v->len, fio);
        if (n != v->len_fio || memcmp(fio, v->fio, n) != 0) {
            ESP_LOGE(TAG, "%s: montagem difere do vetor", v->nome);
            ESP_LOG_BUFFER_HEX_LEVEL(TAG, fio, n, ESP_LOG_ERROR);
            ok = false;
        }

        memset(&dec, 0, sizeof dec);
        contexto_t c = { .v = v };
        decodificador_alimentar(&dec, v->fio, v->len_fio, conferir, &c);
        if (c.recebidos != 1 || !c.igual || dec.descartados != 0) {
            ESP_LOGE(TAG, "%s: desmontagem difere do vetor", v->nome);
            ok = false;
        }
    }

    // Quadro corrompido tem de ser descartado, não entregue
    memset(&dec, 0, sizeof dec);
    memcpy(fio, PING_F, sizeof PING_F);
    fio[3] ^= 0x01;
    contexto_t c = { .v = &VETORES[1] };
    decodificador_alimentar(&dec, fio, sizeof PING_F, conferir, &c);
    if (c.recebidos != 0 || dec.descartados != 1) {
        ESP_LOGE(TAG, "quadro com CRC errado não foi descartado");
        ok = false;
    }

    if (ok) {
        ESP_LOGI(TAG, "CRCs e vetores da seção 10 conferem");
    }
    return ok;
}
