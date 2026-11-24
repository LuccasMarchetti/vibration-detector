/*
 * edge_core.c — FFT, metricas e alarmes de vibracao (C puro; ver edge_core.h).
 */
#include <math.h>
#include <string.h>
#include "edge_core.h"

#define PI_F 3.14159265358979f

size_t edge_core_mem_size(int n)
{
    /* win[n] + tw[n] + z[n] + x[n] + pw[n/2+1] */
    return (size_t)(4 * n + n / 2 + 1) * sizeof(float);
}

static int is_pow2(int n)
{
    return n > 0 && (n & (n - 1)) == 0;
}

int edge_core_init(edge_core_t *c, int n, int window_type, void *mem)
{
    if (!is_pow2(n) || n < 64 || n > EDGE_MAX_N || window_type < 0 || window_type > 2 || !mem) {
        return -1;
    }
    memset(c, 0, sizeof(*c));
    c->n = n;
    c->window_type = window_type;
    float *m = (float *)mem;
    c->win = m;
    c->tw = m + n;
    c->z = m + 2 * n;
    c->x = m + 3 * n;
    c->pw = m + 4 * n;

    /* Janelas simetricas, iguais a numpy.hanning / numpy.hamming */
    c->s1 = c->s2 = 0.0f;
    for (int k = 0; k < n; k++) {
        float ph = 2.0f * PI_F * (float)k / (float)(n - 1);
        float w = window_type == 0 ? 0.5f - 0.5f * cosf(ph)
                : window_type == 1 ? 0.54f - 0.46f * cosf(ph)
                : 1.0f;
        c->win[k] = w;
        c->s1 += w;
        c->s2 += w * w;
    }
    /* W_N^k = cos - i sin, k < n/2 */
    for (int k = 0; k < n / 2; k++) {
        float ph = 2.0f * PI_F * (float)k / (float)n;
        c->tw[2 * k] = cosf(ph);
        c->tw[2 * k + 1] = sinf(ph);
    }
    c->trip_rule = 0xFF;
    return 0;
}

void edge_core_reset_alarm(edge_core_t *c)
{
    c->latched = 0;
    c->trip_rule = 0xFF;
    memset(c->hold_cnt, 0, sizeof(c->hold_cnt));
}

void edge_core_reset_baseline(edge_core_t *c)
{
    c->ema_valid = 0;
}

/* FFT complexa radix-2 in-place de m pontos (z intercalado re/im). Os fatores
 * W_m^j = W_n^(2j) vem da tabela de n = 2m pontos. */
static void fft_complex(float *z, int m, const float *tw)
{
    for (int i = 1, j = 0; i < m; i++) { /* permutacao bit-reversa */
        int bit = m >> 1;
        for (; j & bit; bit >>= 1) {
            j ^= bit;
        }
        j ^= bit;
        if (i < j) {
            float tr = z[2 * i], ti = z[2 * i + 1];
            z[2 * i] = z[2 * j];
            z[2 * i + 1] = z[2 * j + 1];
            z[2 * j] = tr;
            z[2 * j + 1] = ti;
        }
    }
    for (int len = 2; len <= m; len <<= 1) {
        int half = len >> 1;
        int step = m / len;
        for (int i = 0; i < m; i += len) {
            for (int j = 0; j < half; j++) {
                int k = 2 * j * step; /* indice em W_n */
                float wr = tw[2 * k], wi = -tw[2 * k + 1];
                float *a = &z[2 * (i + j)];
                float *b = &z[2 * (i + j + half)];
                float tr = b[0] * wr - b[1] * wi;
                float ti = b[0] * wi + b[1] * wr;
                b[0] = a[0] - tr;
                b[1] = a[1] - ti;
                a[0] += tr;
                a[1] += ti;
            }
        }
    }
}

