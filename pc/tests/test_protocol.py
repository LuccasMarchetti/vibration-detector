import struct

import numpy as np

from vibmon.protocol import (HEADER_SIZE, PKT_DATA, PKT_STATUS, STATUS, StreamParser, crc16,
                             encode_packet)


def test_crc_vector_matches_firmware():
    # mesmo vetor de teste documentado em firmware/components/vib_protocol/vib_protocol.c
    assert crc16(b"123456789") == 0x29B1


def test_header_size_matches_firmware():
    assert HEADER_SIZE == 26
    assert STATUS.size == 24


def _data_pkt(node=1, first=0, seq=0, n=512):
    counts = (np.arange(n * 3, dtype=np.int16) - 700).astype("<i2")
    return encode_packet(PKT_DATA, node, counts.tobytes(), seq=seq, first_sample=first,
                         timestamp_us=123456789, full_scale_g=8), counts.reshape(-1, 3)


def test_roundtrip_byte_by_byte():
    raw, counts = _data_pkt()
    p = StreamParser()
    out = []
    for b in raw:
        out += p.feed(bytes([b]))
    assert len(out) == 1
    pkt = out[0]
    assert pkt.node_id == 1 and pkt.n_samples == 512 and pkt.timestamp_us == 123456789
    np.testing.assert_array_equal(pkt.counts(), counts)
    np.testing.assert_allclose(pkt.samples_g()[0, 0], counts[0, 0] * 8 / 32768, rtol=1e-6)
    assert pkt.raw == raw


def test_resync_after_garbage_and_crc_error():
    a, _ = _data_pkt(seq=1)
    b, _ = _data_pkt(seq=2, first=512)
    bad = bytearray(_data_pkt(seq=3)[0])
    bad[100] ^= 0x55
    boot_log = b"ets Jun  8 2016 00:22:57\r\nrst:0x1 (POWERON_RESET)\xa5\x00junk"
    p = StreamParser()
    out = p.feed(boot_log + a + bytes(bad) + b"\xa5" + b)
    assert [x.seq for x in out] == [1, 2]
    assert p.crc_errors >= 1


def test_status_payload():
    st = STATUS.pack(1, 2, 3, 4000, 512, 1600, 0xD1, 3, 684)
    raw = encode_packet(PKT_STATUS, 2, st, sensor_type=3, odr_hz=1600)
    (pkt,) = StreamParser().feed(raw)
    s = pkt.status()
    assert s["who_am_i"] == 0xD1 and s["fifo_overruns"] == 1 and s["wtm_samples"] == 512
    assert s["sensor_type"] == 3 and s["bandwidth_hz"] == 684
    assert pkt.node_id == 2 and pkt.sensor_type == 3 and pkt.odr_hz == 1600


def test_crc_little_endian_at_end():
    raw, _ = _data_pkt(n=2)
    (crc,) = struct.unpack("<H", raw[-2:])
    assert crc == crc16(raw[2:-2])
