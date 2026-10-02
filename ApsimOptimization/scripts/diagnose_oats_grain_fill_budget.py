#!/usr/bin/env python3
"""Compare daily oat grain-fill budgets using existing APSIM databases only."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sys
import tarfile
from pathlib import Path
from statistics import fmean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_oats_yield_components import source_grain_sizes
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint
from src.multifield_optimizer import ROOT, load_config
from src.oats_physiology import collect_oats_diagnostics


DEFAULT_HISTORY_RUN = (
    ROOT / "outputs" / "multifield" / "OATS_SOURCE_FACTORS"
    / "20260928_112031" / "current_yakov"
)
TRANSITION_ROOT = ROOT / "outputs" / "multifield" / "OATS_WHEAT_RESIDUE"
CASES = (
    ("historical_near_match", 2021, "Field6"),
    ("historical_near_match", 2022, "Field10"),
    ("validation_transition", 2023, "Field2"),
    ("validation_transition", 2023, "Field7"),
)
DAILY_COLUMNS = (
    "date", "OatsZadok", "OatsAGWt", "OatsAGN", "OatsYield",
    "OatsLeafPhotosynthesis", "OatsLeafFn", "OatsLeafFw",
    "OatsNitrogenDemandGm2", "OatsNitrogenSupplyGm2",
    "OatsGrainDMDemand", "OatsGrainPotentialDMAllocation",
    "OatsGrainDMAllocation", "OatsGrainNDemand", "OatsGrainNAllocation",
    "OatsStemLiveWt", "OatsLeafLiveWt", "OatsGrainNumber", "OatsGrainSize",
    "MineralN",
)


def latest_transition_run() -> Path:
    candidates = [
        path / "documented_2022_harvest_export_new_residue"
        for path in TRANSITION_ROOT.glob("*") if path.is_dir()
        and (path / "documented_2022_harvest_export_new_residue" / "candidate.db").exists()
    ]
    if not candidates:
        raise FileNotFoundError("no completed OATS_WHEAT_RESIDUE transition run found")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def total(rows: list[dict], key: str) -> float:
    return sum(value for row in rows if (value := number(row.get(key))) is not None)


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def mean(rows: list[dict], key: str) -> float | None:
    values = [value for row in rows if (value := number(row.get(key))) is not None]
    return fmean(values) if values else None


def endpoint_change(rows: list[dict], key: str) -> float | None:
    values = [value for row in rows if (value := number(row.get(key))) is not None]
    return values[-1] - values[0] if len(values) >= 2 else None


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_run(run: Path, observations: dict, cases: list[tuple[int, str]], scenario: str):
    database = run / "candidate.db"
    inventory_path = run / "model_inventory.json"
    if not database.exists() or not inventory_path.exists():
        raise FileNotFoundError(f"incomplete saved APSIM run: {run}")
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    daily, summaries, schemas = [], [], {}
    for year in sorted({year for year, _ in cases}):
        fields = {field for case_year, field in cases if case_year == year}
        records = [
            row for row in records_for_year(observations, year)
            if row["field"] in fields
        ]
        year_daily, year_summaries, schema = collect_oats_diagnostics(
            database, inventory, records, year=year, scenario=scenario
        )
        daily.extend(year_daily)
        summaries.extend(year_summaries)
        schemas[str(year)] = schema
    return daily, summaries, schemas


def build_case_budget(scope: str, year: int, field: str, daily: list[dict],
                      summaries: list[dict], grain_source: dict) -> tuple[dict, list[dict]]:
    active = [
        row for row in daily
        if int(row.get("year", year)) == year and row.get("field") == field
    ]
    # Demand-positive days follow the model's own definition of grain filling.
    fill = [
        row for row in active
        if (number(row.get("OatsGrainDMDemand")) or 0) > 0
        or (number(row.get("OatsGrainDMAllocation")) or 0) > 0
    ]
    if not fill:
        raise ValueError(f"{field}/{year}: no demand-positive grain-fill rows")
    summary = next(
        row for row in summaries
        if int(row["year"]) == year and row["field"] == field
    )
    dm_demand = total(fill, "OatsGrainDMDemand")
    dm_potential = total(fill, "OatsGrainPotentialDMAllocation")
    dm_actual = total(fill, "OatsGrainDMAllocation")
    n_demand = total(fill, "OatsGrainNDemand")
    n_actual = total(fill, "OatsGrainNAllocation")
    plant_n_demand = total(fill, "OatsNitrogenDemandGm2")
    plant_n_supply = total(fill, "OatsNitrogenSupplyGm2")
    observed_tkw = grain_source[(year, field)]["observed_thousand_grain_mass_g"]
    model_size = number(summary.get("final_grain_size_g"))
    flags = []
    if (ratio(dm_actual, dm_potential) or 0) < 0.8:
        flags.append("actual_grain_dm_below_potential_allocation")
    if (ratio(n_actual, n_demand) or 0) < 0.8:
        flags.append("grain_n_allocation_below_demand")
    if (mean(fill, "OatsLeafFn") or 0) < 0.8:
        flags.append("leaf_n_stress_during_fill")
    if (mean(fill, "OatsLeafFw") or 0) < 0.8:
        flags.append("leaf_water_stress_during_fill")
    if observed_tkw and model_size is not None and model_size * 1000 < 0.6 * observed_tkw:
        flags.append("grain_mass_far_below_observation")
    budget = {
        "scope": scope, "year": year, "field": field,
        "grain_fill_start": fill[0]["date"], "grain_fill_end": fill[-1]["date"],
        "grain_fill_days": len(fill),
        "observed_yield_t_ha": summary.get("observed_yield_t_ha"),
        "simulated_yield_t_ha": summary.get("simulated_yield_t_ha"),
        "observed_thousand_grain_mass_g": observed_tkw,
        "simulated_thousand_grain_mass_g": model_size * 1000 if model_size is not None else None,
        "final_grain_number_m2": summary.get("final_grain_number_m2"),
        "grain_dm_demand_sum_g_m2": dm_demand,
        "grain_dm_potential_allocation_sum_g_m2": dm_potential,
        "grain_dm_actual_allocation_sum_g_m2": dm_actual,
        "grain_dm_actual_to_demand_ratio": ratio(dm_actual, dm_demand),
        "grain_dm_actual_to_potential_ratio": ratio(dm_actual, dm_potential),
        "grain_n_demand_sum_g_m2": n_demand,
        "grain_n_actual_allocation_sum_g_m2": n_actual,
        "grain_n_actual_to_demand_ratio": ratio(n_actual, n_demand),
        "plant_n_supply_to_demand_ratio": ratio(plant_n_supply, plant_n_demand),
        "leaf_photosynthesis_sum_g_m2": total(fill, "OatsLeafPhotosynthesis"),
        "mean_leaf_fn": mean(fill, "OatsLeafFn"),
        "mean_leaf_fw": mean(fill, "OatsLeafFw"),
        "stem_live_wt_change_g_m2": endpoint_change(fill, "OatsStemLiveWt"),
        "leaf_live_wt_change_g_m2": endpoint_change(fill, "OatsLeafLiveWt"),
        "aboveground_wt_change_g_m2": endpoint_change(fill, "OatsAGWt"),
        "aboveground_n_change_g_m2": endpoint_change(fill, "OatsAGN"),
        "mineral_n_mean_native": mean(fill, "MineralN"),
        "diagnostic_flags": ";".join(flags),
    }
    selected = [{
        "scope": scope, "year": year, "field": field,
        **{key: row.get(key) for key in DAILY_COLUMNS},
    } for row in fill]
    return budget, selected


def group_summary(rows: list[dict], scope: str) -> dict:
    selected = [row for row in rows if row["scope"] == scope]
    keys = (
        "grain_dm_actual_to_demand_ratio", "grain_dm_actual_to_potential_ratio",
        "grain_n_actual_to_demand_ratio", "plant_n_supply_to_demand_ratio",
        "mean_leaf_fn", "mean_leaf_fw", "stem_live_wt_change_g_m2",
    )
    result = {"n": len(selected)}
    for key in keys:
        values = [float(row[key]) for row in selected if row[key] is not None]
        result[f"mean_{key}"] = fmean(values) if values else None
    return result


def archive_output(output: Path) -> Path:
    archive = ROOT / f"oats_grain_fill_budget_{output.name}.tar.gz"
    files = sorted(file for file in output.iterdir() if file.is_file())
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [{
            "path": file.name, "size": file.stat().st_size,
            "sha256": fingerprint(file),
        } for file in files],
    }
    with tarfile.open(archive, "w:gz") as stream:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        stream.addfile(info, io.BytesIO(payload))
        for file in files:
            stream.add(file, arcname=file.name, recursive=False)
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-run", type=Path, default=DEFAULT_HISTORY_RUN)
    parser.add_argument("--transition-run", type=Path)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "configs" / "multifield_historical_replay.yaml",
    )
    args = parser.parse_args()
    transition_run = args.transition_run or latest_transition_run()
    config = load_config(args.config)
    observations = load_observations(config["historical_observations"])
    grain_source = source_grain_sizes(observations)

    historical_cases = [(year, field) for scope, year, field in CASES
                        if scope == "historical_near_match"]
    validation_cases = [(year, field) for scope, year, field in CASES
                        if scope == "validation_transition"]
    history_daily, history_summaries, history_schema = read_run(
        args.history_run, observations, historical_cases, "historical_near_match"
    )
    validation_daily, validation_summaries, validation_schema = read_run(
        transition_run, observations, validation_cases, "validation_transition"
    )

    budgets, daily_rows = [], []
    for scope, year, field in CASES:
        source_daily, source_summaries = (
            (history_daily, history_summaries)
            if scope == "historical_near_match"
            else (validation_daily, validation_summaries)
        )
        budget, selected = build_case_budget(
            scope, year, field, source_daily, source_summaries, grain_source
        )
        budgets.append(budget)
        daily_rows.extend(selected)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = ROOT / "outputs" / "multifield" / "OATS_GRAIN_FILL_BUDGET" / stamp
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "case_budget.csv", budgets)
    write_csv(output / "daily_grain_fill.csv", daily_rows)
    (output / "report_schema.json").write_text(json.dumps({
        "historical": history_schema, "validation": validation_schema,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    groups = {
        scope: group_summary(budgets, scope)
        for scope in ("historical_near_match", "validation_transition")
    }
    (output / "summary.json").write_text(json.dumps({
        "experiment": "oats_daily_grain_fill_budget_from_saved_databases",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "apsim_rerun": False, "base_model_changed": False,
        "history_run": str(args.history_run),
        "transition_run": str(transition_run),
        "history_database_sha256": fingerprint(args.history_run / "candidate.db"),
        "transition_database_sha256": fingerprint(transition_run / "candidate.db"),
        "groups": groups,
        "interpretation_limits": [
            "Stem and leaf weight changes combine growth, senescence and remobilisation; they are mechanism indicators, not direct flux measurements.",
            "Observed grain number is not available and is not used as an independent measurement here.",
            "The validation transition assumes complete export of fresh 2022 wheat harvest residue.",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = archive_output(output)
    print(json.dumps({
        "output": str(output), "archive": str(archive),
        "archive_sha256": fingerprint(archive), "groups": groups,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
