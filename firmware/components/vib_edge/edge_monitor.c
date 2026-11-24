/*
 * edge_monitor.c — task de DSP, NVS, buzzer, saida de parada e botao (ver edge_monitor.h).
 *
 * Fluxo de dados:
 *   acquisition_task (Core 1, prio alta) --edge_monitor_feed()--> anel de amostras
 *   edge_task        (Core 1, prio baixa) copia as ultimas N amostras a cada N/2
 *                    novas, roda edge_core_analyze() e publica o resultado
 *   usb_task         (Core 0) envia os resultados ao PC (VIB_PKT_METRICS)
 *   esp_timer 50 ms  aciona buzzer / saida de parada e le o botao
 *
 * Concorrencia: o anel tem 4096 amostras e a janela no maximo 2048. Mesmo que a
 * aquisicao escreva um bloco inteiro (1364 amostras) durante a copia, a regiao
 * copiada nao e sobrescrita.
 */
#include <math.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "nvs.h"
#include "sdkconfig.h"
#include "edge_core.h"
#include "edge_monitor.h"

static const char *TAG = "edge";

#define RING 4096u /* amostras XYZ; potencia de 2 */
#define NVS_NS "vibedge"
#define TICK_MS 50

/* ---- configuracao (protegida por s_mtx) */
static SemaphoreHandle_t s_mtx;
static vib_edge_config_t s_cfg;
static vib_edge_baseline_t s_base;
static uint32_t s_base_gen;
static uint8_t s_node;

/* ---- sensor e anel de amostras */
static float s_odr = 0, s_gpl = 0;
static int16_t *s_ring;          /* RING * 3 */
static volatile uint32_t s_w;    /* total de amostras escritas */
static volatile uint32_t s_valid_from;
static uint32_t s_notified_at;
static TaskHandle_t s_task;
static uint64_t s_fs_t0;
static uint32_t s_fs_i0;
static volatile float s_fs_est;

/* ---- resultados e saidas */
static QueueHandle_t s_metrics_q;
static volatile uint8_t s_state = VIB_STATE_OK;
static volatile bool s_latched, s_silenced, s_reset_req, s_trip_out;
static volatile int64_t s_last_result_us;
static volatile int64_t s_test_buzzer_until, s_test_trip_until;

/* ================================================================ NVS */

static int baseline_ok(const vib_edge_baseline_t *b);

static void nvs_load(void)
{
    edge_default_config(&s_cfg);
    memset(&s_base, 0, sizeof(s_base));
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) != ESP_OK) {
        ESP_LOGI(TAG, "sem configuracao gravada: usando padrao");
        return;
    }
    vib_edge_config_t c;
    size_t len = sizeof(c);
    if (nvs_get_blob(h, "cfg", &c, &len) == ESP_OK && len == sizeof(c) && edge_validate_config(&c) == 0) {
        s_cfg = c;
    }
    len = sizeof(s_base);
    if (nvs_get_blob(h, "base", &s_base, &len) != ESP_OK || len != sizeof(s_base) || !baseline_ok(&s_base)) {
        memset(&s_base, 0, sizeof(s_base));
    }
    nvs_close(h);
    ESP_LOGI(TAG, "config carregada da NVS; baseline com %u bandas", s_base.n_bands);
}

static esp_err_t nvs_save(const char *key, const void *data, size_t len)
{
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }
    err = data ? nvs_set_blob(h, key, data, len) : nvs_erase_key(h, key);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        err = ESP_OK;
    }
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);
    return err;
}

/* ================================================================ saidas */

static bool buzzer_on_hw;

static void buzzer(bool on)
{
#if CONFIG_VIB_PIN_BUZZER >= 0
    if (on == buzzer_on_hw) {
        return;
    }
    buzzer_on_hw = on;
#if CONFIG_VIB_BUZZER_PASSIVE
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, on ? 512 : 0); /* 50 % de 10 bits */
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
#else
    gpio_set_level(CONFIG_VIB_PIN_BUZZER, on);
#endif
#else
    (void)on;
#endif
}

static void trip_output(bool stop_motor)
{
    s_trip_out = stop_motor;
#if CONFIG_VIB_PIN_TRIP >= 0
#if CONFIG_VIB_TRIP_ACTIVE_HIGH
    gpio_set_level(CONFIG_VIB_PIN_TRIP, stop_motor ? 1 : 0);
#else
    gpio_set_level(CONFIG_VIB_PIN_TRIP, stop_motor ? 0 : 1); /* fail-safe: nivel alto = motor liberado */
#endif
#endif
}

