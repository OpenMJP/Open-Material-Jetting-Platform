// sessao.c - máquina de estados do protocolo, lado do ESP
//
// Tudo aqui roda na tarefa de comunicação, então o estado não precisa de
// trava. Quando o movimento entrar (etapa 2), a posição e o fim dos
// comandos virão de outra tarefa e esta fronteira ganha sincronização.
//
// Referência executável do comportamento esperado:
// SLICER 1.0/pajm/machine/simulador_v11.py
#include "esp_log.h"
#include "esp_timer.h"
#include "sessao.h"
#include "enlace.h"
#include "parametros.h"
#include "placa.h"
#include "protocolo.h"
#include "maquina.h"

static const char *TAG = "sessao";

// Transporte e sessão (seções 4.2 e 5)
static struct {
    bool ativa;
    uint8_t esperado;           // E: próximo seq esperado
    bool nack_enviado;          // um NACK por lacuna
    int64_t t_ultimo_rx_us;
    int64_t t_status_us;
} s;

// Aplicação (seção 7)
static struct {
    uint8_t estado;
    uint8_t fault;
    uint8_t last_error;
    uint16_t job_id;
    uint16_t swath_id;
    uint8_t referenciados;      // bits 0-2 = X, Y, Z
    int32_t pos[3];
} m;

// ---- envio --------------------------------------------------------------

static uint8_t swath_livres(void) { return SWATH_SLOTS; }
static uint8_t fila_livres(void) { return QUEUE_SLOTS; }

static void enviar_ack(uint8_t seq)
{
    const uint8_t p[4] = { seq, swath_livres(), fila_livres(), 0 };
    enlace_enviar(OP_ACK, p, sizeof p);
}

static void enviar_nack(uint8_t esperado)
{
    const uint8_t p[2] = { esperado, NACK_SEQ_GAP };
    enlace_enviar(OP_NACK, p, sizeof p);
}

// ERR vai ANTES do ACK do quadro que causou o erro (seção 4.2)
static void enviar_err(uint8_t seq, uint8_t codigo, int32_t detalhe)
{
    uint8_t p[8] = { seq, codigo };
    put_i32(p + 4, detalhe);
    m.last_error = codigo;
    enlace_enviar(OP_ERR, p, sizeof p);
}

static void enviar_hello_ack(void)
{
    uint8_t p[40] = {
        PROTO_MAJOR, PROTO_MINOR, FW_MAJOR, FW_MINOR, FW_PATCH,
        WINDOW, SWATH_SLOTS, QUEUE_SLOTS,
    };
    put_u16(p + 8, MAX_PAYLOAD);
    put_u16(p + 10, MAX_COLS);
    put_u16(p + 12, (uint16_t)parametro(PARAM_MIN_FIRE_PERIOD));
    put_u32(p + 16, PASSOS_POR_M_X);
    put_u32(p + 20, PASSOS_POR_M_Y);
    put_u32(p + 24, PASSOS_POR_M_Z);
    put_i32(p + 28, CURSO_X);
    put_i32(p + 32, CURSO_Y);
    put_i32(p + 36, CURSO_Z);
    enlace_enviar(OP_HELLO_ACK, p, sizeof p);
}

static void enviar_status(void)
{
    uint8_t flags = m.referenciados & 0x07;
    if (placa_vpp_ligado()) {
        flags |= FLAG_VPP;
    }
    if (placa_nfault_ativo()) {
        flags |= FLAG_NFAULT;
    }

    uint8_t p[24] = { m.estado, flags };
    put_u16(p + 2, m.job_id);
    put_u16(p + 4, m.swath_id);
    p[6] = swath_livres();
    p[7] = fila_livres();
    put_i32(p + 8, m.pos[0]);
    put_i32(p + 12, m.pos[1]);
    put_i32(p + 16, m.pos[2]);
    p[20] = m.last_error;
    p[21] = m.fault;
    put_u16(p + 22, enlace_descartados());
    enlace_enviar(OP_STATUS, p, sizeof p);
}

// ---- ações --------------------------------------------------------------

