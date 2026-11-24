/*
 * icm20948.c — TDK InvenSense ICM-20948 (somente acelerometro + FIFO), via SPI.
 *
 * Referencia: ICM-20948 datasheet DS-000189 v1.6.
 *
 *  - ACCEL_FCHOICE = 0 (DLPF desligado): taxa de 4500 Hz e banda de 3 dB de
 *    1209 Hz (tabela 18). E o modo de maior banda do acelerometro.
 *  - SPI ate 7 MHz. Registradores organizados em bancos (REG_BANK_SEL = 0x7F).
 *  - FIFO de 4 KB (o datasheet avisa que o tamanho pode variar com o DMP, que
 *    nao usamos). Dados na FIFO sao BIG-endian: o driver converte.
 *  - O chip nao tem nivel de watermark configuravel pelo usuario, entao a FIFO e
 *    lida por polling a cada 5 ms (~23 amostras; a FIFO aguenta >100 ms).
 *    O pino INT nao e usado.
 *  - Sensibilidade: +/-8 g = 4096 LSB/g = 32768/8, igual aos demais sensores.
 */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_check.h"
#include "sensor_priv.h"

static const char *TAG = "icm20948";

/* Banco 0 */
#define REG_USER_CTRL     0x03 /* FIFO_EN[6] I2C_IF_DIS[4] */
#define REG_PWR_MGMT_1    0x06 /* DEVICE_RESET[7] SLEEP[6] CLKSEL[2:0] */
#define REG_PWR_MGMT_2    0x07 /* DISABLE_ACCEL[5:3] DISABLE_GYRO[2:0] */
#define REG_INT_STATUS_2  0x1B /* FIFO_OVERFLOW_INT[4:0] (limpa na leitura) */
#define REG_FIFO_EN_1     0x66
#define REG_FIFO_EN_2     0x67 /* ACCEL_FIFO_EN[4] */
#define REG_FIFO_RST      0x68
#define REG_FIFO_MODE     0x69 /* 0 = stream */
#define REG_FIFO_COUNTH   0x70 /* FIFO_CNT[12:8]; ler H trava H+L */
#define REG_FIFO_R_W      0x72
#define REG_BANK_SEL      0x7F
/* Banco 2 */
#define REG_ACCEL_CONFIG  0x14 /* DLPFCFG[5:3] FS_SEL[2:1] FCHOICE[0] */

#define FIFO_BYTES 4096
#define FRAME_BYTES 6

static esp_err_t bank(vib_sensor_t *s, uint8_t b)
{
    return vs_write(s, REG_BANK_SEL, (uint8_t)(b << 4));
}

static esp_err_t fifo_reset(vib_sensor_t *s)
{
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_RST, 0x1F), TAG, "rst");
    return vs_write(s, REG_FIFO_RST, 0x00);
}

static esp_err_t configure(vib_sensor_t *s, const vib_sensor_config_t *cfg)
{
    s->odr_hz = 4500;
    s->bandwidth_hz = 1209;
    s->fifo_capacity_samples = FIFO_BYTES / FRAME_BYTES;
    s->use_irq = false;
    s->poll_ms = 5;
    s->watermark_samples = cfg->watermark_samples; /* usado so para fechar blocos */

    ESP_RETURN_ON_ERROR(bank(s, 0), TAG, "bank0");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_PWR_MGMT_1, 0x80), TAG, "reset");
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(bank(s, 0), TAG, "bank0");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_USER_CTRL, 0x10), TAG, "spi only"); /* I2C_IF_DIS */
    ESP_RETURN_ON_ERROR(vs_write(s, REG_PWR_MGMT_1, 0x01), TAG, "wake");    /* acorda, clock auto */
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_RETURN_ON_ERROR(vs_write(s, REG_PWR_MGMT_2, 0x07), TAG, "gyro off"); /* acel. ligado */

    s->full_scale_g = (cfg->full_scale_g == 4 || cfg->full_scale_g == 16) ? cfg->full_scale_g : 8;
    uint8_t fs = s->full_scale_g == 4 ? 1 : s->full_scale_g == 16 ? 3 : 2;
    ESP_RETURN_ON_ERROR(bank(s, 2), TAG, "bank2");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_ACCEL_CONFIG, (uint8_t)(fs << 1)), TAG, "accel"); /* FCHOICE=0 */
    ESP_RETURN_ON_ERROR(bank(s, 0), TAG, "bank0");

    vTaskDelay(pdMS_TO_TICKS(30)); /* partida do acelerometro (datasheet: 20-30 ms) */
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_EN_1, 0x00), TAG, "fifo en1");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_EN_2, 0x10), TAG, "fifo en2"); /* so acelerometro */
    ESP_RETURN_ON_ERROR(vs_write(s, REG_FIFO_MODE, 0x00), TAG, "stream");
    ESP_RETURN_ON_ERROR(fifo_reset(s), TAG, "fifo rst");
    ESP_RETURN_ON_ERROR(vs_write(s, REG_USER_CTRL, 0x50), TAG, "fifo on"); /* FIFO_EN | I2C_IF_DIS */
    uint8_t clr;
    return vs_read(s, REG_INT_STATUS_2, &clr, 1); /* limpa flag de overflow antiga */
}

static esp_err_t fifo_read(vib_sensor_t *s, int16_t *dst, uint32_t max_samples, uint32_t *n, bool *overrun)
{
    *n = 0;
    *overrun = false;
    uint8_t ovf;
    ESP_RETURN_ON_ERROR(vs_read(s, REG_INT_STATUS_2, &ovf, 1), TAG, "ovf");
    if (ovf & 0x1F) {
        *overrun = true; /* quadros podem ter ficado desalinhados: zera a FIFO */
        return fifo_reset(s);
    }
    uint8_t c[2];
    ESP_RETURN_ON_ERROR(vs_read(s, REG_FIFO_COUNTH, c, 2), TAG, "count");
    uint32_t bytes = ((c[0] & 0x1F) << 8) | c[1];
    uint32_t k = bytes / FRAME_BYTES;
    if (k > max_samples) {
        k = max_samples;
    }
    k &= ~1u;
    if (k == 0) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(vs_read_fifo(s, REG_FIFO_R_W, dst, k * FRAME_BYTES), TAG, "fifo");
    for (uint32_t i = 0; i < k * 3; i++) { /* big-endian -> little-endian */
        dst[i] = (int16_t)__builtin_bswap16((uint16_t)dst[i]);
    }
    *n = k;
    return ESP_OK;
}

const vib_sensor_driver_t vib_drv_icm20948 = {
    .name = "ICM-20948",
    .max_clock_hz = 7000000,
    .configure = configure,
    .fifo_read = fifo_read,
};
