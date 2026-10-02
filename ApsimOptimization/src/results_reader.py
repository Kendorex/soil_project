from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from .nitrogen import calculate_mineral_n_end


class ResultsError(RuntimeError):
    """Raised when a required APSIMX SQLite result cannot be read."""


OPTIONAL_OUTPUT_COLUMNS = (
    "MineralN", "MineralNWithUrea", "NO3kgHa", "NH4kgHa", "OrganicC",
    "DenitrifiedN", "NAtmosphereLoss", "NO3Leaching",
)

FINAL_CROP_STATE_COLUMNS = {
    "Wheat.Phenology.CurrentStageName": "crop_stage_end",
    "Wheat.Phenology.Zadok.Stage": "zadok_stage_end",
    "Wheat.LAI": "lai_end",
}

LIFECYCLE_COLUMNS = (
    "Wheat.Phenology.CurrentStageName",
    "Wheat.LAI",
    "Wheat.AboveGround.Wt",
)


def _get_config_value(config: dict[str, Any], names: list[str], default: Any = None) -> Any:
    for name in names:
        if name in config:
            return config[name]
    return default


def _diagnose_database(database: Path) -> dict[str, Any]:
    diagnosis: dict[str, Any] = {"database": str(database), "tables": {}}
    con = sqlite3.connect(database)
    try:
        cur = con.cursor()
        tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            columns = [row[1] for row in cur.execute(f'PRAGMA table_info("{table}")')]
            row_count = cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            diagnosis["tables"][table] = {"columns": columns, "row_count": row_count}
    finally:
        con.close()
    return diagnosis


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) else result


def convert_yield_to_t_ha(yield_raw: float, config: dict[str, Any]) -> tuple[float, str]:
    """Convert Report yield using an explicit unit when configured."""
    source_unit = str(config.get("yield_source_unit", "auto")).strip().casefold().replace("-", "_")
    if source_unit in {"kg_per_ha", "kg/ha", "kg_ha"}:
        return yield_raw / 1000.0, "yield_read_kg_ha_converted_to_t_ha"
    if source_unit in {"t_per_ha", "t/ha", "t_ha"}:
        return yield_raw, "yield_read_t_ha"
    if source_unit not in {"", "auto", "heuristic"}:
        raise ResultsError(f"Unsupported results.yield_source_unit: {source_unit!r}")
    if yield_raw > 100:
        return yield_raw / 1000.0, "yield_read_kg_ha_converted_to_t_ha"
    return yield_raw, "yield_read_t_ha"


def _status(values: list[Any]) -> str:
    present = sum(_number(value) is not None for value in values)
    if present == 0:
        return "missing"
    return "available" if present == len(values) else "partial"


def _date_column(columns: list[str]) -> str | None:
    for candidate in ("Date", "Today", "Clock.Today"):
        if candidate in columns:
            return candidate
    return None


def _indexed_columns(columns: list[str], base_name: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(base_name)}\((\d+)\)$")
    matched: list[tuple[int, str]] = []
    for column in columns:
        match = pattern.fullmatch(column)
        if match:
            matched.append((int(match.group(1)), column))
    return [column for _, column in sorted(matched)]


def _output_value(
    row_values: dict[str, Any],
    columns: list[str],
    base_name: str,
) -> tuple[float | None, str]:
    """Read an exact scalar alias or sum its exact APSIM layer columns."""
    scalar = _number(row_values.get(base_name))
    if scalar is not None:
        return scalar, "available"
    indexed = _indexed_columns(columns, base_name)
    if not indexed:
        return None, "missing"
    layer_values = [_number(row_values.get(column)) for column in indexed]
    present = [value for value in layer_values if value is not None]
    if not present:
        return None, "missing"
    return sum(present), "available" if len(present) == len(indexed) else "partial"