static void outputs_init(void)
{
#if CONFIG_VIB_PIN_TRIP >= 0
    /* Estado definido ANTES de virar saida: motor liberado. */
    trip_output(false);
    gpio_config_t t = {.pin_bit_mask = 1ULL << CONFIG_VIB_PIN_TRIP, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&t);
    trip_output(false);
#endif
#if CONFIG_VIB_PIN_BUZZER >= 0
#if CONFIG_VIB_BUZZER_PASSIVE
    ledc_timer_config_t tm = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = CONFIG_VIB_BUZZER_FREQ_HZ,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_timer_config(&tm);
    ledc_channel_config_t ch = {
        .gpio_num = CONFIG_VIB_PIN_BUZZER,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    ledc_channel_config(&ch);
#else
    gpio_config_t b = {.pin_bit_mask = 1ULL << CONFIG_VIB_PIN_BUZZER, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&b);
    gpio_set_level(CONFIG_VIB_PIN_BUZZER, 0);
#endif
#endif
#if CONFIG_VIB_PIN_ACK_BUTTON >= 0
    gpio_config_t k = {
        .pin_bit_mask = 1ULL << CONFIG_VIB_PIN_ACK_BUTTON,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&k);
#endif
}

/* Roda a cada 50 ms (task do esp_timer). */
static void outputs_tick(void *arg)
{
    static uint32_t tick;
    static uint8_t prev_state = VIB_STATE_OK;
    static int btn_prev = 1;
    tick++;
    int64_t now = esp_timer_get_time();

#if CONFIG_VIB_PIN_ACK_BUTTON >= 0
    int btn = gpio_get_level(CONFIG_VIB_PIN_ACK_BUTTON);
    if (btn == 0 && btn_prev == 1) { /* apertou: silencia e pede rearme */
        s_silenced = true;
        s_reset_req = true;
    }
    btn_prev = btn;
#endif

    uint8_t outputs, enabled;
    xSemaphoreTake(s_mtx, portMAX_DELAY);
    outputs = s_cfg.outputs;
    enabled = s_cfg.enabled;
    xSemaphoreGive(s_mtx);

    uint8_t st = s_state;
    /* sensor nao detectado, ou sem resultados novos ha 3 s com a analise ligada -> alerta */
    bool stale = enabled && (s_odr < 0 || (s_odr > 0 && (now - s_last_result_us) > 3000000));
    if (stale && st == VIB_STATE_OK) {
        st = VIB_STATE_WARN;
    }
    if (st > prev_state) {
        s_silenced = false; /* evento mais grave: volta a tocar */
    }
    if (st == VIB_STATE_OK) {
        s_silenced = false;
    }
    prev_state = st;

    /* saida de parada */
    bool trip = ((outputs & VIB_OUT_TRIP) && st == VIB_STATE_TRIP) || now < s_test_trip_until;
    if (trip != s_trip_out) {
        trip_output(trip);
        if (trip) {
            ESP_LOGW(TAG, "SAIDA DE PARADA ACIONADA");
        }
    }

    /* buzzer: PARADA = 250 ms liga/desliga; ALERTA = bip de 100 ms a cada 2 s */
    bool on = false;
    if ((outputs & VIB_OUT_BUZZER) && !s_silenced) {
        if (st == VIB_STATE_TRIP) {
            on = (tick / 5) % 2 == 0;
        } else if (st == VIB_STATE_WARN) {
            on = (tick % 40) < 2;
        }
    }
    if (now < s_test_buzzer_until) {
        on = true;
    }
    buzzer(on);
}

/* ================================================================ alimentacao (Core 1, prio alta) */

void edge_monitor_feed(const int16_t *xyz, uint32_t n, uint64_t t_us, bool gap)
{
    if (!s_ring) {
        return;
    }
    uint32_t w = s_w;
    if (gap) {
        s_valid_from = w; /* janelas nao podem atravessar a lacuna */
        s_fs_t0 = 0;
        return;
    }
    for (uint32_t i = 0; i < n; i++) {
        uint32_t k = (w + i) & (RING - 1);
        s_ring[3 * k] = xyz[3 * i];
        s_ring[3 * k + 1] = xyz[3 * i + 1];
        s_ring[3 * k + 2] = xyz[3 * i + 2];
    }
    w += n;
    s_w = w;

    /* taxa real: (amostras) / (tempo) num intervalo longo */
    if (s_fs_t0 == 0) {
        s_fs_t0 = t_us;
        s_fs_i0 = w;
    } else if (t_us - s_fs_t0 > 2000000) {
        s_fs_est = (float)(w - s_fs_i0) / ((float)(t_us - s_fs_t0) * 1e-6f);
    }

    uint32_t hop = s_cfg.n_window / 2; /* leitura sem trava: so decide quando acordar */
    if (s_task && w - s_notified_at >= hop) {
        s_notified_at = w;
        xTaskNotifyGive(s_task);
    }
}

/* ================================================================ task de DSP (Core 1, prio baixa) */

static void edge_task(void *arg)
{
    edge_core_t core;
    void *mem = NULL;
    int16_t *win = NULL;
    int cur_n = 0, cur_wt = -1;
    uint32_t base_gen_seen = 0;
    vib_edge_config_t cfg;
    vib_edge_baseline_t *base = heap_caps_malloc(sizeof(vib_edge_baseline_t), MALLOC_CAP_8BIT);

    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000));
        if (s_odr <= 0) {
            continue;
        }
        xSemaphoreTake(s_mtx, portMAX_DELAY);
        cfg = s_cfg;
        bool new_base = base_gen_seen != s_base_gen;
        if (new_base) {
            *base = s_base;
            base_gen_seen = s_base_gen;
        }
        xSemaphoreGive(s_mtx);
        if (!cfg.enabled) {
            s_state = VIB_STATE_OK;
            continue;
        }

        if (cfg.n_window != cur_n || cfg.window_type != cur_wt) { /* (re)aloca */
            free(mem);
            free(win);
            mem = heap_caps_malloc(edge_core_mem_size(cfg.n_window), MALLOC_CAP_8BIT);
            win = heap_caps_malloc((size_t)cfg.n_window * 6, MALLOC_CAP_8BIT);
            if (!mem || !win || edge_core_init(&core, cfg.n_window, cfg.window_type, mem) != 0) {
                ESP_LOGE(TAG, "sem memoria para N=%u", cfg.n_window);
                free(mem);
                free(win);
                mem = win = NULL;
                cur_n = 0;
                vTaskDelay(pdMS_TO_TICKS(1000));
                continue;
            }
            cur_n = cfg.n_window;
            cur_wt = cfg.window_type;
        }
        if (new_base) {
            edge_core_reset_baseline(&core);
        }
        if (s_reset_req) {
            s_reset_req = false;
            edge_core_reset_alarm(&core);
        }

        uint32_t w = s_w;
        if (w - s_valid_from < (uint32_t)cur_n) {
            continue; /* ainda nao ha uma janela continua */
        }
        for (int i = 0; i < cur_n; i++) {
            uint32_t k = (w - cur_n + i) & (RING - 1);
            win[3 * i] = s_ring[3 * k];
            win[3 * i + 1] = s_ring[3 * k + 1];
            win[3 * i + 2] = s_ring[3 * k + 2];
        }
        float fs = s_fs_est > 0 ? s_fs_est : s_odr;
        bool base_match = base->n_bands && base->n_window == cfg.n_window && base->window_type == cfg.window_type;
        vib_edge_metrics_t m;
        edge_core_analyze(&core, win, s_gpl, fs, &cfg, base_match ? base : NULL, &m);
        if (base->n_bands && !base_match) {
            m.flags |= VIB_MF_BASE_MISMATCH;
        }
        s_state = m.state;
        s_latched = (m.flags & VIB_MF_LATCHED) != 0;
        s_last_result_us = esp_timer_get_time();
        if (s_silenced) {
            m.flags |= VIB_MF_SILENCED;
        }
        if (s_trip_out) {
            m.flags |= VIB_MF_TRIP_OUT;
        }
        xQueueSend(s_metrics_q, &m, 0); /* fila cheia (PC lento): descarta */
    }
}

