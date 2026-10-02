"""Quality gates for APSIM runs made with an incompletely known soil profile."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).strip()).date()


def longest_consecutive_true(values: Iterable[bool]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def evaluate_run_hydrology_quality(
    run: Mapping[str, object],
    daily_rows: Iterable[Mapping[str, object]],
    *,
    retention_profile_validated: bool,
    establishment_days: int = 14,
    severe_stress_limit: float = 0.05,
    near_zero_yield_t_ha: float = 0.05,
) -> dict:
    """Classify a completed run separately from its fitness as a yield prediction."""
    rows = sorted(
        (row for row in daily_rows if _date(row.get("Clock.Today"))),
        key=lambda row: _date(row.get("Clock.Today")),
    )
    emergence = next(
        (
            _date(row.get("Clock.Today"))
            for row in rows
            if str(row.get("Wheat.Phenology.CurrentStageName", "")).strip() == "Emergence"
        ),
        None,
    )
    window_end = emergence + timedelta(days=establishment_days - 1) if emergence else None
    establishment = [
        row
        for row in rows
        if emergence <= _date(row.get("Clock.Today")) <= window_end
    ] if emergence else []
    leaf_fw = [_number(row.get("LeafFw")) for row in establishment]
    stress_flags = [value is not None and value <= severe_stress_limit for value in leaf_fw]
    severe_stress_streak = longest_consecutive_true(stress_flags)

    lai_values = [_number(row.get("Wheat.LAI")) for row in rows]
    lai_values = [value for value in lai_values if value is not None]
    max_lai = max(lai_values, default=None)
    yield_raw = _number(run.get("simulated_yield_raw"))
    yield_t_ha = yield_raw / 1000.0 if yield_raw is not None else None

    reasons = []
    hydrology_failure = False
    if emergence is None:
        reasons.append("emergence_not_detected")
    if severe_stress_streak >= 3 and (yield_t_ha is None or yield_t_ha <= near_zero_yield_t_ha):
        hydrology_failure = True
        reasons.append("early_severe_water_stress_with_near_zero_yield")
    if not retention_profile_validated:
        reasons.append("soil_retention_profile_not_field_validated")

    if hydrology_failure:
        status = "rejected_hydrology_failure"
    elif not retention_profile_validated:
        status = "diagnostic_only_unvalidated_soil"
    else:
        status = "accepted"

    return {
        "year": run.get("year"),
        "candidate_id": run.get("candidate_id"),
        "apsim_run_status": run.get("run_status"),
        "simulated_yield_t_ha": yield_t_ha,
        "observed_yield_t_ha": _number(run.get("observed_yield_t_ha")),
        "emergence_date": emergence.isoformat() if emergence else None,
        "establishment_severe_stress_streak_days": severe_stress_streak,
        "max_lai": max_lai,
        "retention_profile_validated": retention_profile_validated,
        "prediction_allowed": status == "accepted",
        "quality_status": status,
        "quality_reasons": ";".join(reasons),
    }
