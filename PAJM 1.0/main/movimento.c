// movimento.c - tarefa de movimento e gerador de passos
//
// Rampa pelo algoritmo do AVR446 (D. Austin, "Generate stepper-motor speed
// profiles in real time"): o período do passo n sai do anterior com uma
// divisão inteira, sem raiz quadrada nem ponto flutuante na ISR (no Xtensa,
// a FPU não pode ser usada em interrupção).
//
//   aceleração:   c_n = c_(n-1) - 2·c_(n-1) / (4n + 1)
//   desaceleração, faltando r passos:  c = c + 2·c / (4r - 1)
//
// Os períodos ficam em ticks de 0,1 µs com 8 bits de fração.
#include <math.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gptimer.h"
#include "hal/gpio_ll.h"
#include "soc/gpio_struct.h"
#include "esp_attr.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "movimento.h"
#include "parametros.h"
#include "placa.h"
#include "pinos.h"
#include "protocolo.h"
#include "maquina.h"

static const char *TAG = "mov";

#define TIMER_HZ      10000000u
#define FRAC          8
#define C_MAX         (1000000u << FRAC)   // 10 passos/s: o mais lento da rampa
#define C_MIN         (250u << FRAC)       // 40 kHz: teto de segurança da ISR
#define DEBOUNCE_FIM  3                    // leituras seguidas acionado para valer

typedef struct {
    gpio_num_t step, dir, fim;
    int nivel_dir_negativo;
    int32_t curso;
    uint32_t passos_por_m;
    uint16_t param_accel, param_vmax;
} eixo_t;

static const eixo_t EIXOS[3] = {
    { PINO_X_STEP, PINO_X_DIR, PINO_FIM_X, DIR_NEGATIVO_X, CURSO_X, PASSOS_POR_M_X,
      PARAM_ACCEL_X, PARAM_VMAX_X },
    { PINO_Y_STEP, PINO_Y_DIR, PINO_FIM_Y, DIR_NEGATIVO_Y, CURSO_Y, PASSOS_POR_M_Y,
      PARAM_ACCEL_Y, PARAM_VMAX_Y },
    { PINO_Z_STEP, PINO_Z_DIR, PINO_FIM_Z, DIR_NEGATIVO_Z, CURSO_Z, PASSOS_POR_M_Z,
      PARAM_ACCEL_Z, PARAM_VMAX_Z },
};

enum { FASE_ACEL, FASE_CRUZ, FASE_DESACEL };

typedef enum {
    RES_OK,         // fez todos os passos
    RES_FIM,        // endstop acionado (só com checar_fim)
    RES_PARADO,     // interrompido por CANCEL ou ESTOP
} resultado_t;

// Movimento em curso, compartilhado com a ISR. A tarefa só escreve com o
// timer parado (ativo = false) ou dentro da trava.
static struct {
    volatile bool ativo;
    volatile bool parar;            // pedido de parada com rampa
    bool interrompido;
    bool checar_fim;
    gpio_num_t step, fim;
    int8_t sentido;
    volatile int32_t *pos;
    uint32_t total, feitos;
    uint32_t n, n_acel;
    uint32_t c, c_cruz;
    uint8_t fase;
    uint8_t fim_cont;
    volatile resultado_t resultado;
} mv;

static volatile int32_t posicao[3];
static volatile uint8_t referenciados;
static atomic_uint geracao;         // muda a cada CANCEL e ESTOP
static atomic_int ocupadas;         // na fila + em execução
static atomic_uint falha;
static QueueHandle_t fila;
static gptimer_handle_t timer;
static TaskHandle_t tarefa;
static portMUX_TYPE trava = portMUX_INITIALIZER_UNLOCKED;

// ---- ISR ----------------------------------------------------------------

static bool IRAM_ATTR terminar(gptimer_handle_t t, resultado_t r)
{
    gptimer_stop(t);
    mv.resultado = r;
    mv.ativo = false;
    BaseType_t acordou = pdFALSE;
    vTaskNotifyGiveFromISR(tarefa, &acordou);
    return acordou == pdTRUE;
}

