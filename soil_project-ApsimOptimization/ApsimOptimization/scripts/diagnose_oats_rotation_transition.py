#!/usr/bin/env python3
"""Test whether old perennial cohorts incorrectly survive into a new oat sowing."""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations, records_for_year
from src.multifield_model import (child, fingerprint, inventory, load_model,
                                  prepare, read_json, write_json)
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports
from src.oats_rotation_transition import (find_oat_after_perennial_targets,
                                           patch_end_old_perennials_after_final_cut,
                                           patch_oat_after_perennial_restart)


YEARS = tuple(range(2019, 2024))
CANDIDATES = (
    "current_transition",
    "restart_old_perennials_before_new_oats",
    "end_old_perennials_after_existing_final_cut",
)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def metrics(rows: list[dict], years: set[int], yakov_only: bool = True) -> dict:
    selected = [
        row for row in rows
        if int(row["year"]) in years
        and (not yakov_only or str(row.get("observed_cultivar", "")).casefold() in {"яков", "yakov"})
        and row.get("simulated_yield_t_ha") is not None
    ]
    errors = [float(row["simulated_yield_t_ha"]) - float(row["observed_yield_t_ha"])
              for row in selected]
    return {
        "cases": len(errors),
        "mae_t_ha": sum(abs(value) for value in errors) / len(errors) if errors else None,
        "rmse_t_ha": math.sqrt(sum(value * value for value in errors) / len(errors)) if errors else None,
        "bias_t_ha": sum(errors) / len(errors) if errors else None,
    }


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "candidate_metrics.csv", "case_predictions.csv",
        "transition_targets.json", "candidate_summary.json", "oats_history.csv",
        "report_schema.json", "config_snapshot.json", "model_inventory.json",
        "preparation.json", "apsim.log", "code_changes.json",
    }
    files = sorted(
        file for file in root.rglob("*") if file.is_file() and file.name in allowed
    )
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [{
            "path": file.relative_to(root).as_posix(),
            "size": file.stat().st_size,
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        } for file in files],
    }
    with tarfile.open(path, "w:gz") as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for file in files:
            archive.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)


def run_candidate(config: dict, observations: dict, oat_fields: tuple[str, ...],
                  name: str, output: Path) -> tuple[dict, list[dict]]:
    output.mkdir(parents=True, exist_ok=False)
    data = read_json(config["model"])
    report_changes = patch_oats_diagnostic_reports(data, oat_fields)
    if name == "restart_old_perennials_before_new_oats":
        manager_changes = patch_oat_after_perennial_restart(data)
    elif name == "end_old_perennials_after_existing_final_cut":
        manager_changes = patch_end_old_perennials_after_final_cut(data)
    else:
        manager_changes = []
    source = output / "source_candidate.apsimx"
    write_json(source, data)
    case = copy.deepcopy(config)
    case["model"] = str(source.resolve())
    case["model_provenance"] = None
    case["scenario"] = {
        "kind": "diagnostic", "name": name, "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)

    rows, schemas = [], {}
    for year in YEARS:
        records = records_for_year(observations, year)
        if not any("Oats" in row.get("yield_components", []) for row in records):
            continue
        _, summaries, schema = collect_oats_diagnostics(
            database, meta, records, year=year, scenario=name
        )
        rows.extend(summaries)
        schemas[str(year)] = schema
    write_csv(output / "oats_history.csv", rows)
    write_json(output / "report_schema.json", schemas)
    write_json(output / "code_changes.json", {
        "candidate": name,
        "manager_paths_changed": manager_changes,
        "report_changes": report_changes,
        "base_model_changed": False,
    })
    result = {
        "candidate": name,
        "development_yakov_2020_2022": metrics(rows, {2020, 2021, 2022}),
        "validation_yakov_2023": metrics(rows, {2023}),
        "all_oats_context": metrics(rows, set(YEARS), yakov_only=False),
    }
    write_json(output / "candidate_summary.json", result)
    return result, rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "configs" / "multifield_historical_replay.yaml",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    observations = load_observations(config["historical_observations"])
    model, simulation, _, _ = load_model(config)
    start_year = int(child(simulation, "Clock")["Start"][:4])
    targets = find_oat_after_perennial_targets(model, start_year)
    if not targets:
        raise ValueError("No oat-after-perennial transition was found")
    oat_fields = tuple(dict.fromkeys(
        row["field"] for year in YEARS for row in records_for_year(observations, year)
        if "Oats" in row.get("yield_components", [])
    ))

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "outputs" / "multifield" / "OATS_ROTATION_TRANSITION" / stamp
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "transition_targets.json", targets)
    candidate_results, case_rows = [], []
    for index, name in enumerate(CANDIDATES, 1):
        print(f"[{index}/{len(CANDIDATES)}] {name}", flush=True)
        result, rows = run_candidate(config, observations, oat_fields, name, root / name)
        candidate_results.append(result)
        case_rows.extend({"candidate": name, **row} for row in rows)
        development = result["development_yakov_2020_2022"]
        validation = result["validation_yakov_2023"]
        print(
            f"  development RMSE={development['rmse_t_ha']:.3f}; "
            f"validation RMSE={validation['rmse_t_ha']:.3f}", flush=True,
        )

    control = candidate_results[0]
    metric_rows = []
    for result in candidate_results:
        for scope in ("development_yakov_2020_2022", "validation_yakov_2023", "all_oats_context"):
            metric_rows.append({"candidate": result["candidate"], "scope": scope,
                                **result[scope]})
    write_csv(root / "candidate_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", case_rows)
    target_ids = {(int(row["year"]), row["field"]) for row in targets}
    by_candidate = {
        name: {(int(row["year"]), row["field"]): row for row in case_rows
               if row["candidate"] == name}
        for name in CANDIDATES
    }
    control_rows = by_candidate["current_transition"]
    non_target_changes = []
    for name in CANDIDATES[1:]:
        for key, row in by_candidate[name].items():
            if key in target_ids:
                continue
            delta = (float(row["simulated_yield_t_ha"])
                     - float(control_rows[key]["simulated_yield_t_ha"]))
            if abs(delta) > 1e-9:
                non_target_changes.append({
                    "candidate": name, "year": key[0], "field": key[1],
                    "yield_delta_t_ha": delta,
                })
    if non_target_changes:
        raise ValueError(f"Rotation diagnostic changed non-target cases: {non_target_changes}")
    write_json(root / "summary.json", {
        "experiment": "oats_perennial_to_new_undersowing_transition",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "transition_targets": targets,
        "candidates": candidate_results,
        "development_rmse_changes_t_ha": {
            result["candidate"]: (
                result["development_yakov_2020_2022"]["rmse_t_ha"]
                - control["development_yakov_2020_2022"]["rmse_t_ha"]
            ) for result in candidate_results[1:]
        },
        "non_target_cases_unchanged": True,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "interpretation_limits": [
            "The change repairs only the lifecycle of an already documented rotation transition.",
            "No cultivar, soil, fertilizer, nutrient target, or optimization bound is changed.",
            "The expected affected case is Field1/2021; 2023 is an unchanged validation check.",
            "The final-cut candidate uses the existing 30-Sep 2020 rotation cut and invents no date.",
            "The base APSIMX model is not modified by this diagnostic.",
        ],
    })
    archive = ROOT / f"oats_rotation_transition_{stamp}.tar.gz"
    create_archive(archive, root)
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