/* ================================================================ API */

esp_err_t edge_monitor_init(uint8_t node_id)
{
    s_node = node_id;
    s_mtx = xSemaphoreCreateMutex();
    s_metrics_q = xQueueCreate(4, sizeof(vib_edge_metrics_t));
    s_ring = heap_caps_malloc(RING * 6, MALLOC_CAP_8BIT);
    if (!s_mtx || !s_metrics_q || !s_ring) {
        return ESP_ERR_NO_MEM;
    }
    nvs_load();
    outputs_init();

    const esp_timer_create_args_t ta = {.callback = outputs_tick, .name = "edge_out"};
    esp_timer_handle_t timer;
    ESP_ERROR_CHECK(esp_timer_create(&ta, &timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(timer, TICK_MS * 1000));

    /* Core 1, prioridade baixa: a aquisicao (prio maxima-2) sempre preempta */
    BaseType_t ok = xTaskCreatePinnedToCore(edge_task, "edge", 4096, NULL, 3, &s_task, 1);
    return ok == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

void edge_monitor_set_sensor(float odr_hz, float g_per_lsb)
{
    s_gpl = g_per_lsb;
    s_odr = odr_hz;
    s_last_result_us = esp_timer_get_time();
}

bool edge_monitor_get_metrics(vib_edge_metrics_t *out, TickType_t wait)
{
    return s_metrics_q && xQueueReceive(s_metrics_q, out, wait) == pdTRUE;
}

static int baseline_ok(const vib_edge_baseline_t *b)
{
    if (b->n_bands > VIB_EDGE_MAX_BASE_BANDS || b->window_type > 2 ||
        (b->n_window != 1024 && b->n_window != 2048)) {
        return 0;
    }
    for (int i = 0; i < b->n_bands; i++) {
        if (!(b->f_lo[i] > 0.0f) || !(b->f_hi[i] > b->f_lo[i])) {
            return 0;
        }
        for (int a = 0; a < 3; a++) {
            if (!isfinite(b->mean_db[a][i])) {
                return 0;
            }
        }
    }
    return 1;
}

bool edge_monitor_command(const vib_header_t *hdr, const uint8_t *payload,
                          uint8_t *reply_type, uint8_t *reply, uint16_t *reply_len)
{
    if (hdr->node_id != 0 && hdr->node_id != s_node) {
        return false;
    }
    vib_ack_payload_t ack = {.cmd = hdr->type, .status = VIB_ACK_OK, .cmd_seq = hdr->seq};
    uint16_t len = hdr->payload_len;

    switch (hdr->type) {
    case VIB_CMD_SET_CONFIG: {
        vib_edge_config_t c;
        if (len != sizeof(c)) { ack.status = VIB_ACK_BAD_LENGTH; break; }
        memcpy(&c, payload, sizeof(c));
        if (edge_validate_config(&c) != 0) { ack.status = VIB_ACK_BAD_VALUE; break; }
        xSemaphoreTake(s_mtx, portMAX_DELAY);
        s_cfg = c;
        xSemaphoreGive(s_mtx);
        s_reset_req = true; /* limites novos: recomeca contagens */
        if (nvs_save("cfg", &c, sizeof(c)) != ESP_OK) ack.status = VIB_ACK_STORAGE;
        ESP_LOGI(TAG, "nova configuracao (saidas 0x%02X)", c.outputs);
        break;
    }
    case VIB_CMD_SET_BASELINE: {
        if (len != sizeof(vib_edge_baseline_t)) { ack.status = VIB_ACK_BAD_LENGTH; break; }
        vib_edge_baseline_t *b = heap_caps_malloc(sizeof(*b), MALLOC_CAP_8BIT);
        if (!b) { ack.status = VIB_ACK_UNAVAILABLE; break; }
        memcpy(b, payload, sizeof(*b));
        if (!baseline_ok(b)) {
            ack.status = VIB_ACK_BAD_VALUE;
        } else {
            xSemaphoreTake(s_mtx, portMAX_DELAY);
            s_base = *b;
            s_base_gen++;
            xSemaphoreGive(s_mtx);
            if (nvs_save("base", b, sizeof(*b)) != ESP_OK) ack.status = VIB_ACK_STORAGE;
        }
        free(b);
        break;
    }
    case VIB_CMD_CLEAR_BASE:
        xSemaphoreTake(s_mtx, portMAX_DELAY);
        s_base.n_bands = 0;
        s_base_gen++;
        xSemaphoreGive(s_mtx);
        if (nvs_save("base", NULL, 0) != ESP_OK) ack.status = VIB_ACK_STORAGE;
        break;
    case VIB_CMD_RESET_ALARM:
        s_reset_req = true;
        s_silenced = false;
        break;
    case VIB_CMD_GET_CONFIG:
        xSemaphoreTake(s_mtx, portMAX_DELAY);
        memcpy(reply, &s_cfg, sizeof(s_cfg));
        reply[sizeof(s_cfg)] = s_base.n_bands;
        xSemaphoreGive(s_mtx);
        *reply_type = VIB_PKT_CONFIG;
        *reply_len = sizeof(s_cfg) + 1;
        return true;
    case VIB_CMD_TEST_OUTPUT: {
        int64_t now = esp_timer_get_time();
        uint8_t what = len ? payload[0] : 0;
        if (what == 1 && CONFIG_VIB_PIN_BUZZER >= 0) {
            s_test_buzzer_until = now + 1000000;
        } else if (what == 2 && CONFIG_VIB_PIN_TRIP >= 0) {
            s_test_trip_until = now + 2000000;
        } else {
            ack.status = VIB_ACK_UNAVAILABLE;
        }
        break;
    }
    default:
        ack.status = VIB_ACK_UNKNOWN;
    }
    memcpy(reply, &ack, sizeof(ack));
    *reply_type = VIB_PKT_ACK;
    *reply_len = sizeof(ack);
    return true;
}
