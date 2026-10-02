"""Audit which historical sowing applications come from evidence or model fallback."""
from __future__ import annotations

import datetime as dt
import copy
from collections import Counter, defaultdict
from typing import Any

from .historical_case import parse_technology_nutrients


PERENNIALS = {"RedClover", "PlantainForage"}
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parts(value: str, separator: str) -> list[str]:
    return [item.strip() for item in str(value).split(separator)]


def _date(year: int, day_month: str) -> dt.date:
    day_text, month_text = day_month.strip().split("-", 1)
    month = MONTHS.get(month_text[:3].lower())
    if month is None:
        raise ValueError(f"Unknown month in rotation date: {day_month}")
    return dt.date(year, month, int(day_text))


def _event(
    harvest_year: int, index: int, start_date: dt.date,
    crops: str, cultivars: str, sowing_day_month: str,
) -> tuple[str, str | None]:
    crop_parts = _parts(crops, "+")
    pure_perennial = all(crop in PERENNIALS for crop in crop_parts)
    winter = "Wheat" in crop_parts and "Winter_rye" in cultivars
    if index == 0 and (pure_perennial or winter):
        return "startup_approximation", start_date.isoformat()
    if pure_perennial:
        return "perennial_carry_over", None
    if sowing_day_month in ("", "-"):
        return "missing_rotation_sowing_date", None
    event_year = harvest_year - 1 if winter else harvest_year
    return ("autumn_sowing_for_next_harvest" if winter else "scheduled_sowing",
            _date(event_year, sowing_day_month).isoformat())


