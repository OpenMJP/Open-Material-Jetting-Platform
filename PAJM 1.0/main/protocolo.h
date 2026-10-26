// protocolo.h - constantes e códigos do PROTOCOL.md v1.1
//
// Este arquivo só traduz o PROTOCOL.md para C. Qualquer mudança começa
// lá, com incremento de versão (seção 12), e depois vem para cá.
#pragma once

#include <stdint.h>

#define PROTO_MAJOR  1
#define PROTO_MINOR  1

#define FW_MAJOR  0
#define FW_MINOR  1
#define FW_PATCH  0

// Seção 8.1
#define BAUD                 2000000
#define MAX_PAYLOAD          1028
#define MAX_QUADRO_BRUTO     (MAX_PAYLOAD + 4)   // type + seq + payload + crc16
#define MAIOR_QUADRO_NO_FIO  1038                // COBS + delimitador
#define WINDOW               8
#define UART_RX_BUF          (16 * 1024)
#define STATUS_PERIOD_MS     100
#define LINK_TIMEOUT_MS      1000
#define SWATH_SLOTS          4
#define MAX_COLS             8192
#define QUEUE_SLOTS          32
#define BYTES_POR_COLUNA     16

// Seção 7.2
enum {
    OP_HELLO        = 0x01,
    OP_PING         = 0x02,
    OP_CLEAR_FAULT  = 0x03,
    OP_ESTOP        = 0x08,
    OP_CANCEL       = 0x09,
    OP_JOB_BEGIN    = 0x10,
    OP_JOB_END      = 0x11,
    OP_HOME         = 0x12,
    OP_HEAD_POWER   = 0x13,
    OP_MOVE         = 0x14,
    OP_DWELL        = 0x15,
    OP_SPIT         = 0x16,
    OP_SWATH_BEGIN  = 0x30,
    OP_SWATH_DATA   = 0x31,
    OP_SWATH_END    = 0x32,
    OP_JOG          = 0x40,
    OP_SET_PARAM    = 0x41,
    OP_GET_PARAM    = 0x42,
    OP_SAVE_PARAMS  = 0x43,

    OP_HELLO_ACK    = 0x80,
    OP_ACK          = 0x81,
    OP_NACK         = 0x82,
    OP_STATUS       = 0x83,
    OP_ERR          = 0x84,
    OP_PARAM_VALUE  = 0x85,
    OP_LOG          = 0x86,
};

// Mensagens de trabalho e de dados: erro nelas, em JOB, aborta o job (7.1)
#define OP_DE_TRABALHO(op)  ((op) >= 0x10 && (op) <= 0x3F)

// Seção 7.1
enum {
    EST_IDLE     = 0,
    EST_JOB      = 1,
    EST_STOPPING = 2,
    EST_FAULT    = 3,
};

// Seção 7.9, ERR.code
enum {
    ERR_OK               = 0x00,
    ERR_BAD_STATE        = 0x01,
    ERR_BAD_LENGTH       = 0x02,
    ERR_UNKNOWN_OPCODE   = 0x03,
    ERR_BAD_PARAM        = 0x04,
    ERR_NOT_HOMED        = 0x05,
    ERR_OUT_OF_TRAVEL    = 0x06,
    ERR_FIRE_TOO_FAST    = 0x07,
    ERR_NO_SWATH_SLOT    = 0x08,
    ERR_NO_QUEUE_SLOT    = 0x09,
    ERR_SWATH_SEQUENCE   = 0x0A,
    ERR_SWATH_INCOMPLETE = 0x0B,
    ERR_SWATH_CRC        = 0x0C,
    ERR_HEAD_OFF         = 0x0D,
    ERR_LINK_LOST        = 0x0E,
    ERR_CANCELLED        = 0x0F,
};

// Seção 7.9, STATUS.fault
enum {
    FALHA_ESTOP         = 0x01,
    FALHA_HEAD_NFAULT   = 0x02,
    FALHA_READY_TIMEOUT = 0x03,
    FALHA_FIRE_OVERRUN  = 0x04,
    FALHA_ENDSTOP       = 0x05,
    FALHA_INTERNAL      = 0x06,
};

#define NACK_SEQ_GAP  0x01

// STATUS.flags
#define FLAG_REF_X        0x01
#define FLAG_REF_Y        0x02
#define FLAG_REF_Z        0x04
#define FLAG_VPP          0x08
#define FLAG_NFAULT       0x10
#define FLAG_SWATH_ATIVA  0x20

// Seção 8.2, param_id
enum {
    PARAM_ACCEL_X         = 0x0001,
    PARAM_ACCEL_Y         = 0x0002,
    PARAM_ACCEL_Z         = 0x0003,
    PARAM_VMAX_X          = 0x0011,
    PARAM_VMAX_Y          = 0x0012,
    PARAM_VMAX_Z          = 0x0013,
    PARAM_RUNUP_MARGIN    = 0x0020,
    PARAM_CHECK_READY     = 0x0030,
    PARAM_LOG_ENABLE      = 0x0031,
    PARAM_MIN_FIRE_PERIOD = 0x0032,
};

#define MIN_FIRE_PERIOD_PISO_US  180   // ciclo T2 do manual do XJ128

// Campos little-endian e empacotados (seção 6). Funções explícitas em vez
// de structs packed: não dependem do alinhamento do buffer.
static inline void put_u16(uint8_t *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
static inline void put_u32(uint8_t *p, uint32_t v)
{
    p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24;
}
static inline void put_i32(uint8_t *p, int32_t v) { put_u32(p, (uint32_t)v); }
static inline uint16_t get_u16(const uint8_t *p) { return p[0] | (p[1] << 8); }
static inline uint32_t get_u32(const uint8_t *p)
{
    return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline int32_t get_i32(const uint8_t *p) { return (int32_t)get_u32(p); }
