/*
 * usb_link.c — Task de comunicacao USB (Core 0)
 *
 * Recebe blocos prontos da fila g_tx_q, monta o pacote do protocolo
 * (cabecalho + payload + CRC16) e escreve na UART0, que na placa DevKit
 * esta ligada a ponte USB-Serial (CP2102 / CH340). Periodicamente envia
 * um pacote STATUS (heartbeat), que tambem permite ao PC identificar
 * qual porta e o MANCAL_1 e qual e o MANCAL_2 mesmo sem sensor.
 *
 * Tambem envia os resultados da analise local (VIB_PKT_METRICS) e recebe
 * comandos do PC (configuracao/baseline do alarme local), respondendo com
 * VIB_PKT_ACK ou VIB_PKT_CONFIG. So esta task escreve na UART.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "sdkconfig.h"
#include "vib_protocol.h"
#include "edge_monitor.h"
#include "app.h"

#define LINK_UART UART_NUM_0
#define LINK_TX_BUF (32 * 1024)

static uint16_t s_seq;
static vib_rx_parser_t s_rx;

static void send_packet(uint8_t type, uint8_t flags, uint64_t ts, uint32_t first,
                        const void *payload, uint16_t len)
{
    vib_header_t h = {
        .sync = {VIB_SYNC0, VIB_SYNC1},
        .version = VIB_PROTO_VERSION,
        .type = type,
        .node_id = g_stats.node_id,
        .flags = flags,
        .full_scale_g = g_stats.full_scale_g,
        .sensor_type = g_stats.sensor_type,
        .seq = s_seq++,
        .payload_len = len,
        .timestamp_us = ts,
        .first_sample = first,
        .odr_hz = g_stats.odr_hz,
    };
    uint16_t crc = vib_crc16(0xFFFF, (const uint8_t *)&h + 2, sizeof(h) - 2);
    crc = vib_crc16(crc, payload, len);

    uart_write_bytes(LINK_UART, &h, sizeof(h));
    if (len) {
        uart_write_bytes(LINK_UART, payload, len);
    }
    uart_write_bytes(LINK_UART, &crc, sizeof(crc));
}

static void send_status(void)
{
    vib_status_payload_t s = {
        .fifo_overruns = g_stats.fifo_overruns,
        .block_drops = g_stats.block_drops,
        .packets_sent = g_stats.packets_sent,
        .uptime_ms = (uint32_t)(esp_timer_get_time() / 1000),
        .wtm_samples = g_stats.wtm_samples,
        .max_fifo_words = g_stats.max_fifo_words,
        .who_am_i = g_stats.who_am_i,
        .sensor_type = g_stats.sensor_type,
        .bandwidth_hz = g_stats.bandwidth_hz,
    };
    send_packet(VIB_PKT_STATUS, 0, esp_timer_get_time(), 0, &s, sizeof(s));
}

/* Comando completo recebido do PC (chamado dentro de vib_rx_feed). */
static void on_command(const vib_header_t *hdr, const uint8_t *payload, void *ctx)
{
    static uint8_t reply[sizeof(vib_edge_config_t) + 8];
    uint8_t type = 0;
    uint16_t len = 0;
    if (hdr->type < 0x80) {
        return; /* eco de pacote de dados: ignora */
    }
    if (edge_monitor_command(hdr, payload, &type, reply, &len)) {
        send_packet(type, 0, esp_timer_get_time(), 0, reply, len);
    }
}

static void poll_rx(void)
{
    uint8_t buf[128];
    int n;
    while ((n = uart_read_bytes(LINK_UART, buf, sizeof(buf), 0)) > 0) {
        vib_rx_feed(&s_rx, buf, (size_t)n, on_command, NULL);
    }
}

static void usb_task(void *arg)
{
    const int64_t status_period_us = (int64_t)CONFIG_VIB_STATUS_PERIOD_MS * 1000;
    int64_t next_status = 0;

    for (;;) {
        vib_block_t *blk = NULL;
        if (xQueueReceive(g_tx_q, &blk, pdMS_TO_TICKS(20)) == pdTRUE) {
            send_packet(VIB_PKT_DATA, blk->flags, blk->timestamp_us, blk->first_sample,
                        blk->data, (uint16_t)(blk->n_samples * 6));
            g_stats.packets_sent++;
            xQueueSend(g_free_q, &blk, portMAX_DELAY);
        }
        vib_edge_metrics_t m;
        while (edge_monitor_get_metrics(&m, 0)) {
            send_packet(VIB_PKT_METRICS, 0, esp_timer_get_time(), 0, &m, sizeof(m));
        }
        poll_rx();
        int64_t now = esp_timer_get_time();
        if (now >= next_status) {
            send_status();
            next_status = now + status_period_us;
        }
    }
}

esp_err_t usb_link_start(void)
{
    uart_config_t cfg = {
        .baud_rate = CONFIG_VIB_UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    vib_rx_init(&s_rx);
    ESP_ERROR_CHECK(uart_driver_install(LINK_UART, 2048, LINK_TX_BUF, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(LINK_UART, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(LINK_UART, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));

    BaseType_t ok = xTaskCreatePinnedToCore(usb_task, "usb", 4096, NULL,
                                            configMAX_PRIORITIES - 4, NULL, 0);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
