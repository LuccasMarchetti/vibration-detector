"""Baseline espectral: "assinatura" do motor saudavel e comparacao com o sinal atual.

Como funciona
-------------
1. Pega-se um trecho de sinal com o motor em bom estado, numa rotacao fixa
   (de uma gravacao .vib ou capturado ao vivo pela GUI).
2. O trecho e dividido em janelas (Welch, 50 % de sobreposicao). Para cada eixo
   guardamos:
     - o espectro de amplitude medio (para desenhar sobre a FFT ao vivo);
     - a potencia em cada banda de 1/3 de oitava, em dB (media e desvio padrao).
3. Ao vivo, a potencia por banda e recalculada e comparada: delta_dB = atual - media.
   Banda que sobe muitos dB = algo novo apareceu (desbalanceamento, folga,
   rolamento...). A comparacao por bandas nao depende do tamanho da janela FFT.

Use uma baseline por rotacao: a assinatura muda com a velocidade do motor.

Linha de comando (a partir de uma gravacao):
    python -m vibmon.baseline recordings/<sessao> --label 1200rpm --start 10 --end 70
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import dsp
from .analysis import AXES, NodeAnalysis

DB_EPS = 1e-12      # g^2 (evita log de zero)
DEFAULT_FLOOR_DB = -60.0  # 1 mg RMS: bandas mais fracas que isso nao geram desvio


def third_octave_bands(f_max: float, f_min: float = 10.0) -> np.ndarray:
    """Bandas de 1/3 de oitava (base 2, centradas em 1 kHz): linhas (lo, centro, hi)."""
    k = np.arange(-40, 30)
    fc = 1000.0 * 2.0 ** (k / 3.0)
    lo, hi = fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)
    m = (lo >= f_min) & (hi <= f_max)
    return np.column_stack([lo[m], fc[m], hi[m]])


def band_db(spec: dsp.Spectrum, bands: np.ndarray) -> np.ndarray:
    """Potencia por banda em dB re 1 g^2 (0 dB = 1 g RMS na banda)."""
    out = np.empty(len(bands))
    for i, (lo, _, hi) in enumerate(bands):
        m = (spec.freqs >= lo) & (spec.freqs < hi)
        out[i] = 10 * np.log10(spec.power[m].sum() + DB_EPS)
    return out


@dataclass
class NodeBaseline:
    fs: float
    n_window: int
    window: str
    sensor: str
    n_windows: int
    freqs: np.ndarray
    amp: dict[str, np.ndarray]          # espectro de amplitude medio por eixo
    bands: np.ndarray                   # (k, 3) lo, centro, hi
    band_mean: dict[str, np.ndarray]    # dB
    band_std: dict[str, np.ndarray]     # dB
    # Media por banda recalculada com as janelas que o ESP32 usa ("2048_hann",
    # "1024_rect"...): perto de tons fortes o vazamento espectral depende do
    # tamanho/tipo da janela, entao cada configuracao precisa da sua referencia.
    edge_mean: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)

    def to_json(self) -> dict:
        r = lambda a, d=6: np.round(np.asarray(a, dtype=float), d).tolist()  # noqa: E731
        return {"fs": self.fs, "n_window": self.n_window, "window": self.window, "sensor": self.sensor,
                "n_windows": self.n_windows, "freqs": r(self.freqs, 3), "bands": r(self.bands, 3),
                "amp": {a: r(v, 7) for a, v in self.amp.items()},
                "band_mean": {a: r(v, 3) for a, v in self.band_mean.items()},
                "band_std": {a: r(v, 3) for a, v in self.band_std.items()},
                "edge_mean": {k: {a: r(v, 3) for a, v in d.items()} for k, d in self.edge_mean.items()}}

    @classmethod
    def from_json(cls, d: dict) -> "NodeBaseline":
        arr = lambda v: np.asarray(v, dtype=float)  # noqa: E731
        return cls(d["fs"], d["n_window"], d["window"], d.get("sensor", ""), d["n_windows"], arr(d["freqs"]),
                   {a: arr(v) for a, v in d["amp"].items()}, arr(d["bands"]),
                   {a: arr(v) for a, v in d["band_mean"].items()}, {a: arr(v) for a, v in d["band_std"].items()},
                   {k: {a: arr(v) for a, v in e.items()} for k, e in d.get("edge_mean", {}).items()})


@dataclass
class Baseline:
    label: str
    nodes: dict[int, NodeBaseline]
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    rpm: str = ""
    source: str = ""

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        d = {"format": "vibmon-baseline-1", "label": self.label, "created": self.created, "rpm": self.rpm,
             "source": self.source, "nodes": {str(k): v.to_json() for k, v in self.nodes.items()}}
        path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Baseline":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("format") != "vibmon-baseline-1":
            raise ValueError(f"{path} nao e um arquivo de baseline do vibmon")
        nodes = {int(k): NodeBaseline.from_json(v) for k, v in d["nodes"].items()}
        return cls(d["label"], nodes, d.get("created", ""), d.get("rpm", ""), d.get("source", ""))


EDGE_WINDOWS = [(n, w) for n in (1024, 2048) for w in ("hann", "hamming", "rect")]


def _band_matrix(freqs: np.ndarray, bands: np.ndarray) -> np.ndarray:
    """Matriz (raias x bandas) com 1 onde a raia cai em [lo, hi)."""
    return ((freqs[:, None] >= bands[None, :, 0]) & (freqs[:, None] < bands[None, :, 2])).astype(float)


def _segment_band_db(x: np.ndarray, fs: float, n: int, window: str, bands: np.ndarray,
                     overlap: float = 0.5) -> np.ndarray:
    """dB por banda de cada segmento (segmentos x bandas), vetorizado.
    Mesmas formulas de dsp.spectrum().power e do edge_core.c."""
    step = max(1, int(n * (1 - overlap)))
    idx = np.arange(0, x.size - n + 1, step)[:, None] + np.arange(n)[None, :]
    seg = x[idx]
    seg = seg - seg.mean(axis=1, keepdims=True)
    w = dsp.make_window(window, n)
    X = np.fft.rfft(seg * w, axis=1)
    p = 2.0 * np.abs(X) ** 2 / (n * np.sum(w ** 2))
    p[:, 0] /= 2
    p[:, -1] /= 2
    M = _band_matrix(np.fft.rfftfreq(n, 1 / fs), bands)
    return 10 * np.log10(p @ M + DB_EPS)


def build_node(xyz: np.ndarray, fs: float, n_window: int = 4096, window: str = "hann",
               sensor: str = "", overlap: float = 0.5) -> NodeBaseline:
    """Baseline de um mancal a partir de um trecho continuo (n, 3) em g."""
    xyz = np.asarray(xyz, dtype=np.float64)
    step = max(1, int(n_window * (1 - overlap)))
    starts = range(0, xyz.shape[0] - n_window + 1, step)
    if len(starts) < 4:
        raise ValueError(f"trecho curto demais: {xyz.shape[0]} amostras; precisa de pelo menos "
                         f"{n_window + 3 * step} (~{(n_window + 3 * step) / fs:.1f} s)")
    bands = third_octave_bands(fs / 2)
    amp_sum = {a: 0.0 for a in AXES}
    dbs = {a: [] for a in AXES}
    freqs = None
    for s0 in starts:
        for i, a in enumerate(AXES):
            spec = dsp.spectrum(xyz[s0 : s0 + n_window, i], fs, window)
            freqs = spec.freqs
            amp_sum[a] = amp_sum[a] + spec.amp
            dbs[a].append(band_db(spec, bands))
    n = len(starts)
    edge = {}
    for en, ew in EDGE_WINDOWS:
        if xyz.shape[0] >= en * 2:
            edge[f"{en}_{ew}"] = {a: _segment_band_db(xyz[:, i], fs, en, ew, bands, overlap).mean(axis=0)
                                  for i, a in enumerate(AXES)}
    return NodeBaseline(fs, n_window, window, sensor, n, freqs, {a: amp_sum[a] / n for a in AXES}, bands,
                        {a: np.mean(dbs[a], axis=0) for a in AXES}, {a: np.std(dbs[a], axis=0) for a in AXES},
                        edge)


@dataclass
class Deviation:
    axis: str
    band_hz: float       # centro da banda com maior desvio
    delta_db: float      # atual - media da baseline
    z: float             # delta / desvio padrao da baseline (min. 1 dB)
    deltas: np.ndarray   # desvio de todas as bandas (nan = ignorada)
    bands: np.ndarray


def compare(nb: NodeBaseline, res: NodeAnalysis, floor_db: float = DEFAULT_FLOOR_DB,
            current_db: dict[str, np.ndarray] | None = None) -> dict[str, Deviation]:
    """Desvio por banda de cada eixo em relacao a baseline.

    Bandas acima da metade da taxa atual ou com potencia abaixo de floor_db
    (tanto na baseline quanto agora) sao ignoradas, para o ruido de fundo do
    sensor nao gerar falsos desvios. 'current_db' permite passar a potencia
    atual ja suavizada (ver DeviationTracker); sem ela usa-se so a janela de 'res'."""
    out = {}
    valid_f = nb.bands[:, 2] <= res.fs / 2
    for a in AXES:
        cur = current_db[a] if current_db is not None else band_db(res.axes[a].spectrum, nb.bands)
        delta = cur - nb.band_mean[a]
        ok = valid_f & ((cur > floor_db) | (nb.band_mean[a] > floor_db))
        delta = np.where(ok, delta, np.nan)
        if np.all(np.isnan(delta)):
            out[a] = Deviation(a, 0.0, 0.0, 0.0, delta, nb.bands)
            continue
        k = int(np.nanargmax(delta))
        z = delta[k] / max(nb.band_std[a][k], 1.0)
        out[a] = Deviation(a, float(nb.bands[k, 1]), float(delta[k]), float(z), delta, nb.bands)
    return out


class DeviationTracker:
    """Compara com a baseline usando a potencia por banda SUAVIZADA no tempo.

    Uma unica janela FFT tem incerteza de alguns dB em bandas estreitas (poucas
    raias), o que geraria alarmes falsos. A media exponencial (em potencia
    linear) sobre as ultimas analises reduz essa variancia; com alpha = 0,2 e
    5 analises/s a memoria e de ~1 s."""

    def __init__(self, nb: NodeBaseline, alpha: float = 0.2, floor_db: float = DEFAULT_FLOOR_DB):
        self.nb = nb
        self.alpha = alpha
        self.floor_db = floor_db
        self._ema: dict[str, np.ndarray] | None = None

    def update(self, res: NodeAnalysis) -> dict[str, Deviation]:
        p = {a: 10 ** (band_db(res.axes[a].spectrum, self.nb.bands) / 10) for a in AXES}
        if self._ema is None:
            self._ema = p
        else:
            self._ema = {a: (1 - self.alpha) * self._ema[a] + self.alpha * p[a] for a in AXES}
        cur = {a: 10 * np.log10(self._ema[a]) for a in AXES}
        return compare(self.nb, res, self.floor_db, cur)


def build_from_session(session: Path, label: str, n_window: int = 4096, window: str = "hann",
                       start_s: float = 0.0, end_s: float | None = None, rpm: str = "") -> Baseline:
    from .export import load_vib
    from .recorder import read_vib
    from .protocol import PKT_DATA
    from .sensors import sensor_name

    files = [session] if session.suffix == ".vib" else sorted(session.glob("mancal_*.vib"))
    nodes = {}
    for f in files:
        d = load_vib(f)
        if d["sample_index"].size < n_window:
            continue
        sensor = next((sensor_name(p.sensor_type) for p in read_vib(f) if p.type == PKT_DATA), "")
        idx, t = d["sample_index"], d["t_s"]
        fs = (idx[-1] - idx[0]) / (t[-1] - t[0]) if t[-1] > t[0] else float(d["fs_nominal"])
        m = (t >= start_s) & (t <= (end_s if end_s is not None else np.inf))
        node_id = int(f.stem.split("_")[-1])
        nodes[node_id] = build_node(d["xyz_g"][m], fs, n_window, window, sensor)
    if not nodes:
        raise ValueError(f"nenhum dado utilizavel em {session}")
    return Baseline(label, nodes, rpm=rpm, source=str(session))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", type=Path, help="pasta da gravacao (ou arquivo .vib)")
    ap.add_argument("--label", required=True, help="nome da baseline, ex.: 1200rpm")
    ap.add_argument("--rpm", default="")
    ap.add_argument("--start", type=float, default=0.0, help="inicio do trecho estavel (s)")
    ap.add_argument("--end", type=float, default=None, help="fim do trecho estavel (s)")
    ap.add_argument("--window", type=int, default=4096)
    ap.add_argument("--window-type", default="hann", choices=("hann", "hamming", "rect"))
    ap.add_argument("-o", "--out", type=Path, help="arquivo de saida (padrao: baselines/<label>.json)")
    a = ap.parse_args(argv)
    b = build_from_session(a.session, a.label, a.window, a.window_type, a.start, a.end, a.rpm)
    out = b.save(a.out or Path("baselines") / f"{a.label}.json")
    for nid, nb in b.nodes.items():
        print(f"mancal {nid}: {nb.sensor or '?'} fs={nb.fs:.0f} Hz, {nb.n_windows} janelas, {len(nb.bands)} bandas")
    print("baseline salva em", out)


if __name__ == "__main__":
    main()
