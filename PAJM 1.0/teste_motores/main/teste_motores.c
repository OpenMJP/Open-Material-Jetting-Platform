// teste_motores.c - firmware mínimo de teste dos eixos do PAJM (ESP-IDF 5.5)
//
// Revisão de 19/10/2026: pinagem de Y e Z corrigida conforme o esquemático
// pajm_V2.1 (Y_STEP 1, Y_DIR 14, Z_STEP 40, Z_DIR 39). A versão de 15/10 tinha
// STEP e DIR trocados nesses dois eixos. Os níveis das saídas agora são
// definidos antes de configurar os pinos como saída, para o MOTOR_EN não
// passar por nível baixo (drivers ligados) durante a inicialização.
//
// Revisão de 22/10/2026: na placa pajm_V2.1 o conector dos jumpers de
// microstepping (MS1..MS3) e do curto RESET-SLEEP dos A4988 está com a ordem
// dos pinos trocada na PCB em relação ao esquemático. Sem retrabalho na solda
// os drivers ficam em reset e o motor não segura posição, mesmo com MOTOR_EN
// em 0. Placas V2.1 precisam do reparo; corrigir o footprint na próxima revisão.
//
// Comandos pelo idf.py monitor (uma tecla por comando, sem Enter):
//   e        liga/desliga os drivers (MOTOR_EN)
//   x / X    move X no sentido negativo / positivo
//   y / Y    idem para Y
//   z / Z    idem para Z
//   + / -    aumenta / diminui a velocidade de pico
//   1..4     tamanho do movimento: 1/16, 1/4, 1/2, 1 volta do motor
//   h        lê os endstops
//   g + eixo home: g x, g y, g z, ou g a (X, Y e Z em sequência)
//   p        mostra a pinagem usada
//   ?        ajuda
//
// Home: aproxima rápido no sentido negativo até o endstop, afasta
// HOME_BACKOFF_STEPS e reaproxima devagar. Qualquer tecla interrompe.
// Antes do primeiro home, confira ENDSTOP_HIT_LEVEL com 'h' apertando o
// endstop com a mão, e confira com x/y/z que o sentido negativo vai para ele.
//
// O CABEÇOTE DEVE ESTAR DESCONECTADO. Relé do VPP e nRESET ficam em nível baixo.
// O movimento é gerado por laço ocupado (esp_rom_delay_us): é só para teste.
// Movimentos longos em baixa velocidade podem imprimir um aviso do task
// watchdog (> 5 s bloqueando o core 0); é inofensivo, só não reinicia.
//
// Uso:
//   idf.py create-project teste_motores
//   cd teste_motores
//   (substitua main/teste_motores.c por este arquivo)
//   idf.py set-target esp32s3
//   idf.py -p COMx build flash monitor

#include <stdio.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_rom_sys.h"

#define PIN_MOTOR_EN   GPIO_NUM_41   // m_en; A4988: ENABLE ativo em nível baixo (pull-ups de 100k na placa)
#define PIN_RELAY_VPP  GPIO_NUM_13   // r_vpp; módulo de relé ativo em alto -> manter 0
#define PIN_NRESET     GPIO_NUM_7    // ESP_nRESET; mantém o cabeçote em reset

#define ENDSTOP_HIT_LEVEL  0         // chave NF: solta liga o sinal ao 3V3, apertada abre (pull-down leva a 0)
#define STEPS_PER_REV      3200      // 200 passos/volta x 1/16
#define RAMP_STEPS         800       // passos de aceleração/desaceleração
#define F_START_HZ         500       // frequência de partida da rampa

#define HOME_FAST_HZ       1000      // aproximação rápida (sem rampa)
#define HOME_SLOW_HZ       250       // reaproximação lenta
#define HOME_BACKOFF_STEPS (STEPS_PER_REV / 4)
#define HOME_MAX_STEPS     (STEPS_PER_REV * 50)  // curso máximo procurando o endstop
#define ENDSTOP_DEBOUNCE   3         // leituras seguidas acionado para valer

typedef struct {
    const char *name;
    gpio_num_t step, dir, endstop;
} axis_t;

// Conferido no esquemático pajm_V2.1:
//   x_stp GPIO47, x_dir GPIO21, endstop_x GPIO42
//   y_stp GPIO1,  y_dir GPIO14, endstop_y GPIO2
//   z_stp GPIO40, z_dir GPIO39, endstop_z GPIO8
static const axis_t AXES[3] = {
    { "X", GPIO_NUM_47, GPIO_NUM_21, GPIO_NUM_42 },
    { "Y", GPIO_NUM_1,  GPIO_NUM_14, GPIO_NUM_2  },
    { "Z", GPIO_NUM_40, GPIO_NUM_39, GPIO_NUM_8  },
};

static const int SPEEDS_HZ[] = { 500, 1000, 2000, 4000, 8000, 12000 };
#define N_SPEEDS ((int)(sizeof(SPEEDS_HZ) / sizeof(SPEEDS_HZ[0])))