static bool IRAM_ATTR ao_alarme(gptimer_handle_t t, const gptimer_alarm_event_data_t *ev,
                                void *arg)
{
    if (!mv.ativo) {
        gptimer_stop(t);
        return false;
    }

    // Endstop NF: acionado (ou fio rompido) lê 0
    if (mv.checar_fim) {
        if (gpio_ll_get_level(&GPIO, mv.fim) == 0) {
            if (++mv.fim_cont >= DEBOUNCE_FIM) {
                return terminar(t, RES_FIM);
            }
        } else {
            mv.fim_cont = 0;
        }
    }

    gpio_ll_set_level(&GPIO, mv.step, 1);
    esp_rom_delay_us(2);            // A4988: pulso mínimo de 1 µs
    gpio_ll_set_level(&GPIO, mv.step, 0);
    *mv.pos += mv.sentido;
    mv.feitos++;

    uint32_t r = mv.total - mv.feitos;
    if (r == 0) {
        return terminar(t, mv.interrompido ? RES_PARADO : RES_OK);
    }

    if (mv.parar) {
        mv.parar = false;
        mv.interrompido = true;
        if (mv.fase != FASE_DESACEL) {
            // Para frear leva o mesmo número de passos que levou para acelerar
            uint32_t frear = (mv.fase == FASE_ACEL) ? mv.feitos : mv.n_acel;
            if (frear == 0) {
                return terminar(t, RES_PARADO);     // velocidade constante (HOME)
            }
            if (frear < r) {
                mv.total = mv.feitos + frear;
                r = frear;
            }
            mv.fase = FASE_DESACEL;
        }
    }

    if (mv.fase == FASE_ACEL) {
        if (r <= mv.feitos) {
            mv.fase = FASE_DESACEL;         // perfil triangular
        } else {
            mv.n++;
            mv.c -= (2 * mv.c) / (4 * mv.n + 1);
            if (mv.c <= mv.c_cruz) {
                mv.c = mv.c_cruz;
                mv.fase = FASE_CRUZ;
                mv.n_acel = mv.feitos;
            }
        }
    } else if (mv.fase == FASE_CRUZ && r <= mv.n_acel) {
        mv.fase = FASE_DESACEL;
    }
    if (mv.fase == FASE_DESACEL) {
        mv.c += (2 * mv.c) / (4 * r - 1);
        if (mv.c > C_MAX) {
            mv.c = C_MAX;
        }
    }

    const gptimer_alarm_config_t alarme = {
        .alarm_count = mv.c >> FRAC,
        .flags.auto_reload_on_alarm = true,
    };
    gptimer_set_alarm_action(t, &alarme);
    return false;
}

// ---- execução -----------------------------------------------------------

static int32_t mm_para_passos(int e, int32_t mm)
{
    return (int32_t)((int64_t)mm * EIXOS[e].passos_por_m / 1000);
}

static bool fim_acionado(int e)
{
    return gpio_get_level(EIXOS[e].fim) == 0;
}

static void falhar(uint8_t codigo, const char *motivo, int e)
{
    ESP_LOGE(TAG, "eixo %c: %s", "XYZ"[e], motivo);
    atomic_store(&falha, codigo);
}

// Move `passos` no `sentido` e espera terminar. accel = 0: velocidade
// constante, sem rampa. Devolve RES_PARADO sem mexer em nada se a geração
// mudou (CANCEL ou ESTOP depois que o comando entrou na fila).
static resultado_t executar(int e, int8_t sentido, uint32_t passos, uint32_t vel,
                            uint32_t accel, bool checar_fim, unsigned ger)
{
    const eixo_t *x = &EIXOS[e];
    if (passos == 0) {
        return RES_OK;
    }

    // Liga os drivers dentro da trava: um ESTOP entre a conferência da
    // geração e o gpio_set_level religaria os motores em FAULT.
    taskENTER_CRITICAL(&trava);
    bool valido = ger == atomic_load(&geracao);
    bool ligou = valido && placa_motores_ligar();
    taskEXIT_CRITICAL(&trava);
    if (!valido) {
        return RES_PARADO;
    }
    if (ligou) {
        vTaskDelay(pdMS_TO_TICKS(2));
    }

    gpio_set_level(x->dir, sentido < 0 ? x->nivel_dir_negativo : !x->nivel_dir_negativo);
    esp_rom_delay_us(5);            // setup do DIR (A4988: 200 ns)

    uint32_t c_cruz = (uint32_t)(((uint64_t)TIMER_HZ << FRAC) / vel);
    c_cruz = c_cruz > C_MAX ? C_MAX : c_cruz < C_MIN ? C_MIN : c_cruz;
    uint32_t c0 = c_cruz;
    uint8_t fase = FASE_CRUZ;
    if (accel > 0) {
        // Primeiro período do AVR446, com o fator 0,676 de correção
        double c0f = 0.676 * TIMER_HZ * sqrt(2.0 / accel) * (1 << FRAC);
        if (c0f > C_MAX) {
            c0f = C_MAX;
        }
        if (c0f > c_cruz) {
            c0 = (uint32_t)c0f;
            fase = FASE_ACEL;
        }
    }

    taskENTER_CRITICAL(&trava);
    if (ger != atomic_load(&geracao)) {
        taskEXIT_CRITICAL(&trava);
        return RES_PARADO;
    }
    mv.parar = false;
    mv.interrompido = false;
    mv.checar_fim = checar_fim;
    mv.step = x->step;
    mv.fim = x->fim;
    mv.sentido = sentido;
    mv.pos = &posicao[e];
    mv.total = passos;
    mv.feitos = 0;
    mv.n = 0;
    mv.n_acel = 0;
    mv.c = c0;
    mv.c_cruz = c_cruz;
    mv.fase = fase;
    mv.fim_cont = 0;
    mv.ativo = true;
    const gptimer_alarm_config_t alarme = {
        .alarm_count = c0 >> FRAC,
        .flags.auto_reload_on_alarm = true,
    };
    gptimer_set_raw_count(timer, 0);
    gptimer_set_alarm_action(timer, &alarme);
    gptimer_start(timer);
    taskEXIT_CRITICAL(&trava);

    while (mv.ativo) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));
    }
    return mv.resultado;
}

