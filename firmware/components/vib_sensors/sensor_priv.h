/* sensor_priv.h — acesso SPI comum aos drivers (bit 7 = leitura, 7 bits de endereco) */
#pragma once
#include "vib_sensor.h"

/* Maior transacao SPI unica: multiplo de 4 (DMA de RX do ESP32) e de 6 (1 amostra XYZ) */
#define VS_MAX_CHUNK_BYTES 4092

esp_err_t vs_read(vib_sensor_t *s, uint8_t reg, uint8_t *data, size_t len);
esp_err_t vs_write(vib_sensor_t *s, uint8_t reg, uint8_t value);
/* Leitura longa via DMA (dst alinhado a 4, len multiplo de 4), dividida em
 * transacoes de ate VS_MAX_CHUNK_BYTES que sempre recomecam em 'reg'
 * (valido para registradores de FIFO). */
esp_err_t vs_read_fifo(vib_sensor_t *s, uint8_t reg, void *dst, size_t len);

extern const vib_sensor_driver_t vib_drv_lsm6ds3;
extern const vib_sensor_driver_t vib_drv_bmi160;
extern const vib_sensor_driver_t vib_drv_icm20948;
