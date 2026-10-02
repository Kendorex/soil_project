"""Layer-resolved mineral nitrogen diagnostics for oat simulations."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _phase_rows(rows: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Choose one row for each agronomically useful diagnostic phase."""
    selected: list[tuple[str, dict[str, Any]]] = [("first_active", rows[0])]
    heading = next(
        (row for row in rows
         if _number(row.get("OatsZadok")) is not None
         and float(row["OatsZadok"]) >= 50),
        None,
    )
    grain_fill = next(
        (row for row in rows
         if _number(row.get("OatsZadok")) is not None
         and 71 <= float(row["OatsZadok"]) < 90),
        None,
    )
    if heading is not None:
        selected.append(("heading", heading))
    if grain_fill is not None:
        selected.append(("grain_fill_start", grain_fill))
    selected.append(("last_active", rows[-1]))
    return selected


def _layer_values(row: dict[str, Any], prefix: str, count: int) -> list[float]:
    values = [_number(row.get(f"{prefix}_layer_{index}")) for index in range(1, count + 1)]
    if any(value is None for value in values):
        missing = [index for index, value in enumerate(values, 1) if value is None]
        raise ValueError(f"{row.get('field')}/{row.get('date')}: missing {prefix} layers {missing}")
    return [float(value) for value in values if value is not None]


