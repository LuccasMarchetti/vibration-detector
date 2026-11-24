/*
 * lsm6ds3.c — LSM6DS3 / LSM6DS3TR-C (somente acelerometro + FIFO), via SPI.
 *
 *  - ODR 6,66 kHz; FIFO continua com watermark sinalizado no INT1.
 *  - XL_BW_SCAL_ODR fica em 0 DE PROPOSITO: a 3,33/6,66 kHz o filtro
 *    anti-aliasing analogico nao e usado e so resta o LPF1 digital (3320 Hz),
 *    banda total 3160 Hz (AN4650 Rev 2, tabelas 7-9). Com o bit em 1 a banda
 *    passaria a ser BW_XL (maximo 400 Hz).
 *  - Leitura multipla de FIFO_DATA_OUT_L/H (0x3E/0x3F): o endereco volta
 *    sozinho para 0x3E ("rounding" automatico, AN4650 sec. 3.6.1).
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "sensor_priv.h"

static const char *TAG = "lsm6ds3";

#define REG_FIFO_CTRL1   0x06 /* FTH[7:0]                                */
#define REG_FIFO_CTRL2   0x07 /* FTH[11:8] (bits 3:0)                    */
#define REG_FIFO_CTRL3   0x08 /* DEC_FIFO_GYRO[5:3] DEC_FIFO_XL[2:0]      */
#define REG_FIFO_CTRL4   0x09
#define REG_FIFO_CTRL5   0x0A /* ODR_FIFO[6:3] FIFO_MODE[2:0]             */
#define REG_INT1_CTRL    0x0D
#define REG_CTRL1_XL     0x10 /* ODR_XL[7:4] FS_XL[3:2] BW_XL[1:0]        */
#define REG_CTRL2_G      0x11
#define REG_CTRL3_C      0x12
#define REG_CTRL4_C      0x13
#define REG_FIFO_STATUS1 0x3A /* DIFF_FIFO[7:0], seguido de STATUS2..4     */
#define REG_FIFO_DATA    0x3E

#define CTRL3_SW_RESET 0x01
#define CTRL3_IF_INC   0x04
#define CTRL3_BDU      0x40
#define CTRL4_I2C_DIS  0x04
#define INT1_FTH       0x08
#define ST2_OVR        0x40
#define ST2_EMPTY      0x10
#define ODR_6660       0x0A /* 1010 em ODR_XL e ODR_FIFO */
#define FIFO_CONTINUOUS 0x06

static uint8_t fs_code(uint8_t g)
{
    /* FS_XL: 00=2g 01=16g 10=4g 11=8g */
    return g == 4 ? 0x2 : g == 16 ? 0x1 : 0x3;
}

static esp_err_t fifo_restart(vib_sensor_t *s)
{
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_CTRL5, 0x00), TAG, "bypass");
    return vs_write(s, REG_FIFO_CTRL5, (ODR_6660 << 3) | FIFO_CONTINUOUS);
}

static esp_err_t configure(vib_sensor_t *s, const vib_sensor_config_t *cfg)
{
    bool trc = (s->type == VIB_SENSOR_LSM6DS3TRC);
    s->odr_hz = 6660;
    s->bandwidth_hz = trc ? 1500 : 3160;
    s->fifo_capacity_samples = trc ? 682 : 1365; /* 4 KB / 8 KB */
    s->use_irq = true;

    ESP_RETURN_ON_ERROR(vs_write(s, REG_CTRL3_C, CTRL3_SW_RESET), TAG, "rst");
    vTaskDelay(pdMS_TO_TICKS(20));
    ESP_RETURN_ON_ERROR(vs_write(s, REG_CTRL3_C, CTRL3_BDU | CTRL3_IF_INC), TAG, "c3");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_CTRL4_C, CTRL4_I2C_DIS), TAG, "c4"); /* XL_BW_SCAL_ODR = 0 */
    ESP_RETURN_ON_ERROR(vs_write(s, REG_CTRL2_G, 0x00), TAG, "gyro off");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_CTRL5, 0x00), TAG, "f5");

    uint16_t wtm = cfg->watermark_samples;
    uint16_t max_wtm = s->fifo_capacity_samples - 64; /* margem */
    if (wtm > max_wtm) {
        wtm = max_wtm;
    }
    s->watermark_samples = wtm;
    uint16_t fth = (uint16_t)(wtm * 3); /* em palavras de 16 bits */
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_CTRL1, fth & 0xFF), TAG, "f1");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_CTRL2, (fth >> 8) & 0x0F), TAG, "f2");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_CTRL3, 0x01), TAG, "f3"); /* XL sem decimacao */
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_CTRL4, 0x00), TAG, "f4");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_INT1_CTRL, INT1_FTH), TAG, "int1");

    s->full_scale_g = (cfg->full_scale_g == 4 || cfg->full_scale_g == 16) ? cfg->full_scale_g : 8;
    ESP_RETURN_ON_ERROR(vs_write(s, REG_CTRL1_XL, (ODR_6660 << 4) | (fs_code(s->full_scale_g) << 2)), TAG, "c1");
    return vs_write(s, REG_FIFO_CTRL5, (ODR_6660 << 3) | FIFO_CONTINUOUS);
}

static esp_err_t fifo_read(vib_sensor_t *s, int16_t *dst, uint32_t max_samples, uint32_t *n, bool *overrun)
{
    *n = 0;
    *overrun = false;
    uint8_t st[4];
    ESP_RETURN_ON_ERROR(vs_read(s, REG_FIFO_STATUS1, st, 4), TAG, "status");
    if (st[1] & ST2_OVR) {
        *overrun = true;
        return fifo_restart(s);
    }
    if (st[1] & ST2_EMPTY) {
        return ESP_OK;
    }
    uint16_t mask_hi = (s->type == VIB_SENSOR_LSM6DS3) ? 0x0F : 0x07;
    uint32_t words = st[0] | ((st[1] & mask_hi) << 8);
    uint16_t pattern = st[2] | ((st[3] & 0x03) << 8); /* proxima palavra: 0=X 1=Y 2=Z */

    if (pattern != 0 && words >= 3) {
        uint8_t junk[4];
        uint16_t skip = (uint16_t)(3 - pattern); /* alinha no eixo X */
        ESP_RETURN_ON_ERROR(vs_read(s, REG_FIFO_DATA, junk, skip * 2), TAG, "align");
        words -= skip;
    }
    uint32_t k = words / 3;
    if (k > max_samples) {
        k = max_samples;
    }
    k &= ~1u; /* numero par -> bytes multiplos de 4 (DMA) */
    if (k == 0) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(vs_read_fifo(s, REG_FIFO_DATA, dst, k * 6), TAG, "fifo");
    *n = k;
    return ESP_OK;
}

const vib_sensor_driver_t vib_drv_lsm6ds3 = {
    .name = "LSM6DS3",
    .max_clock_hz = 10000000,
    .configure = configure,
    .fifo_read = fifo_read,
};
