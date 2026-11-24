/*
 * vib_protocol.h — Protocolo binario ESP32 <-> PC (versao 1)
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
 * Payload DATA:    n x { int16 x, int16 y, int16 z }  (contagens brutas do sensor)
 * Payload STATUS:  vib_status_payload_t
 * Payload METRICS: vib_edge_metrics_t  (resultado da FFT/alarmes feitos no ESP32)
 * Payload CONFIG:  vib_edge_config_t + uint8 n_bands da baseline (resposta a GET_CONFIG)
 * Payload ACK:     vib_ack_payload_t
 *
 * Comandos PC -> ESP32 usam o MESMO cabecalho/CRC, com type >= 0x80.
 * node_id do comando: 1 ou 2 = so esse mancal executa; 0 = qualquer um.
 * Campos timestamp_us/first_sample/odr_hz/full_scale_g/sensor_type: 0.
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

#define VIB_PKT_DATA    1
#define VIB_PKT_STATUS  2
#define VIB_PKT_METRICS 3
#define VIB_PKT_CONFIG  4
#define VIB_PKT_ACK     5

/* Comandos PC -> ESP32 */
#define VIB_CMD_SET_CONFIG   0x81 /* payload: vib_edge_config_t (grava na NVS) */
#define VIB_CMD_SET_BASELINE 0x82 /* payload: vib_edge_baseline_t (grava na NVS) */
#define VIB_CMD_CLEAR_BASE   0x83 /* sem payload */
#define VIB_CMD_RESET_ALARM  0x84 /* sem payload: rearma a parada travada e silencia */
#define VIB_CMD_GET_CONFIG   0x85 /* sem payload: responde com VIB_PKT_CONFIG */
#define VIB_CMD_TEST_OUTPUT  0x86 /* payload: uint8 1 = buzzer 1 s, 2 = saida de parada 2 s */

#define VIB_ACK_OK          0
#define VIB_ACK_BAD_LENGTH  1
#define VIB_ACK_BAD_VALUE   2
#define VIB_ACK_UNKNOWN     3
#define VIB_ACK_STORAGE     4
#define VIB_ACK_UNAVAILABLE 5

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

/* ------------------------------------------------------------------ borda (FFT no ESP32) */

#define VIB_EDGE_CONFIG_VERSION 1
#define VIB_EDGE_MAX_BANDS 4      /* bandas de RMS configuraveis */
#define VIB_EDGE_MAX_BASE_BANDS 32 /* bandas de 1/3 de oitava da baseline */

/* bits de vib_edge_config_t.outputs */
#define VIB_OUT_BUZZER   0x01 /* buzzer toca em ALERTA (intermitente) e PARADA (continuo) */
#define VIB_OUT_TRIP     0x02 /* saida de parada do motor habilitada */
#define VIB_OUT_LATCH    0x04 /* PARADA fica travada ate rearme (botao BOOT ou comando) */

typedef struct __attribute__((packed)) {
    float f_lo, f_hi;         /* Hz; f_hi = 0 -> banda desativada */
    float warn, trip;         /* RMS resultante (g) na banda; 0 = regra desligada */
} vib_edge_band_t;

/* Todos os limites em g (aceleracao) ou dB; 0 = regra desligada. */
typedef struct __attribute__((packed)) {
    uint8_t  version;         /* VIB_EDGE_CONFIG_VERSION */
    uint8_t  enabled;         /* 1 = calcula e envia METRICS */
    uint16_t n_window;        /* 1024 ou 2048 amostras */
    uint8_t  window_type;     /* 0 = Hann, 1 = Hamming, 2 = retangular */
    uint8_t  hold;            /* janelas seguidas acima do limite para disparar (>=1) */
    uint8_t  outputs;         /* VIB_OUT_* */
    uint8_t  reserved;
    float    f_min_hz;        /* ignora abaixo disso na frequencia dominante */
    float    overall_rms_warn, overall_rms_trip;   /* RMS global sqrt(x^2+y^2+z^2) */
    float    kurtosis_warn, kurtosis_trip;         /* maior curtose entre os eixos */
    float    baseline_db_warn, baseline_db_trip;   /* maior aumento de banda vs baseline */
    float    baseline_floor_db;                    /* bandas mais fracas sao ignoradas */
    vib_edge_band_t band[VIB_EDGE_MAX_BANDS];
} vib_edge_config_t;

_Static_assert(sizeof(vib_edge_config_t) == 104, "edge config size");

