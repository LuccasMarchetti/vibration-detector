"""Fontes de dados: portas seriais reais (uma thread por porta) e simulador."""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from .protocol import (COMMAND_TYPES, PKT_ACK, PKT_CONFIG, PKT_DATA, PKT_METRICS, PKT_STATUS, STATUS, Packet,
                       StreamParser, encode_packet)
from .sensors import SensorInfo, by_name

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
        self._ser = None
        self._wlock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()

    def send(self, data: bytes) -> bool:
        """Envia um comando ao ESP32 (pode ser chamado de outra thread)."""
        with self._wlock:
            if self._ser is None:
                return False
            self._ser.write(data)
            return True

    def run(self) -> None:
        import serial  # pyserial

        while not self._stop.is_set():
            try:
                with serial.serial_for_url(self.port, self.baud, timeout=0.05) as ser:
                    # Evita reset do ESP32 ao abrir a porta (DTR/RTS ligados ao EN/IO0)
                    ser.dtr = False
                    ser.rts = False
                    ser.reset_input_buffer()
                    self._ser = ser
                    self.error = None
                    log.info("porta %s aberta @ %d bps", self.port, self.baud)
                    while not self._stop.is_set():
                        data = ser.read(max(1, min(ser.in_waiting, 65536)))
                        if data:
                            for pkt in self.parser.feed(data):
                                self.on_packet(pkt, self.port)
            except Exception as exc:  # porta desconectada, permissao, etc.
                self._ser = None
                self.error = str(exc)
                log.warning("porta %s: %s — tentando reconectar", self.port, exc)
                self._stop.wait(1.0)


class SimulatedSource(threading.Thread):
    """Gera o MESMO fluxo de bytes do firmware (com CRC) para testar tudo sem hardware.

    Mancal 1: desbalanceamento leve (1x rotacao) + ruido.
    Mancal 2: 1x e 2x mais fortes + impactos periodicos de rolamento (BPFO),
              o que eleva a curtose — bom para ver os indicadores reagirem.

    sensor: emula a taxa e a banda do sensor (LSM6DS3, BMI160, ICM-20948).
            Componentes acima da banda sao atenuados como um filtro de 1a ordem.
    fault_after_s: apos esse tempo surge um defeito novo (tom em 3,2x rotacao +
            ressonancia), para testar a comparacao com a baseline.
    edge:   emula tambem a analise local do ESP32 (FFT/alarmes) rodando o
            MESMO codigo C do firmware (edge_core.c) via edge_native. Precisa de
            um compilador C; sem ele o simulador funciona sem essa parte.
    """

    def __init__(self, node_id: int, on_packet: PacketCallback, *, rpm: float = 1770.0,
                 sensor: str = "LSM6DS3", block: int = 512, full_scale_g: int = 8,
                 corrupt_every: int = 0, seed: int | None = None, fault_after_s: float = 0.0,
                 edge: bool = True):
        super().__init__(daemon=True, name=f"sim-{node_id}")
        self.node_id = node_id
        self.on_packet = on_packet
        self.rpm = rpm
        self.sensor: SensorInfo = by_name(sensor)
        self.fs = float(self.sensor.odr_hz)
        self.fault_after_s = fault_after_s
        self.block = block
        self.fs_g = full_scale_g
        self.corrupt_every = corrupt_every
        self.port = f"SIM{node_id}"
        self.parser = StreamParser()
        self._rng = np.random.default_rng(seed if seed is not None else node_id)
        self._stop = threading.Event()
        self._replies: queue.Queue[bytes] = queue.Queue()
        self._cmd_parser = StreamParser(accept=COMMAND_TYPES)
        self.edge = None
        if edge:
            from . import edge_native

            if edge_native.available():
                self.edge = _VirtualEdge(node_id, self.fs, full_scale_g)
            else:
                log.info("simulador sem analise local: %s", edge_native.error())

    def stop(self) -> None:
        self._stop.set()

    def send(self, data: bytes) -> bool:
        """Recebe comandos como o ESP32 real (ver _VirtualEdge.command)."""
        if self.edge is None:
            return False
        for pkt in self._cmd_parser.feed(data):
            reply = self.edge.command(pkt)
            if reply:
                self._replies.put(reply)
        return True

    def _gain(self, f: float) -> float:
        """Atenuacao de 1a ordem acima da banda do sensor."""
        return 1.0 / np.sqrt(1.0 + (f / self.sensor.bandwidth_hz) ** 2)

    def make_block(self, first: int) -> np.ndarray:
        n = self.block
        t = (first + np.arange(n)) / self.fs
        f1 = self.rpm / 60.0
        g = self._gain
        rng = self._rng
        if self.node_id == 1:
            a1, a2, imp = 0.05, 0.01, 0.0
        else:
            a1, a2, imp = 0.12, 0.06, 0.8
        x = a1 * np.sin(2 * np.pi * f1 * t) + a2 * g(2 * f1) * np.sin(2 * np.pi * 2 * f1 * t + 0.3)
        y = 0.8 * a1 * np.cos(2 * np.pi * f1 * t) + 0.02 * g(120) * np.sin(2 * np.pi * 120 * t)
        z = 0.3 * a1 * np.sin(2 * np.pi * f1 * t) + 1.0  # gravidade no eixo Z
        if imp:
            bpfo = 3.58 * f1  # frequencia de defeito na pista externa (exemplo)
            f_res = min(2800.0, 0.8 * self.sensor.bandwidth_hz)  # ressonancia visivel pelo sensor
            phase = (t * bpfo) % 1.0
            ring = imp * g(f_res) * np.exp(-phase * 60) * np.sin(2 * np.pi * f_res * t)
            x, y = x + ring, y + 0.5 * ring
        if self.fault_after_s and t[0] >= self.fault_after_s:
            ff = 3.2 * f1
            x = x + 0.04 * g(ff) * np.sin(2 * np.pi * ff * t)
            y = y + 0.03 * g(ff) * np.sin(2 * np.pi * ff * t + 1.0)
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
                                 first_sample=first, full_scale_g=self.fs_g, odr_hz=int(self.fs),
                                 sensor_type=self.sensor.code)
            seq += 1
            first += self.block
            if self.corrupt_every and seq % self.corrupt_every == 0:
                data = data[:40] + bytes([data[40] ^ 0xFF]) + data[41:]
            if time.monotonic() >= next_status:
                chip_id = {1: 0x69, 2: 0x6A, 3: 0xD1, 4: 0xEA}[self.sensor.code]
                st = STATUS.pack(0, 0, seq, ts // 1000, self.block, self.block * 3, chip_id,
                                 self.sensor.code, self.sensor.bandwidth_hz)
                data += encode_packet(PKT_STATUS, self.node_id, st, seq=seq, timestamp_us=ts,
                                      full_scale_g=self.fs_g, odr_hz=int(self.fs),
                                      sensor_type=self.sensor.code)
                seq += 1
                next_status += 1.0
            if self.edge is not None:
                data += self.edge.feed(counts, ts)
            while not self._replies.empty():
                data += self._replies.get_nowait()
            for pkt in self.parser.feed(data):
                self.on_packet(pkt, self.port)
            # mantem o ritmo de tempo real
            target = t0 + first / self.fs
            self._stop.wait(max(0.0, target - time.monotonic()))


