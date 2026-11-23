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
