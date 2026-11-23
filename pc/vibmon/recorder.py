"""Gravacao de sessoes (baseline) em formato binario compacto + anotacoes.

Estrutura de uma sessao:
    recordings/20261123_143000_ensaio/
        session.json       metadados (inicio, fim, portas, versao do protocolo)
        mancal_1.vib       pacotes DATA/STATUS exatamente como recebidos (com CRC)
        mancal_2.vib
        annotations.csv    rotacao/velocidade do tunel e observacoes ao longo do ensaio

Guardar os pacotes crus e sem perdas (~41 kB/s por mancal, ~150 MB/h) e
permite reprocessar tudo depois com qualquer janela/FFT. Para CSV/NumPy use:
    python -m vibmon.export recordings/<sessao>
"""
from __future__ import annotations

import csv
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .protocol import NODE_NAMES, VERSION, Packet, StreamParser


class Recorder:
    def __init__(self, base_dir: str | Path = "recordings"):
        self.base = Path(base_dir)
        self.session_dir: Path | None = None
        self._files: dict[int, object] = {}
        self._lock = threading.Lock()
        self._ann = None
        self._ann_writer = None
        self._meta: dict = {}
        self.bytes_written = 0

    @property
    def recording(self) -> bool:
        return self.session_dir is not None

    def start(self, label: str = "", info: dict | None = None) -> Path:
        with self._lock:
            if self.session_dir:
                return self.session_dir
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label.strip())
            d = self.base / (f"{stamp}_{safe}" if safe else stamp)
            d.mkdir(parents=True, exist_ok=True)
            self._files = {nid: open(d / f"{NODE_NAMES[nid].lower()}.vib", "wb") for nid in NODE_NAMES}
            self._ann = open(d / "annotations.csv", "w", newline="", encoding="utf-8")
            self._ann_writer = csv.writer(self._ann)
            self._ann_writer.writerow(["pc_time_iso", "pc_time_unix", "rpm", "velocidade_m_s", "nota",
                                       "mancal1_amostra", "mancal2_amostra"])
            self._meta = {"label": label, "start": datetime.now().isoformat(), "protocol_version": VERSION,
                          **(info or {})}
            (d / "session.json").write_text(json.dumps(self._meta, indent=2, ensure_ascii=False), encoding="utf-8")
            self.bytes_written = 0
            self.session_dir = d
            return d

    def on_packet(self, pkt: Packet, port: str) -> None:
        """Listener do AcquisitionHub (roda na thread da serial)."""
        if not self.session_dir:
            return
        with self._lock:
            f = self._files.get(pkt.node_id)
            if f:
                f.write(pkt.raw)
                self.bytes_written += len(pkt.raw)

    def annotate(self, rpm: str = "", speed: str = "", note: str = "", sample_idx: dict | None = None) -> None:
        if not self.session_dir:
            return
        s = sample_idx or {}
        with self._lock:
            self._ann_writer.writerow([datetime.now().isoformat(timespec="milliseconds"), f"{time.time():.3f}",
                                       rpm, speed, note, s.get(1, ""), s.get(2, "")])
            self._ann.flush()

    def stop(self) -> Path | None:
        with self._lock:
            d = self.session_dir
            if not d:
                return None
            for f in self._files.values():
                f.close()
            self._ann.close()
            self._meta["stop"] = datetime.now().isoformat()
            self._meta["bytes"] = self.bytes_written
            (d / "session.json").write_text(json.dumps(self._meta, indent=2, ensure_ascii=False), encoding="utf-8")
            self.session_dir = None
            self._files = {}
            return d


def read_vib(path: str | Path, chunk: int = 1 << 20) -> Iterator[Packet]:
    """Itera pelos pacotes validos de um arquivo .vib."""
    parser = StreamParser()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            yield from parser.feed(data)