class _VirtualEdge:
    """Imita edge_monitor.c: janela deslizante (passo N/2), edge_core_analyze()
    em C e tratamento de comandos com as mesmas respostas do firmware."""

    def __init__(self, node_id: int, fs: float, full_scale_g: int):
        from . import edge_native

        self.native = edge_native
        self.node_id = node_id
        self.fs = fs
        self.gpl = full_scale_g / 32768.0
        self.cfg = edge_native.default_config()
        self.base: bytes | None = None
        self.n_bands = 0
        self._make()
        self._buf = np.zeros((0, 3), dtype="<i2")
        self._since = 0

    def _make(self) -> None:
        n = int.from_bytes(self.cfg[2:4], "little")
        self.core = self.native.NativeEdge(n, self.cfg[4])
        self.n = n

    def feed(self, counts: np.ndarray, ts: int) -> bytes:
        self._buf = np.concatenate([self._buf, counts])[-self.n:]
        self._since += counts.shape[0]
        if not self.cfg[1] or self._buf.shape[0] < self.n or self._since < self.n // 2:
            return b""
        self._since = 0
        match = self.base is not None and self.base[1:4] == bytes([self.cfg[4]]) + self.cfg[2:4]
        m = bytearray(self.core.analyze(self._buf, self.gpl, self.fs, self.cfg, self.base if match else None))
        if self.base is not None and not match:
            m[7] |= 0x10  # baseline de outra janela: ignorada
        outputs, state = self.cfg[6], m[6]
        if outputs & 0x02 and state == 2:
            m[7] |= 0x08  # saida de parada ativa
        return encode_packet(PKT_METRICS, self.node_id, bytes(m), timestamp_us=ts, odr_hz=0, sensor_type=0)

    def command(self, pkt: Packet) -> bytes | None:
        from .edge import BASELINE, CONFIG

        if pkt.node_id not in (0, self.node_id):
            return None
        cmd, pl, status = pkt.type, pkt.payload, 0
        if cmd == 0x81:
            if len(pl) != CONFIG.size:
                status = 1
            elif self.native.validate_config(pl) != 0:
                status = 2
            else:
                old = self.cfg
                self.cfg = bytes(pl)
                if old[2:5] != self.cfg[2:5]:
                    self._make()
                self.core.reset_alarm()
        elif cmd == 0x82:
            n_win = int.from_bytes(pl[2:4], "little") if len(pl) >= 4 else 0
            if len(pl) != BASELINE.size or pl[0] > 32 or pl[1] > 2 or n_win not in (1024, 2048):
                status = 1 if len(pl) != BASELINE.size else 2
            else:
                self.base, self.n_bands = bytes(pl), pl[0]
                self.core.reset_baseline()
        elif cmd == 0x83:
            self.base, self.n_bands = None, 0
        elif cmd == 0x84:
            self.core.reset_alarm()
        elif cmd == 0x85:
            return encode_packet(PKT_CONFIG, self.node_id, self.cfg + bytes([self.n_bands]), odr_hz=0, sensor_type=0)
        elif cmd == 0x86:
            status = 0 if pl[:1] in (b"\x01", b"\x02") else 5
        else:
            status = 3
        ack = bytes([cmd, status]) + pkt.seq.to_bytes(2, "little")
        return encode_packet(PKT_ACK, self.node_id, ack, odr_hz=0, sensor_type=0)
