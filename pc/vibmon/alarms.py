"""Verificacao de limiares (hook de deteccao de anomalias).

Ainda NAO ha baseline do motor, entao nenhum limite vem habilitado.
Depois de levantar os valores normais (gravacoes + export), edite
config/thresholds.json e rode com --thresholds config/thresholds.json.

Cada regra compara uma metrica de um eixo (ou de todos) com um limite.
A metrica 'baseline_db' e o maior aumento (dB) de uma banda de 1/3 de oitava
em relacao a baseline carregada (ver baseline.py); sem baseline ela e ignorada.
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
    metric: str                 # rms | peak_to_peak | kurtosis | crest | band_rms | overall_rms | baseline_db
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


def _value(res: NodeAnalysis, rule: Threshold, axis: str, deviations: dict | None) -> float | None:
    if rule.metric == "overall_rms":
        return res.overall_rms
    if rule.metric == "baseline_db":
        return deviations[axis].delta_db if deviations else None
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
        self.baseline = None                  # baseline.Baseline | None
        self._trackers: dict[int, object] = {}
        self.baseline_floor_db = -60.0
        self.last_deviation: dict[int, dict] = {}  # node_id -> {eixo: Deviation}

    @classmethod
    def from_file(cls, path: str | Path) -> "AlarmChecker":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        rules = []
        for r in raw.get("rules", []):
            if r.get("band") is not None:
                r["band"] = tuple(r["band"])
            rules.append(Threshold(**r))
        return cls(rules)

    def set_baseline(self, baseline, default_limit_db: float | None = 6.0) -> None:
        """Carrega uma baseline. Se nao houver regra 'baseline_db', cria um ALERTA
        padrao (+6 dB numa banda por 5 janelas seguidas)."""
        self.baseline = baseline
        self.last_deviation.clear()
        self._trackers.clear()
        if default_limit_db and not any(r.metric == "baseline_db" for r in self.rules):
            self.rules.append(Threshold("Desvio da baseline", "baseline_db", default_limit_db,
                                        level="warning", hold=5))

    def deviation(self, res: NodeAnalysis) -> dict | None:
        if self.baseline is None or res.node_id not in self.baseline.nodes:
            return None
        from .baseline import DeviationTracker

        tr = self._trackers.get(res.node_id)
        if tr is None:
            tr = self._trackers[res.node_id] = DeviationTracker(self.baseline.nodes[res.node_id],
                                                                floor_db=self.baseline_floor_db)
        dev = tr.update(res)
        self.last_deviation[res.node_id] = dev
        return dev

    def check(self, res: NodeAnalysis) -> list[AlarmEvent]:
        """Chame a cada janela analisada. Retorna os eventos NOVOS desta janela."""
        new = []
        deviations = self.deviation(res)
        for rule in self.rules:
            if not rule.enabled or (rule.node is not None and rule.node != res.node_id):
                continue
            axes = ["all"] if rule.metric == "overall_rms" else (
                list(res.axes) if rule.axis == "any" else [rule.axis])
            for ax in axes:
                key = (rule.name, res.node_id, ax)
                v = _value(res, rule, ax, deviations)
                if v is None:
                    continue
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
