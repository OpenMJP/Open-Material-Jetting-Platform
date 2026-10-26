// sessao.c - máquina de estados do protocolo, lado do ESP
//
// Tudo aqui roda na tarefa de comunicação, então o estado não precisa de
// trava. A posição real, a referência e a fila pertencem ao movimento.c,
// que roda em outra tarefa; a conversa com ele é só pela interface dele.
//
// Referência executável do comportamento esperado:
// SLICER 1.0/pajm/machine/simulador_v11.py
#include "esp_log.h"
#include "esp_timer.h"
#include "sessao.h"
#include "enlace.h"
#include "movimento.h"
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
} m;

// Estado planejado (seção 7.1): como a máquina vai estar depois de executar
// tudo o que já está na fila. As validações olham para ele, não para o
// estado real. Ressincroniza com o real sempre que a fila esvazia.
static struct {
    uint8_t referenciados;
    int32_t pos[3];
} plano;

static const int32_t CURSO[3] = { CURSO_X, CURSO_Y, CURSO_Z };
static const uint16_t PARAM_VMAX[3] = { PARAM_VMAX_X, PARAM_VMAX_Y, PARAM_VMAX_Z };

// ---- envio --------------------------------------------------------------

static uint8_t swath_livres(void) { return SWATH_SLOTS; }
static uint8_t fila_livres(void) { return movimento_fila_livre(); }

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
    uint8_t flags = movimento_referenciados() & 0x07;
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
    put_i32(p + 8, movimento_posicao(0));
    put_i32(p + 12, movimento_posicao(1));
    put_i32(p + 16, movimento_posicao(2));
    p[20] = m.last_error;
    p[21] = m.fault;
    put_u16(p + 22, enlace_descartados());
    enlace_enviar(OP_STATUS, p, sizeof p);
}

// ---- ações --------------------------------------------------------------

// Rotina de desligamento (seção 7.7). A rampa de parada corre na tarefa de
// movimento; a sessão não espera por ela, então o STOPPING ainda não aparece.
static void desligar(uint8_t motivo)
{
    placa_vpp_desligar();
    movimento_cancelar();
    m.estado = EST_IDLE;
    m.last_error = motivo;
}

// ESTOP (seção 7.8) e falhas: corte sem rampa, drivers desligados, eixos
// sem referência.
static void falhar(uint8_t codigo)
{
    movimento_parar_ja();
    placa_estado_seguro();
    plano.referenciados = 0;
    m.estado = EST_FAULT;
    m.fault = codigo;
    ESP_LOGW(TAG, "FAULT 0x%02x", codigo);
}

static void cancelar(void)
{
    if (m.estado == EST_JOB) {
        desligar(ERR_CANCELLED);
    } else if (m.estado == EST_IDLE) {
        movimento_cancelar();
    }
}

static uint8_t enfileirar(comando_t c)
{
    return movimento_enfileirar(c) ? ERR_OK : ERR_NO_QUEUE_SLOT;
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
        // Os eixos já perderam a referência ao entrar em FAULT. Os drivers
        // continuam desligados até o próximo movimento.
        m.estado = EST_IDLE;
        m.fault = 0;
        return ERR_OK;

    case OP_SET_PARAM:
        if (m.estado != EST_IDLE || !movimento_ocioso()) {
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

    case OP_HOME: {
        uint8_t mascara = p[0];
        if (mascara & ~0x07) {
            *det = 0;
            return ERR_BAD_PARAM;
        }
        uint8_t codigo = enfileirar((comando_t){ .tipo = CMD_HOME, .eixo = mascara });
        if (codigo == ERR_OK) {
            if (mascara & 0x04) {
                mascara |= 0x01;            // HOME com Z também referencia X
            } else if (mascara && (plano.referenciados & 0x04)) {
                // o Z sobe antes de X e Y andarem e fica lá em cima
                int32_t z = plano.pos[2] + MM_PARA_PASSOS(HOME_SUBIDA_Z_MM, PASSOS_POR_M_Z);
                plano.pos[2] = z < CURSO_Z ? z : CURSO_Z;
            }
            for (int e = 0; e < 3; e++) {
                if (mascara & (1 << e)) {
                    plano.referenciados |= 1 << e;
                    plano.pos[e] = 0;
                }
            }
        }
        return codigo;
    }

    case OP_MOVE: {
        uint8_t e = p[0];
        int32_t pos = get_i32(p + 4);
        uint32_t vel = get_u32(p + 8);
        if (e > 2) {
            *det = 0;
            return ERR_BAD_PARAM;
        }
        if (!(plano.referenciados & (1 << e))) {
            *det = 1 << e;
            return ERR_NOT_HOMED;
        }
        if (pos < 0 || pos > CURSO[e]) {
            *det = pos;
            return ERR_OUT_OF_TRAVEL;
        }
        if (vel == 0 || vel > (uint32_t)parametro(PARAM_VMAX[e])) {
            *det = 8;
            return ERR_BAD_PARAM;
        }
        uint8_t codigo = enfileirar((comando_t){ .tipo = CMD_MOVE, .eixo = e,
                                                 .valor = pos, .vel = vel });
        if (codigo == ERR_OK) {
            plano.pos[e] = pos;
        }
        return codigo;
    }

    case OP_JOG: {
        if (m.estado != EST_IDLE) {
            *det = m.estado;
            return ERR_BAD_STATE;
        }
        uint8_t e = p[0];
        int32_t delta = get_i32(p + 4);
        uint32_t vel = get_u32(p + 8);
        if (e > 2) {
            *det = 0;
            return ERR_BAD_PARAM;
        }
        if (vel == 0 || vel > (uint32_t)parametro(PARAM_VMAX[e])) {
            *det = 8;
            return ERR_BAD_PARAM;
        }
        int32_t destino = plano.pos[e] + delta;
        bool ref = plano.referenciados & (1 << e);
        if (ref && (destino < 0 || destino > CURSO[e])) {
            *det = destino;
            return ERR_OUT_OF_TRAVEL;
        }
        uint8_t codigo = enfileirar((comando_t){ .tipo = CMD_JOG, .eixo = e,
                                                 .valor = delta, .vel = vel });
        if (codigo == ERR_OK) {
            plano.pos[e] = destino;
        }
        return codigo;
    }

    case OP_DWELL:
        return enfileirar((comando_t){ .tipo = CMD_DWELL, .valor = (int32_t)get_u32(p) });

    default:
        // Conhecido pelo protocolo, mas ainda não implementado
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
        falhar(FALHA_ESTOP);
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
    uint8_t f = movimento_falha_pendente();
    if (f && m.estado != EST_FAULT) {
        falhar(f);
    }
    if (movimento_ocioso()) {
        plano.referenciados = movimento_referenciados();
        for (int e = 0; e < 3; e++) {
            plano.pos[e] = movimento_posicao(e);
        }
    }

    if (!s.ativa) {
        return;
    }

    if (agora - s.t_ultimo_rx_us > LINK_TIMEOUT_MS * 1000LL) {
        s.ativa = false;
        if (m.estado == EST_JOB) {
            desligar(ERR_LINK_LOST);
        } else if (m.estado == EST_IDLE) {
            movimento_cancelar();           // movimento manual: para com rampa
        }
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
