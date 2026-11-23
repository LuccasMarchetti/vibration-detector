/*
 * lsm6ds3.c — Driver SPI do LSM6DS3 / LSM6DS3TR-C (somente acelerometro + FIFO)
 *
 * Notas de hardware:
 *  - SPI modo 3 (CPOL=1, CPHA=1), ate 10 MHz.
 *  - Bit 7 do primeiro byte = 1 para leitura. Com IF_INC=1 o endereco
 *    auto-incrementa; ao ler a FIFO (0x3E/0x3F) o endereco "volta" para 0x3E,
 *    permitindo ler a FIFO inteira em uma unica rajada.
 */
#include <string.h>
#include "lsm6ds3.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "lsm6ds3";

/* Maior transacao SPI unica (bytes de dados). Leituras maiores sao divididas;
 * cada transacao reinicia no endereco 0x3E, o que e valido para a FIFO.
 * 4092 = multiplo de 4 (exigencia do DMA de RX do ESP32) e de 6 (1 amostra XYZ). */
#define LSM_MAX_CHUNK_BYTES 4092
#define LSM_ODR_6660HZ 0x0A /* codigo 1010 para ODR_XL e ODR_FIFO */

static uint8_t fs_code(lsm6ds3_fs_t fs)
{
    /* FS_XL: 00=2g 01=16g 10=4g 11=8g */
    switch (fs) {
    case LSM6DS3_FS_4G:  return 0x2;
    case LSM6DS3_FS_16G: return 0x1;
    case LSM6DS3_FS_8G:
    default:             return 0x3;
    }
}

esp_err_t lsm6ds3_read_reg(lsm6ds3_t *dev, uint8_t reg, uint8_t *data, size_t len)
{
    spi_transaction_t t = {
        .cmd = 0x80 | reg,
        .length = len * 8,
        .rxlength = len * 8,
    };
    if (len <= 4) {
        /* Leituras curtas usam o buffer interno da transacao (sem exigencia de DMA) */
        t.flags = SPI_TRANS_USE_RXDATA;
        esp_err_t err = spi_device_polling_transmit(dev->spi, &t);
        memcpy(data, t.rx_data, len);
        return err;
    }
    t.rx_buffer = data;
    return spi_device_polling_transmit(dev->spi, &t);
}

esp_err_t lsm6ds3_write_reg(lsm6ds3_t *dev, uint8_t reg, uint8_t value)
{
    spi_transaction_t t = {
        .cmd = reg & 0x7F,
        .length = 8,
        .flags = SPI_TRANS_USE_TXDATA,
        .tx_data = {value},
    };
    return spi_device_polling_transmit(dev->spi, &t);
}

esp_err_t lsm6ds3_fifo_status(lsm6ds3_t *dev, lsm6ds3_fifo_status_t *st)
{
    uint8_t s[4];
    ESP_RETURN_ON_ERROR(lsm6ds3_read_reg(dev, LSM6DS3_REG_FIFO_STATUS1, s, 4), TAG, "status");
    uint16_t mask_hi = (dev->who_am_i == LSM6DS3_WHO_AM_I_DS3) ? 0x0F : 0x07;
    st->words = (uint16_t)(s[0] | ((s[1] & mask_hi) << 8));
    st->watermark = s[1] & LSM6DS3_FIFO_ST2_FTH;
    st->overrun = s[1] & LSM6DS3_FIFO_ST2_OVR;
    st->empty = s[1] & LSM6DS3_FIFO_ST2_EMPTY;
    st->pattern = (uint16_t)(s[2] | ((s[3] & 0x03) << 8));
    if (st->empty) {
        st->words = 0;
    }
    return ESP_OK;
}

esp_err_t lsm6ds3_fifo_read_words(lsm6ds3_t *dev, int16_t *dst, size_t n_words)
{
    uint8_t *p = (uint8_t *)dst;
    size_t remaining = n_words * 2;
    while (remaining) {
        size_t chunk = remaining > LSM_MAX_CHUNK_BYTES ? LSM_MAX_CHUNK_BYTES : remaining;
        spi_transaction_t t = {
            .cmd = 0x80 | LSM6DS3_REG_FIFO_DATA_OUT_L,
            .length = chunk * 8,
            .rxlength = chunk * 8,
            .rx_buffer = p,
        };
        /* Transacao com DMA (sem polling) — a CPU fica livre durante a rajada */
        ESP_RETURN_ON_ERROR(spi_device_transmit(dev->spi, &t), TAG, "fifo read");
        p += chunk;
        remaining -= chunk;
    }
    return ESP_OK;
}

esp_err_t lsm6ds3_fifo_restart(lsm6ds3_t *dev)
{
    /* FIFO_MODE = 000 (bypass) limpa a FIFO; depois volta para 110 (continuo) */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL5, 0x00), TAG, "bypass");
    return lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL5, (LSM_ODR_6660HZ << 3) | 0x06);
}