def build_management_audit(
    inventory: dict[str, Any], observations: dict[str, Any], replay_plan: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return rotation-year history, field coverage and aggregate evidence counts."""
    start_date = dt.date.fromisoformat(inventory["start"][:10])
    end_date = dt.date.fromisoformat(inventory["end"][:10])
    start_year, end_year = start_date.year, end_date.year
    observed = {
        (int(year), row["field"]): row
        for year, year_rows in (observations.get("years") or {}).items()
        for row in year_rows
    }
    replay_entries = {
        (int(row["harvest_year"]), row["field"]): row
        for row in replay_plan.get("entries", [])
    }
    skipped = defaultdict(list)
    for row in replay_plan.get("skipped", []):
        skipped[(int(row["harvest_year"]), row["field"])].append(row["reason"])

    rows = []
    for field, field_meta in sorted(
        inventory["fields"].items(), key=lambda item: int(item[0][5:])
    ):
        rotation = field_meta["rotation"]
        arrays = {
            key: _parts(rotation[key], ",") for key in (
                "RotationCrops", "RotationCultivars", "RotationSowingDates",
                "RotationHarvestDates",
            )
        }
        lengths = {len(values) for values in arrays.values()}
        if lengths != {end_year - start_year + 1}:
            raise ValueError(f"{field}: rotation length does not match model clock")
        static = field_meta["baseline_fertilizer"]
        static_amount = float(static["Amount"])
        static_type = static["FertiliserType"]
        for index, harvest_year in enumerate(range(start_year, end_year + 1)):
            crop = arrays["RotationCrops"][index]
            cultivar = arrays["RotationCultivars"][index]
            sowing_day = arrays["RotationSowingDates"][index]
            event_kind, event_date = _event(
                harvest_year, index, start_date, crop, cultivar, sowing_day
            )
            record = observed.get((harvest_year, field))
            technology = record.get("technology") if record else None
            nutrients = parse_technology_nutrients(technology)
            replay = replay_entries.get((harvest_year, field))

            if event_date is None:
                source = "no_sowing_event"
                effective_amount = None
                effective_type = None
            elif replay is not None:
                source = "observed_n_label"
                effective_amount = float(replay["amount_kg_n_ha"])
                effective_type = replay["fertilizer_type"]
            else:
                source = "model_static_fallback"
                effective_amount = static_amount
                effective_type = static_type

            if source != "model_static_fallback":
                fallback_reason = None
            elif record is None:
                fallback_reason = "historical_record_missing"
            elif nutrients["N"] is None:
                fallback_reason = "technology_has_no_explicit_n_rate"
            elif event_date < start_date.isoformat():
                fallback_reason = "observed_sowing_before_model_clock"
            else:
                fallback_reason = ";".join(skipped.get((harvest_year, field), [])) or "not_replayed"

            rows.append({
                "harvest_year": harvest_year,
                "field": field,
                "rotation_crop": crop,
                "rotation_cultivar": cultivar,
                "rotation_sowing_day_month": sowing_day,
                "sowing_event_kind": event_kind,
                "model_sowing_event_date": event_date,
                "historical_record_present": record is not None,
                "observed_sowing_date": record.get("observed_sow_date") if record else None,
                "observed_technology": technology,
                "label_n": nutrients["N"],
                "label_p": nutrients["P"],
                "label_k": nutrients["K"],
                "model_static_fertilizer_type": static_type,
                "model_static_amount": static_amount,
                "effective_application_source": source,
                "effective_fertilizer_type": effective_type,
                "effective_amount_model_value": effective_amount,
                "fallback_reason": fallback_reason,
            })

    by_field = defaultdict(list)
    for row in rows:
        by_field[row["field"]].append(row)
    field_rows = []
    for field, values in sorted(by_field.items(), key=lambda item: int(item[0][5:])):
        counts = Counter(row["effective_application_source"] for row in values)
        fallbacks = [row for row in values if row["effective_application_source"] == "model_static_fallback"]
        field_rows.append({
            "field": field,
            "rotation_years": len(values),
            "sowing_events": sum(row["model_sowing_event_date"] is not None for row in values),
            "observed_n_label_events": counts["observed_n_label"],
            "model_static_fallback_events": counts["model_static_fallback"],
            "no_sowing_event_years": counts["no_sowing_event"],
            "fallback_years": ";".join(str(row["harvest_year"]) for row in fallbacks),
        })
    counts = Counter(row["effective_application_source"] for row in rows)
    reasons = Counter(
        row["fallback_reason"] for row in rows if row["fallback_reason"] is not None
    )
    summary = {
        "rotation_year_rows": len(rows),
        "fields": len(field_rows),
        "sowing_events": sum(row["model_sowing_event_date"] is not None for row in rows),
        "observed_n_label_events": counts["observed_n_label"],
        "model_static_fallback_events": counts["model_static_fallback"],
        "no_sowing_event_years": counts["no_sowing_event"],
        "fallback_reasons": dict(sorted(reasons.items())),
        "field2_field7_pre_2023_fallbacks": [
            row for row in rows
            if row["field"] in {"Field2", "Field7"}
            and row["harvest_year"] < 2023
            and row["effective_application_source"] == "model_static_fallback"
        ],
    }
    return rows, field_rows, summary


def inject_missing_n_records(
    observations: dict[str, Any], audit_rows: list[dict[str, Any]],
    amount: float, fields: set[str], before_year: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Add diagnostic replay records only where the audit found missing history."""
    if amount < 0:
        raise ValueError("Diagnostic N amount must be nonnegative")
    result = copy.deepcopy(observations)
    changes = []
    candidates = [
        row for row in audit_rows
        if row["field"] in fields
        and int(row["harvest_year"]) < before_year
        and row["effective_application_source"] == "model_static_fallback"
        and row["fallback_reason"] == "historical_record_missing"
    ]
    for row in candidates:
        year = int(row["harvest_year"])
        records = (result.setdefault("years", {})).setdefault(str(year), [])
        if any(record.get("field") == row["field"] for record in records):
            raise ValueError(f"Refusing duplicate diagnostic record for {row['field']}/{year}")
        record = {
            "field": row["field"],
            "crop_group": "diagnostic_missing_history",
            "technology": f"diagnostic missing history (N{amount:g})",
            "observed_sow_date": row["model_sowing_event_date"],
        }
        records.append(record)
        changes.append({
            "harvest_year": year,
            "field": row["field"],
            "rotation_crop": row["rotation_crop"],
            "sowing_date": row["model_sowing_event_date"],
            "replayed_n_model_value": float(amount),
            "source": "diagnostic level already present in supplied historical labels",
        })
    return result, changes
