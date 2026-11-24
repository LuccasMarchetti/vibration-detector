"""Executa no PC o MESMO codigo C de processamento do ESP32 (edge_core.c).

Compila firmware/components/vib_edge/edge_core.c + vib_protocol.c como
biblioteca compartilhada (cache em ~/.cache/vibmon) e a carrega via ctypes.
Usado pelos testes (comparacao com o NumPy) e pelo simulador (--sim-edge),
para que a logica de alarme testada no PC seja byte a byte a do firmware.

Precisa de um compilador C (gcc/clang/cc). Sem ele, available() devolve False
e o resto do vibmon funciona normalmente.
"""
from __future__ import annotations

import ctypes
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

FW = Path(__file__).resolve().parents[2] / "firmware" / "components"
SOURCES = [FW / "vib_edge" / "edge_core.c", FW / "vib_protocol" / "vib_protocol.c"]
INCLUDES = [FW / "vib_edge" / "include", FW / "vib_protocol" / "include"]
_lib = None
_error = ""


def _build() -> ctypes.CDLL | None:
    global _error
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        _error = "nenhum compilador C encontrado"
        return None
    if not all(p.exists() for p in SOURCES):
        _error = "fontes do firmware nao encontradas"
        return None
    h = hashlib.sha256()
    for p in SOURCES + [i / f for i in INCLUDES for f in sorted(x.name for x in i.glob("*.h"))]:
        h.update(p.read_bytes())
    ext = ".dll" if sys.platform == "win32" else ".dylib" if sys.platform == "darwin" else ".so"
    cache = Path.home() / ".cache" / "vibmon"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"edge_{h.hexdigest()[:16]}{ext}"
    if not out.exists():
        cmd = [cc, "-O2", "-shared", "-fPIC", "-std=c11", "-o", str(out), *map(str, SOURCES),
               *[f"-I{i}" for i in INCLUDES], "-lm"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            _error = r.stderr.strip()[:500]
            return None
    lib = ctypes.CDLL(str(out))
    lib.edge_core_mem_size.restype = ctypes.c_size_t
    lib.edge_core_mem_size.argtypes = [ctypes.c_int]
    lib.edge_core_struct_size.restype = ctypes.c_size_t
    lib.edge_core_init.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.edge_core_analyze.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_float, ctypes.c_float,
                                      ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]
    lib.edge_core_reset_alarm.argtypes = [ctypes.c_void_p]
    lib.edge_core_reset_baseline.argtypes = [ctypes.c_void_p]
    lib.edge_default_config.argtypes = [ctypes.c_void_p]
    lib.edge_validate_config.argtypes = [ctypes.c_char_p]
    lib.vib_crc16.restype = ctypes.c_uint16
    lib.vib_crc16.argtypes = [ctypes.c_uint16, ctypes.c_char_p, ctypes.c_size_t]
    return lib


def lib() -> ctypes.CDLL | None:
    global _lib
    if _lib is None:
        _lib = _build() or False
    return _lib or None


def available() -> bool:
    return lib() is not None


def error() -> str:
    return _error


class NativeEdge:
    """Uma instancia de edge_core_t (estado de alarme/baseline de um mancal)."""

    def __init__(self, n: int = 2048, window_type: int = 0):
        L = lib()
        if L is None:
            raise RuntimeError(f"biblioteca nativa indisponivel: {_error}")
        self.L = L
        self.n = n
        self._core = ctypes.create_string_buffer(L.edge_core_struct_size())
        self._mem = (ctypes.c_float * (L.edge_core_mem_size(n) // 4 + 1))()
        if L.edge_core_init(self._core, n, window_type, self._mem) != 0:
            raise ValueError(f"n={n} / janela={window_type} invalidos")
        self._out = ctypes.create_string_buffer(108)

    def analyze(self, counts: np.ndarray, g_per_lsb: float, fs: float, cfg: bytes,
                baseline: bytes | None = None) -> bytes:
        xyz = np.ascontiguousarray(counts[-self.n:], dtype="<i2")
        if xyz.shape != (self.n, 3):
            raise ValueError(f"esperado ({self.n}, 3), recebido {xyz.shape}")
        self.L.edge_core_analyze(self._core, xyz.ctypes.data, g_per_lsb, fs, cfg, baseline, self._out)
        return self._out.raw

    def reset_alarm(self) -> None:
        self.L.edge_core_reset_alarm(self._core)

    def reset_baseline(self) -> None:
        self.L.edge_core_reset_baseline(self._core)


def default_config() -> bytes:
    buf = ctypes.create_string_buffer(104)
    lib().edge_default_config(buf)
    return buf.raw


def validate_config(cfg: bytes) -> int:
    return lib().edge_validate_config(cfg)


def parse_commands(data: bytes) -> list[tuple[int, int, bytes]]:
    """Passa bytes pelo parser C do firmware (vib_rx_feed); devolve (tipo, no, payload)."""
    L = lib()
    got = []
    CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hdr, payload, ctx):
        h = ctypes.string_at(hdr, 26)
        plen = int.from_bytes(h[10:12], "little")
        got.append((h[3], h[4], ctypes.string_at(payload, plen) if plen else b""))

    parser = ctypes.create_string_buffer(26 + 1024 + 2 + 64)  # vib_rx_parser_t
    L.vib_rx_init.argtypes = [ctypes.c_void_p]
    L.vib_rx_feed.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, CB, ctypes.c_void_p]
    L.vib_rx_init(parser)
    L.vib_rx_feed(parser, data, len(data), CB(cb), None)
    return got
