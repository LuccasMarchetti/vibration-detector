/*
 * bmi160.c — Bosch BMI160 (somente acelerometro + FIFO), via SPI.
 *
 * Referencia: BMI160 datasheet BST-BMI160-DS000-08 rev 0.9 e o driver oficial
 * boschsensortec/BMI160_driver (bmi160_defs.h).
 *
 *  - ODR maximo do acelerometro: 1600 Hz, filtro "normal" (acc_bwp = 2):
 *    corte de 3 dB em 684 Hz (tabela 12). E a menor banda dos sensores suportados.
 *  - FIFO de 1024 bytes em modo "headerless" so com acelerometro: quadros de
 *    6 bytes (X,Y,Z int16 little-endian) -> 170 amostras (~106 ms).
 *  - Watermark (FIFO_CONFIG_0) em unidades de 4 bytes; interrupcao mapeada no INT1,
 *    push-pull, ativa em nivel alto, nao travada.
 *  - Interface SPI: apos energizar e preciso uma leitura "dummy" de 0x7F
 *    (feita na deteccao, vib_sensor.c).
 *  - Sensibilidade: +/-8 g = 4096 LSB/g = 32768/8, igual aos demais sensores.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "esp_rom_sys.h"
#include "sensor_priv.h"

static const char *TAG = "bmi160";

#define REG_FIFO_LENGTH_0 0x22 /* fifo_byte_counter[7:0], 0x23 = [10:8] */
#define REG_FIFO_DATA     0x24
#define REG_ACC_CONF      0x40 /* acc_us[7] acc_bwp[6:4] acc_odr[3:0] */
#define REG_ACC_RANGE     0x41
#define REG_FIFO_CONFIG_0 0x46 /* fifo_water_mark (unidades de 4 bytes) */
#define REG_FIFO_CONFIG_1 0x47 /* gyr[7] acc[6] mag[5] header[4] ... */
#define REG_INT_EN_1      0x51 /* int_fwm_en = bit 6 */
#define REG_INT_OUT_CTRL  0x53 /* int1_output_en[3] int1_od[2] int1_lvl[1] int1_edge[0] */
#define REG_INT_LATCH     0x54
#define REG_INT_MAP_1     0x56 /* int1_fwm = bit 6 */
#define REG_CMD           0x7E

#define CMD_SOFTRESET     0xB6
#define CMD_FIFO_FLUSH    0xB0
#define CMD_ACC_NORMAL    0x11
#define ACC_CONF_1600HZ   0x2C /* acc_us=0, acc_bwp=2 (normal), acc_odr=0b1100 */
#define FIFO_BYTES        1024
#define FRAME_BYTES       6

static uint8_t range_code(uint8_t g)
{
    return g == 4 ? 0x05 : g == 16 ? 0x0C : 0x08; /* 2g=0x03 nao usado */
}

/* Fora do modo normal o BMI160 exige >= 450 us entre escritas; usamos 1 ms. */
static esp_err_t wr(vib_sensor_t *s, uint8_t reg, uint8_t v)
{
    esp_err_t err = vs_write(s, reg, v);
    esp_rom_delay_us(1000);
    return err;
}

static esp_err_t configure(vib_sensor_t *s, const vib_sensor_config_t *cfg)
{
    s->odr_hz = 1600;
    s->bandwidth_hz = 684;
    s->fifo_capacity_samples = FIFO_BYTES / FRAME_BYTES; /* 170 */
    s->use_irq = true;

    ESP_RETURN_ON_ERROR(wr(s, REG_CMD, CMD_SOFTRESET), TAG, "reset");
    vTaskDelay(pdMS_TO_TICKS(20));
    uint8_t dummy;
    vs_read(s, 0x7F, &dummy, 1); /* o reset volta para I2C: reentra em modo SPI */
    vTaskDelay(pdMS_TO_TICKS(2));

    s->full_scale_g = (cfg->full_scale_g == 4 || cfg->full_scale_g == 16) ? cfg->full_scale_g : 8;
    ESP_RETURN_ON_ERROR(wr(s, REG_ACC_CONF, ACC_CONF_1600HZ), TAG, "acc_conf");
    ESP_RETURN_ON_ERROR(wr(s, REG_ACC_RANGE, range_code(s->full_scale_g)), TAG, "range");

    /* Watermark: ate ~70 % da FIFO para sobrar margem para a latencia da task */
    uint16_t wtm = cfg->watermark_samples;
    uint16_t max_wtm = 120;
    if (wtm > max_wtm) {
        wtm = max_wtm;
    }
    s->watermark_samples = wtm;
    ESP_RETURN_ON_ERROR(wr(s, REG_FIFO_CONFIG_0, (uint8_t)((wtm * FRAME_BYTES) / 4)), TAG, "fwm");
    ESP_RETURN_ON_ERROR(wr(s, REG_FIFO_CONFIG_1, 0x40), TAG, "fifo cfg"); /* so acel., sem header */

    ESP_RETURN_ON_ERROR(wr(s, REG_INT_OUT_CTRL, 0x0A), TAG, "int out");   /* INT1: saida, push-pull, ativo alto, nivel */
    ESP_RETURN_ON_ERROR(wr(s, REG_INT_LATCH, 0x00), TAG, "latch");        /* nao travado */
    ESP_RETURN_ON_ERROR(wr(s, REG_INT_MAP_1, 0x40), TAG, "map");          /* watermark -> INT1 */
    ESP_RETURN_ON_ERROR(wr(s, REG_INT_EN_1, 0x40), TAG, "int en");

    ESP_RETURN_ON_ERROR(wr(s, REG_CMD, CMD_ACC_NORMAL), TAG, "normal");
    vTaskDelay(pdMS_TO_TICKS(10)); /* partida do acelerometro */
    return wr(s, REG_CMD, CMD_FIFO_FLUSH);
}

static esp_err_t fifo_read(vib_sensor_t *s, int16_t *dst, uint32_t max_samples, uint32_t *n, bool *overrun)
{
    *n = 0;
    *overrun = false;
    uint8_t len_b[2];
    ESP_RETURN_ON_ERROR(vs_read(s, REG_FIFO_LENGTH_0, len_b, 2), TAG, "len");
    uint32_t bytes = len_b[0] | ((len_b[1] & 0x07) << 8);
    if (bytes >= FIFO_BYTES - FRAME_BYTES) {
        /* FIFO cheia: o modo stream ja sobrescreveu amostras antigas */
        *overrun = true;
        return vs_write(s, REG_CMD, CMD_FIFO_FLUSH);
    }
    uint32_t k = bytes / FRAME_BYTES;
    if (k > max_samples) {
        k = max_samples;
    }
    k &= ~1u; /* par -> multiplo de 4 bytes (DMA) */
    if (k == 0) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(vs_read_fifo(s, REG_FIFO_DATA, dst, k * FRAME_BYTES), TAG, "fifo");
    *n = k;
    return ESP_OK;
}

const vib_sensor_driver_t vib_drv_bmi160 = {
    .name = "BMI160",
    .max_clock_hz = 10000000,
    .configure = configure,
    .fifo_read = fifo_read,
};
