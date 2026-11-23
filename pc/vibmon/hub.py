"""Hub de aquisicao: liga as fontes (portas) aos estados dos mancais,
identifica automaticamente qual porta e qual mancal e repassa os pacotes
ao gravador."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from .node import NodeState
from .protocol import Packet
from .sources import SerialSource, SimulatedSource

log = logging.getLogger(__name__)


class AcquisitionHub:
    def __init__(self, buffer_seconds: float = 10.0):
        self.nodes: dict[int, NodeState] = {1: NodeState(1, buffer_seconds), 2: NodeState(2, buffer_seconds)}
        self.port_map: dict[str, int] = {}  # porta -> node_id (aprendido dos pacotes)
        self.sources: list = []
        self.packet_listeners: list[Callable[[Packet, str], None]] = []
        self.warnings: list[str] = []
        self._lock = threading.Lock()

    # chamado nas threads das fontes
    def _on_packet(self, pkt: Packet, port: str) -> None:
        node = self.nodes.get(pkt.node_id)
        if node is None:
            return
        with self._lock:
            known = self.port_map.get(port)
            if known != pkt.node_id:
                self.port_map[port] = pkt.node_id
                other = [p for p, n in self.port_map.items() if n == pkt.node_id and p != port]
                if other:
                    msg = f"ATENCAO: portas {port} e {other[0]} dizem ser {node.name} — confira o jumper de ID"
                    self.warnings.append(msg)
                    log.warning(msg)
                else:
                    log.info("%s identificado na porta %s", node.name, port)
        node.stats.port = port
        node.on_packet(pkt)
        for cb in self.packet_listeners:
            cb(pkt, port)

    def add_serial(self, port: str, baud: int) -> None:
        self.sources.append(SerialSource(port, baud, self._on_packet))

    def add_simulated(self, node_id: int, **kw) -> None:
        self.sources.append(SimulatedSource(node_id, self._on_packet, **kw))

    def start(self) -> None:
        for s in self.sources:
            s.start()

    def stop(self) -> None:
        for s in self.sources:
            s.stop()
        for s in self.sources:
            s.join(timeout=2)

    def refresh_crc_stats(self) -> None:
        for s in self.sources:
            nid = self.port_map.get(s.port)
            if nid in self.nodes:
                self.nodes[nid].stats.crc_errors = s.parser.crc_errors


def list_serial_ports() -> list[tuple[str, str]]:
    from serial.tools import list_ports

    return [(p.device, p.description) for p in list_ports.comports()]


def guess_esp32_ports() -> list[str]:
    """Portas com cara de ponte USB-Serial de ESP32 (CP210x, CH340, FTDI, USB JTAG)."""
    keys = ("cp210", "ch340", "ch910", "usb-serial", "usb serial", "uart", "ftdi", "jtag", "silicon labs", "wch")
    return [dev for dev, desc in list_serial_ports() if any(k in (desc or "").lower() for k in keys)
            or "ttyUSB" in dev or "ttyACM" in dev or "usbserial" in dev]