static const int MOVE_SIZES[] = {
    STEPS_PER_REV / 16, STEPS_PER_REV / 4, STEPS_PER_REV / 2, STEPS_PER_REV
};

static bool endstop_hit(const axis_t *a)
{
    return gpio_get_level(a->endstop) == ENDSTOP_HIT_LEVEL;
}

static bool key_pressed(void)
{
    if (getchar() == EOF) {
        clearerr(stdin);
        return false;
    }
    return true;
}

static void set_dir(const axis_t *a, int dir)
{
    gpio_set_level(a->dir, dir > 0 ? 0 : 1);    // invertido: nos 3 eixos o negativo vai para o endstop
    esp_rom_delay_us(10);                       // setup do DIR (A4988 pede 200 ns)
}

static void step_pulse(const axis_t *a, int period_us)
{
    gpio_set_level(a->step, 1);
    esp_rom_delay_us(3);                        // pulso mínimo do A4988: 1 µs
    gpio_set_level(a->step, 0);
    esp_rom_delay_us(period_us - 3);
}

static void move(const axis_t *a, int dir, int steps, int f_max)
{
    int f_min = f_max < F_START_HZ ? f_max : F_START_HZ;

    set_dir(a, dir);

    int done = 0;
    for (; done < steps; done++) {
        // Parada por endstop só no sentido negativo. Se o endstop do eixo
        // estiver do outro lado, inverta aqui (ou o fio do motor).
        if (dir < 0 && endstop_hit(a)) {
            printf("  endstop %s acionado, parando\n", a->name);
            break;
        }

        // Rampa trapezoidal simples: sobe nos primeiros RAMP_STEPS,
        // desce nos últimos RAMP_STEPS.
        int k = done < (steps - done) ? done : (steps - done);
        if (k > RAMP_STEPS) k = RAMP_STEPS;
        int f = f_min + (f_max - f_min) * k / RAMP_STEPS;
        step_pulse(a, 1000000 / f);
    }

    printf("  %s %s%d passos, pico %d Hz\n", a->name, dir > 0 ? "+" : "-", done, f_max);
}

typedef enum { RUN_DONE, RUN_HIT, RUN_ABORT } run_result_t;

// Velocidade constante, sem rampa. Com stop_on_hit, para depois de
// ENDSTOP_DEBOUNCE leituras seguidas com o endstop acionado.
static run_result_t run_const(const axis_t *a, int dir, int max_steps, int f_hz, bool stop_on_hit)
{
    int period_us = 1000000 / f_hz;
    int hits = 0;

    set_dir(a, dir);
    for (int i = 0; i < max_steps; i++) {
        if (stop_on_hit) {
            hits = endstop_hit(a) ? hits + 1 : 0;
            if (hits >= ENDSTOP_DEBOUNCE) return RUN_HIT;
        }
        if ((i % 32) == 0 && key_pressed()) return RUN_ABORT;
        step_pulse(a, period_us);
    }
    return RUN_DONE;
}

static bool home(const axis_t *a)
{
    run_result_t r;
    printf("  home %s...\n", a->name);

    // Se já começa no endstop, afasta antes de procurar.
    if (endstop_hit(a)) {
        if (run_const(a, +1, HOME_BACKOFF_STEPS, HOME_FAST_HZ, false) == RUN_ABORT) goto abort;
        if (endstop_hit(a)) {
            printf("  endstop %s continua acionado depois de afastar: confira "
                   "ENDSTOP_HIT_LEVEL e a fiação\n", a->name);
            return false;
        }
    }

    r = run_const(a, -1, HOME_MAX_STEPS, HOME_FAST_HZ, true);
    if (r == RUN_ABORT) goto abort;
    if (r == RUN_DONE) {
        printf("  endstop %s não encontrado em %d passos\n", a->name, HOME_MAX_STEPS);
        return false;
    }

    // Afasta e reaproxima devagar para uma posição repetível.
    if (run_const(a, +1, HOME_BACKOFF_STEPS, HOME_FAST_HZ, false) == RUN_ABORT) goto abort;
    r = run_const(a, -1, 2 * HOME_BACKOFF_STEPS, HOME_SLOW_HZ, true);
    if (r == RUN_ABORT) goto abort;
    if (r == RUN_DONE) {
        printf("  endstop %s não acionou na reaproximação lenta\n", a->name);
        return false;
    }

    printf("  %s em home\n", a->name);
    return true;

abort:
    printf("  home %s interrompido\n", a->name);
    return false;
}

static void print_endstops(void)
{
    for (int i = 0; i < 3; i++) {
        printf("  endstop %s (GPIO%d) = %d%s\n",
               AXES[i].name, AXES[i].endstop, gpio_get_level(AXES[i].endstop),
               endstop_hit(&AXES[i]) ? "   <- acionado" : "");
    }
}

