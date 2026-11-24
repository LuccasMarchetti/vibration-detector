"""Analise por janela: FFT + metricas por eixo, para cada mancal."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import dsp
from .node import NodeState

AXES = ("x", "y", "z")


@dataclass(slots=True)
class AxisResult:
    metrics: dsp.Metrics
    spectrum: dsp.Spectrum
    dom_freq: float
    dom_amp: float


@dataclass(slots=True)
class NodeAnalysis:
    node_id: int
    fs: float
    n: int
    unit: str
    axes: dict[str, AxisResult]
    window: str = "hann"

    @property
    def overall_rms(self) -> float:
        return float(np.sqrt(sum(a.metrics.rms ** 2 for a in self.axes.values())))


def analyze(node: NodeState, n_window: int = 2048, window: str = "hann",
            unit: str = "g", f_min: float = 2.0) -> NodeAnalysis | None:
    data = node.latest(n_window)
    if data.shape[0] < n_window:
        return None
    if unit == "m/s²":
        data = data * dsp.G
    fs = node.fs_estimate()
    axes = {}
    for i, ax in enumerate(AXES):
        x = data[:, i]
        spec = dsp.spectrum(x, fs, window)
        f, a = dsp.dominant_frequency(spec, f_min)
        axes[ax] = AxisResult(dsp.metrics(x), spec, f, a)
    return NodeAnalysis(node.node_id, fs, n_window, unit, axes, window)
