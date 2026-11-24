# Protocolo serial ESP32 → PC (versão 1)

Fonte da verdade: `firmware/components/vib_protocol/include/vib_protocol.h`
(espelhado em `pc/vibmon/protocol.py`; o teste `pc/tests/test_protocol.py` garante a compatibilidade).

Little-endian. Cabeçalho de 26 bytes + payload + CRC16.

| Offset | Bytes | Campo         | Descrição |
|-------:|------:|---------------|-----------|
| 0  | 2 | sync          | `0xA5 0x5A` |
| 2  | 1 | version       | 1 |
| 3  | 1 | type          | 1 = DATA, 2 = STATUS |
| 4  | 1 | node_id       | 1 = MANCAL_1, 2 = MANCAL_2 |
| 5  | 1 | flags         | bit0 = overrun da FIFO do sensor; bit1 = bloco descartado no ESP32 |
| 6  | 1 | full_scale_g  | 4, 8 ou 16 |
| 7  | 1 | sensor_type   | 0 = ?, 1 = LSM6DS3, 2 = LSM6DS3TR-C, 3 = BMI160, 4 = ICM-20948 |
| 8  | 2 | seq           | contador de pacotes |
| 10 | 2 | payload_len   | bytes de payload |
| 12 | 8 | timestamp_us  | `esp_timer` no início da leitura da FIFO (≈ última amostra do bloco) |
| 20 | 4 | first_sample  | índice global da 1ª amostra do bloco (detecta perdas) |
| 24 | 2 | odr_hz        | taxa nominal do sensor: 6660, 1600 ou 4500 |
| 26 | N | payload       | DATA: N/6 × {int16 x, y, z}; STATUS: 24 bytes |
| 26+N | 2 | crc16       | CRC-16/CCITT-FALSE sobre bytes `[2, 26+N)` |

Conversão: `aceleração[g] = contagem × full_scale_g / 32768`.

**STATUS** (1×/s, também enviado sem sensor): `fifo_overruns u32, block_drops u32, packets_sent u32,
uptime_ms u32, wtm_samples u16, max_fifo_words u16, who_am_i u8 (ID lido do chip; 0 = sensor ausente),
sensor_type u8, bandwidth_hz u16` (banda de −3 dB do acelerômetro na configuração usada).

Banda: no pior caso (LSM6DS3) 6660 amostras/s × 6 B ≈ 40 kB/s por mancal (+0,5 % de cabeçalho);
921 600 bps (≈92 kB/s) dá folga de 2×. BMI160 e ICM-20948 geram menos dados.

A conversão `contagem × full_scale_g / 32768` vale para os quatro sensores (ex.: ±8 g = 4096 LSB/g).