def _lifecycle_diagnostics(
    cursor: sqlite3.Cursor,
    table: str,
    columns: list[str],
    date_column: str | None,
    sowing_stage_names: tuple[str, ...],
    harvest_stage_names: tuple[str, ...],
) -> dict[str, Any]:
    """Summarise crop lifecycle events without interpreting a zero yield as success."""
    stage_column = "Wheat.Phenology.CurrentStageName"
    stage_observable = stage_column in columns
    date_expression = f'"{date_column}"' if date_column else "NULL"

    stages: list[tuple[str, Any]] = []
    if stage_observable:
        rows = cursor.execute(
            f'SELECT "{stage_column}", {date_expression} FROM "{table}" ORDER BY rowid'
        ).fetchall()
        stages = [(str(row[0] or "").strip(), row[1]) for row in rows]

    def transitions(names: tuple[str, ...]) -> list[Any]:
        accepted = set(names)
        dates: list[Any] = []
        previous_matched = False
        for stage, day in stages:
            matched = stage in accepted
            if matched and not previous_matched:
                dates.append(day)
            previous_matched = matched
        return dates

    sowing_dates = transitions(sowing_stage_names)
    harvest_dates = transitions(harvest_stage_names)
    nonempty_stages = [(stage, day) for stage, day in stages if stage]
    crop_sown = bool(sowing_dates or nonempty_stages) if stage_observable else None

    def maximum(column: str) -> float | None:
        if column not in columns:
            return None
        value = cursor.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
        return _number(value)

    return {
        "lifecycle_observable": stage_observable,
        "crop_sown": crop_sown,
        "sowing_event_count": len(sowing_dates),
        "sowing_detected_date": str(
            sowing_dates[0] if sowing_dates else (nonempty_stages[0][1] if nonempty_stages else "")
        ),
        "harvest_reached": bool(harvest_dates) if stage_observable else None,
        "harvest_event_count": len(harvest_dates),
        "harvest_detected_date": str(harvest_dates[-1] if harvest_dates else ""),
        "peak_lai": maximum("Wheat.LAI"),
        "peak_biomass": maximum("Wheat.AboveGround.Wt"),
    }


