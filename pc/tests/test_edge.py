"""Processamento no ESP32: o codigo C do firmware (edge_core.c, vib_protocol.c)
compilado no PC e comparado com o vibmon em Python."""
from pathlib import Path

import numpy as np
import pytest

from vibmon import dsp, edge_native
from vibmon.analysis import analyze
from vibmon.baseline import Baseline, DeviationTracker, build_node
from vibmon.edge import (BASELINE, CMD_GET_CONFIG, CMD_RESET_ALARM, CMD_SET_BASELINE, CMD_SET_CONFIG, EdgeBand,
                         EdgeConfig, EdgeMetrics, baseline_payload, command, parse_ack, parse_config_reply)
from vibmon.node import NodeState
from vibmon.protocol import PKT_DATA, StreamParser, encode_packet
from vibmon.sources import SimulatedSource

pytestmark = pytest.mark.skipif(not edge_native.available(),
                                reason=f"sem compilador C: {edge_native.error()}")
G8 = 8 / 32768


def _signal(n, fs, seed=0):
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    sig = np.stack([0.3 * np.sin(2 * np.pi * 123.4 * t) + 0.02 * rng.normal(size=n),
                    0.1 * np.sin(2 * np.pi * 29.5 * t) + 1.0,
                    rng.normal(0, 0.05, n)], axis=1)
    return np.round(sig / G8).astype("<i2")


@pytest.mark.parametrize("n", [1024, 2048])
@pytest.mark.parametrize("window", ["hann", "hamming", "rect"])
def test_c_metrics_match_numpy(n, window):
    fs = 6660.0
    counts = _signal(n, fs)
    g = counts.astype(float) * G8
    cfg = EdgeConfig(n_window=n, window=window)
    cfg.bands[0] = EdgeBand(100, 150, 0, 0)
    core = edge_native.NativeEdge(n, {"hann": 0, "hamming": 1, "rect": 2}[window])
    m = EdgeMetrics.from_bytes(core.analyze(counts, G8, fs, cfg.to_bytes()))
    band2 = 0.0
    for i in range(3):
        spec = dsp.spectrum(g[:, i], fs, window)
        f, a = dsp.dominant_frequency(spec, 2.0)
        mt = dsp.metrics(g[:, i])
        assert m.rms[i] == pytest.approx(mt.rms, rel=1e-4)
        assert m.p2p[i] == pytest.approx(mt.peak_to_peak, rel=1e-4)
        assert m.kurtosis[i] == pytest.approx(mt.kurtosis, rel=1e-4)
        assert m.dom_hz[i] == pytest.approx(f, rel=1e-4)
        assert m.dom_amp[i] == pytest.approx(a, rel=1e-4)
        band2 += dsp.band_rms(spec, 100, 150) ** 2
    assert m.band_rms[0] == pytest.approx(np.sqrt(band2), rel=1e-4)
    assert m.overall_rms == pytest.approx(np.sqrt(sum(x ** 2 for x in m.rms)), rel=1e-6)


def test_c_baseline_deviation_matches_python_tracker():
    sim = SimulatedSource(1, lambda *a: None, rpm=1800, edge=False)
    node = NodeState(1)
    parser = StreamParser()
    first = 0

    def push(blocks):
        nonlocal first
        for _ in range(blocks):
            c = sim.make_block(first)
            for p in parser.feed(encode_packet(PKT_DATA, 1, c.tobytes(), first_sample=first,
                                               timestamp_us=int(first / sim.fs * 1e6))):
                node.on_packet(p)
            first += sim.block

    push(160)  # ~12 s
    nb = build_node(node.latest(int(10 * sim.fs)), sim.fs, 2048)
    assert len(nb.bands) <= 32
    payload = baseline_payload(nb, 2048, "hann")
    tracker = DeviationTracker(nb, alpha=0.1)  # mesma suavizacao do ESP32 (EDGE_EMA_ALPHA)
    core = edge_native.NativeEdge(2048, 0)
    cfg = EdgeConfig().to_bytes()
    sim.fault_after_s = 1e-9  # aparece o defeito: os desvios precisam crescer igual nos dois
    for _ in range(12):
        push(2)
        g = node.latest(2048)
        counts = np.round(g / G8).astype("<i2")
        m = EdgeMetrics.from_bytes(core.analyze(counts, G8, sim.fs, cfg, payload))
        dev = tracker.update(analyze(node, 2048))
        worst = max(dev.values(), key=lambda d: d.delta_db)
        assert m.base_delta_db == pytest.approx(worst.delta_db, abs=0.05)
        assert m.base_band_hz == pytest.approx(worst.band_hz, rel=1e-3)
    assert m.base_delta_db > 6


