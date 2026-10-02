"""Preseason nitrogen and residue lineage for the 2023 oat fields."""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from pathlib import Path
from typing import Any


ARRAY_PREFIXES = (
    "NO3kgHa", "NH4kgHa", "UreakgHa", "MineralisedN", "TotalOrganicN",
    "Denitrification", "NFlow", "NutrientNatm", "NutrientDenitrifiedN",
    "NO3RunoffLoss", "NH4RunoffLoss", "UreaRunoffLoss",
)
SCALARS = (
    "MineralN", "Rain", "OatsAGWt", "OatsZadok", "OatsRootDepth",
    "SurfaceResidueWt", "SurfaceResidueN", "SurfaceResidueIncorporatedN",
    "WheatRootNUptake", "RedCloverRootNUptake", "PlantainForageRootNUptake",
    "OatsRootNUptake", "LeachNO3", "LeachNH4", "LeachUrea",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _prefix_values(row: sqlite3.Row, columns: list[str], prefix: str) -> list[float]:
    values = []
    for column in columns:
        if column == prefix or column.startswith(prefix + "("):
            value = _number(row[column])
            if value is not None:
                values.append(value)
    return values


def collect_preseason_rows(
    database: Path,
    inventory: dict[str, Any],
    windows: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Read complete daily report rows across the preceding fallow and oat crop."""
    output: list[dict[str, Any]] = []
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        simulation_rows = connection.execute(
            'SELECT ID FROM "_Simulations" WHERE Name=?',
            (inventory["simulation"],),
        ).fetchall()
        if len(simulation_rows) != 1:
            raise ValueError("Missing or ambiguous simulation in diagnostic database")
        simulation_id = simulation_rows[0]["ID"]
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        for field, window in windows.items():
            candidates = [
                name for name, report in inventory["fields"][field]["reports"].items()
                if any("OatsLAI" in expression for expression in report["variables"])
            ]
            if len(candidates) != 1 or candidates[0] not in tables:
                raise ValueError(f"{field}: missing or ambiguous daily oat report")
            table = candidates[0]
            columns = _columns(connection, table)
            required = {"Clock.Today", *SCALARS}
            missing = sorted(required - set(columns))
            for prefix in ARRAY_PREFIXES:
                if not any(name == prefix or name.startswith(prefix + "(") for name in columns):
                    missing.append(prefix)
            if missing:
                raise ValueError(f"{field}: missing preseason outputs {missing}")
            quoted = ", ".join('"' + value.replace('"', '""') + '"' for value in columns)
            raw_rows = connection.execute(
                f'SELECT {quoted} FROM "{table}" WHERE SimulationID=? '
                'AND substr("Clock.Today",1,10)>=? AND substr("Clock.Today",1,10)<=? '
                'ORDER BY "Clock.Today"',
                (simulation_id, window["previous_harvest_date"], window["end_date"]),
            ).fetchall()
            for raw in raw_rows:
                item: dict[str, Any] = {
                    "field": field, "date": str(raw["Clock.Today"])[:10],
                }
                for scalar in SCALARS:
                    item[scalar] = _number(raw[scalar])
                for prefix in ARRAY_PREFIXES:
                    values = _prefix_values(raw, columns, prefix)
                    item[f"{prefix}_total"] = sum(values)
                    for index, value in enumerate(values, 1):
                        item[f"{prefix}_layer_{index}"] = value
                    if prefix == "NFlow":
                        item["NFlow_bottom_native"] = values[-1] if values else None
                output.append(item)
    return output


def _snapshot(rows: list[dict[str, Any]], date: str, side: str) -> dict[str, Any] | None:
    if side == "on_or_after":
        return next((row for row in rows if str(row["date"]) >= date), None)
    candidates = [row for row in rows if str(row["date"]) <= date]
    return candidates[-1] if candidates else None


def _snapshot_output(field: str, phase: str, row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "MineralN", "NO3kgHa_total", "NH4kgHa_total", "UreakgHa_total",
        "TotalOrganicN_total", "SurfaceResidueWt", "SurfaceResidueN",
        "OatsAGWt", "OatsZadok", "OatsRootDepth",
    )
    return {"field": field, "phase": phase, "date": row["date"],
            **{key: row.get(key) for key in keys}}


def build_preseason_n_summary(
    daily_rows: list[dict[str, Any]],
    windows: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build phase snapshots and interval changes without claiming mass closure."""
    snapshots: list[dict[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    for field, window in sorted(windows.items()):
        rows = sorted(
            (row for row in daily_rows if row["field"] == field),
            key=lambda row: str(row["date"]),
        )
        if not rows:
            raise ValueError(f"{field}: no preseason rows")
        sowing = dt.date.fromisoformat(window["sowing_date"])
        previous_harvest = dt.date.fromisoformat(window["previous_harvest_date"])
        pre_sowing = (sowing - dt.timedelta(days=1)).isoformat()
        named: dict[str, dict[str, Any] | None] = {
            "previous_harvest": _snapshot(rows, previous_harvest.isoformat(), "on_or_after"),
            "post_harvest_day": _snapshot(
                rows, (previous_harvest + dt.timedelta(days=1)).isoformat(), "on_or_after"
            ),
            "year_end": _snapshot(rows, f"{previous_harvest.year}-12-31", "on_or_before"),
            "pre_sowing": _snapshot(rows, pre_sowing, "on_or_before"),
            "sowing": _snapshot(rows, sowing.isoformat(), "on_or_after"),
        }
        crop_rows = [row for row in rows if row["date"] >= sowing.isoformat()]
        active = [row for row in crop_rows if (_number(row.get("OatsAGWt")) or 0) > 0]
        if not active:
            raise ValueError(f"{field}: no active oat rows")
        named["first_active"] = active[0]
        named["grain_fill_start"] = next(
            (row for row in active
             if _number(row.get("OatsZadok")) is not None
             and float(row["OatsZadok"]) >= 71),
            None,
        )
        named["last_active"] = active[-1]
        if any(row is None for row in named.values()):
            missing = [name for name, row in named.items() if row is None]
            raise ValueError(f"{field}: missing phase snapshots {missing}")
        for phase, row in named.items():
            snapshots.append(_snapshot_output(field, phase, row))  # type: ignore[arg-type]

        interval_specs = (
            ("post_harvest_to_pre_sowing", named["post_harvest_day"], named["pre_sowing"]),
            ("oat_active", named["first_active"], named["last_active"]),
        )
        for name, start, end in interval_specs:
            assert start is not None and end is not None
            selected = [row for row in rows if start["date"] <= row["date"] <= end["date"]]
            mineralised = [
                _number(row.get("MineralisedN_total")) for row in selected
                if _number(row.get("MineralisedN_total")) is not None
            ]
            rain = [
                _number(row.get("Rain")) for row in selected
                if _number(row.get("Rain")) is not None
            ]
            def total(key: str) -> float:
                return sum(
                    float(value) for row in selected
                    if (value := _number(row.get(key))) is not None
                )
            crop_uptake_native = sum(total(key) for key in (
                "WheatRootNUptake", "RedCloverRootNUptake",
                "PlantainForageRootNUptake",
            ))
            intervals.append({
                "field": field, "interval": name,
                "start_date": start["date"], "end_date": end["date"],
                "days": len(selected),
                "net_mineralised_n_sum_kg_ha": sum(mineralised),
                "rain_sum_mm": sum(rain),
                "denitrification_native_sum": total("Denitrification_total"),
                "nutrient_n_atmosphere_loss_sum_kg_ha": total("NutrientNatm_total"),
                "nutrient_denitrified_n_sum_kg_ha": total("NutrientDenitrifiedN_total"),
                "bottom_n_flow_native_sum": total("NFlow_bottom_native"),
                "leached_no3_sum_kg_ha": total("LeachNO3"),
                "leached_nh4_sum_kg_ha": total("LeachNH4"),
                "leached_urea_sum_kg_ha": total("LeachUrea"),
                "runoff_no3_loss_sum_kg_ha": total("NO3RunoffLoss_total"),
                "runoff_nh4_loss_sum_kg_ha": total("NH4RunoffLoss_total"),
                "runoff_urea_loss_sum_kg_ha": total("UreaRunoffLoss_total"),
                "surface_residue_incorporated_n_sum_kg_ha": total(
                    "SurfaceResidueIncorporatedN"
                ),
                "wheat_root_n_uptake_native_sum": total("WheatRootNUptake"),
                "oats_root_n_uptake_native_sum": total("OatsRootNUptake"),
                "red_clover_root_n_uptake_native_sum": total("RedCloverRootNUptake"),
                "plantain_root_n_uptake_native_sum": total(
                    "PlantainForageRootNUptake"
                ),
                "reported_crop_root_n_uptake_native_sum": crop_uptake_native,
                "mineral_n_change_kg_ha": (
                    float(end["MineralN"]) - float(start["MineralN"])
                ),
                "total_organic_n_change_kg_ha": (
                    float(end["TotalOrganicN_total"])
                    - float(start["TotalOrganicN_total"])
                ),
                "surface_residue_n_change_kg_ha": (
                    float(end["SurfaceResidueN"]) - float(start["SurfaceResidueN"])
                ),
                "status": "partial_reported_terms_only",
            })
    return snapshots, intervals
