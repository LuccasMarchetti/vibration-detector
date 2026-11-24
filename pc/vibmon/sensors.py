"""Sensores suportados pelo firmware (espelho de firmware/components/vib_sensors).

A banda vem do proprio ESP32 (pacote STATUS); esta tabela serve para nomes,
para o simulador e como valor padrao enquanto o STATUS nao chega.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorInfo:
    code: int
    name: str
    odr_hz: int
    bandwidth_hz: int      # -3 dB na configuracao usada pelo firmware
    source: str            # de onde veio o numero


SENSORS: dict[int, SensorInfo] = {
    1: SensorInfo(1, "LSM6DS3", 6660, 3160, "ST AN4650 rev 2, tab. 9"),
    2: SensorInfo(2, "LSM6DS3TR-C", 6660, 1500, "nominal, nao conferido"),
    3: SensorInfo(3, "BMI160", 1600, 684, "Bosch BMI160 DS rev 0.9, tab. 12"),
    4: SensorInfo(4, "ICM-20948", 4500, 1209, "TDK DS-000189 v1.6, tab. 18"),
}

BY_NAME = {s.name.lower().replace("-", ""): s for s in SENSORS.values()}


def sensor_name(code: int) -> str:
    s = SENSORS.get(code)
    return s.name if s else "desconhecido"


def by_name(name: str) -> SensorInfo:
    key = name.lower().replace("-", "").replace("_", "")
    if key not in BY_NAME:
        raise ValueError(f"sensor desconhecido: {name} (opcoes: {', '.join(s.name for s in SENSORS.values())})")
    return BY_NAME[key]
