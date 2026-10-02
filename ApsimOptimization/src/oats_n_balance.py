"""Phase-resolved partial nitrogen accounting for oat diagnostics."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_number(row.get(key)) for row in rows]
    usable = [value for value in values if value is not None]
    return sum(usable) if usable else None


def _first(rows: list[dict[str, Any]], key: str) -> float | None:
    for row in rows:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def _last(rows: list[dict[str, Any]], key: str) -> float | None:
    return _first(list(reversed(rows)), key)


def build_oats_n_balance(
    daily_rows: list[dict[str, Any]], summary_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Summarize reported N stores and fluxes without claiming full closure."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        grouped[(str(row["scenario"]), str(row["field"]))].append(row)
    summaries = {
        (str(row["scenario"]), str(row["field"])): row for row in summary_rows
    }
    result = []
    for key, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: str(row["date"]))
        summary = summaries[key]
        pre_heading = [row for row in rows if (_number(row.get("OatsZadok")) or 0) < 50]
        grain_fill = [
            row for row in rows
            if _number(row.get("OatsZadok")) is not None
            and 71 <= float(row["OatsZadok"]) < 90
        ]
        heading_onward = [
            row for row in rows
            if _number(row.get("OatsZadok")) is not None
            and float(row["OatsZadok"]) >= 50
        ]
        root_native = _sum(rows, "OatsRootNUptake_total")
        root_values = [
            _number(row.get("OatsRootNUptake_total")) for row in rows
            if _number(row.get("OatsRootNUptake_total")) is not None
        ]
        root_magnitude = (
            -root_native if root_native is not None and root_values
            and all(value <= 0 for value in root_values) else None
        )
        demand_active = _sum(rows, "OatsNitrogenDemandGm2")
        supply_active = _sum(rows, "OatsNitrogenSupplyGm2")
        demand_grain_fill = _sum(grain_fill, "OatsNitrogenDemandGm2")
        supply_grain_fill = _sum(grain_fill, "OatsNitrogenSupplyGm2")
        result.append({
            "scenario": key[0], "field": key[1],
            "observed_yield_t_ha": _number(summary.get("observed_yield_t_ha")),
            "simulated_yield_t_ha": _number(summary.get("simulated_yield_t_ha")),
            "mineral_n_first_active_model_value": _first(rows, "MineralN"),
            "mineral_n_at_heading_model_value": _first(heading_onward, "MineralN"),
            "mineral_n_at_grain_fill_start_model_value": _first(grain_fill, "MineralN"),
            "mineral_n_last_active_model_value": _last(rows, "MineralN"),
            "mineral_n_active_change_model_value": (
                _last(rows, "MineralN") - _first(rows, "MineralN")
                if _last(rows, "MineralN") is not None and _first(rows, "MineralN") is not None
                else None
            ),
            "root_n_uptake_pre_heading_native_sum": _sum(
                pre_heading, "OatsRootNUptake_total"
            ),
            "root_n_uptake_grain_fill_native_sum": _sum(
                grain_fill, "OatsRootNUptake_total"
            ),
            "root_n_uptake_active_native_sum": root_native,
            "root_n_uptake_magnitude_if_native_nonpositive": root_magnitude,
            "plant_n_demand_active_sum_g_m2": demand_active,
            "soil_n_supply_active_sum_g_m2": supply_active,
            "soil_n_supply_to_demand_active_ratio": (
                supply_active / demand_active
                if supply_active is not None and demand_active not in (None, 0) else None
            ),
            "plant_n_demand_grain_fill_sum_g_m2": demand_grain_fill,
            "soil_n_supply_grain_fill_sum_g_m2": supply_grain_fill,
            "soil_n_supply_to_demand_grain_fill_ratio": (
                supply_grain_fill / demand_grain_fill
                if supply_grain_fill is not None and demand_grain_fill not in (None, 0)
                else None
            ),
            "days_soil_n_supply_below_plant_demand": sum(
                1 for row in rows
                if _number(row.get("OatsNitrogenDemandGm2")) is not None
                and _number(row.get("OatsNitrogenSupplyGm2")) is not None
                and float(row["OatsNitrogenSupplyGm2"])
                < float(row["OatsNitrogenDemandGm2"]) - 1e-12
            ),
            "grain_fill_days_soil_n_supply_below_plant_demand": sum(
                1 for row in grain_fill
                if _number(row.get("OatsNitrogenDemandGm2")) is not None
                and _number(row.get("OatsNitrogenSupplyGm2")) is not None
                and float(row["OatsNitrogenSupplyGm2"])
                < float(row["OatsNitrogenDemandGm2"]) - 1e-12
            ),
            "denitrification_active_native_sum": _sum(
                rows, "Denitrification_total_native"
            ),
            "bottom_n_flow_active_native_sum": _sum(rows, "NFlow_bottom_native"),
            "oats_aboveground_n_last_native": _last(rows, "OatsAGN"),
            "grain_n_demand_active_native_sum": _sum(rows, "OatsGrainNDemand"),
            "grain_n_allocation_active_native_sum": _sum(rows, "OatsGrainNAllocation"),
            "grain_fill_days": len(grain_fill),
            "grain_fill_n_stress_days": _number(
                summary.get("grain_fill_nitrogen_stress_days_fn_below_0_8")
            ),
            "leaf_n_stress_factor_mean": _number(summary.get("OatsLeafFn_mean")),
            "balance_closure_status": "partial_reported_terms_only",
        })
    return result