static void print_pins(void)
{
    printf("  eixo  STEP  DIR  ENDSTOP\n");
    for (int i = 0; i < 3; i++) {
        printf("  %s     %2d    %2d   %2d\n",
               AXES[i].name, AXES[i].step, AXES[i].dir, AXES[i].endstop);
    }
    printf("  MOTOR_EN %d | r_vpp %d | nRESET %d\n",
           PIN_MOTOR_EN, PIN_RELAY_VPP, PIN_NRESET);
}

static void help(void)
{
    printf("\n=== PAJM - teste de motores ===\n"
           "  e      liga/desliga drivers\n"
           "  x/X y/Y z/Z   move eixo (-/+)\n"
           "  + / -  velocidade\n"
           "  1..4   tamanho do movimento (1/16, 1/4, 1/2, 1 volta)\n"
           "  h      endstops\n"
           "  g + x/y/z/a   home do eixo (a = todos); tecla interrompe\n"
           "  p      pinagem\n"
           "  ?      ajuda\n\n");
}

void app_main(void)
{
    uint64_t out_mask = (1ULL << PIN_MOTOR_EN) | (1ULL << PIN_RELAY_VPP) | (1ULL << PIN_NRESET);
    uint64_t in_mask = 0;
    for (int i = 0; i < 3; i++) {
        out_mask |= (1ULL << AXES[i].step) | (1ULL << AXES[i].dir);
        in_mask  |= (1ULL << AXES[i].endstop);
    }

    // Níveis definidos ANTES de habilitar as saídas: o registrador de saída
    // é carregado agora e o pino já sai no nível certo quando vira saída.
    gpio_set_level(PIN_RELAY_VPP, 0);
    gpio_set_level(PIN_NRESET, 0);
    gpio_set_level(PIN_MOTOR_EN, 1);            // começa com os drivers desligados
    for (int i = 0; i < 3; i++) {
        gpio_set_level(AXES[i].step, 0);
        gpio_set_level(AXES[i].dir, 0);
    }

    gpio_config_t out = {
        .pin_bit_mask = out_mask,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&out);

    // A placa não tem resistor nos endstops (J3 a J5: 1 = alimentação, 2 = GND,
    // 3 = sinal). A chave é NF: solta, liga o sinal ao 3V3; apertada, abre e o
    // pull-down interno leva o sinal a 0. Fio rompido também lê como acionado.
    // O sinal não pode passar de 3V3 (GPIO do ESP32-S3 não tolera 5 V).
    gpio_config_t in = {
        .pin_bit_mask = in_mask,
        .mode = GPIO_MODE_INPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
    };
    gpio_config(&in);

    setvbuf(stdin, NULL, _IONBF, 0);

    bool enabled = false;
    int speed = 0;
    int size = 1;
    help();
    print_pins();
    printf("  drivers DESLIGADOS | %d Hz | %d passos\n", SPEEDS_HZ[speed], MOVE_SIZES[size]);

    while (1) {
        int c = getchar();
        if (c == EOF) {
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        switch (c) {
        case 'e':
            enabled = !enabled;
            gpio_set_level(PIN_MOTOR_EN, enabled ? 0 : 1);
            printf("  drivers %s\n", enabled ? "LIGADOS" : "DESLIGADOS");
            break;

        case 'x': case 'X':
        case 'y': case 'Y':
        case 'z': case 'Z': {
            if (!enabled) {
                printf("  drivers desligados, aperte 'e'\n");
                break;
            }
            int idx = (c | 0x20) - 'x';         // x/X -> 0, y/Y -> 1, z/Z -> 2
            int dir = (c >= 'a') ? -1 : +1;     // minúscula = negativo
            move(&AXES[idx], dir, MOVE_SIZES[size], SPEEDS_HZ[speed]);
            break;
        }

        case '+':
            if (speed < N_SPEEDS - 1) speed++;
            printf("  velocidade de pico: %d Hz\n", SPEEDS_HZ[speed]);
            break;

        case '-':
            if (speed > 0) speed--;
            printf("  velocidade de pico: %d Hz\n", SPEEDS_HZ[speed]);
            break;

        case '1': case '2': case '3': case '4':
            size = c - '1';
            printf("  movimento: %d passos\n", MOVE_SIZES[size]);
            break;

        case 'h':
            print_endstops();
            break;

        case 'g': {
            if (!enabled) {
                printf("  drivers desligados, aperte 'e'\n");
                break;
            }
            printf("  home: x, y, z ou a (todos)? ");
            fflush(stdout);
            int k;
            while ((k = getchar()) == EOF) {
                clearerr(stdin);
                vTaskDelay(pdMS_TO_TICKS(10));
            }
            printf("%c\n", k);

            if (k == 'a') {
                for (int i = 0; i < 3; i++) {
                    if (!home(&AXES[i])) break;
                }
            } else if ((k | 0x20) >= 'x' && (k | 0x20) <= 'z') {
                home(&AXES[(k | 0x20) - 'x']);
            } else {
                printf("  cancelado\n");
            }
            break;
        }

        case 'p':
            print_pins();
            break;

        case '?':
            help();
            break;

        default:
            break;
        }
    }
}