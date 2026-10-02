"""Yield-only blocked comparison for oat source and sink mechanism screens."""
from __future__ import annotations

from typing import Any


def source_candidate_comparison(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare development and validation RMSE without weights or normalization."""
    vectors = {}
    for candidate in candidates:
        metrics = candidate["metrics"]
        vectors[candidate["name"]] = (
            metrics["development_yakov_2020_2022"]["yield_rmse_t_ha"],
            metrics["validation_yakov_2023"]["yield_rmse_t_ha"],
        )
    current = vectors["current_yakov"]
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
    return {
        "objectives": [
            "development Yakov 2020-2022 yield RMSE t/ha",
            "validation Yakov 2023 yield RMSE t/ha",
        ],
        "normalization": "none",
        "weighting": "none",
        "vectors": {name: list(vector) for name, vector in vectors.items()},
        "equivalent_to_current": [
            name for name, vector in vectors.items()
            if name != "current_yakov" and vector == current
        ],
        "nondominated_candidates": nondominated,
        "candidates_dominating_current": [
            name for name, vector in vectors.items() if name != "current_yakov"
            and all(a <= b for a, b in zip(vector, current))
            and any(a < b for a, b in zip(vector, current))
        ],
        "automatic_model_change": False,
    }
