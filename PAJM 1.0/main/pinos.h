// pinos.h - pinagem da placa pajm_V2.1 (CONTEXTO-PAJM.md, seção 3)
//
// Não usar: 35, 36, 37 (PSRAM octal); 0, 3, 45, 46 (strapping);
// 19, 20 (USB nativa, console). 43 e 44 são a UART0 do protocolo.
#pragma once

#include "driver/gpio.h"

// Cabeçote Xaar XJ128, através do 74HCT244 U5 (ESP -> Xaar)
#define PINO_HEAD_CLK   GPIO_NUM_4    // 1 MHz pelo LEDC (etapa futura)
#define PINO_NFIRE      GPIO_NUM_5    // ativo em baixo
#define PINO_PHO        GPIO_NUM_6    // 0 = ordem A-B-C
#define PINO_NRESET     GPIO_NUM_7    // ativo em baixo; pull-down na placa
#define PINO_NSS2       GPIO_NUM_9    // ativo em baixo
#define PINO_NSS1       GPIO_NUM_10   // ativo em baixo
#define PINO_MOSI       GPIO_NUM_11   // SPI2
#define PINO_SCK        GPIO_NUM_12   // SPI2

// Cabeçote, através do U6 (Xaar -> ESP)
#define PINO_READY      GPIO_NUM_15
#define PINO_NFAULT     GPIO_NUM_16   // ativo em baixo

// Alimentação e segurança
#define PINO_RELE_VPP   GPIO_NUM_13   // relés do VPP, ativo em alto
#define PINO_HEARTBEAT  GPIO_NUM_38   // trigger do TLC555; parar de oscilar corta o VPP

// Motores (A4988; ENABLE ativo em baixo, pull-ups de 100k na placa)
#define PINO_X_STEP     GPIO_NUM_47
#define PINO_X_DIR      GPIO_NUM_21
#define PINO_Y_STEP     GPIO_NUM_1
#define PINO_Y_DIR      GPIO_NUM_14
#define PINO_Z_STEP     GPIO_NUM_40
#define PINO_Z_DIR      GPIO_NUM_39
#define PINO_MOTOR_EN   GPIO_NUM_41

// Endstops (chave NF: solta = 1, acionada ou fio rompido = 0)
#define PINO_FIM_X      GPIO_NUM_42
#define PINO_FIM_Y      GPIO_NUM_2
#define PINO_FIM_Z      GPIO_NUM_8

// Protocolo (CH343P)
#define PINO_UART_TX    GPIO_NUM_43
#define PINO_UART_RX    GPIO_NUM_44