/* |X[k]|^2 da FFT real de x (n pontos) para k = 0..n/2, em c->pw. */
static void real_fft_power(edge_core_t *c, const float *x)
{
    const int n = c->n, m = n / 2;
    float *z = c->z;
    for (int i = 0; i < m; i++) {
        z[2 * i] = x[2 * i];
        z[2 * i + 1] = x[2 * i + 1];
    }
    fft_complex(z, m, c->tw);
    for (int k = 0; k <= m; k++) {
        int a = k % m, b = (m - k) % m;
        float zr = z[2 * a], zi = z[2 * a + 1];
        float cr = z[2 * b], ci = -z[2 * b + 1];      /* conj(Z[m-k]) */
        float er = 0.5f * (zr + cr), ei = 0.5f * (zi + ci);
        float dr = zr - cr, di = zi - ci;
        float or_ = 0.5f * di, oi = -0.5f * dr;        /* (Zk - conj(Zm-k)) / 2i */
        float wr, wi;
        if (k < m) {
            wr = c->tw[2 * k];
            wi = -c->tw[2 * k + 1];
        } else {
            wr = -1.0f;
            wi = 0.0f;
        }
        float xr = er + wr * or_ - wi * oi;
        float xi = ei + wr * oi + wi * or_;
        c->pw[k] = xr * xr + xi * xi;
    }
}

static void rule(edge_core_t *c, int bit, float value, float limit, int hold, uint32_t *active)
{
    if (limit > 0.0f && value > limit) {
        if (c->hold_cnt[bit] < 0xFFFF) {
            c->hold_cnt[bit]++;
        }
    } else {
        c->hold_cnt[bit] = 0;
    }
    if (c->hold_cnt[bit] >= hold) {
        *active |= 1u << bit;
    }
}

#define TRIP_MASK ((1u << VIB_RULE_OVERALL_TRIP) | (1u << VIB_RULE_KURT_TRIP) | (0xFu << VIB_RULE_BAND_TRIP0) | \
                   (1u << VIB_RULE_BASE_TRIP))

