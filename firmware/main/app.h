/* app.h — tipos e estado compartilhados entre as tasks do no de vibracao */
#pragma once
#include <stdint.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

/* Maior FIFO suportada: LSM6DS3 original, 4096 palavras = 1365 amostras XYZ */
#define VIB_BLOCK_MAX_SAMPLES 1364  /* par (multiplo de 2) — ver regra de DMA */
#define VIB_POOL_BLOCKS 6           /* ~6 x 8 KB = 49 KB de RAM DMA */

typedef struct {
    uint64_t timestamp_us;   /* esp_timer no inicio da leitura da FIFO */
    uint32_t first_sample;   /* indice global da 1a amostra */
    uint16_t n_samples;
    uint8_t flags;           /* VIB_FLAG_* */
    uint8_t _pad;
    int16_t data[VIB_BLOCK_MAX_SAMPLES * 3] __attribute__((aligned(4)));
} vib_block_t;

typedef struct {
    volatile uint32_t fifo_overruns;
    volatile uint32_t block_drops;
    volatile uint32_t packets_sent;
    volatile uint16_t max_fifo_words;
    uint16_t wtm_samples;
    uint8_t who_am_i;        /* 0 = sensor nao encontrado */
    uint8_t node_id;
    uint8_t full_scale_g;
    uint8_t sensor_type;     /* vib_sensor_type_t */
    uint16_t odr_hz;
    uint16_t bandwidth_hz;
} vib_stats_t;

extern vib_stats_t g_stats;

/* Fila de blocos livres e fila de blocos prontos para envio (ponteiros vib_block_t*) */
extern QueueHandle_t g_free_q;
extern QueueHandle_t g_tx_q;

/* node_id.c */
uint8_t node_id_detect(void);

/* acquisition.c — Core 1 */
esp_err_t acquisition_start(void);

/* usb_link.c — Core 0 */
esp_err_t usb_link_start(void);
