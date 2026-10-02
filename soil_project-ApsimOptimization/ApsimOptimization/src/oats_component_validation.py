"""Component-aware validation of historical oat simulations.

The module deliberately keeps unlike errors separate.  It does not create a
weighted score: a candidate is compared with a reference by Pareto dominance
within the development (2020-2022) and validation (2023) blocks.
"""
from __future__ import annotations

import datetime as dt
import math
from statistics import fmean
from typing import Any, Iterable


SCOPES = {
    "development_yakov_2020_2022": lambda row: (
        2020 <= int(row["year"]) <= 2022
        and str(row.get("observed_cultivar", "")).casefold() == "яков"
    ),
    "validation_yakov_2023": lambda row: (
        int(row["year"]) == 2023
        and str(row.get("observed_cultivar", "")).casefold() == "яков"
    ),
}

OBJECTIVE_PATHS = (
    "yield.rmse_t_ha",
    "phenology.tillering_mae_days",
    "phenology.heading_mae_days",
    "thousand_grain_mass.rmse_g",
    "grain_number.mean_absolute_relative_error",
    "biomass.rmse_g_m2",
)


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def date_error(observed: Any, simulated: Any) -> int | None:
    if not observed or not simulated:
        return None
    return (
        dt.date.fromisoformat(str(simulated)[:10])
        - dt.date.fromisoformat(str(observed)[:10])
    ).days


def error_metrics(errors: Iterable[float], unit_suffix: str = "") -> dict[str, Any]:
    values = [float(value) for value in errors if number(value) is not None]
    suffix = f"_{unit_suffix}" if unit_suffix else ""
    return {
        "n": len(values),
        f"mae{suffix}": fmean(abs(value) for value in values) if values else None,
        f"rmse{suffix}": math.sqrt(fmean(value * value for value in values))
        if values else None,
        f"bias{suffix}": fmean(values) if values else None,
    }