def read_report(
    database: Path | str,
    config: dict[str, Any],
    nitrogen_config: dict[str, Any] | None = None,
    quality_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    database = Path(database)
    if not database.exists():
        raise ResultsError(f"SQLite database not found: {database}")

    diagnosis = _diagnose_database(database)
    table = _get_config_value(config, ["report_table", "table", "report"], "Report")
    configured_yield_column = _get_config_value(config, ["yield_column", "yield", "yield_col"], "Yield")
    if table not in diagnosis["tables"]:
        raise ResultsError(f"Exact report table {table!r} not found; available: {list(diagnosis['tables'])}")
    columns = diagnosis["tables"][table]["columns"]
    yield_candidates = [configured_yield_column, *config.get("yield_fallback_columns", [])]
    yield_column = next((candidate for candidate in yield_candidates if candidate in columns), None)
    if yield_column is None:
        raise ResultsError(f"Exact yield columns {yield_candidates!r} not found in {table}; available: {columns}")

    selected_columns = [yield_column]
    selected_columns.extend(column for column in OPTIONAL_OUTPUT_COLUMNS if column in columns)
    for base_name in OPTIONAL_OUTPUT_COLUMNS:
        selected_columns.extend(_indexed_columns(columns, base_name))
    selected_columns.extend(column for column in ("N_NO3", "P_2O5", "SOC", "SOC_0") if column in columns)
    selected_columns.extend(column for column in FINAL_CROP_STATE_COLUMNS if column in columns)
    date_column = _date_column(columns)
    if date_column:
        selected_columns.append(date_column)
    selected_columns = list(dict.fromkeys(selected_columns))
    quoted = ", ".join(f'"{column}"' for column in selected_columns)

    con = sqlite3.connect(database)
    try:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        stage_column = "Wheat.Phenology.CurrentStageName"
        stage_names = tuple(config.get("harvest_stage_names", ["HarvestRipe"]))
        stage_harvest = None
        if stage_column in columns and stage_names:
            placeholders = ", ".join("?" for _ in stage_names)
            stage_harvest = cur.execute(
                f'SELECT rowid AS "__rowid__", {quoted} FROM "{table}" '
                f'WHERE "{stage_column}" IN ({placeholders}) ORDER BY rowid DESC LIMIT 1',
                stage_names,
            ).fetchone()

        if stage_harvest is not None:
            # APSIMX may simulate more than one crop cycle. Prefer the latest
            # completed harvest instead of an unfinished later crop which still
            # has non-zero standing grain biomass at the simulation end.
            control = stage_harvest
            timing = "harvest_row"
        else:
            control = cur.execute(
                f'SELECT rowid AS "__rowid__", {quoted} FROM "{table}" '
                f'WHERE "{yield_column}" IS NOT NULL AND "{yield_column}" != 0 '
                f'ORDER BY rowid DESC LIMIT 1'
            ).fetchone()
            timing = "last_nonzero_without_harvest_stage"
            if control is None:
                control = cur.execute(
                    f'SELECT rowid AS "__rowid__", {quoted} FROM "{table}" ORDER BY rowid DESC LIMIT 1'
                ).fetchone()
                timing = "end_of_simulation_no_harvest"

        lifecycle = _lifecycle_diagnostics(
            cur,
            table,
            columns,
            date_column,
            tuple(config.get("sowing_stage_names", ["Sowing"])),
            stage_names,
        )
    finally:
        con.close()

    selected_yield = _number(control[yield_column]) if control is not None else None
    if selected_yield is not None and selected_yield != 0:
        yield_raw = selected_yield
        yield_t_ha, yield_status = convert_yield_to_t_ha(yield_raw, config)
        run_status = "completed"
        warning = (
            ""
            if timing == "harvest_row"
            else f"Exact harvest stage {list(stage_names)!r} not found; using last nonzero {table}.{yield_column}"
        )
    else:
        yield_raw = 0.0
        yield_t_ha = 0.0
        yield_status = "no_yield_or_no_harvest"
        run_status = "completed_without_yield"
        warning = f"{table}.{yield_column} exists but contains no usable nonzero values at the selected control row"
        if stage_harvest is not None:
            warning += f"; harvest stage detected: {stage_harvest[stage_column]}"

    quality_config = quality_config or {}
    if quality_config:
        failure_status = None
        failure_reason = ""
        if quality_config.get("require_sowing", True) and lifecycle["crop_sown"] is False:
            failure_status = "invalid_not_sown"
            failure_reason = "crop was never sown during the simulation"
        elif quality_config.get("require_harvest", True) and lifecycle["harvest_reached"] is False:
            failure_status = "invalid_no_harvest"
            failure_reason = "crop was sown but no configured harvest stage was reached"
        elif yield_t_ha == 0 and not quality_config.get("allow_zero_yield", False):
            failure_status = "rejected_zero_yield"
            failure_reason = "harvest produced zero yield and strict run quality rejects silent zero results"

        minimum_sowings = int(quality_config.get("minimum_sowing_events", 0))
        minimum_harvests = int(quality_config.get("minimum_harvest_events", 0))
        if failure_status is None and lifecycle["lifecycle_observable"]:
            if lifecycle["sowing_event_count"] < minimum_sowings:
                failure_status = "invalid_not_sown"
                failure_reason = (
                    f"sowing events={lifecycle['sowing_event_count']}, required={minimum_sowings}"
                )
            elif lifecycle["harvest_event_count"] < minimum_harvests:
                failure_status = "invalid_no_harvest"
                failure_reason = (
                    f"harvest events={lifecycle['harvest_event_count']}, required={minimum_harvests}"
                )

        if failure_status:
            run_status = failure_status
            yield_status = failure_status
            warning = f"{failure_reason}; {warning}" if warning else failure_reason
        elif yield_t_ha == 0:
            run_status = "completed_zero_yield"

    row_values = {column: (control[column] if control is not None and column in control.keys() else None) for column in selected_columns}
    optional_values: dict[str, float | None] = {}
    optional_statuses: dict[str, str] = {}
    for column in OPTIONAL_OUTPUT_COLUMNS:
        optional_values[column], optional_statuses[column] = _output_value(row_values, columns, column)
    resolved_row_values = dict(row_values)
    resolved_row_values.update({
        column: value for column, value in optional_values.items() if value is not None
    })
    mineral = calculate_mineral_n_end(resolved_row_values, nitrogen_config or {})
    mineral_value = mineral["value"]
    if isinstance(mineral_value, float) and math.isnan(mineral_value):
        mineral_value = None

    nitrogen_config = nitrogen_config or {}
    additional_outputs = list(nitrogen_config.get(
        "additional_outputs", ["DenitrifiedN", "NAtmosphereLoss", "NO3Leaching"]
    ))
    if mineral_value is None:
        nitrogen_status = "missing"
    elif all(optional_statuses.get(column) == "available" for column in additional_outputs):
        nitrogen_status = "available"
    else:
        nitrogen_status = "partial"
    soil_values = list(optional_values.values())
    crop_stage = row_values.get("Wheat.Phenology.CurrentStageName")
    zadok_stage = _number(row_values.get("Wheat.Phenology.Zadok.Stage"))
    lai_end = _number(row_values.get("Wheat.LAI"))
    if run_status == "completed_without_yield":
        final_state = []
        if crop_stage not in (None, ""):
            final_state.append(f"final_stage={crop_stage}")
        if zadok_stage is not None:
            final_state.append(f"zadok={zadok_stage:g}")
        if lai_end is not None:
            final_state.append(f"lai={lai_end:g}")
        if final_state:
            warning += "; " + ", ".join(final_state)
    values = {
        "yield_t_ha": yield_t_ha,
        "yield_raw": yield_raw,
        "yield_status": yield_status,
        "run_status": run_status,
        "warning": warning,
        "N_NO3": _number(row_values.get("N_NO3")),
        "P_2O5": _number(row_values.get("P_2O5")),
        "SOC": _number(row_values.get("SOC")),
        "SOC_0": _number(row_values.get("SOC_0")),
        "MineralN_end": mineral_value,
        "MineralN_source": mineral["source"],
        "MineralNWithUrea_end": optional_values["MineralNWithUrea"],
        "NO3kgHa_end": optional_values["NO3kgHa"],
        "NH4kgHa_end": optional_values["NH4kgHa"],
        "OrganicC_end": optional_values["OrganicC"],
        "DenitrifiedN": optional_values["DenitrifiedN"],
        "DenitrifiedN_status": optional_statuses["DenitrifiedN"],
        "NAtmosphereLoss": optional_values["NAtmosphereLoss"],
        "NAtmosphereLoss_status": optional_statuses["NAtmosphereLoss"],
        "NO3Leaching": optional_values["NO3Leaching"],
        "NO3Leaching_status": optional_statuses["NO3Leaching"],
        "nitrogen_balance_outputs_status": nitrogen_status,
        "soil_output_status": _status(soil_values),
        "soil_output_timing": timing,
        "soil_output_date": str(row_values.get(date_column)) if date_column and row_values.get(date_column) is not None else "",
        "crop_stage_end": str(crop_stage) if crop_stage not in (None, "") else "",
        "zadok_stage_end": zadok_stage,
        "lai_end": lai_end,
        **lifecycle,
    }
    diagnosis["selected_report_rowid"] = int(control["__rowid__"]) if control is not None else None
    diagnosis["soil_output_timing"] = timing
    diagnosis["lifecycle"] = lifecycle
    return values, diagnosis
