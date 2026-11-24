/*
 * vib_sensor.h — Camada generica de acelerometro SPI com FIFO.
 *
 * Sensores suportados (detectados automaticamente pelo registrador de ID):
 *
 *   Sensor        ID (reg)      ODR usado   Banda -3 dB  FIFO   Leitura
 *   LSM6DS3       0x69 (0x0F)   6660 Hz     3160 Hz      8 KB   INT1 = watermark
 *   LSM6DS3TR-C   0x6A (0x0F)   6660 Hz     ~1500 Hz*    4 KB   INT1 = watermark
 *   BMI160        0xD1 (0x00)   1600 Hz      684 Hz      1 KB   INT1 = watermark
 *   ICM-20948     0xEA (0x00)   4500 Hz     1209 Hz      4 KB   polling (5 ms)
 *
 * Fontes: AN4650 (LSM6DS3, tab. 7-9); BMI160 DS rev 0.9 (tab. 12);
 *         ICM-20948 DS-000189 v1.6 (tab. 18). *TR-C: valor nominal nao conferido.
 *
 * Todas as leituras devolvem amostras int16 little-endian X,Y,Z em contagens
 * tais que  g = contagem * full_scale_g / 32768  (vale para os 4 sensores).
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"
#include "driver/spi_master.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    VIB_SENSOR_NONE = 0,       /* nao detectado / automatico */
    VIB_SENSOR_LSM6DS3 = 1,
    VIB_SENSOR_LSM6DS3TRC = 2,
    VIB_SENSOR_BMI160 = 3,
    VIB_SENSOR_ICM20948 = 4,
} vib_sensor_type_t;

typedef struct {
    spi_host_device_t host;
    int pin_sclk, pin_mosi, pin_miso, pin_cs;
    int max_clock_hz;              /* limitado ainda pelo maximo de cada sensor */
    uint8_t full_scale_g;          /* 4, 8 ou 16 */
    uint16_t watermark_samples;    /* desejado; cada driver limita a sua FIFO */
    vib_sensor_type_t forced;      /* VIB_SENSOR_NONE = deteccao automatica */
} vib_sensor_config_t;

typedef struct vib_sensor vib_sensor_t;

typedef struct {
    const char *name;
    int max_clock_hz;
    esp_err_t (*configure)(vib_sensor_t *s, const vib_sensor_config_t *cfg);
    /* Le ate 'max_samples' amostras XYZ disponiveis na FIFO para 'dst'
     * (buffer DMA, alinhado a 4). Devolve em *n um numero PAR de amostras.
     * *overrun = true se a FIFO transbordou (o driver ja a reiniciou). */
    esp_err_t (*fifo_read)(vib_sensor_t *s, int16_t *dst, uint32_t max_samples,
                           uint32_t *n, bool *overrun);
} vib_sensor_driver_t;

struct vib_sensor {
    const vib_sensor_driver_t *drv;
    spi_device_handle_t spi;
    vib_sensor_type_t type;
    uint8_t chip_id;
    uint16_t odr_hz;
    uint16_t bandwidth_hz;
    uint8_t full_scale_g;
    uint16_t watermark_samples;
    uint16_t fifo_capacity_samples;
    bool use_irq;                  /* true: INT1 = watermark; false: polling */
    uint16_t poll_ms;
};

/* Inicializa o barramento SPI, detecta o sensor e o configura. */
esp_err_t vib_sensor_init(const vib_sensor_config_t *cfg, vib_sensor_t *s);

static inline esp_err_t vib_sensor_fifo_read(vib_sensor_t *s, int16_t *dst, uint32_t max_samples,
                                             uint32_t *n, bool *overrun)
{
    return s->drv->fifo_read(s, dst, max_samples, n, overrun);
}

const char *vib_sensor_type_name(vib_sensor_type_t t);

#ifdef __cplusplus
}
#endif
