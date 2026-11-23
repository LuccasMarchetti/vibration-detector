import numpy as np
import pytest

from vibmon import dsp


@pytest.mark.parametrize("win", ["hann", "hamming", "rect"])
def test_sine_amplitude_frequency_and_band_rms(win):
    fs, n, f0, A = 6660.0, 8192, 123.4, 0.5
    t = np.arange(n) / fs
    x = 1.0 + A * np.sin(2 * np.pi * f0 * t)  # DC de 1 g (gravidade)
    spec = dsp.spectrum(x, fs, win)
    f, a = dsp.dominant_frequency(spec)
    assert abs(f - f0) < fs / n
    assert abs(a - A) / A < 0.2 if win == "rect" else abs(a - A) / A < 0.16
    assert abs(dsp.band_rms(spec, 100, 150) - A / np.sqrt(2)) / (A / np.sqrt(2)) < 0.03


def test_metrics_sine_and_gaussian():
    rng = np.random.default_rng(0)
    t = np.arange(20000) / 6660
    m = dsp.metrics(3.0 + np.sin(2 * np.pi * 50 * t))
    assert m.rms == pytest.approx(1 / np.sqrt(2), rel=1e-2)
    assert m.peak_to_peak == pytest.approx(2.0, rel=1e-3)
    assert m.kurtosis == pytest.approx(1.5, rel=2e-2)  # senoide pura
    g = dsp.metrics(rng.normal(0, 1, 200000))
    assert g.kurtosis == pytest.approx(3.0, abs=0.05)  # gaussiano


def test_impulsive_signal_raises_kurtosis():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.01, 6660)
    x[::300] += 0.5
    assert dsp.metrics(x).kurtosis > 10
