"""Metrics for discrete APSIM-reference oat phenology experiments."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from .oats_history import cultivar_mapping_status


def _date_error(row: dict[str, Any], observed: str, simulated: str) -> int | None:
    if not row.get(observed) or not row.get(simulated):
        return None
    return (
        dt.date.fromisoformat(str(row[simulated]))
        - dt.date.fromisoformat(str(row[observed]))
    ).days


def _scope(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [
        row for row in rows
        if row.get("observed_yield_t_ha") is not None
        and row.get("simulated_yield_t_ha") is not None
    ]
    errors = [
        float(row["simulated_yield_t_ha"]) - float(row["observed_yield_t_ha"])
        for row in usable
    ]
    tillering = [
        value for row in rows
        if (value := _date_error(
            row, "observed_tillering_date", "simulated_tillering_date"
        )) is not None
    ]
    heading = [
        value for row in rows
        if (value := _date_error(
            row, "observed_heading_date", "simulated_heading_date"
        )) is not None
    ]

    def date_metrics(values: list[int]) -> dict[str, Any]:
        return {
            "n": len(values),
            "mae_days": sum(abs(value) for value in values) / len(values)
            if values else None,
            "bias_days": sum(values) / len(values) if values else None,
        }

    return {
        "cases": len(usable),
        "yield_mae_t_ha": sum(abs(value) for value in errors) / len(errors)
        if errors else None,
        "yield_rmse_t_ha": math.sqrt(
            sum(value * value for value in errors) / len(errors)
        ) if errors else None,
        "yield_bias_t_ha": sum(errors) / len(errors) if errors else None,
        "tillering": date_metrics(tillering),
        "heading": date_metrics(heading),
    }


def phenology_candidate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep the cultivar-confounded 2019 case separate from Yakov evaluation."""
    yakov = [
        row for row in rows
        if cultivar_mapping_status(
            row.get("observed_cultivar"), row.get("model_cultivar")
        ).startswith("matched")
    ]
    return {
        "all_cases_context": _scope(rows),
        "development_yakov_2020_2022": _scope([
            row for row in yakov if 2020 <= int(row["year"]) <= 2022
        ]),
        "validation_yakov_2023": _scope([
            row for row in yakov if int(row["year"]) == 2023
        ]),
        "argamak_2019_context_only": _scope([
            row for row in rows if int(row["year"]) == 2019
        ]),
    }


def pareto_comparison(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare candidates without combining unlike metrics into one score."""
    vectors = {}
    for candidate in candidates:
        scope = candidate["metrics"]["development_yakov_2020_2022"]
        vectors[candidate["name"]] = (
            scope["yield_rmse_t_ha"],
            scope["tillering"]["mae_days"],
            scope["heading"]["mae_days"],
        )

    nondominated = []
    for name, vector in vectors.items():
        dominated = any(
            other != name
            and all(a <= b for a, b in zip(other_vector, vector))
            and any(a < b for a, b in zip(other_vector, vector))
            for other, other_vector in vectors.items()
        )
        if not dominated:
            nondominated.append(name)
    current = vectors["current_yakov"]
    equivalent_to_current = [
        name for name, vector in vectors.items()
        if name != "current_yakov" and vector == current
    ]
    improves_current = [
        name for name, vector in vectors.items() if name != "current_yakov"
        and all(a <= b for a, b in zip(vector, current))
        and any(a < b for a, b in zip(vector, current))
    ]
    return {
        "objectives": [
            "development yield RMSE t/ha",
            "development tillering MAE days",
            "development heading MAE days",
        ],
        "normalization": "none",
        "weighting": "none",
        "vectors": {name: list(value) for name, value in vectors.items()},
        "nondominated_candidates": nondominated,
        "equivalent_to_current": equivalent_to_current,
        "candidates_dominating_current": improves_current,
        "automatic_model_change": False,
    }
