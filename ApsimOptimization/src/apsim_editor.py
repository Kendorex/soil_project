from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .report_variables import normalize_report_in_data


class ApsimEditError(ValueError):
    """Raised when a temporary APSIMX copy cannot be patched safely."""


@dataclass
class FixedParameters:
    sowing_date: str
    population: float
    depth: float
    row: float
    defaults_used: list[str]


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _named(data: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [node for node in _walk(data) if node.get("Name") == name]
    if len(matches) != 1:
        raise ApsimEditError(f"Expected exactly one APSIMX node named {name!r}; found {len(matches)}")
    return matches[0]


def _parameters(manager: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {item["Key"]: item for item in manager.get("Parameters", [])}


def read_fixed_parameters(template: Path, sowing_manager_name: str, defaults: dict[str, Any], log: Callable[[str], None]) -> FixedParameters:
    data = json.loads(template.read_text(encoding="utf-8"))
    params = _parameters(_named(data, sowing_manager_name))
    used: list[str] = []

    def get(key: str, default_key: str, cast):
        value = params.get(key, {}).get("Value")
        if value in (None, ""):
            value = defaults[default_key]
            used.append(default_key)
            log(f"WARNING: {key} missing in {template}; using configured default {value!r}")
        return cast(value)

    start = params.get("StartDate", {}).get("Value")
    end = params.get("EndDate", {}).get("Value")
    if start and end:
        sowing_date = f"{start}..{end}"
    else:
        sowing_date = str(defaults["sowing_date"])
        used.append("sowing_date")
        log(f"WARNING: sowing window missing in {template}; using configured default {sowing_date!r}")
    return FixedParameters(sowing_date, get("Population", "population", float), get("SowingDepth", "depth", float), get("RowSpacing", "row", float), used)


def create_working_copy(template: Path, destination: Path, weather: Path, cultivar: str, scheme: dict[str, Any], config: dict[str, Any], fixed: FixedParameters) -> Path:
    data = json.loads(template.read_text(encoding="utf-8"))
    normalize_report_in_data(data)
    apsim = config["apsim"]
    clock = _named(data, "Clock")
    clock["Start"] = f'{config["simulation"]["start_date"]}T00:00:00'
    clock["End"] = f'{config["simulation"]["end_date"]}T00:00:00'

    destination.parent.mkdir(parents=True, exist_ok=True)
    weather_node = _named(data, "Weather")
    weather_node["FileName"] = Path(os.path.relpath(weather, destination.parent)).as_posix()
    datastore = _named(data, "DataStore")
    datastore["FileName"] = destination.with_suffix(".db").name

    sow = _parameters(_named(data, apsim["manager_names"]["sowing"]))
    required = {"CultivarName", "Population", "SowingDepth", "RowSpacing"}
    missing = required - sow.keys()
    if missing:
        raise ApsimEditError(f"Sowing manager lacks parameters: {sorted(missing)}")
    sow["CultivarName"]["Value"] = cultivar
    sow["Population"]["Value"] = str(fixed.population)
    sow["SowingDepth"]["Value"] = str(fixed.depth)
    sow["RowSpacing"]["Value"] = str(fixed.row)
    if ".." in fixed.sowing_date:
        start, end = fixed.sowing_date.split("..", 1)
        if not {"StartDate", "EndDate"} <= sow.keys():
            raise ApsimEditError("Sowing manager lacks StartDate or EndDate")
        sow["StartDate"]["Value"] = start
        sow["EndDate"]["Value"] = end
    else:
        if not {"StartDate", "EndDate"} <= sow.keys():
            raise ApsimEditError("Sowing manager lacks StartDate or EndDate")
        # A single configured date is represented as a one-day sowing window.
        sow["StartDate"]["Value"] = fixed.sowing_date
        sow["EndDate"]["Value"] = fixed.sowing_date

    fert = _parameters(_named(data, apsim["manager_names"]["fertilizer"]))
    if not {"FertiliserType", "Amount"} <= fert.keys():
        raise ApsimEditError("Fertilizer manager lacks FertiliserType or Amount")
    events = scheme.get("events", [])
    if len(events) > 1:
        raise ApsimEditError(f"First version supports at most one fertilizer event: {scheme['id']}")
    if events:
        fert["FertiliserType"]["Value"] = str(events[0]["type_apsimx"])
        fert["Amount"]["Value"] = str(events[0]["amount"])
    else:
        fert["Amount"]["Value"] = "0"

    destination.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
