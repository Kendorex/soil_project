"""Engine-independent simulation and optimization contracts.

Mass is harvested dry matter in kg/ha, area in ha, and water in mm.
Missing metrics are absent; they must never be replaced with zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol
import math


@dataclass(frozen=True)
class Capabilities:
    metrics: frozenset[str]
    decisions: frozenset[str]

    def require(self, metrics=(), decisions=()):
        missing_metrics = set(metrics) - self.metrics
        missing_decisions = set(decisions) - self.decisions
        if missing_metrics or missing_decisions:
            raise ValueError(f"Unsupported metrics: {sorted(missing_metrics)}; "
                             f"unsupported decisions: {sorted(missing_decisions)}")


@dataclass(frozen=True)
class FieldYearResult:
    field_id: str
    year: int
    area_ha: float
    harvested_dm_kg_ha: Mapping[str, float]
    metrics: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if not math.isfinite(self.area_ha) or self.area_ha <= 0:
            raise ValueError("Field area must be positive and finite")
        for name, value in self.harvested_dm_kg_ha.items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid harvested dry matter: {name}")
        if not all(math.isfinite(value) for value in self.metrics.values()):
            raise ValueError("Non-finite simulation metric")


@dataclass(frozen=True)
class SimulationResult:
    engine: str
    fields: tuple[FieldYearResult, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        keys = [(row.field_id, row.year) for row in self.fields]
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("Expected unique, nonempty field/year results")


class SimulationBackend(Protocol):
    capabilities: Capabilities

    def simulate(self, decisions: Mapping[str, Any], workdir: Path) -> SimulationResult: ...
