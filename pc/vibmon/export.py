"""Exporta uma sessao gravada (.vib) para CSV ou NumPy (.npz).

Uso:
    python -m vibmon.export recordings/20261123_143000_ensaio            # CSV
    python -m vibmon.export recordings/20261123_143000_ensaio --format npz

Colunas do CSV: sample_index, t_s, x_g, y_g, z_g
  sample_index = indice global da amostra no ESP32 (lacunas = amostras perdidas)
  t_s          = tempo relativo calculado a partir do timestamp do ESP32
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .protocol import PKT_DATA
from .recorder import read_vib


def load_vib(path: str | Path) -> dict[str, np.ndarray]:
    idx, ts, xyz = [], [], []
    fs = 6660.0
    for p in read_vib(path):
        if p.type != PKT_DATA:
            continue
        n = p.n_samples
        fs = float(p.odr_hz)
        idx.append(p.first_sample + np.arange(n, dtype=np.int64))
        # timestamp do pacote ~ ultima amostra do bloco
        ts.append(p.timestamp_us * 1e-6 - (n - 1 - np.arange(n)) / fs)
        xyz.append(p.samples_g())
    if not idx:
        return {"sample_index": np.zeros(0, np.int64), "t_s": np.zeros(0), "xyz_g": np.zeros((0, 3), np.float32)}
    t = np.concatenate(ts)
    return {"sample_index": np.concatenate(idx), "t_s": t - t[0], "xyz_g": np.concatenate(xyz), "fs_nominal": fs}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", type=Path, help="pasta da sessao (ou um arquivo .vib)")
    ap.add_argument("--format", choices=("csv", "npz"), default="csv")
    a = ap.parse_args(argv)
    files = [a.session] if a.session.suffix == ".vib" else sorted(a.session.glob("*.vib"))
    for f in files:
        d = load_vib(f)
        n = d["sample_index"].size
        if a.format == "npz":
            out = f.with_suffix(".npz")
            np.savez_compressed(out, **d)
        else:
            out = f.with_suffix(".csv")
            table = np.column_stack([d["sample_index"], d["t_s"], d["xyz_g"]])
            np.savetxt(out, table, delimiter=",", header="sample_index,t_s,x_g,y_g,z_g", comments="",
                       fmt=["%d", "%.6f", "%.5f", "%.5f", "%.5f"])
        print(f"{f.name}: {n} amostras -> {out}")


if __name__ == "__main__":
    main()
