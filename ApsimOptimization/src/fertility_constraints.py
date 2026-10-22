"""Explicit fertility constraints for the multi-year rotation optimizer.

Only quantities actually returned by APSIM may be evaluated here.  Regional
soil-property norms are useful for screening a soil profile, but they must not
be silently substituted for simulated P, K, pH or organic-carbon dynamics.
"""
from __future__ import annotations

import math
from typing import Any


SUPPORTED_MODES = {"disabled", "objective_only", "hard"}
SUPPORTED_REFERENCE = "fixed_map_baseline"
MISSING_DATA_VIOLATION = 1.0e6


def humic_enabled(config: dict[str, Any] | None) -> bool:
    return ((config or {}).get("humic_carbon") or {}).get("mode", "disabled") == "hard"


def inequality_constraint_count(config: dict[str, Any] | None) -> int:
    # Retain the existing N slot for backwards compatibility.
    return 1 + int(humic_enabled(config))


def _validate_humic(settings):
    humic = settings.get("humic_carbon") or {}
    errors = []
    mode = humic.get("mode", "disabled")
    if mode not in {"disabled", "hard"}:
        errors.append("fertility_constraints.humic_carbon.mode must be disabled or hard")
    if mode == "hard":
        if humic.get("reference") != "period_start":
            errors.append("fertility_constraints.humic_carbon.reference must be period_start")
        if humic.get("scope") != "whole_profile":
            errors.append("fertility_constraints.humic_carbon.scope must be whole_profile")
    return errors


def validate_config(config: dict[str, Any] | None) -> list[str]:
    """Return configuration errors without changing or running the model."""
    settings = config or {}
    errors: list[str] = _validate_humic(settings)
    mode = settings.get("mode", "disabled")
    if mode not in SUPPORTED_MODES:
        errors.append(
            "fertility_constraints.mode must be disabled, objective_only or hard"
        )

    mineral = settings.get("mineral_n_end") or {}
    if not mineral.get("enabled", False):
        return errors
    if mineral.get("reference", SUPPORTED_REFERENCE) != SUPPORTED_REFERENCE:
        errors.append(
            "fertility_constraints.mineral_n_end.reference must be "
            f"{SUPPORTED_REFERENCE}"
        )

    threshold_names = (
        "min_fraction_of_reference",
        "max_fraction_of_reference",
        "absolute_min_kg_ha",
        "absolute_max_kg_ha",
    )
    configured = []
    for name in threshold_names:
        value = mineral.get(name)
        if value is None:
            continue
        configured.append(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"fertility_constraints.mineral_n_end.{name} must be numeric")
        elif not math.isfinite(float(value)) or float(value) < 0:
            errors.append(
                f"fertility_constraints.mineral_n_end.{name} must be finite and non-negative"
            )
    if not configured and mode != "disabled":
        errors.append(
            "fertility_constraints.mineral_n_end requires at least one explicit threshold"
        )
    minimum = mineral.get("absolute_min_kg_ha")
    maximum = mineral.get("absolute_max_kg_ha")
    if (
        isinstance(minimum, (int, float))
        and not isinstance(minimum, bool)
        and isinstance(maximum, (int, float))
        and not isinstance(maximum, bool)
        and float(minimum) > float(maximum)
    ):
        errors.append(
            "fertility_constraints.mineral_n_end absolute minimum exceeds maximum"
        )
    return errors


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _constraint_row(
    *,
    constraint_id: str,
    year: int,
    field: str,
    observed: float,
    threshold: float,
    scale: float,
    direction: str,
    reference: float | None,
    metric: str = "mineral_n_end_kg_ha",
) -> dict[str, Any]:
    if direction == "minimum":
        g = (threshold - observed) / scale
    elif direction == "maximum":
        g = (observed - threshold) / scale
    else:  # pragma: no cover - internal programming error
        raise ValueError(f"Unsupported direction: {direction}")
    return {
        "constraint_id": constraint_id,
        "metric": metric,
        "year": int(year),
        "field": str(field),
        "observed_kg_ha": observed,
        "reference_kg_ha": reference,
        "threshold_kg_ha": threshold,
        "direction": direction,
        "normalization_scale_kg_ha": scale,
        "g": g,
        "violation": max(0.0, g),
        "passed": g <= 0.0,
        "status": "checked",
    }


