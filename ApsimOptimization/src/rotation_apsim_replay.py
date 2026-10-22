"""Patch the calibrated APSIM model with a selected multi-year crop map."""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

from .multifield_model import ROTATION_KEYS, child, kind, parameters, read_json, walk, write_json
from .met_weather import calculate_tav_amp, read_met


PROFILE_TO_ROTATION_KEY = {
    "model_crop": "RotationCrops",
    "cultivar": "RotationCultivars",
    "sowing_depth": "RotationSowingDepths",
    "row_spacing": "RotationRowSpacings",
    "population": "RotationPopulations",
    "sowing_date": "RotationSowingDates",
    "harvest_date": "RotationHarvestDates",
}


def load_rotation_map(path: str | Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported selected-rotation schema")
    start = int(payload["start_year"])
    end = int(payload["end_year"])
    years = end - start + 1
    fields = payload.get("fields") or {}
    if not fields or any(len(sequence) != years for sequence in fields.values()):
        raise ValueError("Every selected-rotation field must cover the full period")
    return payload


def resolve_profile(profiles: dict, state_id: str, year: int) -> dict:
    if state_id not in profiles:
        raise ValueError(f"Missing APSIM profile for rotation state: {state_id}")
    source = profiles[state_id]
    result = {}
    for profile_key in PROFILE_TO_ROTATION_KEY:
        by_year = source.get(profile_key + "_by_year")
        value = by_year.get(year) if isinstance(by_year, dict) else None
        if value is None:
            value = source.get(profile_key)
        if value is None:
            raise ValueError(f"{state_id}/{year}: missing {profile_key}")
        result[profile_key] = str(value)
    result["primary_yield_components"] = list(source["primary_yield_components"])
    result["product"] = source["product"]
    crops = result["model_crop"].split("+")
    for key in ("cultivar", "sowing_depth", "row_spacing", "population"):
        if len(result[key].split(";")) != len(crops):
            raise ValueError(f"{state_id}/{year}: {key} component count differs")
    return result


def patch_selected_rotation(
    base_model: str | Path, rotation_map: dict, profiles: dict,
    output_path: str | Path, warmup: dict | None = None,
) -> dict:
    data = read_json(base_model)
    simulations = [
        node for _, node in walk(data)
        if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)
    ]
    if len(simulations) != 1:
        raise ValueError("Expected one enabled APSIM Simulation")
    sim = simulations[0]
    zones = {
        node["Name"]: node for node in sim.get("Children", [])
        if kind(node) == "Models.Core.Zone" and node.get("Enabled", True)
    }
    if set(zones) != set(rotation_map["fields"]):
        raise ValueError("Selected map fields differ from APSIM zones")
    clock = child(sim, "Clock")
    clock_start = int(str(clock["Start"])[:4])
    if clock_start != int(rotation_map["start_year"]):
        raise ValueError("Selected map start year differs from APSIM clock")

    changes = []
    warmup_enabled = bool(warmup and warmup.get("enabled", False))
    simulation_start = clock_start
    if warmup_enabled:
        simulation_start_date = dt.date.fromisoformat(str(warmup["start_date"]))
        simulation_start = simulation_start_date.year
        if simulation_start != clock_start - 1:
            raise ValueError("Warm-up must start in the year immediately before the selected map")
        if set(warmup.get("field_states", {})) != set(zones):
            raise ValueError("Warm-up field states must cover every APSIM zone exactly once")
        clock["Start"] = f"{simulation_start_date.isoformat()}T00:00:00"
        # Perennials are artificially instantiated on the first simulation day.
        # That technical Sowing event must not create an extra fertiliser application;
        # later genuine sowings (including autumn rye) retain the configured rate.
        old_guard = "            if (Clock.Today.Date == lastApplication.Date)"
        new_guard = (
            "            if (Clock.Today.Date == Clock.StartDate.Date || "
            "Clock.Today.Date == lastApplication.Date)"
        )
        for field, zone in zones.items():
            fertiliser = child(zone, "Fertilise at sowing")
            code = fertiliser.get("CodeArray", [])
            if old_guard not in code:
                raise ValueError(f"{field}: unsupported fertiliser manager for warm-up")
            code[code.index(old_guard)] = new_guard
            changes.append({
                "field": field,
                "parameter": "Fertilise at sowing/startup guard",
                "before": old_guard.strip(),
                "after": new_guard.strip(),
            })

    resolved = {}
    for field, sequence in rotation_map["fields"].items():
        manager = child(zones[field], "Sow using a variable rule")
        old_values = parameters(manager)
        old_arrays = {
            key: [part.strip() for part in old_values[key].split(",")]
            for key in ROTATION_KEYS
        }
        if any(len(parts) != len(sequence) for parts in old_arrays.values()):
            raise ValueError(f"{field}: source rotation length differs from selected map")
        new_arrays = {key: [] for key in ROTATION_KEYS}
        resolved[field] = {}
        effective_sequence = list(sequence)
        if warmup_enabled:
            effective_sequence.insert(0, warmup["field_states"][field])
        for index, state_id in enumerate(effective_sequence):
            year = simulation_start + index
            profile = resolve_profile(profiles, state_id, year)
            resolved[field][str(year)] = {
                "state_id": state_id,
                "product": profile["product"],
                "primary_yield_components": profile["primary_yield_components"],
            }
            for profile_key, rotation_key in PROFILE_TO_ROTATION_KEY.items():
                new_arrays[rotation_key].append(profile[profile_key])
        by_key = {item["Key"]: item for item in manager.get("Parameters", [])}
        for key in ROTATION_KEYS:
            before = ",".join(old_arrays[key])
            after = ",".join(new_arrays[key])
            by_key[key]["Value"] = after
            if before != after:
                changes.append({"field": field, "parameter": key,
                                "before": before, "after": after})

    output_path = Path(output_path)
    write_json(output_path, data)
    return {
        "base_model": str(Path(base_model).resolve()),
        "output_model": str(output_path.resolve()),
        "start_year": rotation_map["start_year"],
        "end_year": rotation_map["end_year"],
        "simulation_start": str(clock["Start"]),
        "warmup": warmup if warmup_enabled else None,
        "fields": resolved,
        "changes": changes,
    }


