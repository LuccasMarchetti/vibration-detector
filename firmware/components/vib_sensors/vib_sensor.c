/*
 * vib_sensor.c — barramento SPI, deteccao automatica e acesso comum.
 *
 * Deteccao (somente LEITURAS, para nao escrever em registradores reservados
 * de um chip desconhecido):
 *   1. leitura "dummy" de 0x7F — exigida pelo BMI160 para entrar em modo SPI
 *      (inofensiva nos outros chips);
 *   2. reg 0x00: 0xEA = ICM-20948, 0xD1 = BMI160;
 *   3. reg 0x0F: 0x69 = LSM6DS3, 0x6A = LSM6DS3TR-C.
 * A deteccao roda a 1 MHz; depois o dispositivo e recriado no clock maximo
 * permitido pelo sensor (ICM-20948: 7 MHz; demais: 10 MHz).
 */
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_check.h"
#include "sensor_priv.h"

static const char *TAG = "vib_sensor";

#define PROBE_CLOCK_HZ 1000000

const char *vib_sensor_type_name(vib_sensor_type_t t)
{
    switch (t) {
    case VIB_SENSOR_LSM6DS3:    return "LSM6DS3";
    case VIB_SENSOR_LSM6DS3TRC: return "LSM6DS3TR-C";
    case VIB_SENSOR_BMI160:     return "BMI160";
    case VIB_SENSOR_ICM20948:   return "ICM-20948";
    default:                    return "nenhum";
    }
}

esp_err_t vs_read(vib_sensor_t *s, uint8_t reg, uint8_t *data, size_t len)
{
    spi_transaction_t t = {
        .cmd = 0x80 | reg,
        .length = len * 8,
        .rxlength = len * 8,
    };
    if (len <= 4) {
        /* leituras curtas usam o buffer interno da transacao (sem exigencia de DMA) */
        t.flags = SPI_TRANS_USE_RXDATA;
        esp_err_t err = spi_device_polling_transmit(s->spi, &t);
        memcpy(data, t.rx_data, len);
        return err;
    }
    t.rx_buffer = data;
    return spi_device_polling_transmit(s->spi, &t);
}

esp_err_t vs_write(vib_sensor_t *s, uint8_t reg, uint8_t value)
{
    spi_transaction_t t = {
        .cmd = reg & 0x7F,
        .length = 8,
        .flags = SPI_TRANS_USE_TXDATA,
        .tx_data = {value},
    };
    return spi_device_polling_transmit(s->spi, &t);
}

esp_err_t vs_read_fifo(vib_sensor_t *s, uint8_t reg, void *dst, size_t len)
{
    uint8_t *p = dst;
    while (len) {
        size_t chunk = len > VS_MAX_CHUNK_BYTES ? VS_MAX_CHUNK_BYTES : len;
        spi_transaction_t t = {
            .cmd = 0x80 | reg,
            .length = chunk * 8,
            .rxlength = chunk * 8,
            .rx_buffer = p,
        };
        /* com DMA e sem polling: a CPU fica livre durante a rajada */
        ESP_RETURN_ON_ERROR(spi_device_transmit(s->spi, &t), TAG, "fifo");
        p += chunk;
        len -= chunk;
    }
    return ESP_OK;
}

static esp_err_t add_device(vib_sensor_t *s, const vib_sensor_config_t *cfg, int clock_hz)
{
    spi_device_interface_config_t dev = {
        .command_bits = 8,
        .mode = 3, /* os tres fabricantes aceitam CPOL=1/CPHA=1 */
        .clock_speed_hz = clock_hz,
        .spics_io_num = cfg->pin_cs,
        .queue_size = 2,
    };
    return spi_bus_add_device(cfg->host, &dev, &s->spi);
}

static vib_sensor_type_t probe(vib_sensor_t *s, uint8_t *id)
{
    uint8_t v = 0;
    vs_read(s, 0x7F, &v, 1);          /* BMI160: borda de subida do CS -> modo SPI */
    vTaskDelay(pdMS_TO_TICKS(2));
    for (int attempt = 0; attempt < 2; attempt++) {
        vs_read(s, 0x00, &v, 1);
        if (v == 0xEA) { *id = v; return VIB_SENSOR_ICM20948; }
        if (v == 0xD1) { *id = v; return VIB_SENSOR_BMI160; }
        vs_read(s, 0x0F, &v, 1);
        if (v == 0x69) { *id = v; return VIB_SENSOR_LSM6DS3; }
        if (v == 0x6A) { *id = v; return VIB_SENSOR_LSM6DS3TRC; }
        /* Ultimo recurso: um ICM-20948 que nao foi desligado num reset do ESP32
         * pode ter ficado em outro banco de registradores. So escrevemos em
         * REG_BANK_SEL (0x7F) depois de nada ter sido reconhecido. */
        vs_write(s, 0x7F, 0x00);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    *id = 0;
    return VIB_SENSOR_NONE;
}

esp_err_t vib_sensor_init(const vib_sensor_config_t *cfg, vib_sensor_t *s)
{
    memset(s, 0, sizeof(*s));
    spi_bus_config_t bus = {
        .sclk_io_num = cfg->pin_sclk,
        .mosi_io_num = cfg->pin_mosi,
        .miso_io_num = cfg->pin_miso,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = VS_MAX_CHUNK_BYTES + 4,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(cfg->host, &bus, SPI_DMA_CH_AUTO), TAG, "bus");
    vTaskDelay(pdMS_TO_TICKS(50)); /* tempo de partida dos sensores apos energizar */

    ESP_RETURN_ON_ERROR(add_device(s, cfg, PROBE_CLOCK_HZ), TAG, "probe dev");
    uint8_t id = 0;
    vib_sensor_type_t found = probe(s, &id);
    spi_bus_remove_device(s->spi);
    s->spi = NULL;

    if (found == VIB_SENSOR_NONE) {
        ESP_LOGE(TAG, "nenhum sensor respondeu (verifique a fiacao SPI)");
        return ESP_ERR_NOT_FOUND;
    }
    if (cfg->forced != VIB_SENSOR_NONE && cfg->forced != found &&
        !(cfg->forced == VIB_SENSOR_LSM6DS3 && found == VIB_SENSOR_LSM6DS3TRC)) {
        ESP_LOGE(TAG, "menuconfig pede %s mas foi detectado %s",
                 vib_sensor_type_name(cfg->forced), vib_sensor_type_name(found));
        return ESP_ERR_INVALID_STATE;
    }

    switch (found) {
    case VIB_SENSOR_ICM20948: s->drv = &vib_drv_icm20948; break;
    case VIB_SENSOR_BMI160:   s->drv = &vib_drv_bmi160; break;
    default:                  s->drv = &vib_drv_lsm6ds3; break;
    }
    s->type = found;
    s->chip_id = id;

    int clk = cfg->max_clock_hz < s->drv->max_clock_hz ? cfg->max_clock_hz : s->drv->max_clock_hz;
    ESP_RETURN_ON_ERROR(add_device(s, cfg, clk), TAG, "dev");
    ESP_RETURN_ON_ERROR(s->drv->configure(s, cfg), TAG, "configure %s", s->drv->name);
    ESP_LOGI(TAG, "%s (ID 0x%02X): ODR %u Hz, banda %u Hz, +/-%u g, SPI %d Hz, %s",
             vib_sensor_type_name(found), id, s->odr_hz, s->bandwidth_hz, s->full_scale_g, clk,
             s->use_irq ? "INT1" : "polling");
    return ESP_OK;
}