// Rampa com os parâmetros do eixo (MOVE, JOG, subida do Z no HOME)
static resultado_t executar_ate(int e, int32_t alvo, uint32_t vel, bool checar_fim,
                                unsigned ger)
{
    int32_t delta = alvo - posicao[e];
    return executar(e, delta < 0 ? -1 : +1, (uint32_t)(delta < 0 ? -delta : delta), vel,
                    parametro(EIXOS[e].param_accel), checar_fim, ger);
}

static void marcar_referenciado(int e, unsigned ger)
{
    taskENTER_CRITICAL(&trava);
    if (ger == atomic_load(&geracao)) {
        posicao[e] = 0;
        referenciados |= 1 << e;
    }
    taskEXIT_CRITICAL(&trava);
}

// Aproximação rápida, recuo e reaproximação lenta (lógica do teste_motores)
static bool referenciar(int e, unsigned ger)
{
    const bool z = e == 2;
    const uint32_t rapido = mm_para_passos(e, z ? HOME_RAPIDO_Z_MM_S : HOME_RAPIDO_XY_MM_S);
    const uint32_t lento = mm_para_passos(e, z ? HOME_LENTO_Z_MM_S : HOME_LENTO_XY_MM_S);
    const uint32_t recuo = mm_para_passos(e, z ? HOME_RECUO_Z_MM : HOME_RECUO_XY_MM);
    const uint32_t maximo = EIXOS[e].curso + EIXOS[e].curso / 10;
    resultado_t r;

    if (fim_acionado(e)) {
        r = executar(e, +1, recuo, rapido, 0, false, ger);
        if (r == RES_PARADO) {
            return false;
        }
        if (fim_acionado(e)) {
            falhar(FALHA_INTERNAL, "endstop continua acionado depois de afastar: "
                   "confira a fiação (chave NF: fio rompido lê como acionado)", e);
            return false;
        }
    }

    r = executar(e, -1, maximo, rapido, 0, true, ger);
    if (r == RES_PARADO) {
        return false;
    }
    if (r == RES_OK) {
        falhar(FALHA_INTERNAL, "endstop não encontrado no curso inteiro", e);
        return false;
    }

    r = executar(e, +1, recuo, rapido, 0, false, ger);
    if (r == RES_PARADO) {
        return false;
    }
    r = executar(e, -1, 2 * recuo, lento, 0, true, ger);
    if (r == RES_PARADO) {
        return false;
    }
    if (r == RES_OK) {
        falhar(FALHA_INTERNAL, "endstop não acionou na reaproximação lenta", e);
        return false;
    }

    marcar_referenciado(e, ger);
    ESP_LOGI(TAG, "eixo %c referenciado", "XYZ"[e]);
    return true;
}

static void comando_home(uint8_t mascara, unsigned ger)
{
    if (mascara == 0) {
        return;
    }
    // O Z só desce com o carro na zona morta de X, fora da mesa
    if (mascara & 0x04) {
        mascara |= 0x01;
    }

    // Z sobe antes de X e Y andarem. Sem referência, a subida é às cegas;
    // com referência, respeita o curso.
    int32_t subida = mm_para_passos(2, HOME_SUBIDA_Z_MM);
    if (referenciados & 0x04) {
        int32_t folga = EIXOS[2].curso - posicao[2];
        subida = subida < folga ? subida : folga;
    }
    if (subida > 0
        && executar(2, +1, subida, parametro(PARAM_VMAX_Z), parametro(PARAM_ACCEL_Z),
                    false, ger) == RES_PARADO) {
        return;
    }

    for (int e = 0; e < 3; e++) {
        if ((mascara & (1 << e)) && !referenciar(e, ger)) {
            return;
        }
    }
}

