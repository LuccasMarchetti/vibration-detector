"""Protocolo binario ESP32 -> PC (espelho de firmware/components/vib_protocol).

Pacote:  sync(2) | header(24) | payload(N) | crc16(2)
O CRC-16/CCITT-FALSE cobre header (sem o sync) + payload.
"""
from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass

import numpy as np

SYNC = b"\xA5\x5A"
VERSION = 1
PKT_DATA = 1
PKT_STATUS = 2
PKT_METRICS = 3   # resultado da FFT/alarmes feitos no ESP32 (ver edge.py)
PKT_CONFIG = 4    # resposta a GET_CONFIG
PKT_ACK = 5       # confirmacao de comando
DEVICE_TYPES = frozenset({PKT_DATA, PKT_STATUS, PKT_METRICS, PKT_CONFIG, PKT_ACK})
COMMAND_TYPES = frozenset(range(0x81, 0x87))

FLAG_FIFO_OVERRUN = 0x01
FLAG_BLOCK_DROP = 0x02

# sync, version, type, node_id, flags, full_scale_g, sensor_type, seq, payload_len,
# timestamp_us, first_sample, odr_hz
HEADER = struct.Struct("<2sBBBBBBHHQIH")
HEADER_SIZE = HEADER.size  # 26
CRC_SIZE = 2
MAX_PAYLOAD = 8192

# fifo_overruns, block_drops, packets_sent, uptime_ms, wtm_samples, max_fifo_words,
# who_am_i, sensor_type, bandwidth_hz
STATUS = struct.Struct("<IIIIHHBBH")  # 24 bytes

NODE_NAMES = {1: "MANCAL_1", 2: "MANCAL_2"}


def crc16(data: bytes, crc: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE (identico ao firmware). '123456789' -> 0x29B1."""
    return binascii.crc_hqx(data, crc)


@dataclass(slots=True)
class Packet:
    type: int
    node_id: int
    flags: int
    full_scale_g: int
    sensor_type: int
    seq: int
    timestamp_us: int
    first_sample: int
    odr_hz: int
    payload: bytes
    raw: bytes  # pacote completo, como recebido (usado pelo gravador)

    @property
    def n_samples(self) -> int:
        return len(self.payload) // 6 if self.type == PKT_DATA else 0

    def counts(self) -> np.ndarray:
        """Amostras brutas int16, shape (n, 3) = colunas X, Y, Z."""
        return np.frombuffer(self.payload, dtype="<i2").reshape(-1, 3)

    def samples_g(self) -> np.ndarray:
        """Amostras em g (float32), shape (n, 3)."""
        return self.counts().astype(np.float32) * (self.full_scale_g / 32768.0)

    def status(self) -> dict:
        ov, drops, sent, up, wtm, maxw, who, stype, bw = STATUS.unpack(self.payload[: STATUS.size])
        return {
            "fifo_overruns": ov,
            "block_drops": drops,
            "packets_sent": sent,
            "uptime_ms": up,
            "wtm_samples": wtm,
            "max_fifo_words": maxw,
            "who_am_i": who,
            "sensor_type": stype,
            "bandwidth_hz": bw,
        }


def encode_packet(
    ptype: int,
    node_id: int,
    payload: bytes,
    *,
    seq: int = 0,
    timestamp_us: int = 0,
    first_sample: int = 0,
    full_scale_g: int = 8,
    flags: int = 0,
    odr_hz: int = 6660,
    sensor_type: int = 1,
) -> bytes:
    """Monta um pacote igual ao do firmware (usado pelo simulador e testes)."""
    hdr = HEADER.pack(
        SYNC, VERSION, ptype, node_id, flags, full_scale_g, sensor_type,
        seq & 0xFFFF, len(payload), timestamp_us, first_sample & 0xFFFFFFFF, odr_hz,
    )
    crc = crc16(hdr[2:] + payload)
    return hdr + payload + struct.pack("<H", crc)


class StreamParser:
    """Parser incremental robusto: aceita bytes em pedacos arbitrarios,
    ressincroniza no sync 0xA5 0x5A e descarta pacotes com CRC invalido."""

    def __init__(self, accept: frozenset[int] = DEVICE_TYPES) -> None:
        self.accept = accept
        self._buf = bytearray()
        self.crc_errors = 0
        self.bytes_skipped = 0
        self.packets_ok = 0

    def feed(self, data: bytes) -> list[Packet]:
        self._buf += data
        out: list[Packet] = []
        buf = self._buf
        pos = 0
        n = len(buf)
        while True:
            i = buf.find(SYNC, pos)
            if i < 0:
                # guarda o ultimo byte (pode ser metade de um sync)
                keep = 1 if n and buf[-1] == SYNC[0] else 0
                self.bytes_skipped += n - pos - keep
                pos = n - keep
                break
            self.bytes_skipped += i - pos
            pos = i
            if n - pos < HEADER_SIZE:
                break
            (_, ver, ptype, node, flags, fs, stype, seq, plen, ts, first, odr) = HEADER.unpack_from(buf, pos)
            if ver != VERSION or ptype not in self.accept or plen > MAX_PAYLOAD:
                pos += 1  # sync falso
                self.bytes_skipped += 1
                continue
            total = HEADER_SIZE + plen + CRC_SIZE
            if n - pos < total:
                break
            body = bytes(buf[pos + 2 : pos + HEADER_SIZE + plen])
            (rx_crc,) = struct.unpack_from("<H", buf, pos + HEADER_SIZE + plen)
            if crc16(body) != rx_crc:
                self.crc_errors += 1
                pos += 1
                self.bytes_skipped += 1
                continue
            raw = bytes(buf[pos : pos + total])
            out.append(Packet(ptype, node, flags, fs, stype, seq, ts, first, odr, body[HEADER_SIZE - 2 :], raw))
            self.packets_ok += 1
            pos += total
        del buf[:pos]
        return out
