/*
 * acquisition.c — Task de aquisicao deterministica (Core 1, alta prioridade)
 *
 * Fluxo:
 *   INT1 (watermark da FIFO) --ISR--> notifica a task
 *   task: le FIFO_STATUS, faz leitura em rajada de TODA a FIFO disponivel
 *         para um bloco do pool e o entrega a fila de TX (Core 0).
 *
 * Zero-copy: os blocos circulam entre g_free_q e g_tx_q como ponteiros.
 * Se nao houver bloco livre (PC lento / USB travada), a FIFO e drenada para
 * um buffer de descarte para que o SENSOR nunca transborde; o bloco seguinte
 * e marcado com VIB_FLAG_BLOCK_DROP e o contador first_sample continua
 * avancando, de modo que o PC enxerga exatamente onde houve lacuna.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "lsm6ds3.h"
#include "vib_protocol.h"
#include "app.h"

static const char *TAG = "acq";
static lsm6ds3_t s_dev;
static TaskHandle_t s_task;
static int16_t *s_discard; /* buffer DMA para drenar a FIFO quando nao ha bloco livre */

static void IRAM_ATTR int1_isr(void *arg)
{
    BaseType_t hp = pdFALSE;
    vTaskNotifyGiveFromISR(s_task, &hp);
    portYIELD_FROM_ISR(hp);
}

/* Instala a ISR de INT1 a partir do Core 1, para que a interrupcao seja
 * alocada (e atendida) no mesmo nucleo da task de aquisicao. */
static void setup_int1(void)
{
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << CONFIG_VIB_PIN_INT1,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_POSEDGE,
    };
    ESP_ERROR_CHECK(gpio_config(&io));
    ESP_ERROR_CHECK(gpio_install_isr_service(ESP_INTR_FLAG_IRAM));
    ESP_ERROR_CHECK(gpio_isr_handler_add(CONFIG_VIB_PIN_INT1, int1_isr, NULL));
}

static void acquisition_task(void *arg)
{
    setup_int1();
    /* Se a FIFO ja passou do watermark antes da ISR existir nao havera nova
     * borda de subida: forca uma primeira leitura. */
    xTaskNotifyGive(xTaskGetCurrentTaskHandle());

    uint32_t sample_index = 0;
    uint8_t pending_flags = 0;
    /* Timeout = ~3 periodos de watermark: so serve para recuperar uma borda perdida */
    const TickType_t timeout = pdMS_TO_TICKS(3 * 1000 * s_dev.watermark_samples / VIB_ODR_HZ + 10);

    for (;;) {
        uint32_t got = ulTaskNotifyTake(pdTRUE, timeout);
        if (!got && gpio_get_level(CONFIG_VIB_PIN_INT1) == 0) {
            continue; /* timeout sem dados acumulados */
        }

        /* Drena enquanto houver dados acima do watermark */
        for (int guard = 0; guard < 4; guard++) {
            uint64_t t_us = esp_timer_get_time();
            lsm6ds3_fifo_status_t st;
            if (lsm6ds3_fifo_status(&s_dev, &st) != ESP_OK) {
                break;
            }
            if (st.words > g_stats.max_fifo_words) {
                g_stats.max_fifo_words = st.words;
            }
            if (st.overrun) {
                /* Nao deveria acontecer: houve perda dentro do sensor */
                g_stats.fifo_overruns++;
                pending_flags |= VIB_FLAG_FIFO_OVERRUN;
                lsm6ds3_fifo_restart(&s_dev);
                /* Estima as amostras perdidas pelo tempo (~1 FIFO cheia) */
                sample_index += s_dev.fifo_words_capacity / 3;
                break;
            }

            uint16_t words = st.words;
            /* Alinha no eixo X (pattern 0) descartando 1 ou 2 palavras */
            if (st.pattern != 0 && words >= 3) {
                uint8_t junk[4];
                uint16_t skip = (uint16_t)(3 - st.pattern);
                lsm6ds3_read_reg(&s_dev, LSM6DS3_REG_FIFO_DATA_OUT_L, junk, skip * 2);
                words -= skip;
            }

            uint32_t n = words / 3;
            n &= ~1u; /* numero par de amostras -> bytes multiplos de 4 (DMA) */
            if (n > VIB_BLOCK_MAX_SAMPLES) {
                n = VIB_BLOCK_MAX_SAMPLES;
            }
            if (n < 2) {
                break;
            }

            vib_block_t *blk = NULL;
            if (xQueueReceive(g_free_q, &blk, 0) != pdTRUE) {
                lsm6ds3_fifo_read_words(&s_dev, s_discard, n * 3);
                g_stats.block_drops++;
                pending_flags |= VIB_FLAG_BLOCK_DROP;
                sample_index += n;
                continue;
            }

            if (lsm6ds3_fifo_read_words(&s_dev, blk->data, n * 3) != ESP_OK) {
                xQueueSend(g_free_q, &blk, 0);
                break;
            }
            blk->timestamp_us = t_us;
            blk->first_sample = sample_index;
            blk->n_samples = (uint16_t)n;
            blk->flags = pending_flags;
            pending_flags = 0;
            sample_index += n;

            if (xQueueSend(g_tx_q, &blk, 0) != pdTRUE) {
                xQueueSend(g_free_q, &blk, 0);
                g_stats.block_drops++;
                pending_flags |= VIB_FLAG_BLOCK_DROP;
            }

            if (gpio_get_level(CONFIG_VIB_PIN_INT1) == 0) {
                break; /* abaixo do watermark: volta a esperar a interrupcao */
            }
        }
    }
}

esp_err_t acquisition_start(void)
{
    lsm6ds3_config_t cfg = {
        .host = SPI3_HOST, /* VSPI no ESP32 classico */
        .pin_sclk = CONFIG_VIB_PIN_SCLK,
        .pin_mosi = CONFIG_VIB_PIN_MOSI,
        .pin_miso = CONFIG_VIB_PIN_MISO,
        .pin_cs = CONFIG_VIB_PIN_CS,
        .clock_hz = CONFIG_VIB_SPI_CLOCK_HZ,
        .full_scale = (lsm6ds3_fs_t)CONFIG_VIB_FS_G,
        .watermark_samples = CONFIG_VIB_WTM_SAMPLES,
    };
    esp_err_t err = lsm6ds3_init(&s_dev, &cfg);
    g_stats.who_am_i = (err == ESP_OK) ? s_dev.who_am_i : 0;
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Sensor nao inicializado (%s)", esp_err_to_name(err));
        return err;
    }
    g_stats.wtm_samples = s_dev.watermark_samples;
    g_stats.full_scale_g = (uint8_t)s_dev.full_scale;

    s_discard = heap_caps_malloc(VIB_BLOCK_MAX_SAMPLES * 6, MALLOC_CAP_DMA);
    if (!s_discard) {
        return ESP_ERR_NO_MEM;
    }

    /* Task fixada no Core 1 com prioridade alta */
    BaseType_t ok = xTaskCreatePinnedToCore(acquisition_task, "acq", 4096, NULL,
                                            configMAX_PRIORITIES - 2, &s_task, 1);
    if (ok != pdPASS) {
        return ESP_ERR_NO_MEM;
    }

    return ESP_OK;
}
