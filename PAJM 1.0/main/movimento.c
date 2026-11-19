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
#include "cabecote.h"
#include "movimento.h"
#include "parametros.h"
#include "placa.h"
#include "pinos.h"
#include "protocolo.h"
#include "maquina.h"
#include "swath.h"

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
static atomic_bool job_terminou;
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

// Movimento absoluto com o endstop como sinal de passos perdidos (MOVE e
// posicionamento da swath)
static resultado_t mover(int e, int32_t alvo, uint32_t vel, unsigned ger)
{
    // Perto do 0 a chave está no limiar de acionamento; abaixo da margem o
    // endstop não é vigiado.
    bool vigiar = alvo < posicao[e] && alvo >= mm_para_passos(e, MARGEM_ENDSTOP_MM);
    resultado_t r = executar_ate(e, alvo, vel, vigiar, ger);
    if (r == RES_FIM) {
        falhar(FALHA_ENDSTOP, "endstop acionado durante movimento: passos perdidos?", e);
    }
    return r;
}

static void comando_move(const comando_t *c)
{
    mover(c->eixo, c->valor, c->vel, c->geracao);
}

// Pulso de nRESET com o VPP ligado (T23 >= 500 ns): reinicia a lógica de
// sequência dos chips sem apagar o registrador de dados (manual, seção 5.3).
// Em 26/10/2026 um dos chips ficou num estado em que a sua metade não
// jateava, e só um ciclo de nRESET o recuperou.
static void pulsar_reset_cabecote(void)
{
    taskENTER_CRITICAL(&trava);
    if (placa_vpp_ligado()) {
        placa_cabecote_resetar();
        esp_rom_delay_us(1);
        placa_cabecote_liberar();
    }
    taskEXIT_CRITICAL(&trava);
    vTaskDelay(pdMS_TO_TICKS(1));
}