void edge_core_analyze(edge_core_t *c, const int16_t *xyz, float g_per_lsb, float fs,
                       const vib_edge_config_t *cfg, const vib_edge_baseline_t *bl,
                       vib_edge_metrics_t *out)
{
    const int n = c->n, m = n / 2;
    const float df = fs / (float)n;
    const float amp_k = 2.0f / c->s1;
    const float pw_k = 2.0f / ((float)n * c->s2);
    const int nb = (bl && bl->n_bands <= VIB_EDGE_MAX_BASE_BANDS) ? bl->n_bands : 0;

    memset(out, 0, sizeof(*out));
    out->window_seq = ++c->window_seq;
    out->n_window = (uint16_t)n;
    out->fs_hz = fs;
    out->trip_rule = 0xFF;

    float band_pw[VIB_EDGE_MAX_BANDS] = {0};
    float overall2 = 0.0f, kurt_max = 0.0f;
    float base_cur[3][VIB_EDGE_MAX_BASE_BANDS];

    for (int a = 0; a < 3; a++) {
        /* ---- dominio do tempo: DC, RMS, pico a pico, curtose */
        float mean = 0.0f;
        for (int i = 0; i < n; i++) {
            mean += (float)xyz[3 * i + a];
        }
        mean /= (float)n;
        float m2 = 0.0f, m4 = 0.0f, mn = 1e30f, mx = -1e30f;
        for (int i = 0; i < n; i++) {
            float d = ((float)xyz[3 * i + a] - mean) * g_per_lsb;
            c->x[i] = d * c->win[i];
            float d2 = d * d;
            m2 += d2;
            m4 += d2 * d2;
            if (d < mn) mn = d;
            if (d > mx) mx = d;
        }
        m2 /= (float)n;
        m4 /= (float)n;
        out->rms[a] = sqrtf(m2);
        out->p2p[a] = mx - mn;
        out->kurtosis[a] = m2 > 0.0f ? m4 / (m2 * m2) : 0.0f;
        overall2 += m2;
        if (out->kurtosis[a] > kurt_max) {
            kurt_max = out->kurtosis[a];
        }

        /* ---- FFT */
        real_fft_power(c, c->x);
        float *pw = c->pw;

        /* frequencia dominante (com interpolacao parabolica, como no PC) */
        int k0 = (int)ceilf(cfg->f_min_hz / df);
        if (k0 < 0) k0 = 0;
        if (k0 > m) k0 = m;
        int kb = k0;
        for (int k = k0 + 1; k <= m; k++) {
            if (pw[k] > pw[kb]) kb = k;
        }
        float amp_b = sqrtf(pw[kb]) * amp_k * ((kb == 0 || kb == m) ? 0.5f : 1.0f);
        float f = (float)kb * df;
        if (kb > 0 && kb < m) {
            float A = sqrtf(pw[kb - 1]) * amp_k, B = sqrtf(pw[kb]) * amp_k, C = sqrtf(pw[kb + 1]) * amp_k;
            if (kb - 1 == 0) A *= 0.5f;
            if (kb + 1 == m) C *= 0.5f;
            float den = A - 2.0f * B + C;
            if (den != 0.0f) f += 0.5f * (A - C) / den * df;
        }
        out->dom_hz[a] = f;
        out->dom_amp[a] = amp_b;

        /* potencia por raia (g^2), como dsp.spectrum().power */
        for (int k = 0; k <= m; k++) {
            pw[k] *= pw_k * ((k == 0 || k == m) ? 0.5f : 1.0f);
        }
        /* bandas configuraveis: RMS resultante, [f_lo, f_hi] inclusivo */
        for (int b = 0; b < VIB_EDGE_MAX_BANDS; b++) {
            const vib_edge_band_t *bd = &cfg->band[b];
            if (bd->f_hi <= 0.0f) continue;
            for (int k = 0; k <= m; k++) {
                float fk = (float)k * df;
                if (fk >= bd->f_lo && fk <= bd->f_hi) band_pw[b] += pw[k];
            }
        }
        /* bandas da baseline: [f_lo, f_hi) */
        for (int b = 0; b < nb; b++) {
            float s = 0.0f;
            for (int k = 0; k <= m; k++) {
                float fk = (float)k * df;
                if (fk >= bl->f_lo[b] && fk < bl->f_hi[b]) s += pw[k];
            }
            base_cur[a][b] = s + 1e-12f;
        }
    }

    out->overall_rms = sqrtf(overall2);
    for (int b = 0; b < VIB_EDGE_MAX_BANDS; b++) {
        out->band_rms[b] = sqrtf(band_pw[b]);
    }

    /* ---- baseline: media exponencial em potencia linear e maior aumento em dB */
    int have_base = nb > 0;
    if (have_base) {
        float best = -1e30f;
        for (int a = 0; a < 3; a++) {
            for (int b = 0; b < nb; b++) {
                float p = base_cur[a][b];
                c->ema[a][b] = c->ema_valid ? (1.0f - EDGE_EMA_ALPHA) * c->ema[a][b] + EDGE_EMA_ALPHA * p : p;
                float cur_db = 10.0f * log10f(c->ema[a][b]);
                float mean_db = bl->mean_db[a][b];
                int ok = bl->f_hi[b] <= fs / 2.0f &&
                         (cur_db > cfg->baseline_floor_db || mean_db > cfg->baseline_floor_db);
                float delta = cur_db - mean_db;
                if (ok && delta > best) {
                    best = delta;
                    out->base_delta_db = delta;
                    out->base_band_hz = sqrtf(bl->f_lo[b] * bl->f_hi[b]);
                    out->base_axis = (uint8_t)a;
                }
            }
        }
        c->ema_valid = 1;
        out->flags |= VIB_MF_BASELINE;
    }

    /* ---- regras */
    const int hold = cfg->hold ? cfg->hold : 1;
    uint32_t active = 0;
    rule(c, VIB_RULE_OVERALL_WARN, out->overall_rms, cfg->overall_rms_warn, hold, &active);
    rule(c, VIB_RULE_OVERALL_TRIP, out->overall_rms, cfg->overall_rms_trip, hold, &active);
    rule(c, VIB_RULE_KURT_WARN, kurt_max, cfg->kurtosis_warn, hold, &active);
    rule(c, VIB_RULE_KURT_TRIP, kurt_max, cfg->kurtosis_trip, hold, &active);
    for (int b = 0; b < VIB_EDGE_MAX_BANDS; b++) {
        int on = cfg->band[b].f_hi > 0.0f;
        rule(c, VIB_RULE_BAND_WARN0 + b, out->band_rms[b], on ? cfg->band[b].warn : 0.0f, hold, &active);
        rule(c, VIB_RULE_BAND_TRIP0 + b, out->band_rms[b], on ? cfg->band[b].trip : 0.0f, hold, &active);
    }
    rule(c, VIB_RULE_BASE_WARN, out->base_delta_db, have_base ? cfg->baseline_db_warn : 0.0f, hold, &active);
    rule(c, VIB_RULE_BASE_TRIP, out->base_delta_db, have_base ? cfg->baseline_db_trip : 0.0f, hold, &active);
    out->active = active;

    uint32_t trips = active & TRIP_MASK;
    if (cfg->outputs & VIB_OUT_LATCH) {
        c->latched |= trips;
    } else {
        c->latched = 0;
    }
    uint32_t trip_now = trips | c->latched;
    if (trip_now) {
        if (c->trip_rule == 0xFF) {
            for (int b = 0; b < VIB_RULE_COUNT; b++) {
                if (trip_now & (1u << b)) {
                    c->trip_rule = (uint8_t)b;
                    break;
                }
            }
        }
        out->state = VIB_STATE_TRIP;
    } else {
        c->trip_rule = 0xFF;
        out->state = (active & ~TRIP_MASK) ? VIB_STATE_WARN : VIB_STATE_OK;
    }
    out->trip_rule = c->trip_rule;
    if (c->latched) {
        out->flags |= VIB_MF_LATCHED;
    }
}