// Rotina de desligamento (seção 7.7). Na etapa 1 não há fila nem
// movimento, então ela termina na hora e o STOPPING não chega a aparecer.
static void desligar(uint8_t motivo)
{
    placa_vpp_desligar();
    m.estado = EST_IDLE;
    m.last_error = motivo;
}

// Seção 7.8
static void estop(void)
{
    placa_estado_seguro();
    m.referenciados = 0;
    m.estado = EST_FAULT;
    m.fault = FALHA_ESTOP;
    ESP_LOGW(TAG, "ESTOP");
}

static void cancelar(void)
{
    if (m.estado == EST_JOB) {
        desligar(ERR_CANCELLED);
    }
    // Em IDLE: parar o movimento com rampa e esvaziar a fila (etapa 2)
}

// ---- tratamento por opcode ----------------------------------------------

#define TAM_DESCONHECIDO  (-1)
#define TAM_VARIAVEL      (-2)

static int tamanho_esperado(uint8_t op)
{
    switch (op) {
    case OP_PING: case OP_CLEAR_FAULT: case OP_JOB_END: case OP_SAVE_PARAMS:
        return 0;
    case OP_HOME: case OP_HEAD_POWER:
        return 1;
    case OP_GET_PARAM:
        return 2;
    case OP_JOB_BEGIN: case OP_DWELL:
        return 4;
    case OP_SWATH_END: case OP_SET_PARAM:
        return 8;
    case OP_MOVE: case OP_JOG:
        return 12;
    case OP_SPIT: case OP_SWATH_BEGIN:
        return 20;
    case OP_SWATH_DATA:
        return TAM_VARIAVEL;
    default:
        return TAM_DESCONHECIDO;
    }
}

static bool tamanho_ok(uint8_t op, uint16_t len)
{
    int t = tamanho_esperado(op);
    if (t == TAM_VARIAVEL) {    // SWATH_DATA: 4 + 16·n, n de 1 a 64
        return len >= 4 + BYTES_POR_COLUNA && len <= MAX_PAYLOAD
            && (len - 4) % BYTES_POR_COLUNA == 0;
    }
    return t == len;
}

static uint8_t despachar(uint8_t op, const uint8_t *p, int32_t *det)
{
    // Restrições por estado (seção 7.1)
    if (m.estado == EST_FAULT
        && op != OP_CLEAR_FAULT && op != OP_GET_PARAM && op != OP_PING) {
        *det = m.estado;
        return ERR_BAD_STATE;
    }
    if (m.estado == EST_STOPPING && op != OP_GET_PARAM && op != OP_PING) {
        *det = m.estado;
        return ERR_BAD_STATE;
    }

    switch (op) {
    case OP_PING:
        return ERR_OK;

    case OP_CLEAR_FAULT:
        if (m.estado != EST_FAULT) {
            *det = m.estado;
            return ERR_BAD_STATE;
        }
        // Os drivers continuam desligados; o HOME da etapa 2 os religa.
        m.estado = EST_IDLE;
        m.fault = 0;
        m.referenciados = 0;
        return ERR_OK;

    case OP_SET_PARAM:
        // Aceito em IDLE com a fila vazia (a fila ainda não existe)
        if (m.estado != EST_IDLE) {
            *det = m.estado;
            return ERR_BAD_STATE;
        }
        return parametros_definir(get_u16(p), get_i32(p + 4), det);

    case OP_GET_PARAM: {
        uint16_t id = get_u16(p);
        int32_t v;
        if (!parametros_obter(id, &v)) {
            *det = 0;
            return ERR_BAD_PARAM;
        }
        uint8_t r[8] = { 0 };
        put_u16(r, id);
        put_i32(r + 4, v);
        enlace_enviar(OP_PARAM_VALUE, r, sizeof r);
        return ERR_OK;
    }

    case OP_SAVE_PARAMS:
        if (m.estado != EST_IDLE) {
            *det = m.estado;
            return ERR_BAD_STATE;
        }
        // O protocolo não tem código de erro para falha de gravação
        if (parametros_salvar() != ESP_OK) {
            ESP_LOGE(TAG, "falha ao gravar os parâmetros na NVS");
        }
        return ERR_OK;

    default:
        // Conhecido pelo protocolo, mas ainda não implementado nesta etapa
        *det = op;
        return ERR_UNKNOWN_OPCODE;
    }
}

