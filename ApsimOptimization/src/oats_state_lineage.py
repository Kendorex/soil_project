"""Summaries of soil state and crop source formation through oat seasons."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum(rows: list[dict[str, Any]], column: str) -> float | None:
    values = [_number(row.get(column)) for row in rows]
    usable = [value for value in values if value is not None]
    return sum(usable) if usable else None


def _first(rows: list[dict[str, Any]], column: str) -> float | None:
    for row in rows:
        value = _number(row.get(column))
        if value is not None:
            return value
    return None


def build_state_lineage(
    daily_rows: list[dict[str, Any]], history_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create one row per oat case and within-year paired field contrasts."""
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        grouped[(int(row["year"]), str(row["field"]))].append(row)
    history = {(int(row["year"]), str(row["field"])): row for row in history_rows}
    lineage = []
    for key, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: str(row["date"]))
        source = history[key]
        pre_heading = [
            row for row in rows
            if (_number(row.get("OatsZadok")) or 0) < 50
        ]
        grain_fill = [
            row for row in rows
            if _number(row.get("OatsZadok")) is not None
            and 71 <= float(row["OatsZadok"]) < 90
        ]
        lineage.append({
            "year": key[0], "field": key[1],
            "observed_cultivar": source.get("observed_cultivar"),
            "technology": source.get("technology"),
            "observed_yield_t_ha": _number(source.get("observed_yield_t_ha")),
            "simulated_yield_t_ha": _number(source.get("simulated_yield_t_ha")),
            "error_t_ha": _number(source.get("error_t_ha")),
            "first_active_date": rows[0]["date"],
            "first_active_mineral_n_model_value": _first(rows, "MineralN"),
            "first_active_paw_model_value": _first(rows, "PAW_total"),
            "first_active_root_fasw": _first(rows, "OatsRootFASW"),
            "rain_active_period_mm": _sum(rows, "Rain"),
            "weather_radiation_active_period": _sum(rows, "Radn"),
            "intercepted_radiation_pre_heading": _sum(
                pre_heading, "OatsLeafRadiationIntercepted"
            ),
            "intercepted_radiation_grain_fill": _sum(
                grain_fill, "OatsLeafRadiationIntercepted"
            ),
            "photosynthesis_pre_heading": _sum(
                pre_heading, "OatsLeafPhotosynthesis"
            ),
            "photosynthesis_grain_fill": _sum(
                grain_fill, "OatsLeafPhotosynthesis"
            ),
            "root_n_uptake_native_sum": _sum(rows, "OatsRootNUptake_total"),
            "max_lai": _number(source.get("max_lai")),
            "max_aboveground_biomass_g_m2": _number(
                source.get("max_aboveground_biomass_g_m2")
            ),
            "final_grain_number_m2": _number(source.get("final_grain_number_m2")),
            "final_grain_size_g": _number(source.get("final_grain_size_g")),
            "grain_fill_days": len(grain_fill),
            "grain_fill_n_stress_days": _number(
                source.get("grain_fill_nitrogen_stress_days_fn_below_0_8")
            ),
        })

    contrasts = []
    by_year: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in lineage:
        by_year[int(row["year"])].append(row)
    contrast_columns = (
        "observed_yield_t_ha", "simulated_yield_t_ha",
        "first_active_mineral_n_model_value", "first_active_paw_model_value",
        "rain_active_period_mm", "photosynthesis_pre_heading",
        "photosynthesis_grain_fill", "max_lai",
        "max_aboveground_biomass_g_m2", "final_grain_number_m2",
        "final_grain_size_g",
    )
    for year, rows in sorted(by_year.items()):
        if len(rows) != 2:
            continue
        a, b = sorted(rows, key=lambda row: row["field"])
        contrast = {"year": year, "field_a": a["field"], "field_b": b["field"]}
        for column in contrast_columns:
            av, bv = _number(a.get(column)), _number(b.get(column))
            contrast[f"{column}_b_minus_a"] = (
                bv - av if av is not None and bv is not None else None
            )
        contrasts.append(contrast)
    return lineage, contrasts
