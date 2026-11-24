/*
 * vib_protocol.h — Protocolo binario ESP32 -> PC (versao 1)
 *
 * Todos os campos multi-byte sao little-endian.
 *
 *  Offset Tam  Campo
 *  0      2    sync            0xA5 0x5A
 *  2      1    version         VIB_PROTO_VERSION
 *  3      1    type            VIB_PKT_DATA / VIB_PKT_STATUS
 *  4      1    node_id         1 = MANCAL_1, 2 = MANCAL_2
 *  5      1    flags           VIB_FLAG_*
 *  6      1    full_scale_g    4, 8 ou 16
 *  7      1    sensor_type     0=?, 1=LSM6DS3, 2=LSM6DS3TR-C, 3=BMI160, 4=ICM-20948
 *  8      2    seq             contador de pacotes (wrap 65535)
 *  10     2    payload_len     bytes de payload (DATA: n_amostras * 6)
 *  12     8    timestamp_us    esp_timer_get_time() no inicio da leitura da FIFO
 *                              (~ instante da ULTIMA amostra do bloco)
 *  20     4    first_sample    indice global da 1a amostra do bloco
 *  24     2    odr_hz          taxa nominal do sensor (6660, 1600 ou 4500)
 *  26     N    payload
 *  26+N   2    crc16           CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)
 *                              calculado sobre os bytes [2 .. 26+N-1]
 *                              (tudo exceto o sync e o proprio CRC)
 *
 * Payload DATA:   n x { int16 x, int16 y, int16 z }  (contagens brutas do sensor)
 * Payload STATUS: vib_status_payload_t (abaixo)
 */
#pragma once
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VIB_SYNC0 0xA5
#define VIB_SYNC1 0x5A
#define VIB_PROTO_VERSION 1

#define VIB_PKT_DATA   1
#define VIB_PKT_STATUS 2

#define VIB_FLAG_FIFO_OVERRUN 0x01 /* FIFO do sensor transbordou: houve perda */
#define VIB_FLAG_BLOCK_DROP   0x02 /* sem buffer livre no MCU: bloco anterior descartado */

#define VIB_HEADER_SIZE 26
#define VIB_CRC_SIZE 2
#define VIB_MAX_PAYLOAD 8192

typedef struct __attribute__((packed)) {
    uint8_t  sync[2];
    uint8_t  version;
    uint8_t  type;
    uint8_t  node_id;
    uint8_t  flags;
    uint8_t  full_scale_g;
    uint8_t  sensor_type;
    uint16_t seq;
    uint16_t payload_len;
    uint64_t timestamp_us;
    uint32_t first_sample;
    uint16_t odr_hz;
} vib_header_t;

_Static_assert(sizeof(vib_header_t) == VIB_HEADER_SIZE, "header size");

typedef struct __attribute__((packed)) {
    uint32_t fifo_overruns;   /* vezes que a FIFO do sensor transbordou */
    uint32_t block_drops;     /* blocos descartados por falta de buffer/fila */
    uint32_t packets_sent;    /* pacotes DATA enviados */
    uint32_t uptime_ms;
    uint16_t wtm_samples;     /* watermark configurado */
    uint16_t max_fifo_words;  /* maior nivel de FIFO observado (palavras de 16 bits) */
    uint8_t  who_am_i;        /* ID lido do chip (0 = nenhum sensor respondeu) */
    uint8_t  sensor_type;     /* igual ao campo do cabecalho */
    uint16_t bandwidth_hz;    /* banda de -3 dB do acelerometro na configuracao usada */
} vib_status_payload_t;

_Static_assert(sizeof(vib_status_payload_t) == 24, "status size");

uint16_t vib_crc16(uint16_t crc, const uint8_t *data, size_t len);

#ifdef __cplusplus
}
#endif
