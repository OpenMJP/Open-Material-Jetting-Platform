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

// Desliga só o VPP (rotina de desligamento, seção 7.7).
void placa_vpp_desligar(void);
bool placa_vpp_ligado(void);

// Inverte o GPIO38. Chamar com período bem menor que 1 s: se parar, o
// TLC555 corta o VPP independentemente do firmware.
void placa_heartbeat(void);

bool placa_nfault_ativo(void);
