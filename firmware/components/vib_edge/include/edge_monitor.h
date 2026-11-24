/*
 * edge_monitor.h — Monitor de vibracao autonomo no ESP32.
 *
 * Roda a analise de edge_core.c continuamente (task de baixa prioridade no
 * Core 1, para nunca atrasar a aquisicao) e aciona as saidas locais:
 *
 *   ALERTA  -> buzzer intermitente (bip curto a cada 2 s)
 *   PARADA  -> buzzer continuo (pulsado) + saida de parada do motor, se habilitada
 *   botao BOOT (GPIO0) -> silencia o buzzer e rearma a parada travada
 *
 * A configuracao e a baseline chegam do PC (comandos VIB_CMD_*) e ficam
 * gravadas na NVS: o alarme continua valendo com o PC desligado ou o
 * programa fechado.
 *
 * IMPORTANTE: isto NAO e uma funcao de seguranca certificada. A parada de
 * emergencia do tunel deve continuar sendo o circuito fisico existente.
 */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "vib_protocol.h"

/* Chamar cedo no boot: le a NVS e coloca as saidas no estado seguro. */
esp_err_t edge_monitor_init(uint8_t node_id);

/* Chamar quando o sensor estiver pronto (odr_hz < 0: sensor nao detectado -> ALERTA). */
void edge_monitor_set_sensor(float odr_hz, float g_per_lsb);

/* Chamado pela task de aquisicao a cada leitura da FIFO (n amostras XYZ).
 * gap = true quando houve perda de amostras (overrun). */
void edge_monitor_feed(const int16_t *xyz, uint32_t n, uint64_t t_us, bool gap);

/* Proximo resultado pronto para envio ao PC (nao bloqueia com wait = 0). */
bool edge_monitor_get_metrics(vib_edge_metrics_t *out, TickType_t wait);

/* Trata um comando do PC. Preenche a resposta a enviar (ACK ou CONFIG).
 * Retorna false se o comando nao era para este no (nao responder). */
bool edge_monitor_command(const vib_header_t *hdr, const uint8_t *payload,
                          uint8_t *reply_type, uint8_t *reply, uint16_t *reply_len);
