/*
 * acquisition.c — Task de aquisicao deterministica (Core 1, alta prioridade)
 *
 * Funciona com qualquer sensor de vib_sensor.h:
 *   - LSM6DS3 / BMI160: INT1 (watermark da FIFO) --ISR--> notifica a task;
 *   - ICM-20948: sem watermark configuravel -> a task acorda a cada poll_ms.
 *
 * A cada despertar a task drena a FIFO do sensor (SPI burst via DMA) direto
 * para o bloco corrente do pool. As leituras se acumulam no mesmo bloco ate
 * ele ter >= watermark_samples amostras; ai o bloco vai para a fila de TX
 * (Core 0). Assim o tamanho dos pacotes e parecido para todos os sensores,
 * mesmo os lidos em pedacos pequenos por polling.
 *
 * Zero-copia: os blocos circulam entre g_free_q e g_tx_q como ponteiros.
 * Se nao houver bloco livre (PC/USB lentos), a FIFO e drenada para um buffer
 * de descarte, para que o SENSOR nunca transborde; o proximo bloco sai com
 * VIB_FLAG_BLOCK_DROP e o indice first_sample continua avancando, entao o PC
 * sabe exatamente quantas amostras faltaram.
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include "vib_sensor.h"
#include "vib_protocol.h"
#include "edge_monitor.h"
#include "app.h"

static const char *TAG = "acq";
static vib_sensor_t s_sens;
static TaskHandle_t s_task;
static int16_t *s_discard;

static void IRAM_ATTR int1_isr(void *arg)
{
    BaseType_t hp = pdFALSE;
    vTaskNotifyGiveFromISR(s_task, &hp);
    portYIELD_FROM_ISR(hp);
}

/* Instalada a partir do Core 1 para que a interrupcao seja atendida no mesmo
 * nucleo da task de aquisicao. */
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

static void send_block(vib_block_t *blk)
{
    if (xQueueSend(g_tx_q, &blk, 0) != pdTRUE) {
        xQueueSend(g_free_q, &blk, 0); /* nao deveria ocorrer: fila do tamanho do pool */
        g_stats.block_drops++;
    }
}

