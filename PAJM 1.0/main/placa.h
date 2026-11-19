// placa.h - saídas de segurança, heartbeat do watchdog externo e leituras simples
#pragma once

#include <stdbool.h>

// Primeira coisa do boot: põe todas as saídas no nível seguro ANTES de
// habilitá-las (cabeçote em reset, VPP desligado, drivers desligados).
void placa_iniciar(void);

// Corte imediato: nFIRE inativo, cabeçote em reset, VPP e drivers desligados.
// Usado pelo ESTOP (seção 7.8).
void placa_estado_seguro(void);

// Liga os drivers dos motores (MOTOR_EN em 0). Devolve true se eles estavam
// desligados, para quem chama esperar antes do primeiro passo. Pode ser
// chamada dentro de seção crítica.
bool placa_motores_ligar(void);

// Desliga os drivers (MOTOR_EN em 1): os eixos ficam livres para mover à mão.
// Pode ser chamada dentro de seção crítica.
void placa_motores_desligar(void);

// Energização do cabeçote em dois passos (manual, seção 6.1.2): liga o VPP
// com o nRESET ainda ativo e, depois de o VPP estabilizar, libera o nRESET.
void placa_vpp_ligar(void);
void placa_cabecote_liberar(void);

// nRESET ativo com o VPP ligado: metade de um pulso de reinício (ver
// pulsar_reset_cabecote() em movimento.c). Pode ser chamada em seção crítica.
void placa_cabecote_resetar(void);

// Desliga só o VPP (rotina de desligamento, seção 7.7): nRESET ativo antes.
void placa_vpp_desligar(void);
bool placa_vpp_ligado(void);

// Mesmo corte do placa_vpp_desligar(), seguro para chamar de ISR (nFAULT).
void placa_corte_isr(void);

// Inverte o GPIO38. Chamar com período bem menor que 1 s: se parar, o
// TLC555 corta o VPP independentemente do firmware.
void placa_heartbeat(void);

bool placa_nfault_ativo(void);
