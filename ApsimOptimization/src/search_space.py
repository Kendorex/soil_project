from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class Technology:
    sowing_rule: str
    cultivar: str
    population: float
    depth: float
    row: float
    fertilizer_scheme_id: str

    def key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class SearchSpace:
    sowing_rules: tuple[str, ...]
    cultivars: tuple[str, ...]
    populations: tuple[float, ...]
    depths: tuple[float, ...]
    rows: tuple[float, ...]
    fertilizer_scheme_ids: tuple[str, ...]

    @property
    def dimensions(self) -> tuple[int, ...]:
        return tuple(len(values) for values in self.axes())

    def axes(self) -> tuple[Sequence[Any], ...]:
        return (self.sowing_rules, self.cultivars, self.populations, self.depths, self.rows, self.fertilizer_scheme_ids)

    def decode(self, indices: Sequence[int | float]) -> Technology:
        if len(indices) != 6:
            raise ValueError(f"Expected 6 NSGA-II indices, got {len(indices)}")
        integer_indices = tuple(int(round(value)) for value in indices)
        for index, size in zip(integer_indices, self.dimensions):
            if index < 0 or index >= size:
                raise IndexError(f"Technology index {index} outside [0, {size - 1}]")
        values = [axis[index] for axis, index in zip(self.axes(), integer_indices)]
        return Technology(*values)

    def encode(self, technology: Technology) -> tuple[int, ...]:
        return tuple(axis.index(value) for axis, value in zip(self.axes(), asdict(technology).values()))

    def technologies(self):
        for values in itertools.product(*self.axes()):
            yield Technology(*values)


def _axis(
    values: dict[str, Any],
    key: str,
    fixed_value: Any,
    expanded: bool,
    legacy_key: str | None = None,
) -> tuple[Any, ...]:
    entry = values.get(key)
    if isinstance(entry, dict):
        configured = tuple(entry.get("values", []))
        if expanded or entry.get("enabled", False):
            return configured
        return (fixed_value,)
    if legacy_key and legacy_key in values:
        return tuple(values[legacy_key])
    if isinstance(entry, list):
        return tuple(entry)
    return (fixed_value,)


def build_search_space(config: dict[str, Any], inputs: dict[str, Any], expanded: bool = False) -> SearchSpace:
    values = config["optimization"]["search_space"]
    defaults = config["apsim"]["defaults"]
    default_cultivar = config.get("cultivar", {}).get("default") or inputs["cultivars"][0]
    all_scheme_ids = tuple(item["id"] for item in inputs["schemes"])
    fertilizer_entry = values.get("fertilizer_schemes")
    if isinstance(fertilizer_entry, dict) and (expanded or fertilizer_entry.get("enabled", False)):
        fertilizer_scheme_ids = tuple(map(str, fertilizer_entry.get("values", [])))
        unknown = sorted(set(fertilizer_scheme_ids) - set(all_scheme_ids))
        if unknown:
            raise ValueError(f"Unknown fertilizer schemes in search space: {unknown}")
    else:
        fertilizer_scheme_ids = all_scheme_ids
    space = SearchSpace(
        tuple(map(str, _axis(values, "sowing_dates", defaults["sowing_date"], expanded, "sowing_rules"))),
        tuple(map(str, _axis(values, "cultivars", default_cultivar, expanded))),
        tuple(map(float, _axis(values, "populations", defaults["population"], expanded))),
        tuple(map(float, _axis(values, "depths", defaults["depth"], expanded))),
        tuple(map(float, _axis(values, "rows", defaults["row"], expanded))),
        fertilizer_scheme_ids,
    )
    if any(size == 0 for size in space.dimensions):
        raise ValueError(f"Search-space axes must be non-empty: {space.dimensions}")
    return space


def search_space_summary(space: SearchSpace, scenario_count: int) -> dict[str, Any]:
    names = ("sowing_date", "cultivar", "population", "depth", "row", "fertilizer_scheme")
    axes = space.axes()
    technologies = 1
    parameters: dict[str, dict[str, Any]] = {}
    for name, axis in zip(names, axes):
        count = len(axis)
        technologies *= count
        parameters[name] = {
            "count": count,
            "status": "fixed" if count == 1 else "varying",
            "values": list(axis),
        }
    return {
        "parameters": parameters,
        "technology_count": technologies,
        "scenario_count": scenario_count,
        "apsim_run_count": technologies * scenario_count,
    }
