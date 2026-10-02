"""Cross-year summaries for oat physiology diagnostics."""
from __future__ import annotations

import math
from typing import Any


MECHANISM_COLUMNS = (
    "max_lai",
    "max_aboveground_biomass_g_m2",
    "simulated_harvest_index",
    "final_grain_number_m2",
    "final_grain_size_g",
    "grain_potential_allocation_to_demand_ratio",
    "grain_n_allocation_to_demand_ratio",
    "water_stress_days_fw_below_0_8",
    "nitrogen_stress_days_fn_below_0_8",
    "grain_fill_water_stress_days_fw_below_0_8",
    "grain_fill_nitrogen_stress_days_fn_below_0_8",
)


def prediction_direction(error: float | None, tolerance: float = 1e-9) -> str:
    """Describe only the sign of an error, without inventing a quality threshold."""
    if error is None:
        return "unavailable"
    if error < -tolerance:
        return "underpredicted"
    if error > tolerance:
        return "overpredicted"
    return "equal"


def cultivar_mapping_status(observed: Any, model: Any) -> str:
    """Flag the known transliterated Yakov match and genuine cultivar differences."""
    observed_text = str(observed or "").strip().casefold()
    model_text = str(model or "").strip().casefold()
    if observed_text in {"яков", "yakov"} and "yakov" in model_text:
        return "matched_yakov"
    if observed_text and observed_text in model_text:
        return "matched_literal"
    return "different_or_unconfirmed"


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [
        row for row in rows
        if row.get("observed_yield_t_ha") is not None
        and row.get("simulated_yield_t_ha") is not None
    ]
    if not usable:
        return {
            "cases": 0, "mae_t_ha": None, "rmse_t_ha": None,
            "mean_bias_t_ha": None,
        }
    errors = [
        float(row["simulated_yield_t_ha"]) - float(row["observed_yield_t_ha"])
        for row in usable
    ]
    return {
        "cases": len(usable),
        "mae_t_ha": sum(abs(value) for value in errors) / len(errors),
        "rmse_t_ha": math.sqrt(sum(value * value for value in errors) / len(errors)),
        "mean_bias_t_ha": sum(errors) / len(errors),
        "underpredicted_cases": sum(value < -1e-9 for value in errors),
        "overpredicted_cases": sum(value > 1e-9 for value in errors),
        "equal_cases": sum(abs(value) <= 1e-9 for value in errors),
    }


def _mean(rows: list[dict[str, Any]], column: str) -> float | None:
    values = [float(row[column]) for row in rows if row.get(column) is not None]
    return sum(values) / len(values) if values else None


def summarize_oats_history(
    rows: list[dict[str, Any]], validation_year: int = 2023
) -> dict[str, Any]:
    """Build transparent train/validation and mechanism-group summaries."""
    enriched = []
    for source in rows:
        row = dict(source)
        row["prediction_direction"] = prediction_direction(row.get("error_t_ha"))
        row["cultivar_mapping_status"] = cultivar_mapping_status(
            row.get("observed_cultivar"), row.get("model_cultivar")
        )
        enriched.append(row)

    supported = [
        row for row in enriched
        if row["cultivar_mapping_status"] != "different_or_unconfirmed"
    ]

    groups = {}
    for direction in ("underpredicted", "overpredicted", "equal", "unavailable"):
        selected = [row for row in supported if row["prediction_direction"] == direction]
        if not selected:
            continue
        groups[direction] = {
            "cases": len(selected),
            "case_ids": [f"{row['year']}/{row['field']}" for row in selected],
            "means": {column: _mean(selected, column) for column in MECHANISM_COLUMNS},
        }

    return {
        "all_years": _metrics(enriched),
        "supported_cultivar_all_years": _metrics(supported),
        "development_years": _metrics([
            row for row in supported if int(row["year"]) != validation_year
        ]),
        "validation_year": {
            "year": validation_year,
            **_metrics([row for row in supported if int(row["year"]) == validation_year]),
        },
        "failed_establishment_cases": [
            f"{row['year']}/{row['field']}" for row in enriched
            if row.get("diagnostic_status") == "no_active_crop_rows"
        ],
        "different_or_unconfirmed_cultivars": [
            f"{row['year']}/{row['field']}" for row in enriched
            if row["cultivar_mapping_status"] == "different_or_unconfirmed"
        ],
        "mechanism_groups_by_error_sign": groups,
        "rows": enriched,
    }