esp_err_t lsm6ds3_init(lsm6ds3_t *dev, const lsm6ds3_config_t *cfg)
{
    memset(dev, 0, sizeof(*dev));

    spi_bus_config_t bus = {
        .sclk_io_num = cfg->pin_sclk,
        .mosi_io_num = cfg->pin_mosi,
        .miso_io_num = cfg->pin_miso,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = LSM_MAX_CHUNK_BYTES + 4,
    };
    ESP_RETURN_ON_ERROR(spi_bus_initialize(cfg->host, &bus, SPI_DMA_CH_AUTO), TAG, "bus");

    spi_device_interface_config_t devcfg = {
        .command_bits = 8,
        .mode = 3,
        .clock_speed_hz = cfg->clock_hz,
        .spics_io_num = cfg->pin_cs,
        .queue_size = 2,
    };
    ESP_RETURN_ON_ERROR(spi_bus_add_device(cfg->host, &devcfg, &dev->spi), TAG, "device");

    /* Primeira leitura com o chip ainda possivelmente em modo I2C: le duas vezes */
    uint8_t who = 0;
    lsm6ds3_read_reg(dev, LSM6DS3_REG_WHO_AM_I, &who, 1);
    ESP_RETURN_ON_ERROR(lsm6ds3_read_reg(dev, LSM6DS3_REG_WHO_AM_I, &who, 1), TAG, "who");
    if (who != LSM6DS3_WHO_AM_I_DS3 && who != LSM6DS3_WHO_AM_I_DS3TRC) {
        ESP_LOGE(TAG, "WHO_AM_I inesperado: 0x%02X (verifique fiacao SPI)", who);
        return ESP_ERR_NOT_FOUND;
    }
    dev->who_am_i = who;
    dev->fifo_words_capacity = (who == LSM6DS3_WHO_AM_I_DS3) ? 4096 : 2048;

    /* Reset de software */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_CTRL3_C, LSM6DS3_CTRL3_SW_RESET), TAG, "rst");
    vTaskDelay(pdMS_TO_TICKS(20));

    /* BDU + auto-incremento; I2C desabilitado (somente SPI) */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_CTRL3_C,
                                          LSM6DS3_CTRL3_BDU | LSM6DS3_CTRL3_IF_INC), TAG, "c3");
    uint8_t ctrl4 = LSM6DS3_CTRL4_I2C_DIS;
    if (who == LSM6DS3_WHO_AM_I_DS3) {
        /* LSM6DS3 original: banda analogica definida por BW_XL (00 = 400 Hz, a maxima) */
        ctrl4 |= LSM6DS3_CTRL4_BW_SCAL;
    }
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_CTRL4_C, ctrl4), TAG, "c4");

    /* Giroscopio desligado */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_CTRL2_G, 0x00), TAG, "g");

    /* FIFO: bypass enquanto configura */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL5, 0x00), TAG, "f5");

    /* Watermark em palavras de 16 bits (3 por amostra XYZ) */
    uint16_t wtm = cfg->watermark_samples;
    uint16_t max_wtm = (uint16_t)((dev->fifo_words_capacity / 3) - 64); /* margem */
    if (wtm > max_wtm) {
        ESP_LOGW(TAG, "Watermark %u > capacidade do chip; usando %u", wtm, max_wtm);
        wtm = max_wtm;
    }
    dev->watermark_samples = wtm;
    uint16_t fth_words = (uint16_t)(wtm * 3);
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL1, fth_words & 0xFF), TAG, "f1");
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL2, (fth_words >> 8) & 0x0F), TAG, "f2");
    /* Acelerometro na FIFO sem decimacao (001), giroscopio fora (000) */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL3, 0x01), TAG, "f3");
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL4, 0x00), TAG, "f4");

    /* INT1 sinaliza watermark da FIFO (ativo em nivel alto, push-pull) */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_INT1_CTRL, LSM6DS3_INT1_FTH), TAG, "int1");

    /* Acelerometro: ODR 6,66 kHz, fundo de escala, BW_XL = 00 */
    dev->full_scale = cfg->full_scale;
    uint8_t ctrl1 = (uint8_t)((LSM_ODR_6660HZ << 4) | (fs_code(cfg->full_scale) << 2));
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_CTRL1_XL, ctrl1), TAG, "c1");

    /* FIFO: ODR 6,66 kHz, modo continuo (110) */
    ESP_RETURN_ON_ERROR(lsm6ds3_write_reg(dev, LSM6DS3_REG_FIFO_CTRL5,
                                          (LSM_ODR_6660HZ << 3) | 0x06), TAG, "f5");

    ESP_LOGI(TAG, "OK: WHO_AM_I=0x%02X FS=+/-%dg WTM=%u amostras", who, cfg->full_scale, wtm);
    return ESP_OK;
}