static void acquisition_task(void *arg)
{
    const bool irq = s_sens.use_irq;
    if (irq) {
        setup_int1();
        /* Se a FIFO ja passou do watermark antes da ISR existir nao havera
         * nova borda de subida: forca uma primeira leitura. */
        xTaskNotifyGive(xTaskGetCurrentTaskHandle());
    }
    /* IRQ: o timeout (~3 periodos de watermark) so recupera uma borda perdida */
    const TickType_t irq_timeout =
        pdMS_TO_TICKS(3 * 1000 * s_sens.watermark_samples / s_sens.odr_hz + 10);
    const TickType_t poll_period = pdMS_TO_TICKS(s_sens.poll_ms ? s_sens.poll_ms : 5);
    const uint32_t target = s_sens.watermark_samples;

    vib_block_t *cur = NULL;
    uint32_t sample_index = 0;
    uint8_t pending_flags = 0;
    TickType_t last_wake = xTaskGetTickCount();

    for (;;) {
        if (irq) {
            ulTaskNotifyTake(pdTRUE, irq_timeout);
        } else {
            vTaskDelayUntil(&last_wake, poll_period);
        }

        for (int guard = 0; guard < 8; guard++) {
            if (!cur && xQueueReceive(g_free_q, &cur, 0) == pdTRUE) {
                cur->n_samples = 0;
                cur->flags = pending_flags;
                pending_flags = 0;
            }
            int16_t *dst = cur ? cur->data + (size_t)cur->n_samples * 3 : s_discard;
            uint32_t room = cur ? VIB_BLOCK_MAX_SAMPLES - cur->n_samples : VIB_BLOCK_MAX_SAMPLES;

            uint64_t t_us = esp_timer_get_time();
            uint32_t n = 0;
            bool overrun = false;
            if (vib_sensor_fifo_read(&s_sens, dst, room, &n, &overrun) != ESP_OK) {
                break;
            }
            if (overrun) {
                /* Perda dentro do sensor (nao deveria ocorrer). Fecha o bloco atual
                 * para nao misturar amostras dos dois lados da lacuna e estima as
                 * perdidas como uma FIFO cheia. */
                g_stats.fifo_overruns++;
                if (cur && cur->n_samples) {
                    send_block(cur);
                    cur = NULL;
                }
                if (cur) {
                    cur->flags |= VIB_FLAG_FIFO_OVERRUN; /* bloco vazio: marca ele mesmo */
                } else {
                    pending_flags |= VIB_FLAG_FIFO_OVERRUN; /* vai no proximo bloco */
                }
                sample_index += s_sens.fifo_capacity_samples;
                edge_monitor_feed(NULL, 0, t_us, true); /* janela da FFT nao atravessa a lacuna */
                break;
            }
            if (n == 0) {
                break;
            }
            /* copia para a analise local (FFT no ESP32), mesmo se o bloco for descartado */
            edge_monitor_feed(dst, n, t_us, false);
            uint16_t fifo_words = (uint16_t)(n * 3);
            if (fifo_words > g_stats.max_fifo_words) {
                g_stats.max_fifo_words = fifo_words;
            }
            if (!cur) {
                g_stats.block_drops++;
                pending_flags |= VIB_FLAG_BLOCK_DROP;
                sample_index += n;
                continue;
            }
            if (cur->n_samples == 0) {
                cur->first_sample = sample_index;
            }
            cur->n_samples += n;
            cur->timestamp_us = t_us; /* ~ instante da ultima amostra do bloco */
            sample_index += n;

            if (cur->n_samples >= target || VIB_BLOCK_MAX_SAMPLES - cur->n_samples < 2) {
                send_block(cur);
                cur = NULL;
            }
            if (n < room) {
                break; /* leu tudo que havia na FIFO */
            }
        }
    }
}

esp_err_t acquisition_start(void)
{
    vib_sensor_config_t cfg = {
        .host = SPI3_HOST, /* VSPI no ESP32 classico */
        .pin_sclk = CONFIG_VIB_PIN_SCLK,
        .pin_mosi = CONFIG_VIB_PIN_MOSI,
        .pin_miso = CONFIG_VIB_PIN_MISO,
        .pin_cs = CONFIG_VIB_PIN_CS,
        .max_clock_hz = CONFIG_VIB_SPI_CLOCK_HZ,
        .full_scale_g = CONFIG_VIB_FS_G,
        .watermark_samples = CONFIG_VIB_WTM_SAMPLES,
        .forced = (vib_sensor_type_t)CONFIG_VIB_SENSOR_TYPE,
    };
    esp_err_t err = vib_sensor_init(&cfg, &s_sens);
    if (err != ESP_OK) {
        g_stats.who_am_i = 0;
        ESP_LOGE(TAG, "Sensor nao inicializado (%s)", esp_err_to_name(err));
        return err;
    }
    g_stats.who_am_i = s_sens.chip_id;
    g_stats.sensor_type = (uint8_t)s_sens.type;
    g_stats.odr_hz = s_sens.odr_hz;
    g_stats.bandwidth_hz = s_sens.bandwidth_hz;
    g_stats.wtm_samples = s_sens.watermark_samples;
    g_stats.full_scale_g = s_sens.full_scale_g;

    s_discard = heap_caps_malloc(VIB_BLOCK_MAX_SAMPLES * 6, MALLOC_CAP_DMA);
    if (!s_discard) {
        return ESP_ERR_NO_MEM;
    }
    BaseType_t ok = xTaskCreatePinnedToCore(acquisition_task, "acq", 4096, NULL,
                                            configMAX_PRIORITIES - 2, &s_task, 1);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