def _run(core, counts, cfg, k=1):
    out = None
    for _ in range(k):
        out = EdgeMetrics.from_bytes(core.analyze(counts, G8, 6660.0, cfg.to_bytes()))
    return out


def test_alarm_hold_latch_and_reset():
    quiet = np.round(np.random.default_rng(1).normal(0, 0.01, (2048, 3)) / G8).astype("<i2")
    loud = _signal(2048, 6660.0)  # RMS global ~0.22 g
    cfg = EdgeConfig(hold=3, overall_rms_warn=0.1, overall_rms_trip=0.2, trip_output=True, latch=True)
    core = edge_native.NativeEdge(2048, 0)
    assert _run(core, loud, cfg, 2).state == 0          # hold: 2 janelas ainda nao bastam
    m = _run(core, loud, cfg)
    assert m.state == 2 and m.trip_reason == "RMS global (parada)" and m.latched
    m = _run(core, quiet, cfg)
    assert m.state == 2 and m.latched                   # travado mesmo com vibracao normal
    core.reset_alarm()
    assert _run(core, quiet, cfg).state == 0

    cfg.latch = False
    _run(core, loud, cfg, 3)
    assert _run(core, quiet, cfg).state == 0            # sem trava: volta sozinho
    cfg.overall_rms_trip = 0.0
    assert _run(core, loud, cfg, 3).state == 1          # so alerta


def test_c_command_parser_accepts_python_commands():
    cfg = EdgeConfig(kurtosis_warn=5).to_bytes()
    good = command(CMD_SET_CONFIG, 2, cfg, seq=7)
    bad = bytearray(command(CMD_RESET_ALARM, 1))
    bad[-1] ^= 0xFF
    stream = b"lixo\xa5" + good + bytes(bad) + b"\xa5\x5a\x09" + command(CMD_GET_CONFIG, 0)
    got = edge_native.parse_commands(stream)
    assert [(t, n) for t, n, _ in got] == [(CMD_SET_CONFIG, 2), (CMD_GET_CONFIG, 0)]
    assert got[0][2] == cfg


def test_config_json_and_validation():
    cfg = EdgeConfig.load(Path(__file__).parents[1] / "config" / "edge.example.json")
    assert edge_native.validate_config(cfg.to_bytes()) == 0
    assert EdgeConfig.from_bytes(cfg.to_bytes()).to_bytes() == cfg.to_bytes()
    with pytest.raises(ValueError):
        EdgeConfig(trip_output=True).validate()          # parada sem limite de parada
    with pytest.raises(ValueError):
        EdgeConfig(n_window=4096).validate()
    raw = bytearray(EdgeConfig().to_bytes())
    raw[2:4] = (4096).to_bytes(2, "little")
    assert edge_native.validate_config(bytes(raw)) != 0  # o firmware tambem recusa


