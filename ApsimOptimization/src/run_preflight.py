from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


class RunPreflightError(ValueError):
    """Raised when a prepared APSIMX input is unsafe or non-portable."""


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def _single_named(data: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [node for node in _walk(data) if node.get("Name") == name]
    if len(matches) != 1:
        raise RunPreflightError(f"Expected exactly one APSIMX node named {name!r}; found {len(matches)}")
    return matches[0]


def _absolute_on_any_platform(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _parse_clock(value: Any, label: str) -> date:
    try:
        return datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError) as exc:
        raise RunPreflightError(f"Clock.{label} is not an ISO date: {value!r}") from exc


def _met_dates(path: Path) -> set[date]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_index = None
    year_index = day_index = None
    for index, line in enumerate(lines):
        fields = line.strip().casefold().split()
        if "year" in fields and "day" in fields:
            header_index = index
            year_index = fields.index("year")
            day_index = fields.index("day")
            break
    if header_index is None or year_index is None or day_index is None:
        raise RunPreflightError(f"Weather file lacks exact 'year day' columns: {path}")

    dates: set[date] = set()
    for line in lines[header_index + 1:]:
        fields = line.strip().split()
        if not fields or fields[0].startswith(("!", "#", "(")):
            continue
        try:
            year = int(float(fields[year_index]))
            day_of_year = int(float(fields[day_index]))
            dates.add(date(year, 1, 1) + timedelta(days=day_of_year - 1))
        except (IndexError, TypeError, ValueError):
            continue
    if not dates:
        raise RunPreflightError(f"Weather file contains no readable daily rows: {path}")
    return dates


def _validate_soil(data: dict[str, Any]) -> list[str]:
    physical = _single_named(data, "Physical")
    water = _single_named(data, "Water")
    names = ("Thickness", "BD", "AirDry", "LL15", "DUL", "SAT", "KS")
    arrays = {name: list(physical.get(name) or []) for name in names}
    initial = list(water.get("InitialValues") or [])
    layer_count = len(arrays["Thickness"])
    if layer_count == 0:
        raise RunPreflightError("Soil Physical.Thickness has no layers")
    invalid_lengths = {name: len(values) for name, values in arrays.items() if len(values) != layer_count}
    if len(initial) != layer_count:
        invalid_lengths["Water.InitialValues"] = len(initial)
    if invalid_lengths:
        raise RunPreflightError(
            f"Soil arrays must all have {layer_count} layers; invalid lengths={invalid_lengths}"
        )

    warnings: list[str] = []
    for index in range(layer_count):
        layer = index + 1
        try:
            thickness = float(arrays["Thickness"][index])
            bd = float(arrays["BD"][index])
            air_dry = float(arrays["AirDry"][index])
            ll15 = float(arrays["LL15"][index])
            dul = float(arrays["DUL"][index])
            sat = float(arrays["SAT"][index])
            ks = float(arrays["KS"][index])
            sw = float(initial[index])
        except (TypeError, ValueError) as exc:
            raise RunPreflightError(f"Soil layer {layer} contains a non-numeric physical value") from exc
        if thickness <= 0 or bd <= 0 or ks <= 0:
            raise RunPreflightError(
                f"Soil layer {layer} requires positive Thickness, BD and KS; "
                f"got Thickness={thickness}, BD={bd}, KS={ks}"
            )
        if not 0 <= air_dry <= ll15 < dul < sat <= 1:
            raise RunPreflightError(
                f"Soil layer {layer} violates AirDry <= LL15 < DUL < SAT <= 1: "
                f"{air_dry}, {ll15}, {dul}, {sat}"
            )
        if not air_dry <= sw <= sat:
            raise RunPreflightError(
                f"Soil layer {layer} InitialValues={sw} is outside AirDry..SAT={air_dry}..{sat}"
            )
        if not 0.5 <= bd <= 2.0:
            warnings.append(f"Soil layer {layer} BD={bd} g/cm3 is outside the broad 0.5..2.0 plausibility range")
    return warnings


def validate_prepared_apsimx(
    apsimx_path: Path,
    quality_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate portable paths, weather coverage and soil invariants before APSIMX."""
    quality_config = quality_config or {}
    data = json.loads(apsimx_path.read_text(encoding="utf-8"))
    clock = _single_named(data, "Clock")
    weather = _single_named(data, "Weather")
    datastore = _single_named(data, "DataStore")
    start = _parse_clock(clock.get("Start"), "Start")
    end = _parse_clock(clock.get("End"), "End")
    if end < start:
        raise RunPreflightError(f"Clock.End {end} precedes Clock.Start {start}")

    warnings: list[str] = []
    paths = {
        "Weather.FileName": str(weather.get("FileName") or ""),
        "DataStore.FileName": str(datastore.get("FileName") or ""),
    }
    for label, value in paths.items():
        if not value:
            if label == "DataStore.FileName" and quality_config.get("allow_gui_datastore_default", False):
                warnings.append(
                    "DataStore.FileName was cleared by GUI; APSIM default database naming is accepted "
                    "because the original package passed manifest preflight"
                )
                continue
            raise RunPreflightError(f"{label} is empty")
        if _absolute_on_any_platform(value):
            if quality_config.get("require_relative_paths", True):
                raise RunPreflightError(
                    f"{label} must be relative for a portable run package; got {value!r}"
                )
            warnings.append(
                f"{label} became absolute after GUI execution: {value!r}; "
                "the original package portability must be verified from run_manifest.json"
            )

    weather_path = Path(paths["Weather.FileName"])
    if not weather_path.is_absolute():
        weather_path = (apsimx_path.parent / weather_path).resolve()
    if not weather_path.is_file():
        raise RunPreflightError(f"Weather file referenced by APSIMX does not exist: {weather_path}")

    if quality_config.get("require_weather_clock_coverage", True) and weather_path.suffix.casefold() == ".met":
        dates = _met_dates(weather_path)
        required_days = (end - start).days + 1
        missing = [start + timedelta(days=offset) for offset in range(required_days) if start + timedelta(days=offset) not in dates]
        if missing:
            preview = ", ".join(day.isoformat() for day in missing[:5])
            raise RunPreflightError(
                f"Weather does not cover every Clock day; missing={len(missing)}, first={preview}"
            )
        if quality_config.get("warn_missing_weather_boundary_days", True):
            absent = [day for day in (start - timedelta(days=1), end + timedelta(days=1)) if day not in dates]
            if absent:
                warnings.append(
                    "Weather lacks APSIM boundary day(s): " + ", ".join(day.isoformat() for day in absent)
                )

    if quality_config.get("validate_soil_physical_bounds", True):
        warnings.extend(_validate_soil(data))

    return {
        "status": "passed",
        "apsimx": str(apsimx_path),
        "clock_start": start.isoformat(),
        "clock_end": end.isoformat(),
        "weather_file": str(weather_path),
        "warnings": warnings,
    }