def build_oats_n_layers(
    daily_rows: list[dict[str, Any]],
    thickness_by_field: dict[str, list[float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare soil N layers with root depth at selected crop phases.

    The fraction inside the root zone assumes uniform N distribution within a
    soil layer.  It is a geometric overlap proxy, not a direct measure of root
    activity or plant availability.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        grouped[(str(row["scenario"]), str(row["field"]))].append(row)

    summary_rows: list[dict[str, Any]] = []
    layer_rows: list[dict[str, Any]] = []
    for (scenario, field), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: str(row["date"]))
        if field not in thickness_by_field:
            raise ValueError(f"Missing soil layer thicknesses for {field}")
        thicknesses = [float(value) for value in thickness_by_field[field]]
        if not thicknesses or any(value <= 0 for value in thicknesses):
            raise ValueError(f"Invalid soil layer thicknesses for {field}")

        for phase, row in _phase_rows(rows):
            root_depth = _number(row.get("OatsRootDepth"))
            if root_depth is None:
                raise ValueError(f"{field}/{row.get('date')}: missing OatsRootDepth")
            no3 = _layer_values(row, "NO3kgHa", len(thicknesses))
            nh4 = _layer_values(row, "NH4kgHa", len(thicknesses))
            urea = _layer_values(row, "UreakgHa", len(thicknesses))
            root_length_density = _layer_values(
                row, "OatsRootLengthDensity", len(thicknesses)
            )
            daily_n_uptake = _layer_values(
                row, "OatsRootNUptakeLayered", len(thicknesses)
            )
            profile_no3_nh4 = 0.0
            rooted_no3_nh4 = 0.0
            profile_urea = 0.0
            top = 0.0
            for index, values in enumerate(
                zip(thicknesses, no3, nh4, urea, root_length_density, daily_n_uptake), 1
            ):
                (thickness, no3_value, nh4_value, urea_value,
                 root_length_density_value, daily_n_uptake_value) = values
                bottom = top + thickness
                overlap = min(max(root_depth - top, 0.0), thickness)
                rooted_fraction = overlap / thickness
                available = no3_value + nh4_value
                rooted = available * rooted_fraction
                profile_no3_nh4 += available
                rooted_no3_nh4 += rooted
                profile_urea += urea_value
                layer_rows.append({
                    "scenario": scenario,
                    "field": field,
                    "phase": phase,
                    "date": row["date"],
                    "OatsZadok": _number(row.get("OatsZadok")),
                    "root_depth_mm": root_depth,
                    "layer": index,
                    "layer_top_mm": top,
                    "layer_bottom_mm": bottom,
                    "layer_thickness_mm": thickness,
                    "root_overlap_mm": overlap,
                    "rooted_layer_fraction": rooted_fraction,
                    "no3_kg_n_ha": no3_value,
                    "nh4_kg_n_ha": nh4_value,
                    "urea_kg_n_ha": urea_value,
                    "no3_nh4_kg_n_ha": available,
                    "no3_nh4_inside_root_depth_proxy_kg_n_ha": rooted,
                    "no3_nh4_below_root_depth_proxy_kg_n_ha": available - rooted,
                    "root_length_density_mm_per_mm3": root_length_density_value,
                    "root_n_uptake_layered_daily_native": daily_n_uptake_value,
                })
                top = bottom

            below = profile_no3_nh4 - rooted_no3_nh4
            summary_rows.append({
                "scenario": scenario,
                "field": field,
                "phase": phase,
                "date": row["date"],
                "OatsZadok": _number(row.get("OatsZadok")),
                "root_depth_mm": root_depth,
                "soil_profile_depth_mm": sum(thicknesses),
                "no3_nh4_profile_kg_n_ha": profile_no3_nh4,
                "no3_nh4_inside_root_depth_proxy_kg_n_ha": rooted_no3_nh4,
                "no3_nh4_below_root_depth_proxy_kg_n_ha": below,
                "no3_nh4_inside_root_depth_fraction": (
                    rooted_no3_nh4 / profile_no3_nh4 if profile_no3_nh4 else None
                ),
                "urea_profile_kg_n_ha": profile_urea,
                "reported_mineral_n_model_value": _number(row.get("MineralN")),
                "geometric_proxy_status": "uniform_within_layer_not_root_activity",
            })
    return summary_rows, layer_rows


def build_oats_n_uptake_layers(
    daily_rows: list[dict[str, Any]],
    thickness_by_field: dict[str, list[float]],
) -> list[dict[str, Any]]:
    """Aggregate reported root N uptake by layer and crop phase."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        grouped[(str(row["scenario"]), str(row["field"]))].append(row)

    result: list[dict[str, Any]] = []
    for (scenario, field), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: str(row["date"]))
        thicknesses = [float(value) for value in thickness_by_field[field]]
        windows = {
            "active": rows,
            "pre_heading": [
                row for row in rows
                if _number(row.get("OatsZadok")) is not None
                and float(row["OatsZadok"]) < 50
            ],
            "grain_fill": [
                row for row in rows
                if _number(row.get("OatsZadok")) is not None
                and 71 <= float(row["OatsZadok"]) < 90
            ],
        }
        uptake_by_window = {
            name: [
                sum(_layer_values(row, "OatsRootNUptakeLayered", len(thicknesses))[layer]
                    for row in window_rows)
                for layer in range(len(thicknesses))
            ]
            for name, window_rows in windows.items()
        }
        active_total = sum(uptake_by_window["active"])
        top = 0.0
        for layer, thickness in enumerate(thicknesses):
            bottom = top + thickness
            active_uptake = uptake_by_window["active"][layer]
            result.append({
                "scenario": scenario,
                "field": field,
                "layer": layer + 1,
                "layer_top_mm": top,
                "layer_bottom_mm": bottom,
                "root_n_uptake_active_native_sum": active_uptake,
                "root_n_uptake_pre_heading_native_sum": uptake_by_window["pre_heading"][layer],
                "root_n_uptake_grain_fill_native_sum": uptake_by_window["grain_fill"][layer],
                "root_n_uptake_active_fraction": (
                    active_uptake / active_total if active_total else None
                ),
                "days_with_positive_root_n_uptake": sum(
                    _layer_values(row, "OatsRootNUptakeLayered", len(thicknesses))[layer] > 0
                    for row in rows
                ),
                "layered_uptake_source_status": "APSIM_Root_NUptakeLayered_native",
            })
            top = bottom
    return result