void edge_default_config(vib_edge_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->version = VIB_EDGE_CONFIG_VERSION;
    cfg->enabled = 1;
    cfg->n_window = 2048;
    cfg->window_type = 0;
    cfg->hold = 3;
    cfg->outputs = VIB_OUT_BUZZER | VIB_OUT_LATCH; /* parada do motor DESLIGADA por padrao */
    cfg->f_min_hz = 2.0f;
    cfg->baseline_floor_db = -60.0f;
}

static int bad(float v)
{
    return !(v >= 0.0f) || isinf(v); /* NaN, negativo ou infinito */
}

int edge_validate_config(const vib_edge_config_t *cfg)
{
    if (cfg->version != VIB_EDGE_CONFIG_VERSION) return -1;
    if (cfg->n_window != 1024 && cfg->n_window != 2048) return -2;
    if (cfg->window_type > 2) return -3;
    if (cfg->hold < 1 || cfg->hold > 100) return -4;
    if (bad(cfg->f_min_hz) || bad(cfg->overall_rms_warn) || bad(cfg->overall_rms_trip) ||
        bad(cfg->kurtosis_warn) || bad(cfg->kurtosis_trip) || bad(cfg->baseline_db_warn) ||
        bad(cfg->baseline_db_trip) || isnan(cfg->baseline_floor_db)) {
        return -5;
    }
    for (int b = 0; b < VIB_EDGE_MAX_BANDS; b++) {
        const vib_edge_band_t *bd = &cfg->band[b];
        if (bad(bd->f_lo) || bad(bd->f_hi) || bad(bd->warn) || bad(bd->trip)) return -6;
        if (bd->f_hi > 0.0f && bd->f_hi <= bd->f_lo) return -7;
    }
    return 0;
}

size_t edge_core_struct_size(void)
{
    return sizeof(edge_core_t); /* usado pelo carregador ctypes do PC */
}
