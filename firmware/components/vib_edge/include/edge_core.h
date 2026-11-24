/*
 * edge_core.h — Processamento de vibracao "na borda" (no proprio ESP32).
 *
 * C puro, sem dependencias do ESP-IDF: o mesmo arquivo e compilado no ESP32
 * e no PC (testes automaticos comparam com o NumPy, e o simulador do vibmon
 * usa exatamente este codigo).
 *
 * Por janela de N amostras (N = 1024 ou 2048):
 *   - remove o DC, aplica a janela (Hann/Hamming/retangular);
 *   - FFT real de N pontos (via FFT complexa de N/2 pontos);
 *   - por eixo: RMS, pico a pico, curtose, frequencia e amplitude dominantes;
 *   - RMS resultante em ate 4 bandas configuraveis;
 *   - desvio em dB por banda de 1/3 de oitava em relacao a baseline, com a
 *     potencia suavizada (media exponencial, EDGE_EMA_ALPHA);
 *   - regras de alarme com 'hold' e estado OK / ALERTA / PARADA (com trava).
 *
 * As formulas sao as mesmas de pc/vibmon/dsp.py e pc/vibmon/baseline.py.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include "vib_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

#define EDGE_MAX_N 2048

/* Suavizacao (media exponencial) da potencia por banda antes de comparar com a
 * baseline. Com N = 2048 as bandas estreitas tem poucas raias e oscilam mais que
 * no PC (N = 4096), por isso a memoria aqui e maior: ~1,5 s a ~6,5 analises/s. */
#define EDGE_EMA_ALPHA 0.1f

typedef struct {
    int n;
    int window_type;
    float s1, s2;            /* soma da janela e dos quadrados */
    float *win;              /* n */
    float *tw;               /* n/2 pares (cos, sin) de W_N^k */
    float *z;                /* n floats = n/2 complexos */
    float *x;                /* n (sinal de um eixo, sem DC) */
    float *pw;               /* n/2 + 1 potencias (g^2 por raia) */
    /* estado entre janelas */
    float ema[3][VIB_EDGE_MAX_BASE_BANDS];
    int ema_valid;
    uint16_t hold_cnt[VIB_RULE_COUNT];
    uint32_t latched;        /* regras de PARADA travadas */
    uint8_t trip_rule;
    uint32_t window_seq;
} edge_core_t;

/* Bytes de memoria de trabalho necessarios para n amostras. */
size_t edge_core_mem_size(int n);

/* n deve ser potencia de 2 entre 64 e EDGE_MAX_N. mem: edge_core_mem_size(n) bytes,
 * alinhada a 4. Retorna 0 se ok. */
int edge_core_init(edge_core_t *c, int n, int window_type, void *mem);

/* Analisa uma janela. xyz: n amostras intercaladas X,Y,Z (contagens int16);
 * g_per_lsb: full_scale_g / 32768; fs: taxa (Hz). bl pode ser NULL. */
void edge_core_analyze(edge_core_t *c, const int16_t *xyz, float g_per_lsb, float fs,
                       const vib_edge_config_t *cfg, const vib_edge_baseline_t *bl,
                       vib_edge_metrics_t *out);

/* Rearma a parada travada (zera contadores de hold). */
void edge_core_reset_alarm(edge_core_t *c);

/* Esquece a media exponencial da baseline (use ao trocar de baseline). */
void edge_core_reset_baseline(edge_core_t *c);

/* Configuracao padrao: analise ligada, N=2048 Hann, todos os limites desligados,
 * buzzer habilitado, saida de parada DESABILITADA. */
void edge_default_config(vib_edge_config_t *cfg);

/* Valida a configuracao recebida do PC. Retorna 0 se ok. */
int edge_validate_config(const vib_edge_config_t *cfg);

/* sizeof(edge_core_t), para quem usa a biblioteca via FFI (testes/simulador no PC). */
size_t edge_core_struct_size(void);

#ifdef __cplusplus
}
#endif