def test_simulated_esp32_commands_end_to_end():
    import time

    from vibmon.hub import AcquisitionHub

    hub = AcquisitionHub()
    hub.add_simulated(1)
    hub.start()
    try:
        t_end = time.monotonic() + 5
        while hub.nodes[1].edge is None and time.monotonic() < t_end:
            time.sleep(0.05)
        node = hub.nodes[1]
        assert node.edge is not None and node.edge.state_text == "OK"

        def ask(cmd, payload=b""):
            node.replies.clear()
            assert hub.send_command(1, command(cmd, 1, payload))
            t_end = time.monotonic() + 3
            while not node.replies and time.monotonic() < t_end:
                time.sleep(0.02)
            return node.replies[-1]

        cfg = EdgeConfig(hold=1, overall_rms_warn=0.01, overall_rms_trip=0.02, trip_output=True)
        assert parse_ack(ask(CMD_SET_CONFIG, cfg.to_bytes()))[1] == 0
        assert parse_config_reply(ask(CMD_GET_CONFIG))[0].to_bytes() == cfg.to_bytes()
        t_end = time.monotonic() + 3
        while node.edge.state != 2 and time.monotonic() < t_end:
            time.sleep(0.05)
        assert node.edge.state_text == "PARADA" and node.edge.trip_output_active
        assert parse_ack(ask(CMD_SET_BASELINE, b"\x00" * (BASELINE.size - 1)))[1] == 1
    finally:
        hub.stop()


class _StrongToneNode:
    """Mancal 2 simulado: 1x rotacao forte (0,12 g) + impactos -> muito vazamento espectral."""

    def __init__(self):
        self.sim = SimulatedSource(2, lambda *a: None, rpm=1770, edge=False)
        self.node = NodeState(2)
        self.parser = StreamParser()
        self.first = 0

    def run(self, seconds):
        sim = self.sim
        for _ in range(max(1, int(seconds * sim.fs / sim.block))):
            c = sim.make_block(self.first)
            for p in self.parser.feed(encode_packet(PKT_DATA, 2, c.tobytes(), first_sample=self.first,
                                                    timestamp_us=int(self.first / sim.fs * 1e6))):
                self.node.on_packet(p)
            self.first += sim.block
        return self


def test_esp32_baseline_uses_its_own_window():
    """Regressao: baseline feita com 4096 pontos comparada com a FFT de 2048 do ESP32
    gerava +20 dB falsos perto do tom de 1x rotacao (vazamento da janela)."""
    import struct

    st = _StrongToneNode().run(16)
    fs = st.sim.fs
    nb = build_node(st.node.latest(int(12 * fs)), fs, 4096)  # baseline do PC (4096)
    cfg = EdgeConfig().to_bytes()  # ESP32: 2048 Hann
    right = baseline_payload(nb, 2048, "hann")
    wrong = bytearray(right)  # o que acontecia antes: medias da janela de 4096
    nb_means = [float(v) for a in "xyz" for v in list(nb.band_mean[a][:wrong[0]]) + [0] * (32 - wrong[0])]
    wrong[4 + 64 * 4: 4 + 64 * 4 + 96 * 4] = struct.pack("<96f", *nb_means)
    ok, bad = edge_native.NativeEdge(2048, 0), edge_native.NativeEdge(2048, 0)
    for _ in range(12):  # 12 janelas NOVAS, passo de 1024 amostras como no ESP32
        st.run(1024 / fs)
        counts = np.round(st.node.latest(2048) / G8).astype("<i2")
        m_ok = EdgeMetrics.from_bytes(ok.analyze(counts, G8, fs, cfg, right))
        m_bad = EdgeMetrics.from_bytes(bad.analyze(counts, G8, fs, cfg, bytes(wrong)))
    assert m_ok.base_delta_db < 3.0
    assert m_bad.base_delta_db > 10.0  # demonstra o problema que a correcao evita


def test_esp32_ignores_baseline_from_other_window():
    from vibmon.hub import AcquisitionHub
    import time

    st = _StrongToneNode().run(8)
    nb = build_node(st.node.latest(int(6 * st.sim.fs)), st.sim.fs, 2048)
    hub = AcquisitionHub()
    hub.add_simulated(1)
    hub.start()
    try:
        time.sleep(1.5)
        hub.send_command(1, command(CMD_SET_BASELINE, 1, baseline_payload(nb, 1024, "hann")))  # ESP32 em 2048
        t_end = time.monotonic() + 3
        while time.monotonic() < t_end and not (hub.nodes[1].edge and hub.nodes[1].edge.base_mismatch):
            time.sleep(0.05)
        m = hub.nodes[1].edge
        assert m.base_mismatch and not m.has_baseline
    finally:
        hub.stop()