// Seção 6.1.2 do manual: nRESET ativo, liga o VPP, espera ele estabilizar
// e só então libera o nRESET. Desligar: nRESET ativo antes de cortar o VPP.
static void comando_head_power(const comando_t *c)
{
    if (!c->eixo) {
        cabecote_vigiar_nfault(false);
        taskENTER_CRITICAL(&trava);
        placa_vpp_desligar();
        taskEXIT_CRITICAL(&trava);
        return;
    }
    if (placa_vpp_ligado()) {
        // "Ligar" de novo também recupera um chip travado
        pulsar_reset_cabecote();
        return;
    }

    // Dentro da trava pelo mesmo motivo dos motores: um ESTOP entre a
    // conferência e o gpio_set_level religaria o VPP em FAULT.
    taskENTER_CRITICAL(&trava);
    bool valido = c->geracao == atomic_load(&geracao);
    if (valido) {
        placa_vpp_ligar();
    }
    taskEXIT_CRITICAL(&trava);
    if (!valido) {
        return;
    }

    TickType_t fim = xTaskGetTickCount() + pdMS_TO_TICKS(VPP_ESTABILIZA_MS);
    while ((int32_t)(fim - xTaskGetTickCount()) > 0) {
        if (c->geracao != atomic_load(&geracao)) {
            // Interrompido no meio: não deixa VPP ligado com o cabeçote em reset
            taskENTER_CRITICAL(&trava);
            placa_vpp_desligar();
            taskEXIT_CRITICAL(&trava);
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    taskENTER_CRITICAL(&trava);
    valido = c->geracao == atomic_load(&geracao) && placa_vpp_ligado();
    if (valido) {
        placa_cabecote_liberar();
    }
    taskEXIT_CRITICAL(&trava);
    if (valido) {
        vTaskDelay(pdMS_TO_TICKS(1));
        // CHECK_READY = 0 é o modo de teste sem cabeçote (seção 8.2): sem
        // ele o nFAULT flutua e derrubaria tudo na hora.
        bool vigiar = parametro(PARAM_CHECK_READY) != 0;
        cabecote_vigiar_nfault(vigiar);
        ESP_LOGI(TAG, "cabeçote energizado%s", vigiar ? "" : " (sem vigiar o nFAULT)");
    }
}

static void comando_spit(const comando_t *c)
{
    if (!placa_vpp_ligado()) {
        return;         // VPP caiu depois da validação (nFAULT): a falha já foi registrada
    }
    pulsar_reset_cabecote();
    cab_resultado_t r = cabecote_disparar_parado((uint16_t)c->valor, (uint16_t)c->vel, c->coluna,
                                                 parametro(PARAM_CHECK_READY) != 0);
    if (r == CAB_READY_TIMEOUT) {
        ESP_LOGE(TAG, "SPIT: READY fora do tempo (CHECK_READY = 0 desliga a conferência)");
        atomic_store(&falha, FALHA_READY_TIMEOUT);
    }
}

// Seção 7.5: posiciona Y e X (recuo d antes de x_start), acelera o X por
// toda a swath e o núcleo 1 dispara as colunas pela posição. O slot é
// liberado no fim, com ou sem sucesso.
static void comando_swath(const comando_t *c)
{
    int slot = c->valor;
    if (!swath_comecar(slot)) {
        return;         // descartado por CANCEL ou falha
    }
    const swath_t *s = swath_slot(slot);
    unsigned ger = c->geracao;
    int32_t inicio = s->x_start - s->recuo;
    int32_t fim = s->x_start + (int32_t)(s->n_cols - 1) * s->pitch + s->recuo;

    if (!placa_vpp_ligado()
        || mover(1, s->y, parametro(PARAM_VMAX_Y), ger) != RES_OK
        || mover(0, inicio, parametro(PARAM_VMAX_X), ger) != RES_OK) {
        swath_liberar(slot);
        return;
    }

    pulsar_reset_cabecote();
    const cab_swath_t cs = {
        .dados = s->dados,
        .n_cols = s->n_cols,
        .x_start = s->x_start,
        .pitch = s->pitch,
        .fire_delay_us = s->fire_delay_us,
        .pho_cba = (s->flags & 0x02) != 0,
    };
    cabecote_swath_iniciar(&cs, parametro(PARAM_CHECK_READY) != 0);
    resultado_t r = executar(0, +1, (uint32_t)(fim - inicio), s->vel,
                             parametro(PARAM_ACCEL_X), false, ger);
    cab_resultado_t rc = cabecote_swath_terminar(r != RES_OK);
    swath_liberar(slot);

    if (r != RES_OK) {
        return;         // CANCEL ou ESTOP: quem parou já tratou
    }
    if (rc == CAB_READY_TIMEOUT) {
        atomic_store(&falha, FALHA_READY_TIMEOUT);
    } else if (rc == CAB_OVERRUN) {
        atomic_store(&falha, FALHA_FIRE_OVERRUN);
    }
}

// Seção 7.7, no fim normal do job: o resto (estado IDLE) é com a sessão
static void comando_job_end(const comando_t *c)
{
    cabecote_vigiar_nfault(false);
    taskENTER_CRITICAL(&trava);
    bool valido = c->geracao == atomic_load(&geracao);
    if (valido) {
        placa_vpp_desligar();
    }
    taskEXIT_CRITICAL(&trava);
    if (valido) {
        atomic_store(&job_terminou, true);
    }
}

// MOTORS_OFF (v1.3): sem os drivers os eixos podem ser movidos à mão, então
// a posição deixa de valer e todos perdem a referência
static void comando_motores_off(const comando_t *c)
{
    taskENTER_CRITICAL(&trava);
    if (c->geracao == atomic_load(&geracao)) {
        placa_motores_desligar();
        referenciados = 0;
    }
    taskEXIT_CRITICAL(&trava);
    ESP_LOGI(TAG, "motores desligados; eixos sem referência");
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
            case CMD_HEAD_POWER: comando_head_power(&c); break;
            case CMD_SPIT:  comando_spit(&c);  break;
            case CMD_SWATH: comando_swath(&c); break;
            case CMD_JOB_END: comando_job_end(&c); break;
            case CMD_MOTORES_OFF: comando_motores_off(&c); break;
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
    cabecote_abortar();
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
    cabecote_abortar();
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

bool movimento_job_terminou(void)
{
    return atomic_exchange(&job_terminou, false);
}

uint8_t movimento_falha_pendente(void)
{
    return (uint8_t)atomic_exchange(&falha, 0);
}

void IRAM_ATTR movimento_sinalizar_falha(uint8_t codigo)
{
    atomic_store(&falha, codigo);
}
