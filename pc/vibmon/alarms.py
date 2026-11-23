"""Verificacao de limiares (hook de deteccao de anomalias).

Ainda NAO ha baseline do motor, entao nenhum limite vem habilitado.
Depois de levantar os valores normais (gravacoes + export), edite
config/thresholds.json e rode com --thresholds config/thresholds.json.

Cada regra compara uma metrica de um eixo (ou de todos) com um limite.
Uma regra so dispara depois de 'hold' janelas consecutivas acima do limite
(evita alarme por um transiente isolado). Os callbacks em AlarmChecker.hooks
recebem cada AlarmEvent — e ai que entram o alerta sonoro da GUI e, no
futuro, um intertravamento (rele, CLP, comando serial ao inversor...).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import dsp
from .analysis import NodeAnalysis


@dataclass
class Threshold:
    name: str
    metric: str                 # rms | peak_to_peak | kurtosis | crest | band_rms | overall_rms
    limit: float
    node: int | None = None     # 1, 2 ou None (ambos)
    axis: str = "any"           # x | y | z | any
    band: tuple[float, float] | None = None  # obrigatorio para band_rms (Hz)
    level: str = "alarm"        # warning | alarm
    hold: int = 3
    enabled: bool = True


@dataclass
class AlarmEvent:
    rule: Threshold
    node_id: int
    axis: str
    value: float
    time: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return (f"[{self.rule.level.upper()}] {self.rule.name}: mancal {self.node_id} eixo {self.axis} "
                f"{self.rule.metric}={self.value:.4g} > {self.rule.limit:.4g}")


def _value(res: NodeAnalysis, rule: Threshold, axis: str) -> float:
    if rule.metric == "overall_rms":
        return res.overall_rms
    ar = res.axes[axis]
    if rule.metric == "band_rms":
        return dsp.band_rms(ar.spectrum, *rule.band)
    return float(getattr(ar.metrics, rule.metric))


class AlarmChecker:
    def __init__(self, rules: list[Threshold] | None = None):
        self.rules = rules or []
        self.hooks: list[Callable[[AlarmEvent], None]] = []
        self._count: dict[tuple, int] = {}
        self.active: dict[tuple, AlarmEvent] = {}

    @classmethod
    def from_file(cls, path: str | Path) -> "AlarmChecker":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = []
        for r in raw.get("rules", []):
            if r.get("band") is not None:
                r["band"] = tuple(r["band"])
            rules.append(Threshold(**r))
        return cls(rules)

    def check(self, res: NodeAnalysis) -> list[AlarmEvent]:
        """Chame a cada janela analisada. Retorna os eventos NOVOS desta janela."""
        new = []
        for rule in self.rules:
            if not rule.enabled or (rule.node is not None and rule.node != res.node_id):
                continue
            axes = ["all"] if rule.metric == "overall_rms" else (
                list(res.axes) if rule.axis == "any" else [rule.axis])
            for ax in axes:
                key = (rule.name, res.node_id, ax)
                v = _value(res, rule, ax)
                if v > rule.limit:
                    self._count[key] = self._count.get(key, 0) + 1
                    if self._count[key] >= rule.hold and key not in self.active:
                        ev = AlarmEvent(rule, res.node_id, ax, v)
                        self.active[key] = ev
                        new.append(ev)
                        for h in self.hooks:
                            h(ev)
                else:
                    self._count[key] = 0
                    self.active.pop(key, None)
        return new

    def active_for(self, node_id: int) -> list[AlarmEvent]:
        return [e for k, e in self.active.items() if k[1] == node_id]
