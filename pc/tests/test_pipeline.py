import numpy as np

from vibmon.alarms import AlarmChecker, Threshold
from vibmon.analysis import analyze
from vibmon.export import load_vib
from vibmon.node import NodeState
from vibmon.protocol import PKT_DATA, StreamParser, encode_packet
from vibmon.recorder import Recorder, read_vib
from vibmon.sources import SimulatedSource


def _feed(node, sim, blocks, first=0, skip=()):
    p = StreamParser()
    pkts = []
    for k in range(blocks):
        if k in skip:
            first += sim.block
            continue
        c = sim.make_block(first)
        raw = encode_packet(PKT_DATA, sim.node_id, c.tobytes(), seq=k, first_sample=first,
                            timestamp_us=int((first + sim.block - 1) / sim.fs * 1e6))
        for pkt in p.feed(raw):
            node.on_packet(pkt)
            pkts.append(pkt)
        first += sim.block
    return pkts


def test_node_gap_detection_and_analysis():
    sim = SimulatedSource(2, lambda *a: None, rpm=1800)
    node = NodeState(2, seconds=2)
    _feed(node, sim, 12, skip=(5,))
    assert node.stats.lost_samples == 512
    assert node.stats.seq_gaps == 1
    assert abs(node.fs_estimate() - 6660) < 1
    res = analyze(node, 2048)
    assert res is not None
    assert abs(res.axes["x"].dom_freq - 30.0) < 4
    assert res.axes["x"].metrics.kurtosis > 3.5  # impactos simulados no mancal 2


def test_alarm_hold_and_hook():
    sim = SimulatedSource(2, lambda *a: None)
    node = NodeState(2, seconds=2)
    _feed(node, sim, 8)
    res = analyze(node, 2048)
    fired = []
    chk = AlarmChecker([Threshold("teste", "overall_rms", 0.01, hold=2)])
    chk.hooks.append(fired.append)
    assert chk.check(res) == []
    assert len(chk.check(res)) == 1 and len(fired) == 1
    assert chk.check(res) == []  # ja ativo, nao repete
    assert chk.active_for(2)


def test_example_thresholds_file_loads():
    from pathlib import Path

    chk = AlarmChecker.from_file(Path(__file__).parents[1] / "config" / "thresholds.example.json")
    assert chk.rules and all(not r.enabled for r in chk.rules)


def test_record_and_export(tmp_path):
    sim = SimulatedSource(1, lambda *a: None)
    rec = Recorder(tmp_path)
    d = rec.start("ensaio 1")
    node = NodeState(1)
    for pkt in _feed(node, sim, 4):
        rec.on_packet(pkt, "SIM1")
    rec.annotate("1200", "15", "teste", {1: 2048})
    rec.stop()
    f = d / "mancal_1.vib"
    assert sum(1 for _ in read_vib(f)) == 4
    data = load_vib(f)
    assert data["sample_index"].size == 2048
    np.testing.assert_array_equal(data["sample_index"], np.arange(2048))
    np.testing.assert_allclose(np.diff(data["t_s"]), 1 / 6660, rtol=1e-3)
    assert "1200" in (d / "annotations.csv").read_text()