/* A media por banda depende do tamanho/tipo da janela (vazamento espectral perto
 * de tons fortes): a baseline so e usada se n_window/window_type baterem com a
 * configuracao atual; senao o ESP32 sinaliza VIB_MF_BASE_MISMATCH. */
typedef struct __attribute__((packed)) {
    uint8_t  n_bands;
    uint8_t  window_type;     /* janela usada para calcular mean_db */
    uint16_t n_window;
    float   f_lo[VIB_EDGE_MAX_BASE_BANDS];
    float   f_hi[VIB_EDGE_MAX_BASE_BANDS];
    float   mean_db[3][VIB_EDGE_MAX_BASE_BANDS];  /* dB re 1 g^2, por eixo X/Y/Z */
} vib_edge_baseline_t;

_Static_assert(sizeof(vib_edge_baseline_t) == 4 + 5 * 4 * VIB_EDGE_MAX_BASE_BANDS, "baseline size");

/* bits de vib_edge_metrics_t.active (regra acima do limite pelo tempo de 'hold') */
#define VIB_RULE_OVERALL_WARN 0
#define VIB_RULE_OVERALL_TRIP 1
#define VIB_RULE_KURT_WARN    2
#define VIB_RULE_KURT_TRIP    3
#define VIB_RULE_BAND_WARN0   4   /* 4..7  */
#define VIB_RULE_BAND_TRIP0   8   /* 8..11 */
#define VIB_RULE_BASE_WARN    12
#define VIB_RULE_BASE_TRIP    13
#define VIB_RULE_COUNT        14

#define VIB_STATE_OK   0
#define VIB_STATE_WARN 1
#define VIB_STATE_TRIP 2

/* bits de vib_edge_metrics_t.flags */
#define VIB_MF_LATCHED    0x01 /* parada travada aguardando rearme */
#define VIB_MF_BASELINE   0x02 /* ha baseline carregada */
#define VIB_MF_SILENCED   0x04 /* buzzer silenciado pelo botao/comando */
#define VIB_MF_TRIP_OUT   0x08 /* saida de parada ATIVA (motor deve parar) */
#define VIB_MF_BASE_MISMATCH 0x10 /* ha baseline, mas feita para outra janela: ignorada */

typedef struct __attribute__((packed)) {
    uint32_t window_seq;
    uint16_t n_window;
    uint8_t  state;           /* VIB_STATE_* */
    uint8_t  flags;           /* VIB_MF_* */
    float    fs_hz;           /* taxa real estimada pelo ESP32 */
    float    rms[3], p2p[3], kurtosis[3], dom_hz[3], dom_amp[3]; /* g, Hz */
    float    overall_rms;
    float    band_rms[VIB_EDGE_MAX_BANDS];
    float    base_delta_db;   /* maior aumento vs baseline (0 sem baseline) */
    float    base_band_hz;    /* centro da banda com esse aumento */
    uint8_t  base_axis;       /* 0..2 */
    uint8_t  trip_rule;       /* regra que causou a parada (VIB_RULE_*), 0xFF = nenhuma */
    uint16_t reserved;
    uint32_t active;          /* bits VIB_RULE_* ativos nesta janela */
} vib_edge_metrics_t;

_Static_assert(sizeof(vib_edge_metrics_t) == 108, "metrics size");

typedef struct __attribute__((packed)) {
    uint8_t  cmd;
    uint8_t  status;          /* VIB_ACK_* */
    uint16_t cmd_seq;
} vib_ack_payload_t;

/* ------------------------------------------------------------------ funcoes */

uint16_t vib_crc16(uint16_t crc, const uint8_t *data, size_t len);

/* Parser incremental (usado pelo ESP32 para receber comandos do PC). */
#define VIB_RX_MAX_PAYLOAD 1024
typedef struct {
    uint8_t buf[VIB_HEADER_SIZE + VIB_RX_MAX_PAYLOAD + VIB_CRC_SIZE];
    size_t len;
    uint32_t crc_errors;
} vib_rx_parser_t;

typedef void (*vib_rx_cb_t)(const vib_header_t *hdr, const uint8_t *payload, void *ctx);

void vib_rx_init(vib_rx_parser_t *p);
/* Alimenta bytes recebidos; chama cb para cada pacote valido (CRC ok). */
void vib_rx_feed(vib_rx_parser_t *p, const uint8_t *data, size_t len, vib_rx_cb_t cb, void *ctx);

/* Monta um pacote completo em out (tamanho >= 28 + len). Retorna o tamanho total. */
size_t vib_build_packet(uint8_t *out, const vib_header_t *hdr_template, const void *payload, uint16_t len);

#ifdef __cplusplus
}
#endif
