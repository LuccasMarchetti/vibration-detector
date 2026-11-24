"""Estado de cada mancal: buffer circular de amostras + diagnosticos do link."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .protocol import FLAG_BLOCK_DROP, FLAG_FIFO_OVERRUN, NODE_NAMES, PKT_DATA, PKT_STATUS, Packet
from .sensors import SENSORS, sensor_name


@dataclass
class LinkStats:
    packets: int = 0
    samples: int = 0
    lost_samples: int = 0       # lacunas detectadas pelo indice first_sample
    seq_gaps: int = 0           # pacotes perdidos (campo seq)
    overrun_flags: int = 0
    drop_flags: int = 0
    crc_errors: int = 0         # preenchido pela fonte (porta serial)
    last_rx: float = 0.0
    port: str = ""
    status: dict = field(default_factory=dict)


class NodeState:
    """Thread-safe: a thread da serial escreve, a GUI/analise le."""

    def __init__(self, node_id: int, seconds: float = 60.0, fs_nominal: float = 6660.0):
        self.node_id = node_id
        self.name = NODE_NAMES.get(node_id, f"NO_{node_id}")
        self.fs_nominal = fs_nominal
        self.full_scale_g = 8
        self.sensor_type = 0
        self._cap = int(seconds * fs_nominal)
        self._buf = np.zeros((self._cap, 3), dtype=np.float32)
        self._w = 0            # total de amostras escritas
        self._expected = None  # proximo first_sample esperado
        self._last_seq = None
        self._lock = threading.Lock()
        self._fs_hist: deque[tuple[int, int]] = deque(maxlen=200)  # (t_us, indice da ultima amostra)
        self.stats = LinkStats()

    # ------------------------------------------------------------------ escrita
    def on_packet(self, pkt: Packet) -> None:
        st = self.stats
        st.last_rx = time.monotonic()
        if self._last_seq is not None:
            gap = (pkt.seq - self._last_seq - 1) & 0xFFFF
            if 0 < gap < 1000:
                st.seq_gaps += gap
        self._last_seq = pkt.seq

        if pkt.type == PKT_STATUS:
            st.status = pkt.status()
            if pkt.sensor_type:
                self.sensor_type = pkt.sensor_type
            return
        if pkt.type != PKT_DATA:
            return

        st.packets += 1
        self.full_scale_g = pkt.full_scale_g
        self.sensor_type = pkt.sensor_type
        if float(pkt.odr_hz) != self.fs_nominal:
            self.fs_nominal = float(pkt.odr_hz)
            self._fs_hist.clear()
        if pkt.flags & FLAG_FIFO_OVERRUN:
            st.overrun_flags += 1
        if pkt.flags & FLAG_BLOCK_DROP:
            st.drop_flags += 1
        if self._expected is not None:
            lost = (pkt.first_sample - self._expected) & 0xFFFFFFFF
            if 0 < lost < 0x80000000:
                st.lost_samples += lost
            elif lost != 0:  # indice voltou: ESP32 reiniciou
                self._fs_hist.clear()
        x = pkt.samples_g()
        n = x.shape[0]
        self._expected = (pkt.first_sample + n) & 0xFFFFFFFF
        self._fs_hist.append((pkt.timestamp_us, pkt.first_sample + n - 1))
        st.samples += n
        with self._lock:
            i = self._w % self._cap
            first = min(n, self._cap - i)
            self._buf[i : i + first] = x[:first]
            if first < n:
                self._buf[: n - first] = x[first:]
            self._w += n

    # ------------------------------------------------------------------ leitura
    def latest(self, n: int) -> np.ndarray:
        """Ultimas n amostras (n, 3) em g; menos se ainda nao houver n."""
        with self._lock:
            n = min(n, self._w, self._cap)
            end = self._w % self._cap
            if n <= end:
                return self._buf[end - n : end].copy()
            return np.concatenate((self._buf[self._cap - (n - end) :], self._buf[:end]))

    @property
    def total_samples(self) -> int:
        return self._w

    def fs_estimate(self) -> float:
        """Taxa real estimada pelos timestamps do ESP32 (o ODR do MEMS tem tolerancia de alguns %)."""
        h = list(self._fs_hist)
        if len(h) < 10:
            return self.fs_nominal
        (t0, i0), (t1, i1) = h[0], h[-1]
        if t1 <= t0 or i1 <= i0:
            return self.fs_nominal
        return (i1 - i0) / ((t1 - t0) * 1e-6)

    @property
    def sensor_name(self) -> str:
        return sensor_name(self.sensor_type)

    @property
    def bandwidth_hz(self) -> float:
        """Banda informada pelo ESP32 (STATUS) ou, na falta dela, a da tabela."""
        bw = self.stats.status.get("bandwidth_hz", 0)
        if bw:
            return float(bw)
        info = SENSORS.get(self.sensor_type)
        return float(info.bandwidth_hz) if info else self.fs_nominal / 2

    def online(self, timeout: float = 2.0) -> bool:
        return (time.monotonic() - self.stats.last_rx) < timeout