def add_observed_grain_components(
    rows: list[dict[str, Any]], source: dict[tuple[int, str], dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add observed TKW and yield-derived grain-number errors to case rows."""
    result = []
    for raw in rows:
        row = dict(raw)
        year, field = int(row["year"]), str(row["field"])
        observed = source.get((year, field), {})
        observed_tkw = number(observed.get("observed_thousand_grain_mass_g"))
        simulated_size = number(row.get("final_grain_size_g"))
        observed_yield = number(row.get("observed_yield_t_ha"))
        simulated_number = number(row.get("final_grain_number_m2"))
        simulated_tkw = simulated_size * 1000.0 if simulated_size is not None else None
        implied_number = (
            observed_yield * 100000.0 / observed_tkw
            if observed_yield is not None and observed_tkw not in (None, 0.0)
            else None
        )
        row.update({
            "observed_thousand_grain_mass_g": observed_tkw,
            "simulated_thousand_grain_mass_g": simulated_tkw,
            "thousand_grain_mass_error_g": (
                simulated_tkw - observed_tkw
                if simulated_tkw is not None and observed_tkw is not None else None
            ),
            "observed_yield_implied_grain_number_m2": implied_number,
            "grain_number_error_m2": (
                simulated_number - implied_number
                if simulated_number is not None and implied_number is not None else None
            ),
            "grain_number_relative_error": (
                simulated_number / implied_number - 1.0
                if simulated_number is not None and implied_number not in (None, 0.0)
                else None
            ),
            "grain_number_observation_status": (
                "inferred_from_yield_and_tkw" if implied_number is not None
                else "unavailable"
            ),
        })
        result.append(row)
    return result


def _scope_metrics(rows: list[dict[str, Any]], biomass_rows: list[dict[str, Any]]) -> dict:
    yield_errors = [
        value for row in rows
        if (value := number(row.get("error_t_ha"))) is not None
    ]
    tillering = [
        value for row in rows
        if (value := date_error(
            row.get("observed_tillering_date"), row.get("simulated_tillering_date")
        )) is not None
    ]
    heading = [
        value for row in rows
        if (value := date_error(
            row.get("observed_heading_date"), row.get("simulated_heading_date")
        )) is not None
    ]
    tkw_errors = [
        value for row in rows
        if (value := number(row.get("thousand_grain_mass_error_g"))) is not None
    ]
    grain_number_errors = [
        value for row in rows
        if (value := number(row.get("grain_number_relative_error"))) is not None
    ]
    biomass_errors = [
        value for row in biomass_rows
        if (value := number(row.get("oats_only_error_g_m2"))) is not None
    ]
    return {
        "cases": len(rows),
        "yield": error_metrics(yield_errors, "t_ha"),
        "phenology": {
            "tillering_n": len(tillering),
            "tillering_mae_days": fmean(abs(value) for value in tillering)
            if tillering else None,
            "tillering_bias_days": fmean(tillering) if tillering else None,
            "heading_n": len(heading),
            "heading_mae_days": fmean(abs(value) for value in heading)
            if heading else None,
            "heading_bias_days": fmean(heading) if heading else None,
        },
        "thousand_grain_mass": error_metrics(tkw_errors, "g"),
        "grain_number": {
            "n": len(grain_number_errors),
            "mean_absolute_relative_error": (
                fmean(abs(value) for value in grain_number_errors)
                if grain_number_errors else None
            ),
            "mean_relative_bias": fmean(grain_number_errors)
            if grain_number_errors else None,
            "observation_status": "inferred_from_observed_yield_and_tkw",
        },
        "biomass": error_metrics(biomass_errors, "g_m2"),
    }


def evaluate_candidate(
    rows: list[dict[str, Any]], biomass_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    result = {}
    for scope, predicate in SCOPES.items():
        selected = [row for row in rows if predicate(row)]
        selected_keys = {(int(row["year"]), str(row["field"])) for row in selected}
        selected_biomass = [
            row for row in biomass_rows
            if (int(row["year"]), str(row["field"])) in selected_keys
        ]
        result[scope] = _scope_metrics(selected, selected_biomass)
    return result


def nested_value(data: dict[str, Any], path: str) -> float | None:
    value: Any = data
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return number(value)


def compare_scope(candidate: dict, reference: dict) -> dict[str, Any]:
    comparisons = []
    for path in OBJECTIVE_PATHS:
        candidate_value = nested_value(candidate, path)
        reference_value = nested_value(reference, path)
        if candidate_value is None or reference_value is None:
            continue
        comparisons.append({
            "metric": path,
            "reference": reference_value,
            "candidate": candidate_value,
            "delta": candidate_value - reference_value,
        })
    if not comparisons:
        status = "incomparable"
    else:
        deltas = [row["delta"] for row in comparisons]
        candidate_no_worse = all(delta <= 0 for delta in deltas)
        reference_no_worse = all(delta >= 0 for delta in deltas)
        if candidate_no_worse and any(delta < 0 for delta in deltas):
            status = "dominates_reference"
        elif reference_no_worse and any(delta > 0 for delta in deltas):
            status = "dominated_by_reference"
        elif all(delta == 0 for delta in deltas):
            status = "equivalent"
        else:
            status = "tradeoff"
    return {"status": status, "metrics_compared": comparisons}


def compare_candidate(candidate: dict, reference: dict) -> dict[str, Any]:
    scopes = {
        scope: compare_scope(candidate[scope], reference[scope])
        for scope in SCOPES
    }
    statuses = [item["status"] for item in scopes.values()]
    if all(status in {"dominates_reference", "equivalent"} for status in statuses) \
            and "dominates_reference" in statuses:
        overall = "dominates_reference_in_both_time_blocks"
    elif all(status in {"dominated_by_reference", "equivalent"} for status in statuses) \
            and "dominated_by_reference" in statuses:
        overall = "dominated_by_reference_in_both_time_blocks"
    elif "incomparable" in statuses:
        overall = "incomparable_missing_metrics"
    else:
        overall = "tradeoff_between_metrics_or_time_blocks"
    return {
        "overall_status": overall,
        "scope_comparisons": scopes,
        "method": "strict Pareto comparison; lower error is better",
        "normalization": "none",
        "weights": "none",
        "automatic_model_change": False,
    }
