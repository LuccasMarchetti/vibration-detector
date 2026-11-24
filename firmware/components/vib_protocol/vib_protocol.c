#include <string.h>
#include "vib_protocol.h"

/*
 * CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, sem reflexao, sem XOR final).
 * Equivalente a binascii.crc_hqx(data, 0xFFFF) no Python.
 * Vetor de teste: "123456789" -> 0x29B1.
 *
 * A tabela e gerada na primeira chamada; apenas a task de TX usa esta funcao.
 */
static uint16_t s_table[256];
static int s_table_ready;

static void build_table(void)
{
    for (int i = 0; i < 256; i++) {
        uint16_t c = (uint16_t)(i << 8);
        for (int b = 0; b < 8; b++) {
            c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
        }
        s_table[i] = c;
    }
    s_table_ready = 1;
}

uint16_t vib_crc16(uint16_t crc, const uint8_t *data, size_t len)
{
    if (!s_table_ready) {
        build_table();
    }
    while (len--) {
        crc = (uint16_t)((crc << 8) ^ s_table[((crc >> 8) ^ *data++) & 0xFF]);
    }
    return crc;
}

/* ------------------------------------------------------------------ parser de recepcao */

void vib_rx_init(vib_rx_parser_t *p)
{
    p->len = 0;
    p->crc_errors = 0;
}

static void drop(vib_rx_parser_t *p, size_t n)
{
    memmove(p->buf, p->buf + n, p->len - n);
    p->len -= n;
}

void vib_rx_feed(vib_rx_parser_t *p, const uint8_t *data, size_t len, vib_rx_cb_t cb, void *ctx)
{
    while (len) {
        size_t room = sizeof(p->buf) - p->len;
        size_t k = len < room ? len : room;
        memcpy(p->buf + p->len, data, k);
        p->len += k;
        data += k;
        len -= k;

        for (;;) {
            /* procura o sync */
            size_t i = 0;
            while (i + 1 < p->len && !(p->buf[i] == VIB_SYNC0 && p->buf[i + 1] == VIB_SYNC1)) {
                i++;
            }
            if (i + 1 >= p->len) { /* sem sync: guarda so um possivel 0xA5 no fim */
                if (p->len && p->buf[p->len - 1] == VIB_SYNC0) {
                    drop(p, p->len - 1);
                } else {
                    p->len = 0;
                }
                break;
            }
            if (i) {
                drop(p, i);
            }
            if (p->len < VIB_HEADER_SIZE) {
                break;
            }
            vib_header_t h;
            memcpy(&h, p->buf, sizeof(h));
            if (h.version != VIB_PROTO_VERSION || h.payload_len > VIB_RX_MAX_PAYLOAD) {
                drop(p, 1); /* sync falso */
                continue;
            }
            size_t total = VIB_HEADER_SIZE + h.payload_len + VIB_CRC_SIZE;
            if (p->len < total) {
                break;
            }
            uint16_t rx_crc = (uint16_t)(p->buf[total - 2] | (p->buf[total - 1] << 8));
            if (vib_crc16(0xFFFF, p->buf + 2, total - 4) != rx_crc) {
                p->crc_errors++;
                drop(p, 1);
                continue;
            }
            cb(&h, p->buf + VIB_HEADER_SIZE, ctx);
            drop(p, total);
        }
    }
}

size_t vib_build_packet(uint8_t *out, const vib_header_t *tmpl, const void *payload, uint16_t len)
{
    vib_header_t h = *tmpl;
    h.sync[0] = VIB_SYNC0;
    h.sync[1] = VIB_SYNC1;
    h.version = VIB_PROTO_VERSION;
    h.payload_len = len;
    memcpy(out, &h, sizeof(h));
    if (len) {
        memcpy(out + sizeof(h), payload, len);
    }
    uint16_t crc = vib_crc16(0xFFFF, out + 2, sizeof(h) - 2 + len);
    out[sizeof(h) + len] = (uint8_t)(crc & 0xFF);
    out[sizeof(h) + len + 1] = (uint8_t)(crc >> 8);
    return sizeof(h) + len + 2;
}