static void tratar(const quadro_t *q)
{
    uint8_t codigo;
    int32_t det = 0;

    if (tamanho_esperado(q->tipo) == TAM_DESCONHECIDO) {
        codigo = ERR_UNKNOWN_OPCODE;
        det = q->tipo;
    } else if (!tamanho_ok(q->tipo, q->len)) {
        codigo = ERR_BAD_LENGTH;
        det = q->len;
    } else {
        codigo = despachar(q->tipo, q->payload, &det);
    }

    if (codigo != ERR_OK) {
        enviar_err(q->seq, codigo, det);
        // Em JOB, erro de mensagem de trabalho ou de dados aborta o job
        if (m.estado == EST_JOB && OP_DE_TRABALHO(q->tipo)) {
            desligar(codigo);
        }
    }
    enviar_ack(q->seq);
}

static void hello(const quadro_t *q, int64_t agora)
{
    if (q->len != 2) {
        return;
    }
    if (m.estado == EST_JOB) {
        desligar(ERR_LINK_LOST);
    }
    s.ativa = true;
    s.esperado = 1;
    s.nack_enviado = false;
    s.t_ultimo_rx_us = agora;
    s.t_status_us = agora;
    enlace_zerar_seq();
    enviar_hello_ack();
    ESP_LOGI(TAG, "sessão aberta pelo PC (protocolo %u.%u)", q->payload[0], q->payload[1]);
}

// ---- interface ----------------------------------------------------------

void sessao_iniciar(bool autoteste_ok)
{
    m.estado = EST_IDLE;
    if (!autoteste_ok) {
        m.estado = EST_FAULT;
        m.fault = FALHA_INTERNAL;
    }
}

void sessao_receber(const quadro_t *q, void *ctx)
{
    (void)ctx;
    int64_t agora = esp_timer_get_time();

    // Prioritários: fora da janela, seq ignorado, sem ACK (seção 4.1).
    // O ESTOP é atendido mesmo sem sessão: parar nunca deve depender dela.
    if (q->tipo == OP_ESTOP) {
        estop();
        s.t_ultimo_rx_us = agora;
        return;
    }
    if (q->tipo == OP_HELLO) {
        hello(q, agora);
        return;
    }
    if (!s.ativa) {             // sem sessão, só HELLO (seção 5.1)
        return;
    }
    s.t_ultimo_rx_us = agora;

    if (q->tipo == OP_CANCEL) {
        cancelar();
        return;
    }

    // Janela Go-Back-N (seção 4.2)
    if (q->seq != s.esperado) {
        uint8_t atras = s.esperado - q->seq;
        if (atras >= 1 && atras <= WINDOW) {
            enviar_ack(s.esperado - 1);     // duplicata
        } else if (!s.nack_enviado) {
            enviar_nack(s.esperado);
            s.nack_enviado = true;
        }
        return;
    }
    s.nack_enviado = false;
    s.esperado++;
    tratar(q);
}

void sessao_periodico(int64_t agora)
{
    if (!s.ativa) {
        return;
    }

    if (agora - s.t_ultimo_rx_us > LINK_TIMEOUT_MS * 1000LL) {
        s.ativa = false;
        if (m.estado == EST_JOB) {
            desligar(ERR_LINK_LOST);
        }
        // Movimento manual em curso: parar com rampa (etapa 2)
        ESP_LOGW(TAG, "enlace perdido, sessão encerrada");
        return;
    }

    if (agora - s.t_status_us >= STATUS_PERIOD_MS * 1000LL) {
        s.t_status_us += STATUS_PERIOD_MS * 1000LL;
        if (agora - s.t_status_us >= STATUS_PERIOD_MS * 1000LL) {
            s.t_status_us = agora;      // atrasou mais de um período: não acumula
        }
        enviar_status();
    }
}
