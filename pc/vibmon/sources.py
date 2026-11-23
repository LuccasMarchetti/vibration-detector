"""Fontes de dados: portas seriais reais (uma thread por porta) e simulador."""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import numpy as np

from .protocol import PKT_DATA, PKT_STATUS, STATUS, Packet, StreamParser, encode_packet

log = logging.getLogger(__name__)

PacketCallback = Callable[[Packet, str], None]


class SerialSource(threading.Thread):
    """Le uma porta serial em thread dedicada. pyserial libera o GIL durante
    a leitura, entao duas portas a 921600 bps rodam em paralelo sem gargalo."""

    def __init__(self, port: str, baud: int, on_packet: PacketCallback):
        super().__init__(daemon=True, name=f"serial-{port}")
        self.port = port
        self.baud = baud
        self.on_packet = on_packet
        self.parser = StreamParser()
        self.error: str | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        import serial  # pyserial

        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, self.baud, timeout=0.05) as ser:
                    # Evita reset do ESP32 ao abrir a porta (DTR/RTS ligados ao EN/IO0)
                    ser.dtr = False
                    ser.rts = False
                    ser.reset_input_buffer()
                    self.error = None
                    log.info("porta %s aberta @ %d bps", self.port, self.baud)
                    while not self._stop.is_set():
                        data = ser.read(max(1, min(ser.in_waiting, 65536)))
                        if data:
                            for pkt in self.parser.feed(data):
                                self.on_packet(pkt, self.port)
            except Exception as exc:  # porta desconectada, permissao, etc.
                self.error = str(exc)
                log.warning("porta %s: %s — tentando reconectar", self.port, exc)
                self._stop.wait(1.0)


class SimulatedSource(threading.Thread):
    """Gera o MESMO fluxo de bytes do firmware (com CRC) para testar tudo sem hardware.

    Mancal 1: desbalanceamento leve (1x rotacao) + ruido.
    Mancal 2: 1x e 2x mais fortes + impactos periodicos de rolamento (BPFO),
              o que eleva a curtose — bom para ver os indicadores reagirem.
    """

    def __init__(self, node_id: int, on_packet: PacketCallback, *, rpm: float = 1770.0,
                 fs: float = 6660.0, block: int = 512, full_scale_g: int = 8,
                 corrupt_every: int = 0, seed: int | None = None):
        super().__init__(daemon=True, name=f"sim-{node_id}")
        self.node_id = node_id
        self.on_packet = on_packet
        self.rpm = rpm
        self.fs = fs
        self.block = block
        self.fs_g = full_scale_g
        self.corrupt_every = corrupt_every
        self.port = f"SIM{node_id}"
        self.parser = StreamParser()
        self._rng = np.random.default_rng(seed if seed is not None else node_id)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def make_block(self, first: int) -> np.ndarray:
        n = self.block
        t = (first + np.arange(n)) / self.fs
        f1 = self.rpm / 60.0
        rng = self._rng
        if self.node_id == 1:
            a1, a2, imp = 0.05, 0.01, 0.0
        else:
            a1, a2, imp = 0.12, 0.06, 0.8
        x = a1 * np.sin(2 * np.pi * f1 * t) + a2 * np.sin(2 * np.pi * 2 * f1 * t + 0.3)
        y = 0.8 * a1 * np.cos(2 * np.pi * f1 * t) + 0.02 * np.sin(2 * np.pi * 120 * t)
        z = 0.3 * a1 * np.sin(2 * np.pi * f1 * t) + 1.0  # gravidade no eixo Z
        if imp:
            bpfo = 3.58 * f1  # frequencia de defeito na pista externa (exemplo)
            phase = (t * bpfo) % 1.0
            ring = imp * np.exp(-phase * 60) * np.sin(2 * np.pi * 2800 * t)
            x, y = x + ring, y + 0.5 * ring
        sig = np.stack([x, y, z], axis=1) + rng.normal(0, 0.01, (n, 3))
        counts = np.clip(np.round(sig * 32768.0 / self.fs_g), -32768, 32767).astype("<i2")
        return counts

    def run(self) -> None:
        first, seq = 0, 0
        t0 = time.monotonic()
        next_status = t0
        while not self._stop.is_set():
            counts = self.make_block(first)
            ts = int((time.monotonic() - t0) * 1e6)
            data = encode_packet(PKT_DATA, self.node_id, counts.tobytes(), seq=seq, timestamp_us=ts,
                                 first_sample=first, full_scale_g=self.fs_g, odr_hz=int(self.fs))
            seq += 1
            first += self.block
            if self.corrupt_every and seq % self.corrupt_every == 0:
                data = data[:40] + bytes([data[40] ^ 0xFF]) + data[41:]
            if time.monotonic() >= next_status:
                st = STATUS.pack(0, 0, seq, ts // 1000, self.block, self.block * 3, 0x69)
                data += encode_packet(PKT_STATUS, self.node_id, st, seq=seq, timestamp_us=ts,
                                      full_scale_g=self.fs_g)
                seq += 1
                next_status += 1.0
            for pkt in self.parser.feed(data):
                self.on_packet(pkt, self.port)
            # mantem o ritmo de tempo real
            target = t0 + first / self.fs
            self._stop.wait(max(0.0, target - time.monotonic()))
