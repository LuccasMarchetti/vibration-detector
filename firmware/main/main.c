/*
 * main.c — No de aquisicao de vibracao (1 ESP32 + 1 LSM6DS3 por mancal)
 *
 *   Core 1: acquisition_task  (INT1 -> SPI burst read da FIFO)
 *   Core 0: usb_task          (empacota + CRC -> UART0/USB)
 *
 * O mesmo binario serve para os dois mancais: o ID vem do pino de selecao
 * (ver node_id.c) ou do menuconfig.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "edge_monitor.h"
#include "app.h"

static const char *TAG = "main";

vib_stats_t g_stats;
QueueHandle_t g_free_q;
QueueHandle_t g_tx_q;

void app_main(void)
{
    g_stats.node_id = node_id_detect();

    /* NVS guarda a configuracao do alarme local e a baseline */
    esp_err_t nerr = nvs_flash_init();
    if (nerr == ESP_ERR_NVS_NO_FREE_PAGES || nerr == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nerr = nvs_flash_init();
    }
    if (nerr != ESP_OK) {
        ESP_LOGE(TAG, "NVS indisponivel: %s", esp_err_to_name(nerr));
    }
    /* Cedo: coloca a saida de parada no estado "motor liberado" logo no boot */
    if (edge_monitor_init(g_stats.node_id) != ESP_OK) {
        ESP_LOGE(TAG, "alarme local nao iniciado (memoria)");
    }

    g_free_q = xQueueCreate(VIB_POOL_BLOCKS, sizeof(vib_block_t *));
    g_tx_q = xQueueCreate(VIB_POOL_BLOCKS, sizeof(vib_block_t *));
    for (int i = 0; i < VIB_POOL_BLOCKS; i++) {
        vib_block_t *b = heap_caps_aligned_alloc(4, sizeof(vib_block_t), MALLOC_CAP_DMA);
        ESP_ERROR_CHECK(b ? ESP_OK : ESP_ERR_NO_MEM);
        xQueueSend(g_free_q, &b, 0);
    }

    /* O link USB sobe primeiro: mesmo sem sensor o PC recebe STATUS com
     * who_am_i = 0, o que ajuda a diagnosticar problemas de fiacao. */
    ESP_ERROR_CHECK(usb_link_start());

    esp_err_t err = acquisition_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Falha na aquisicao: %s — verifique o sensor", esp_err_to_name(err));
        edge_monitor_set_sensor(-1.0f, 0.0f); /* buzzer avisa: sem sensor */
    } else {
        edge_monitor_set_sensor((float)g_stats.odr_hz, (float)g_stats.full_scale_g / 32768.0f);
    }
}
