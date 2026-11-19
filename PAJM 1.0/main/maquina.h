// maquina.h - constantes mecânicas da máquina
//
// Na v1.x do protocolo, passos por metro e curso são constantes de
// compilação, informadas ao PC no HELLO_ACK (PROTOCOL.md, seção 8.2). O PC
// não tem nenhum desses valores fixos: tudo o que ele sabe da mecânica vem
// daqui. Valor errado aqui = peça com escala errada, sem nenhum aviso.
#pragma once

// X e Y: A4988 a 1/16 (200 x 16 = 3200 passos/volta), correia GT2 com polia
// de 20 dentes (40 mm/volta) -> 80 passos/mm.
// [VERIFICAR] a polia: mandar um JOG de 8000 passos e medir 100 mm.
#define PASSOS_POR_M_X  80000
#define PASSOS_POR_M_Y  80000

// Z: fuso T8 (8 mm/volta) a 1/16 = 400 passos/mm, confirmado por medição na
// máquina em 03/11/2026.
#define PASSOS_POR_M_Z  400000

#define MM_PARA_PASSOS(mm, passos_por_m)  ((int32_t)((mm) * (passos_por_m) / 1000))

// Curso total de cada eixo, em passos, com 0 no endstop (seção 6). O
// firmware aceita qualquer posição em [0, curso], inclusive as zonas mortas
// fora da mesa: é ali que o X acelera e freia nas swaths. Onde fica a mesa
// dentro do curso é assunto do slicer.
//   X: 245 mm (zonas mortas de ~35 mm de cada lado)
//   Y: 230 mm (35 mm a partir do zero, 15 mm antes do fim)
//   Z: 200 mm; endstop embaixo, com o bico a 1 mm da mesa no zero
#define CURSO_X  MM_PARA_PASSOS(245, PASSOS_POR_M_X)
#define CURSO_Y  MM_PARA_PASSOS(230, PASSOS_POR_M_Y)
#define CURSO_Z  MM_PARA_PASSOS(200, PASSOS_POR_M_Z)

// Valores iniciais dos parâmetros da seção 8.2, convertidos para os passos
// acima. São pontos de partida: calibrar e ajustar com SET_PARAM.
#define ACCEL_X_INICIAL  MM_PARA_PASSOS(1000, PASSOS_POR_M_X)  // 1000 mm/s²
#define ACCEL_Y_INICIAL  MM_PARA_PASSOS(500,  PASSOS_POR_M_Y)  //  500 mm/s²
#define ACCEL_Z_INICIAL  MM_PARA_PASSOS(50,   PASSOS_POR_M_Z)  //   50 mm/s²
// Teto de 250 mm/s em X e Y (03/11/2026) = 20 kHz de passo, metade do teto
// da ISR (C_MIN, 40 kHz). Antes era 150 mm/s em X, o maior valor exercitado
// no teste_motores, e 100 em Y. Z: 5 mm/s e 50 mm/s², mantidos por decisão
// de 03/11/2026.
#define VMAX_X_INICIAL   MM_PARA_PASSOS(250,  PASSOS_POR_M_X)
#define VMAX_Y_INICIAL   MM_PARA_PASSOS(250,  PASSOS_POR_M_Y)
#define VMAX_Z_INICIAL   MM_PARA_PASSOS(5,    PASSOS_POR_M_Z)
#define RUNUP_MARGIN_INICIAL  MM_PARA_PASSOS(1, PASSOS_POR_M_X) // 1 mm

// Sentido: nos três eixos o negativo vai para o endstop com o pino DIR em 1
// (conferido no teste_motores). Trocar aqui se um motor for religado ao contrário.
#define DIR_NEGATIVO_X  1
#define DIR_NEGATIVO_Y  1
#define DIR_NEGATIVO_Z  1

// HOME (mesma lógica do teste_motores: aproximação rápida, recuo,
// reaproximação lenta). Velocidade constante, sem rampa.
#define HOME_RAPIDO_XY_MM_S  20
#define HOME_LENTO_XY_MM_S   3
#define HOME_RECUO_XY_MM     5
#define HOME_RAPIDO_Z_MM_S   5
#define HOME_LENTO_Z_MM_S    1
#define HOME_RECUO_Z_MM      2

// Antes de X e Y andarem no HOME, o Z sobe isto. O Z só desce até o endstop
// com o carro na zona morta de X, fora da mesa: por isso HOME com Z também
// referencia X, que termina em 0.
#define HOME_SUBIDA_Z_MM     10

// Espera entre ligar o VPP e liberar o nRESET. O manual (seção 6.1.2) pede
// no mínimo 100 ms e manda medir na aplicação o tempo de carga dos
// capacitores do cabeçote. [VERIFICAR] no analisador: o VPP tem de estar
// estável antes da subida do nRESET.
#define VPP_ESTABILIZA_MS  300

// Abaixo disto (perto do endstop), um endstop acionado fora de HOME e JOG
// não é tratado como falha: no 0 a chave está no limiar de acionamento.
#define MARGEM_ENDSTOP_MM    1