def write_combined_weather(
    warmup_weather: str | Path, main_weather: str | Path, destination: str | Path,
) -> dict:
    """Combine full warm-up and main weather files without changing daily values."""
    warmup = read_met(warmup_weather)
    main = read_met(main_weather)
    if warmup["columns"] != main["columns"] or warmup["units"] != main["units"]:
        raise ValueError("Warm-up and main weather columns/units differ")
    rows = sorted(warmup["rows"] + main["rows"], key=lambda row: row["date"])
    dates = [row["date"] for row in rows]
    if len(dates) != len(set(dates)):
        raise ValueError("Combined weather contains duplicate dates")
    if any((later - earlier).days != 1 for earlier, later in zip(dates, dates[1:])):
        raise ValueError("Combined weather contains a date gap")
    tav, amp = calculate_tav_amp(rows)
    columns = main["columns"]
    lines = [
        "[weather.met.weather]",
        "! diagnostic combination of unchanged 2018 and 2019-2023 daily weather",
        f"! warm-up source = {Path(warmup_weather).resolve()}",
        f"! main source = {Path(main_weather).resolve()}",
        "station = VNIIMZ combined warm-up 2018-2023",
        f"latitude = {main['metadata'].get('latitude', '56.8')}",
        f"longitude = {main['metadata'].get('longitude', '35.9')}",
        f"tav = {tav:.3f}",
        f"amp = {amp:.3f}",
        "",
        " ".join(columns),
        " ".join(main["units"][column] for column in columns),
    ]
    for row in rows:
        values = []
        for column in columns:
            value = row[column]
            if column in {"year", "day"}:
                values.append(str(int(value)))
            elif column == "sp":
                values.append(f"{value:.1f}")
            else:
                values.append(f"{value:.3f}")
        lines.append(" ".join(values))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return {
        "path": str(destination.resolve()),
        "start": dates[0].isoformat(),
        "end": dates[-1].isoformat(),
        "rows": len(rows),
        "tav": tav,
        "amp": amp,
    }