def evaluate(
    field_rows: list[dict[str, Any]],
    baseline_n: dict[str, dict[str, Any]] | None,
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate configured rules and return diagnostics plus one aggregate G.

    ``aggregate_g`` is suitable for pymoo (feasible when <= 0).  In
    ``objective_only`` mode the violations remain visible and contribute to the
    third objective, while ``aggregate_g`` is zero so they are not presented as
    confirmed hard agronomic limits.
    """
    settings = config or {}
    errors = validate_config(settings)
    if errors:
        raise ValueError("; ".join(errors))
    mode = settings.get("mode", "disabled")
    mineral = settings.get("mineral_n_end") or {}
    unassessed = list(settings.get("unassessed_indicators") or [])
    if mode == "disabled" or not mineral.get("enabled", False):
        return {
            "mode": mode,
            "status": "disabled",
            "is_feasible": None,
            "normalized_violation": 0.0,
            "aggregate_g": 0.0,
            "checks": [],
            "missing": [],
            "unassessed_indicators": unassessed,
        }

    checks: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    baseline_n = baseline_n or {}
    for item in field_rows:
        year = int(item["year"])
        field = str(item["field"])
        observed = _number(item.get("mineral_n_end_kg_ha"))
        reference = _number((baseline_n.get(str(year)) or {}).get(field))
        if observed is None:
            missing.append({
                "year": year,
                "field": field,
                "metric": "mineral_n_end_kg_ha",
                "reason": "candidate value is missing",
            })
            continue

        min_fraction = mineral.get("min_fraction_of_reference")
        max_fraction = mineral.get("max_fraction_of_reference")
        if (min_fraction is not None or max_fraction is not None) and reference is None:
            missing.append({
                "year": year,
                "field": field,
                "metric": "mineral_n_end_kg_ha",
                "reason": "fixed-map baseline value is missing",
            })
            continue
        if min_fraction is not None:
            threshold = reference * float(min_fraction)
            checks.append(_constraint_row(
                constraint_id="mineral_n_end_min_fraction_of_reference",
                year=year, field=field, observed=observed, threshold=threshold,
                scale=max(abs(reference), 1.0), direction="minimum", reference=reference,
            ))
        if max_fraction is not None:
            threshold = reference * float(max_fraction)
            checks.append(_constraint_row(
                constraint_id="mineral_n_end_max_fraction_of_reference",
                year=year, field=field, observed=observed, threshold=threshold,
                scale=max(abs(reference), 1.0), direction="maximum", reference=reference,
            ))
        absolute_min = mineral.get("absolute_min_kg_ha")
        if absolute_min is not None:
            threshold = float(absolute_min)
            checks.append(_constraint_row(
                constraint_id="mineral_n_end_absolute_min",
                year=year, field=field, observed=observed, threshold=threshold,
                scale=max(abs(threshold), 1.0), direction="minimum", reference=reference,
            ))
        absolute_max = mineral.get("absolute_max_kg_ha")
        if absolute_max is not None:
            threshold = float(absolute_max)
            checks.append(_constraint_row(
                constraint_id="mineral_n_end_absolute_max",
                year=year, field=field, observed=observed, threshold=threshold,
                scale=max(abs(threshold), 1.0), direction="maximum", reference=reference,
            ))

    checked_violation = sum(float(row["violation"]) for row in checks)
    # Missing values must never look like a perfect fertility result.  In
    # objective-only mode this penalty affects F[2]; in hard mode it also makes
    # the candidate infeasible through G.
    missing_penalty = MISSING_DATA_VIOLATION * len(missing)
    violation = checked_violation + missing_penalty
    if missing:
        status = "missing_data"
    elif violation > 0:
        status = "checked_failed"
    else:
        status = "checked_passed"

    if mode == "hard":
        aggregate_g = violation
        feasible: bool | None = not missing and aggregate_g <= 0.0
    else:
        aggregate_g = 0.0
        feasible = None
    return {
        "mode": mode,
        "status": status,
        "is_feasible": feasible,
        "normalized_violation": violation,
        "checked_normalized_violation": checked_violation,
        "missing_data_penalty": missing_penalty,
        "aggregate_g": aggregate_g,
        "checks": checks,
        "missing": missing,
        "unassessed_indicators": unassessed,
    }


def evaluate_humic_period(period, config, expected_fields, evaluation_years):
    """Hard model rule: final Humic C >= own period-start Humic C per field.

    Uses the whole profile, not lab humus or a regional normative threshold.
    No field can compensate another; incomplete/unverified data fail closed.
    """
    settings = config or {}
    errors = _validate_humic(settings)
    if errors:
        raise ValueError("; ".join(errors))
    if not humic_enabled(settings):
        return {"mode": "disabled", "status": "disabled", "aggregate_g": 0.0,
                "is_feasible": None, "checks": [], "missing": []}
    years = list(evaluation_years)
    if (not years or any(type(y) is not int for y in years)
            or years != list(range(years[0], years[-1] + 1))):
        raise ValueError("Humic constraint requires consecutive increasing evaluation_years")
    expected_fields = list(expected_fields)
    start_date, end_date = f"{years[0]-1}-12-31", f"{years[-1]}-12-31"
    checks, missing = [], []
    period = period or {}
    values = period.get("fields") or {}
    reason = None
    if period.get("status") != "checked":
        reason = period.get("reason", "verified Humic period data are missing")
    elif period.get("period_start") != start_date or period.get("period_end") != end_date:
        reason = "Humic period does not match evaluation_years"
    if not expected_fields:
        missing.append({"field": "*", "reason": "expected field set is empty"})
    if set(values) - set(expected_fields):
        missing.append({"field": "*", "reason": "unexpected fields in Humic data"})
    for field in expected_fields:
        row = values.get(field) or {}
        start, end = _number(row.get("start_kg_ha")), _number(row.get("end_kg_ha"))
        if reason or start is None or end is None or start < 0 or end < 0:
            missing.append({"field": field, "metric": "humic_carbon_kg_ha",
                            "reason": reason or "finite non-negative start/end Humic values are required"})
            continue
        check = _constraint_row(
            constraint_id="humic_carbon_no_period_decline", metric="humic_carbon_kg_ha",
            year=years[-1], field=field, observed=end, threshold=start,
            scale=max(abs(start), 1.0), direction="minimum", reference=start)
        check.update(period_start=start_date, period_end=end_date,
                     start_kg_ha=start, end_kg_ha=end, change_kg_ha=end-start)
        checks.append(check)
    checked_violation = sum(row["violation"] for row in checks)
    penalty = MISSING_DATA_VIOLATION * len(missing)
    violation = checked_violation + penalty
    return {
        "mode": "hard", "reference": "period_start", "scope": "whole_profile",
        "period_start": start_date, "period_end": end_date,
        "normative_humus_assessment": "not_assessed",
        "status": "missing_data" if missing else "checked_failed" if violation > 0 else "checked_passed",
        "aggregate_g": violation, "normalized_violation": violation,
        "checked_normalized_violation": checked_violation, "missing_data_penalty": penalty,
        "is_feasible": not missing and violation <= 0,
        "failed_fields": [row["field"] for row in checks if not row["passed"]],
        "checks": checks, "missing": missing,
    }
