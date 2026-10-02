from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


SOIL_COLUMNS = ("N_NO3", "P_2O5", "SOC")
EXTERNAL_REQUIRED_COLUMNS = (
    "technology_id", "scenario_id", "N_NO3", "P_2O5", "SOC", "SOC_0",
    "control_date", "soil_layer", "units", "source_note",
)


def load_external_soil_data(limits: dict[str, Any], root: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], str]:
    if limits.get("source", "report") != "external_file":
        return {}, ""
    configured = limits.get("external_file")
    if not configured:
        return {}, "soil_constraints.external_file is not configured"
    path = Path(configured)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        return {}, f"external soil constraints file not found: {path}"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        missing = [name for name in EXTERNAL_REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            return {}, f"external soil constraints file misses columns: {missing}"
        rows = {(row["technology_id"], row["scenario_id"]): row for row in reader}
    return rows, ""


def soil_values_for_run(
    report_values: dict[str, Any],
    limits: dict[str, Any],
    run_row: dict[str, Any],
    external_rows: dict[tuple[str, str], dict[str, Any]] | None = None,
    external_error: str = "",
) -> dict[str, Any]:
    source = limits.get("source", "report")
    if source == "disabled":
        return {"_soil_source_error": "soil constraints source is disabled"}
    if source == "report":
        return report_values
    if source != "external_file":
        return {"_soil_source_error": f"unsupported soil constraints source: {source}"}
    if external_error:
        return {"_soil_source_error": external_error}
    key = (str(run_row["technology_id"]), str(run_row["scenario_id"]))
    external = (external_rows or {}).get(key)
    if external is None:
        return {"_soil_source_error": f"external soil row not found for technology_id={key[0]}, scenario_id={key[1]}"}
    values: dict[str, Any] = {}
    for name in ("N_NO3", "P_2O5", "SOC", "SOC_0"):
        raw = external.get(name)
        try:
            values[name] = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            values[name] = None
    return values


def evaluate(values: dict[str, Any], limits: dict[str, Any]) -> tuple[str, bool | None, str]:
    if limits.get("source", "report") == "disabled":
        feasible = False if limits.get("strict", False) else None
        return "soil_constraints_unchecked", feasible, "soil constraints source is disabled"
    source_error = values.get("_soil_source_error")
    if source_error:
        feasible = False if limits.get("strict", False) else None
        return "soil_constraints_unchecked", feasible, str(source_error)

    configured_required = tuple(limits.get("required_columns", []))
    required = configured_required or SOIL_COLUMNS
    missing = [name for name in required if values.get(name) is None]
    soc_0 = values.get("SOC_0") if "SOC_0" in required else values.get("SOC_0", limits.get("SOC_0"))
    if missing or soc_0 is None:
        details = [*missing]
        if soc_0 is None and "SOC_0" not in missing:
            details.append("SOC_0 threshold")
        reason = "missing exact soil data: " + ", ".join(details)
        if limits.get("strict", False):
            return "soil_constraints_unchecked", False, reason
        return "soil_constraints_unchecked", None, reason
    failures = []
    if float(values["N_NO3"]) < float(limits["N_NO3_min"]):
        failures.append("N_NO3")
    if float(values["P_2O5"]) < float(limits["P_2O5_min"]):
        failures.append("P_2O5")
    if float(values["SOC"]) < float(soc_0):
        failures.append("SOC")
    if failures:
        return "checked_failed", False, "Below threshold: " + ", ".join(failures)
    return "checked_passed", True, ""
