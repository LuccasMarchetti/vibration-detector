"""Processamento na borda (FFT/alarmes no ESP32): estruturas e comandos.

Espelho de firmware/components/vib_protocol/include/vib_protocol.h
(vib_edge_config_t, vib_edge_baseline_t, vib_edge_metrics_t, comandos 0x8x).

Linha de comando (com a GUI FECHADA, pois ela ocupa as portas):
    python -m vibmon.edge COM3 --show
    python -m vibmon.edge COM3 --config config/edge.example.json
    python -m vibmon.edge COM3 --baseline baselines/1200rpm.json
    python -m vibmon.edge COM3 --reset
    python -m vibmon.edge COM3 --test buzzer
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .protocol import (PKT_ACK, PKT_CONFIG, PKT_METRICS, Packet, StreamParser, encode_packet)

CMD_SET_CONFIG = 0x81
CMD_SET_BASELINE = 0x82
CMD_CLEAR_BASE = 0x83
CMD_RESET_ALARM = 0x84
CMD_GET_CONFIG = 0x85
CMD_TEST_OUTPUT = 0x86
CMD_NAMES = {CMD_SET_CONFIG: "SET_CONFIG", CMD_SET_BASELINE: "SET_BASELINE", CMD_CLEAR_BASE: "CLEAR_BASE",
             CMD_RESET_ALARM: "RESET_ALARM", CMD_GET_CONFIG: "GET_CONFIG", CMD_TEST_OUTPUT: "TEST_OUTPUT"}
ACK_TEXT = {0: "ok", 1: "tamanho inválido", 2: "valor inválido", 3: "comando desconhecido",
            4: "erro ao gravar na NVS", 5: "indisponível"}

OUT_BUZZER, OUT_TRIP, OUT_LATCH = 0x01, 0x02, 0x04
MAX_BANDS, MAX_BASE_BANDS = 4, 32
STATE_TEXT = {0: "OK", 1: "ALERTA", 2: "PARADA"}
WINDOWS = {"hann": 0, "hamming": 1, "rect": 2}

RULES = ["RMS global (alerta)", "RMS global (parada)", "curtose (alerta)", "curtose (parada)",
         "banda 1 (alerta)", "banda 2 (alerta)", "banda 3 (alerta)", "banda 4 (alerta)",
         "banda 1 (parada)", "banda 2 (parada)", "banda 3 (parada)", "banda 4 (parada)",
         "baseline (alerta)", "baseline (parada)"]

CONFIG = struct.Struct("<BBHBBBBf" + "ff" * 3 + "f" + "ffff" * MAX_BANDS)  # 104
BASELINE = struct.Struct(f"<BBH{MAX_BASE_BANDS}f{MAX_BASE_BANDS}f{3 * MAX_BASE_BANDS}f")  # 644
METRICS = struct.Struct("<IHBBf" + "3f" * 5 + "f4fffBBHI")  # 108
ACK = struct.Struct("<BBH")
assert CONFIG.size == 104 and BASELINE.size == 644 and METRICS.size == 108


@dataclass
class EdgeBand:
    f_lo: float = 0.0
    f_hi: float = 0.0      # 0 = banda desligada
    warn: float = 0.0      # RMS resultante (g); 0 = regra desligada
    trip: float = 0.0


@dataclass
class EdgeConfig:
    enabled: bool = True
    n_window: int = 2048
    window: str = "hann"
    hold: int = 3
    buzzer: bool = True
    trip_output: bool = False   # saida de parada do motor: DESLIGADA por padrao
    latch: bool = True
    f_min_hz: float = 2.0
    overall_rms_warn: float = 0.0
    overall_rms_trip: float = 0.0
    kurtosis_warn: float = 0.0
    kurtosis_trip: float = 0.0
    baseline_db_warn: float = 0.0
    baseline_db_trip: float = 0.0
    baseline_floor_db: float = -60.0
    bands: list[EdgeBand] = field(default_factory=lambda: [EdgeBand() for _ in range(MAX_BANDS)])

    def to_bytes(self) -> bytes:
        bands = (list(self.bands) + [EdgeBand()] * MAX_BANDS)[:MAX_BANDS]
        outputs = (OUT_BUZZER if self.buzzer else 0) | (OUT_TRIP if self.trip_output else 0) | (
            OUT_LATCH if self.latch else 0)
        flat = [v for b in bands for v in (b.f_lo, b.f_hi, b.warn, b.trip)]
        return CONFIG.pack(1, int(self.enabled), self.n_window, WINDOWS[self.window], self.hold, outputs, 0,
                           self.f_min_hz, self.overall_rms_warn, self.overall_rms_trip, self.kurtosis_warn,
                           self.kurtosis_trip, self.baseline_db_warn, self.baseline_db_trip,
                           self.baseline_floor_db, *flat)

    @classmethod
    def from_bytes(cls, b: bytes) -> "EdgeConfig":
        v = [round(x, 6) if isinstance(x, float) else x for x in CONFIG.unpack(b[: CONFIG.size])]
        (_, en, n, win, hold, outputs, _r, fmin, ow, ot, kw, kt, bw, bt, floor) = v[:15]
        bands = [EdgeBand(*v[15 + 4 * i: 19 + 4 * i]) for i in range(MAX_BANDS)]
        wname = {i: k for k, i in WINDOWS.items()}[win]
        return cls(bool(en), n, wname, hold, bool(outputs & OUT_BUZZER), bool(outputs & OUT_TRIP),
                   bool(outputs & OUT_LATCH), fmin, ow, ot, kw, kt, bw, bt, floor, bands)

    def validate(self) -> None:
        if self.n_window not in (1024, 2048):
            raise ValueError("n_window deve ser 1024 ou 2048 (limite de memoria do ESP32)")
        if self.window not in WINDOWS:
            raise ValueError(f"window deve ser um de {list(WINDOWS)}")
        if not 1 <= self.hold <= 100:
            raise ValueError("hold deve estar entre 1 e 100")
        for name in ("f_min_hz", "overall_rms_warn", "overall_rms_trip", "kurtosis_warn", "kurtosis_trip",
                     "baseline_db_warn", "baseline_db_trip"):
            v = getattr(self, name)
            if not (v >= 0 and math.isfinite(v)):
                raise ValueError(f"{name} deve ser >= 0")
        for i, b in enumerate(self.bands[:MAX_BANDS]):
            if b.f_hi and b.f_hi <= b.f_lo:
                raise ValueError(f"banda {i + 1}: f_hi deve ser maior que f_lo")
        if self.trip_output and not any((self.overall_rms_trip, self.kurtosis_trip, self.baseline_db_trip,
                                         *[b.trip for b in self.bands])):
            raise ValueError("trip_output ligado mas nenhum limite de PARADA definido")

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "EdgeConfig":
        d = {k: v for k, v in d.items() if not k.startswith("_")}
        bands = [EdgeBand(**{k: v for k, v in b.items() if not k.startswith("_")}) for b in d.pop("bands", [])]
        cfg = cls(**d, bands=bands + [EdgeBand() for _ in range(MAX_BANDS - len(bands))])
        cfg.validate()
        return cfg

    @classmethod
    def load(cls, path: str | Path) -> "EdgeConfig":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def baseline_payload(node_baseline, n_window: int = 2048, window: str = "hann") -> bytes:
    """vib_edge_baseline_t para a janela (n_window, window) configurada no ESP32.

    Usa as medias por banda recalculadas com essa mesma janela (NodeBaseline.edge_mean);
    as 32 bandas mais baixas de 1/3 de oitava cabem no ESP32."""
    key = f"{n_window}_{window}"
    if key not in node_baseline.edge_mean:
        raise ValueError(f"a baseline nao tem dados para a janela {key} do ESP32 (recapture a baseline)")
    means_src = node_baseline.edge_mean[key]
    bands = node_baseline.bands[:MAX_BASE_BANDS]
    n = len(bands)
    pad = lambda arr: list(map(float, arr[:n])) + [0.0] * (MAX_BASE_BANDS - n)  # noqa: E731
    means = []
    for a in ("x", "y", "z"):
        means += pad(means_src[a])
    return BASELINE.pack(n, WINDOWS[window], n_window, *pad(bands[:, 0]), *pad(bands[:, 2]), *means)


@dataclass
class EdgeMetrics:
    window_seq: int
    n_window: int
    state: int
    latched: bool
    has_baseline: bool
    silenced: bool
    trip_output_active: bool
    base_mismatch: bool
    fs_hz: float
    rms: tuple
    p2p: tuple
    kurtosis: tuple
    dom_hz: tuple
    dom_amp: tuple
    overall_rms: float
    band_rms: tuple
    base_delta_db: float
    base_band_hz: float
    base_axis: int
    trip_rule: int
    active: int

    @classmethod
    def from_bytes(cls, b: bytes) -> "EdgeMetrics":
        v = METRICS.unpack(b[: METRICS.size])
        seq, n, state, flags, fs = v[:5]
        tri = [tuple(v[5 + 3 * i: 8 + 3 * i]) for i in range(5)]
        overall = v[20]
        bands = tuple(v[21:25])
        bdelta, bhz, bax, trule, _r, active = v[25:31]
        return cls(seq, n, state, bool(flags & 1), bool(flags & 2), bool(flags & 4), bool(flags & 8),
                   bool(flags & 0x10), fs,
                   *tri, overall, bands, bdelta, bhz, bax, trule, active)

    @property
    def state_text(self) -> str:
        return STATE_TEXT.get(self.state, "?")

    @property
    def active_rules(self) -> list[str]:
        return [RULES[i] for i in range(len(RULES)) if self.active >> i & 1]

    @property
    def trip_reason(self) -> str:
        return RULES[self.trip_rule] if self.trip_rule < len(RULES) else ""


def command(cmd: int, node_id: int = 0, payload: bytes = b"", seq: int = 0) -> bytes:
    """Pacote de comando PC -> ESP32 (mesmo cabecalho/CRC dos dados)."""
    return encode_packet(cmd, node_id, payload, seq=seq, full_scale_g=0, odr_hz=0, sensor_type=0)


def parse_ack(pkt: Packet) -> tuple[int, int, int]:
    cmd, status, seq = ACK.unpack(pkt.payload[: ACK.size])
    return cmd, status, seq


def parse_config_reply(pkt: Packet) -> tuple[EdgeConfig, int]:
    cfg = EdgeConfig.from_bytes(pkt.payload[: CONFIG.size])
    n_bands = pkt.payload[CONFIG.size] if len(pkt.payload) > CONFIG.size else 0
    return cfg, n_bands


# ---------------------------------------------------------------------------- CLI
def _transact(ser, parser: StreamParser, pkt: bytes, cmd: int, timeout: float = 2.0) -> list[Packet]:
    ser.write(pkt)
    got = []
    t_end = time.monotonic() + timeout
    while time.monotonic() < t_end:
        for p in parser.feed(ser.read(4096)):
            if p.type in (PKT_ACK, PKT_CONFIG):
                got.append(p)
                if (p.type == PKT_ACK and p.payload[0] == cmd) or (p.type == PKT_CONFIG and cmd == CMD_GET_CONFIG):
                    return got
    return got


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--show", action="store_true", help="mostra a configuracao atual do ESP32")
    ap.add_argument("--config", help="envia e grava a configuracao (JSON)")
    ap.add_argument("--baseline", help="envia e grava a baseline (JSON do vibmon) do mancal deste ESP32")
    ap.add_argument("--clear-baseline", action="store_true")
    ap.add_argument("--reset", action="store_true", help="rearma a parada travada")
    ap.add_argument("--test", choices=("buzzer", "parada"), help="aciona a saida por 1-2 s")
    ap.add_argument("--watch", type=float, default=0, metavar="S", help="mostra METRICS por S segundos")
    a = ap.parse_args(argv)

    import serial

    parser = StreamParser()
    with serial.serial_for_url(a.port, a.baud, timeout=0.05) as ser:
        ser.dtr = False
        ser.rts = False
        node = 0
        t_end = time.monotonic() + 3
        while not node and time.monotonic() < t_end:  # descobre o mancal pelo trafego
            for p in parser.feed(ser.read(4096)):
                node = p.node_id
        print(f"ESP32 na porta {a.port}: mancal {node or '?'}")

        def run(cmd, payload=b""):
            for p in _transact(ser, parser, command(cmd, node, payload), cmd):
                if p.type == PKT_ACK:
                    c, st, _ = parse_ack(p)
                    print(f"  {CMD_NAMES.get(c, hex(c))}: {ACK_TEXT.get(st, st)}")
                    return st == 0
                if p.type == PKT_CONFIG:
                    cfg, nb = parse_config_reply(p)
                    print(json.dumps(cfg.to_json(), indent=2, ensure_ascii=False))
                    print(f"  baseline no ESP32: {nb} bandas" if nb else "  sem baseline no ESP32")
                    return True
            print("  sem resposta (firmware antigo ou porta errada?)")
            return False

        if a.config:
            run(CMD_SET_CONFIG, EdgeConfig.load(a.config).to_bytes())
        if a.baseline:
            from .baseline import Baseline

            b = Baseline.load(a.baseline)
            if node not in b.nodes:
                raise SystemExit(f"a baseline nao tem dados do mancal {node}")
            # a baseline precisa ser a da janela configurada no ESP32
            cfg = next((parse_config_reply(p)[0] for p in _transact(ser, parser, command(CMD_GET_CONFIG, node),
                                                                     CMD_GET_CONFIG) if p.type == PKT_CONFIG), None)
            if cfg is None:
                raise SystemExit("o ESP32 nao respondeu ao GET_CONFIG")
            run(CMD_SET_BASELINE, baseline_payload(b.nodes[node], cfg.n_window, cfg.window))
        if a.clear_baseline:
            run(CMD_CLEAR_BASE)
        if a.reset:
            run(CMD_RESET_ALARM)
        if a.test:
            run(CMD_TEST_OUTPUT, bytes([1 if a.test == "buzzer" else 2]))
        if a.show or not any((a.config, a.baseline, a.clear_baseline, a.reset, a.test, a.watch)):
            run(CMD_GET_CONFIG)
        t_end = time.monotonic() + a.watch
        while time.monotonic() < t_end:
            for p in parser.feed(ser.read(4096)):
                if p.type == PKT_METRICS:
                    m = EdgeMetrics.from_bytes(p.payload)
                    print(f"#{m.window_seq} {m.state_text:7s} RMS={m.overall_rms:.4f} g "
                          f"f=({m.dom_hz[0]:.1f}, {m.dom_hz[1]:.1f}, {m.dom_hz[2]:.1f}) Hz "
                          f"Δbase={m.base_delta_db:+.1f} dB {', '.join(m.active_rules)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