static void comando_jog(const comando_t *c)
{
    int e = c->eixo;
    int32_t alvo = posicao[e] + c->valor;
    // Referenciado: limitado ao curso. Sem referência: só o endstop limita.
    if (referenciados & (1 << e)) {
        alvo = alvo < 0 ? 0 : alvo > EIXOS[e].curso ? EIXOS[e].curso : alvo;
    }
    // Endstop acionado no JOG só para o eixo; não é falha
    executar_ate(e, alvo, c->vel, alvo < posicao[e], c->geracao);
}

static void comando_move(const comando_t *c)
{
    int e = c->eixo;
    // Perto do 0 a chave está no limiar de acionamento; abaixo da margem o
    // endstop não é vigiado.
    bool vigiar = c->valor < posicao[e] && c->valor >= mm_para_passos(e, MARGEM_ENDSTOP_MM);
    if (executar_ate(e, c->valor, c->vel, vigiar, c->geracao) == RES_FIM) {
        falhar(FALHA_ENDSTOP, "endstop acionado durante MOVE: passos perdidos?", e);
    }
}

static void comando_dwell(const comando_t *c)
{
    TickType_t fim = xTaskGetTickCount() + pdMS_TO_TICKS((uint32_t)c->valor);
    while (c->geracao == atomic_load(&geracao)
           && (int32_t)(fim - xTaskGetTickCount()) > 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static void tarefa_movimento(void *arg)
{
    // O timer é criado aqui para a interrupção ficar no mesmo núcleo
    const gptimer_config_t cfg = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT,
        .direction = GPTIMER_COUNT_UP,
        .resolution_hz = TIMER_HZ,
    };
    ESP_ERROR_CHECK(gptimer_new_timer(&cfg, &timer));
    const gptimer_event_callbacks_t cbs = { .on_alarm = ao_alarme };
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(timer, &cbs, NULL));
    ESP_ERROR_CHECK(gptimer_enable(timer));

    comando_t c;
    for (;;) {
        xQueueReceive(fila, &c, portMAX_DELAY);
        // Comandos de antes de um CANCEL ou ESTOP são só descartados
        if (c.geracao == atomic_load(&geracao)) {
            switch (c.tipo) {
            case CMD_HOME:  comando_home(c.eixo, c.geracao); break;
            case CMD_JOG:   comando_jog(&c);   break;
            case CMD_MOVE:  comando_move(&c);  break;
            case CMD_DWELL: comando_dwell(&c); break;
            }
        }
        atomic_fetch_sub(&ocupadas, 1);
    }
}

// ---- interface ----------------------------------------------------------

void movimento_iniciar(void)
{
    fila = xQueueCreate(QUEUE_SLOTS, sizeof(comando_t));
    xTaskCreatePinnedToCore(tarefa_movimento, "mov", 4096, NULL, 12, &tarefa, 0);
}

bool movimento_enfileirar(comando_t c)
{
    if (atomic_load(&ocupadas) >= QUEUE_SLOTS) {
        return false;
    }
    c.geracao = atomic_load(&geracao);
    atomic_fetch_add(&ocupadas, 1);
    if (xQueueSend(fila, &c, 0) != pdTRUE) {
        atomic_fetch_sub(&ocupadas, 1);
        return false;
    }
    return true;
}

uint8_t movimento_fila_livre(void)
{
    int livres = QUEUE_SLOTS - atomic_load(&ocupadas);
    return livres < 0 ? 0 : (uint8_t)livres;
}

bool movimento_ocioso(void)
{
    return atomic_load(&ocupadas) == 0;
}

void movimento_cancelar(void)
{
    taskENTER_CRITICAL(&trava);
    atomic_fetch_add(&geracao, 1);
    if (mv.ativo) {
        mv.parar = true;
    }
    taskEXIT_CRITICAL(&trava);
}

void movimento_parar_ja(void)
{
    taskENTER_CRITICAL(&trava);
    atomic_fetch_add(&geracao, 1);
    referenciados = 0;
    if (mv.ativo) {
        gptimer_stop(timer);
        mv.resultado = RES_PARADO;
        mv.ativo = false;
    }
    taskEXIT_CRITICAL(&trava);
    xTaskNotifyGive(tarefa);
}

uint8_t movimento_referenciados(void)
{
    return referenciados;
}

int32_t movimento_posicao(int eixo)
{
    return posicao[eixo];
}

uint8_t movimento_falha_pendente(void)
{
    return (uint8_t)atomic_exchange(&falha, 0);
}
