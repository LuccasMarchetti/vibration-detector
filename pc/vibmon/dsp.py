"""Processamento de sinal: remocao de DC, janelamento, FFT e metricas."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

G = 9.80665  # m/s^2 por g


def make_window(name: str, n: int) -> np.ndarray:
    name = name.lower()
    if name == "hann":
        return np.hanning(n)
    if name == "hamming":
        return np.hamming(n)
    if name in ("rect", "retangular", "none"):
        return np.ones(n)
    raise ValueError(f"janela desconhecida: {name}")


@dataclass(slots=True)
class Spectrum:
    freqs: np.ndarray  # Hz
    amp: np.ndarray    # amplitude de pico (mesma unidade do sinal), corrigida pelo ganho coerente
    power: np.ndarray  # potencia por bin (unidade^2); soma de uma banda = RMS^2 da banda


def spectrum(x: np.ndarray, fs: float, window: str = "hann") -> Spectrum:
    """FFT unilateral de um sinal 1-D (o DC e removido antes)."""
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    w = make_window(window, n)
    X = np.fft.rfft((x - x.mean()) * w)
    mag = np.abs(X)
    amp = 2.0 * mag / w.sum()
    amp[0] /= 2.0
    power = 2.0 * mag**2 / (n * np.sum(w**2))
    power[0] /= 2.0
    if n % 2 == 0:
        amp[-1] /= 2.0
        power[-1] /= 2.0
    return Spectrum(np.fft.rfftfreq(n, 1.0 / fs), amp, power)


def band_rms(spec: Spectrum, f_lo: float, f_hi: float) -> float:
    """RMS da vibracao dentro da banda [f_lo, f_hi] Hz."""
    m = (spec.freqs >= f_lo) & (spec.freqs <= f_hi)
    return float(np.sqrt(spec.power[m].sum()))


def dominant_frequency(spec: Spectrum, f_min: float = 2.0) -> tuple[float, float]:
    """(frequencia, amplitude) do maior pico acima de f_min, com interpolacao parabolica."""
    m = spec.freqs >= f_min
    if not m.any():
        return 0.0, 0.0
    idx0 = int(np.argmax(m))
    k = idx0 + int(np.argmax(spec.amp[m]))
    f = spec.freqs[k]
    if 0 < k < spec.amp.size - 1:
        a, b, c = spec.amp[k - 1], spec.amp[k], spec.amp[k + 1]
        den = a - 2 * b + c
        if den != 0:
            f = f + 0.5 * (a - c) / den * (spec.freqs[1] - spec.freqs[0])
    return float(f), float(spec.amp[k])


@dataclass(slots=True)
class Metrics:
    rms: float
    peak_to_peak: float
    kurtosis: float  # Pearson (sinal gaussiano = 3; impactos de rolamento -> >> 3)
    crest: float     # fator de crista = pico / RMS


def metrics(x: np.ndarray) -> Metrics:
    x = np.asarray(x, dtype=np.float64)
    d = x - x.mean()
    m2 = np.mean(d**2)
    rms = float(np.sqrt(m2))
    kurt = float(np.mean(d**4) / m2**2) if m2 > 0 else 0.0
    crest = float(np.max(np.abs(d)) / rms) if rms > 0 else 0.0
    return Metrics(rms, float(d.max() - d.min()), kurt, crest)
