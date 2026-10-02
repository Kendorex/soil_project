"""Controlled oat field-state and fertilizer-label swap diagnostics."""
from __future__ import annotations

import copy
import math
from typing import Any

from .field_soil_audit import INITIAL_STATE_KEYS, digest
from .historical_case import parse_technology_nutrients
from .multifield_model import child, kind, walk


def _enabled_zones(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    simulations = [
        node for _, node in walk(data)
        if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)
    ]
    if len(simulations) != 1:
        raise ValueError(f"Expected one enabled Simulation, found {len(simulations)}")
    return {
        node["Name"]: node for node in simulations[0].get("Children", [])
        if kind(node) == "Models.Core.Zone" and node.get("Enabled", True)
    }


def _named_descendants(node: dict[str, Any], path: tuple[str, ...] = ()):
    yield path, node
    counts: dict[str, int] = {}
    for item in node.get("Children", []):
        name = str(item.get("Name", ""))
        counts[name] = counts.get(name, 0) + 1
        label = name if counts[name] == 1 else f"{name}#{counts[name]}"
        yield from _named_descendants(item, path + (label,))


def swap_soil_initial_state(
    data: dict[str, Any], field_a: str, field_b: str
) -> dict[str, Any]:
    """Exchange existing initial-state values between two equal soil profiles.

    The operation is refused when the static soil profiles differ. This keeps
    the experiment to a state swap and prevents accidental exchange of soil
    parameters such as DUL, LL15 or bulk density.
    """
    zones = _enabled_zones(data)
    missing = {field_a, field_b} - set(zones)
    if missing:
        raise ValueError(f"Unknown fields: {sorted(missing)}")
    soil_a, soil_b = child(zones[field_a], "Soil"), child(zones[field_b], "Soil")
    if digest(soil_a, ignore_initial_state=True) != digest(
        soil_b, ignore_initial_state=True
    ):
        raise ValueError("Static soil profiles differ; initial-state swap refused")

    before_a, before_b = digest(soil_a), digest(soil_b)
    nodes_a = dict(_named_descendants(soil_a))
    nodes_b = dict(_named_descendants(soil_b))
    if set(nodes_a) != set(nodes_b):
        raise ValueError("Soil component structures differ; initial-state swap refused")

    changes = []
    for path in sorted(nodes_a):
        left, right = nodes_a[path], nodes_b[path]
        for key in sorted(INITIAL_STATE_KEYS):
            if (key in left) != (key in right):
                raise ValueError(f"Initial-state key differs at {'/'.join(path)}: {key}")
            if key not in left:
                continue
            left_value, right_value = copy.deepcopy(left[key]), copy.deepcopy(right[key])
            left[key], right[key] = right_value, left_value
            if left_value != right_value:
                changes.append({
                    "path": "/".join(("Soil",) + path),
                    "key": key,
                    f"{field_a}_before": left_value,
                    f"{field_a}_after": right_value,
                    f"{field_b}_before": right_value,
                    f"{field_b}_after": left_value,
                })

    after_a, after_b = digest(soil_a), digest(soil_b)
    if after_a != before_b or after_b != before_a:
        raise ValueError("Initial-state exchange did not produce an exact swap")
    return {
        "field_a": field_a,
        "field_b": field_b,
        "static_soil_profile_unchanged": True,
        "before_sha256": {field_a: before_a, field_b: before_b},
        "after_sha256": {field_a: after_a, field_b: after_b},
        "changed_values": changes,
    }


def swap_observed_technology(
    observations: dict[str, Any], year: int, field_a: str, field_b: str
) -> dict[str, Any]:
    """Exchange two existing technology labels in a copied observation set."""
    records = (observations.get("years") or {}).get(str(year)) or []
    by_field = {row.get("field"): row for row in records}
    missing = {field_a, field_b} - set(by_field)
    if missing:
        raise ValueError(f"Missing observation fields for {year}: {sorted(missing)}")
    a, b = by_field[field_a], by_field[field_b]
    before_a, before_b = a.get("technology"), b.get("technology")
    nutrients_a = parse_technology_nutrients(before_a)
    nutrients_b = parse_technology_nutrients(before_b)
    if nutrients_a["N"] is None or nutrients_b["N"] is None:
        raise ValueError("Both technology labels must contain explicit N rates")
    a["technology"], b["technology"] = before_b, before_a
    return {
        "year": year,
        "field_a": field_a,
        "field_b": field_b,
        "technology_before": {field_a: before_a, field_b: before_b},
        "technology_after": {field_a: before_b, field_b: before_a},
        "n_label_kg_ha_before": {
            field_a: nutrients_a["N"], field_b: nutrients_b["N"]
        },
        "n_label_kg_ha_after": {
            field_a: nutrients_b["N"], field_b: nutrients_a["N"]
        },
        "note": "Only N is replayed by the historical APSIM diagnostic.",
    }


def paired_oat_metrics(
    rows: list[dict[str, Any]], year: int, field_a: str, field_b: str
) -> dict[str, Any]:
    """Calculate accuracy and ordering for one observed two-field oat pair."""
    selected = {
        row["field"]: row for row in rows
        if int(row["year"]) == year and row["field"] in {field_a, field_b}
    }
    if set(selected) != {field_a, field_b}:
        raise ValueError(f"Expected exactly {field_a} and {field_b} for {year}")
    errors = [float(selected[field]["error_t_ha"]) for field in (field_a, field_b)]
    observed_delta = (
        float(selected[field_b]["observed_yield_t_ha"])
        - float(selected[field_a]["observed_yield_t_ha"])
    )
    simulated_delta = (
        float(selected[field_b]["simulated_yield_t_ha"])
        - float(selected[field_a]["simulated_yield_t_ha"])
    )
    return {
        "year": year,
        "fields": [field_a, field_b],
        "mae_t_ha": sum(abs(value) for value in errors) / 2,
        "rmse_t_ha": math.sqrt(sum(value * value for value in errors) / 2),
        "bias_t_ha": sum(errors) / 2,
        "observed_yield_b_minus_a_t_ha": observed_delta,
        "simulated_yield_b_minus_a_t_ha": simulated_delta,
        "ordering_matches_observations": (
            (observed_delta == 0 and simulated_delta == 0)
            or observed_delta * simulated_delta > 0
        ),
        "fields_detail": {
            field: {
                key: selected[field].get(key) for key in (
                    "observed_yield_t_ha", "simulated_yield_t_ha", "error_t_ha",
                    "max_lai", "max_aboveground_biomass_g_m2",
                    "final_grain_number_m2", "final_grain_size_g",
                    "grain_fill_nitrogen_stress_days_fn_below_0_8",
                    "MineralN_mean", "PAW_total_mean",
                )
            } for field in (field_a, field_b)
        },
    }
