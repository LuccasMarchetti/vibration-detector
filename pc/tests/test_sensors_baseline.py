import numpy as np
import pytest

from vibmon.alarms import AlarmChecker
from vibmon.analysis import analyze
from vibmon.baseline import (Baseline, DeviationTracker, build_from_session, build_node, compare,
                             third_octave_bands)
from vibmon.node import NodeState
from vibmon.protocol import PKT_DATA, StreamParser, encode_packet
from vibmon.recorder import Recorder
from vibmon.sensors import SENSORS, by_name
from vibmon.sources import SimulatedSource


def feed(node, sim, seconds, start=0):
    p = StreamParser()
    n_blocks = int(seconds * sim.fs / sim.block)
    first = start
    pkts = []
    for k in range(n_blocks):
        c = sim.make_block(first)
        raw = encode_packet(PKT_DATA, sim.node_id, c.tobytes(), seq=k, first_sample=first,
                            timestamp_us=int((first + sim.block - 1) / sim.fs * 1e6),
                            odr_hz=int(sim.fs), sensor_type=sim.sensor.code)
        for pkt in p.feed(raw):
            node.on_packet(pkt)
            pkts.append(pkt)
        first += sim.block
    return first, pkts


@pytest.mark.parametrize("name", ["lsm6ds3", "bmi160", "icm20948"])
def test_each_sensor_end_to_end(name):
    sim = SimulatedSource(1, lambda *a: None, rpm=1800, sensor=name)
    node = NodeState(1)
    feed(node, sim, 3)
    info = by_name(name)
    assert node.sensor_type == info.code and node.sensor_name == info.name
    assert node.fs_nominal == info.odr_hz
    assert abs(node.fs_estimate() - info.odr_hz) < 1
    assert node.bandwidth_hz == info.bandwidth_hz
    res = analyze(node, 2048)
    assert abs(res.axes["x"].dom_freq - 30.0) < 1.5


def test_sensor_table_matches_firmware_datasheets():
    assert {s.name: (s.odr_hz, s.bandwidth_hz) for s in SENSORS.values()} == {
        "LSM6DS3": (6660, 3160), "LSM6DS3TR-C": (6660, 1500),
        "BMI160": (1600, 684), "ICM-20948": (4500, 1209)}


def test_third_octave_bands():
    b = third_octave_bands(3330)
    assert np.isclose(b[:, 1], 1000).any()
    assert b[0, 0] >= 10 and b[-1, 2] <= 3330
    assert np.allclose(b[1:, 1] / b[:-1, 1], 2 ** (1 / 3))


def _baseline_and_node(sensor="lsm6ds3", fault_after=0.0):
    sim = SimulatedSource(1, lambda *a: None, rpm=1800, sensor=sensor, fault_after_s=fault_after)
    node = NodeState(1)
    end, _ = feed(node, sim, 12)
    nb = build_node(node.latest(int(10 * sim.fs)), sim.fs, 4096, "hann", node.sensor_name)
    return sim, node, nb, end


def _track(node, sim, nb, start, seconds=3.0):
    """Simula a GUI: uma analise a cada ~0,2 s de sinal novo, com suavizacao."""
    tr = DeviationTracker(nb)
    dev = None
    for k in range(int(seconds * 5)):
        start, _ = feed(node, sim, 0.2 if sim.fs * 0.2 >= sim.block else sim.block / sim.fs, start=start)
        dev = tr.update(analyze(node, 4096))
    return dev


@pytest.mark.parametrize("sensor", ["lsm6ds3", "bmi160", "icm20948"])
def test_baseline_detects_new_fault(sensor):
    sim, node, nb, end = _baseline_and_node(sensor)
    # mesmo motor, sinal novo: com suavizacao nenhuma banda passa de 3 dB
    dev = _track(node, sim, nb, end)
    assert max(d.delta_db for d in dev.values()) < 3.0
    # surge o defeito (tom em 3,2x rotacao = 96 Hz): a banda de 100 Hz sobe
    sim.fault_after_s = 1e-9
    dev = _track(node, sim, nb, end + 100000)
    worst = max(dev.values(), key=lambda d: d.delta_db)
    assert worst.delta_db > 6.0
    assert 80 < worst.band_hz < 125


def test_single_window_compare_still_works():
    sim, node, nb, end = _baseline_and_node()
    feed(node, sim, 1, start=end)
    dev = compare(nb, analyze(node, 4096))
    assert set(dev) == {"x", "y", "z"} and all(np.isfinite(d.delta_db) for d in dev.values())


def test_baseline_alarm_and_save_load(tmp_path):
    sim, node, nb, end = _baseline_and_node()
    b = Baseline("teste", {1: nb}, rpm="1800")
    path = b.save(tmp_path / "b.json")
    b2 = Baseline.load(path)
    assert b2.label == "teste" and b2.rpm == "1800"
    np.testing.assert_allclose(b2.nodes[1].band_mean["x"], nb.band_mean["x"], atol=1e-3)

    chk = AlarmChecker()
    chk.set_baseline(b2)
    assert any(r.metric == "baseline_db" for r in chk.rules)
    sim.fault_after_s = 1e-9
    feed(node, sim, 2, start=end + 100000)
    res = analyze(node, 4096)
    events = [e for _ in range(6) for e in chk.check(res)]
    assert events and events[0].rule.metric == "baseline_db"


def test_baseline_from_recording(tmp_path):
    sim = SimulatedSource(1, lambda *a: None, rpm=1800, sensor="bmi160")
    rec = Recorder(tmp_path)
    d = rec.start("base")
    _, pkts = feed(NodeState(1), sim, 15)
    for p in pkts:
        rec.on_packet(p, "SIM1")
    rec.stop()
    b = build_from_session(d, "1800rpm", start_s=2, end_s=14)
    nb = b.nodes[1]
    assert nb.sensor == "BMI160" and abs(nb.fs - 1600) < 1
    assert nb.n_windows >= 4


def test_short_capture_raises():
    with pytest.raises(ValueError):
        build_node(np.zeros((5000, 3)), 6660, 4096)


def test_pc_deviation_ignores_gui_window_and_unit():
    """Regressao: com a GUI em 2048/Hamming e m/s^2, a comparacao com uma baseline de
    4096/Hann em g dava desvios falsos (vazamento) e +19,8 dB (unidade)."""
    sim = SimulatedSource(2, lambda *a: None, rpm=1770)
    node = NodeState(2)
    end, _ = feed(node, sim, 14)
    nb = build_node(node.latest(int(12 * sim.fs)), sim.fs, 4096, "hann")
    chk = AlarmChecker()
    chk.set_baseline(Baseline("b", {2: nb}))
    for _ in range(12):
        end, _ = feed(node, sim, 0.2, start=end)
        chk.check(analyze(node, 2048, "hamming", "m/s²"), node)
    worst = max(d.delta_db for d in chk.last_deviation[2].values())
    assert worst < 3.0
