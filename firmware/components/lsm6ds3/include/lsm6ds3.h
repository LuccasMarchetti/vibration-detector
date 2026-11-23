/*
 * lsm6ds3.h — Driver SPI minimo do acelerometro LSM6DS3 / LSM6DS3TR-C
 * focado em aquisicao de alta taxa via FIFO + interrupcao de watermark.
 *
 * Somente o acelerometro e usado (giroscopio desligado).
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"
#include "driver/spi_master.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- Registradores usados ---- */
#define LSM6DS3_REG_FIFO_CTRL1     0x06 /* FTH[7:0]                                  */
#define LSM6DS3_REG_FIFO_CTRL2     0x07 /* FTH[11:8] (bits 3:0)                      */
#define LSM6DS3_REG_FIFO_CTRL3     0x08 /* DEC_FIFO_GYRO[5:3] DEC_FIFO_XL[2:0]        */
#define LSM6DS3_REG_FIFO_CTRL4     0x09
#define LSM6DS3_REG_FIFO_CTRL5     0x0A /* ODR_FIFO[6:3] FIFO_MODE[2:0]               */
#define LSM6DS3_REG_INT1_CTRL      0x0D
#define LSM6DS3_REG_WHO_AM_I       0x0F
#define LSM6DS3_REG_CTRL1_XL       0x10 /* ODR_XL[7:4] FS_XL[3:2] BW_XL[1:0]          */
#define LSM6DS3_REG_CTRL2_G        0x11
#define LSM6DS3_REG_CTRL3_C        0x12
#define LSM6DS3_REG_CTRL4_C        0x13
#define LSM6DS3_REG_FIFO_STATUS1   0x3A /* DIFF_FIFO[7:0]                             */
#define LSM6DS3_REG_FIFO_STATUS2   0x3B /* FTH OVER_RUN FULL EMPTY DIFF_FIFO[11:8]   */
#define LSM6DS3_REG_FIFO_STATUS3   0x3C /* FIFO_PATTERN[7:0]                          */
#define LSM6DS3_REG_FIFO_STATUS4   0x3D /* FIFO_PATTERN[9:8]                          */
#define LSM6DS3_REG_FIFO_DATA_OUT_L 0x3E

#define LSM6DS3_WHO_AM_I_DS3   0x69 /* LSM6DS3 original  — FIFO 8 KB (4096 palavras) */
#define LSM6DS3_WHO_AM_I_DS3TRC 0x6A /* LSM6DS3TR-C        — FIFO 4 KB (2048 palavras) */

/* Bits */
#define LSM6DS3_CTRL3_SW_RESET   0x01
#define LSM6DS3_CTRL3_IF_INC     0x04
#define LSM6DS3_CTRL3_BDU        0x40
#define LSM6DS3_CTRL4_I2C_DIS    0x04
#define LSM6DS3_CTRL4_BW_SCAL    0x80 /* XL_BW_SCAL_ODR: NAO usar a 6,66 kHz (limitaria a 400 Hz) */
#define LSM6DS3_INT1_FTH         0x08
#define LSM6DS3_FIFO_ST2_FTH     0x80
#define LSM6DS3_FIFO_ST2_OVR     0x40
#define LSM6DS3_FIFO_ST2_FULL    0x20
#define LSM6DS3_FIFO_ST2_EMPTY   0x10

typedef enum {
    LSM6DS3_FS_4G = 4,
    LSM6DS3_FS_8G = 8,
    LSM6DS3_FS_16G = 16,
} lsm6ds3_fs_t;

typedef struct {
    spi_host_device_t host;
    int pin_sclk, pin_mosi, pin_miso, pin_cs;
    int clock_hz;               /* 8..10 MHz */
    lsm6ds3_fs_t full_scale;
    uint16_t watermark_samples; /* amostras XYZ (cada uma = 3 palavras de 16 bits) */
} lsm6ds3_config_t;

typedef struct {
    spi_device_handle_t spi;
    uint8_t who_am_i;
    uint16_t fifo_words_capacity; /* 4096 (DS3) ou 2048 (TR-C) */
    uint16_t watermark_samples;
    lsm6ds3_fs_t full_scale;
} lsm6ds3_t;

typedef struct {
    uint16_t words;    /* palavras de 16 bits nao lidas na FIFO */
    uint16_t pattern;  /* proxima palavra a sair: 0 = X, 1 = Y, 2 = Z */
    bool overrun;
    bool watermark;
    bool empty;
} lsm6ds3_fifo_status_t;

/* Inicializa o barramento SPI, verifica WHO_AM_I e configura o sensor:
 * ODR 6,66 kHz, fundo de escala, FIFO continua com watermark, INT1 = FTH. */
esp_err_t lsm6ds3_init(lsm6ds3_t *dev, const lsm6ds3_config_t *cfg);

esp_err_t lsm6ds3_read_reg(lsm6ds3_t *dev, uint8_t reg, uint8_t *data, size_t len);
esp_err_t lsm6ds3_write_reg(lsm6ds3_t *dev, uint8_t reg, uint8_t value);

esp_err_t lsm6ds3_fifo_status(lsm6ds3_t *dev, lsm6ds3_fifo_status_t *st);

/* Le 'n_words' palavras de 16 bits da FIFO em rajada (SPI burst).
 * n_words*2 deve ser multiplo de 4 (use numero par de palavras) para o DMA.
 * 'dst' deve ser alocado com MALLOC_CAP_DMA. */
esp_err_t lsm6ds3_fifo_read_words(lsm6ds3_t *dev, int16_t *dst, size_t n_words);

/* Reinicia a FIFO (bypass -> continuo). Usado apos overrun. */
esp_err_t lsm6ds3_fifo_restart(lsm6ds3_t *dev);

#ifdef __cplusplus
}
#endif
