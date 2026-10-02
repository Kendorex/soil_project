#!/usr/bin/env python3
"""Cross-year check of the extreme cereal-residue removal mechanism for oats."""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import math
import sqlite3
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations, records_for_year
from src.multifield_model import child, fingerprint, inventory, load_model, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports
from src.oats_residue_sensitivity import (
    cereal_to_oats_targets, patch_export_new_residue_at_cereal_to_oats_harvest,
    patch_remove_all_residue_at_cereal_to_oats_harvest,
)


YEARS = tuple(range(2019, 2024))
CANDIDATES = (
    "current_residue_retention",
    "export_new_cereal_harvest_residue_at_transition",
    "remove_all_residue_at_cereal_to_oats_transition",
)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def metrics(rows: list[dict], years: set[int], yakov_only: bool = True) -> dict:
    selected = [row for row in rows if int(row["year"]) in years
                and (not yakov_only or str(row.get("observed_cultivar", "")).casefold() in {"яков", "yakov"})]
    errors = [float(row["simulated_yield_t_ha"]) - float(row["observed_yield_t_ha"])
              for row in selected]
    return {
        "cases": len(errors),
        "mae_t_ha": sum(abs(value) for value in errors) / len(errors),
        "rmse_t_ha": math.sqrt(sum(value * value for value in errors) / len(errors)),
        "bias_t_ha": sum(errors) / len(errors),
    }


def read_events(database: Path, meta: dict, targets: list[dict], candidate: str) -> list[dict]:
    output = []
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        simulation_id = connection.execute(
            'SELECT ID FROM "_Simulations" WHERE Name=?', (meta["simulation"],)
        ).fetchone()[0]
        for target in targets:
            field = target["field"]
            table = f"Report{field.removeprefix('Field')}"
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            required = {"CerealResidueRemovalCount", "CerealResidueRemovedMassKgHa",
                        "CerealResidueRemovedNKgHa"}
            if not required <= columns:
                values = {"triggered": 0, "removed_mass_kg_ha": 0, "removed_n_kg_ha": 0,
                          "status": "control_without_removal_patch"}
            else:
                row = connection.execute(
                    f'''SELECT CerealResidueRemovalCount AS RemovalCount,
                        CerealResidueRemovedMassKgHa AS RemovedMass,
                        CerealResidueRemovedNKgHa AS RemovedN
                        FROM "{table}" WHERE SimulationID=?
                        AND substr("Clock.Today",1,10)=?''',
                    (simulation_id, target["cereal_harvest_date"]),
                ).fetchone()
                if row is None:
                    raise ValueError(f"{field}: missing report row on removal date")
                values = {
                    "triggered": float(row["RemovalCount"] or 0),
                    "removed_mass_kg_ha": float(row["RemovedMass"] or 0),
                    "removed_n_kg_ha": float(row["RemovedN"] or 0),
                    "status": "diagnostic_complete_removal",
                }
            output.append({"candidate": candidate, **target, **values})
    return output


def create_archive(path: Path, root: Path) -> None:
    allowed = {"summary.json", "transition_targets.json", "candidate_metrics.csv",
               "case_predictions.csv", "residue_events.csv", "candidate_summary.json",
               "oats_history.csv", "report_schema.json", "config_snapshot.json",
               "model_inventory.json", "preparation.json", "apsim.log", "changes.json"}
    files = sorted(file for file in root.rglob("*")
                   if file.is_file() and file.name in allowed)
    manifest = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "files": [{
        "path": file.relative_to(root).as_posix(), "size": file.stat().st_size,
        "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
    } for file in files]}
    with tarfile.open(path, "w:gz") as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for file in files:
            archive.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)


def run_candidate(config: dict, observations: dict, oat_fields: tuple[str, ...],
                  targets: list[dict], candidate: str, output: Path) -> tuple[dict, list[dict]]:
    output.mkdir(parents=True, exist_ok=False)
    data = read_json(config["model"])
    report_changes = patch_oats_diagnostic_reports(data, oat_fields)
    manager_changes = []
    if candidate == "remove_all_residue_at_cereal_to_oats_transition":
        manager_changes = patch_remove_all_residue_at_cereal_to_oats_harvest(data, oat_fields)
    elif candidate == "export_new_cereal_harvest_residue_at_transition":
        manager_changes = patch_export_new_residue_at_cereal_to_oats_harvest(
            data, oat_fields
        )
    source = output / "source_candidate.apsimx"
    write_json(source, data)
    case = copy.deepcopy(config)
    case["model"] = str(source.resolve())
    case["model_provenance"] = None
    case["scenario"] = {"kind": "diagnostic", "name": candidate,
                        "recommendation_status": "prohibited"}
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
            database, meta, records, year=year, scenario=candidate
        )
        rows.extend(summaries)
        schemas[str(year)] = schema
    events = read_events(database, meta, targets, candidate)
    result = {
        "candidate": candidate,
        "development_yakov_2020_2022": metrics(rows, {2020, 2021, 2022}),
        "validation_yakov_2023": metrics(rows, {2023}),
        "all_oats_context": metrics(rows, set(YEARS), yakov_only=False),
        "events": events,
    }
    write_csv(output / "oats_history.csv", rows)
    write_csv(output / "residue_events.csv", events)
    write_json(output / "report_schema.json", schemas)
    write_json(output / "changes.json", {
        "candidate": candidate, "manager_paths_changed": manager_changes,
        "report_changes": report_changes, "base_model_changed": False,
    })
    write_json(output / "candidate_summary.json", result)
    return result, rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--config", type=Path,
                        default=ROOT / "configs" / "multifield_historical_replay.yaml")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    observations = load_observations(config["historical_observations"])
    model, simulation, _, _ = load_model(config)
    start_year = int(child(simulation, "Clock")["Start"][:4])
    targets = cereal_to_oats_targets(model, start_year)
    if not targets:
        raise ValueError("No cereal-to-oats transitions found")
    oat_fields = tuple(dict.fromkeys(
        row["field"] for year in YEARS for row in records_for_year(observations, year)
        if "Oats" in row.get("yield_components", [])
    ))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "outputs" / "multifield" / "OATS_RESIDUE_HISTORY" / stamp
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "transition_targets.json", targets)
    results, prediction_rows, event_rows = [], [], []
    for index, candidate in enumerate(CANDIDATES, 1):
        print(f"[{index}/{len(CANDIDATES)}] {candidate}", flush=True)
        result, rows = run_candidate(
            config, observations, oat_fields, targets, candidate, root / candidate
        )
        results.append(result)
        prediction_rows.extend({"candidate": candidate, **row} for row in rows)
        event_rows.extend(result["events"])
        print(
            f"  development RMSE={result['development_yakov_2020_2022']['rmse_t_ha']:.3f}; "
            f"validation RMSE={result['validation_yakov_2023']['rmse_t_ha']:.3f}", flush=True,
        )
    metric_rows = []
    for result in results:
        for scope in ("development_yakov_2020_2022", "validation_yakov_2023", "all_oats_context"):
            metric_rows.append({"candidate": result["candidate"], "scope": scope,
                                **result[scope]})
    write_csv(root / "candidate_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", prediction_rows)
    write_csv(root / "residue_events.csv", event_rows)

    target_ids = {(row["oat_year"], row["field"]) for row in targets}
    control = {(int(row["year"]), row["field"]): row for row in prediction_rows
               if row["candidate"] == CANDIDATES[0]}
    unexpected = []
    for candidate in CANDIDATES[1:]:
        changed = {(int(row["year"]), row["field"]): row for row in prediction_rows
                   if row["candidate"] == candidate}
        for key, row in changed.items():
            delta = (
                float(row["simulated_yield_t_ha"])
                - float(control[key]["simulated_yield_t_ha"])
            )
            if key not in target_ids and abs(delta) > 1e-9:
                unexpected.append({
                    "candidate": candidate,
                    "year": key[0], "field": key[1], "yield_delta_t_ha": delta,
                })
    if unexpected:
        raise ValueError(f"Residue screen changed non-target oat cases: {unexpected}")

    base = results[0]
    write_json(root / "summary.json", {
        "experiment": "cross_year_cereal_harvest_residue_export_before_oats",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only", "base_model_changed": False,
        "targets": targets, "candidates": results, "non_target_cases_unchanged": True,
        "rmse_changes_t_ha": {
            result["candidate"]: {
                scope: result[scope]["rmse_t_ha"] - base[scope]["rmse_t_ha"]
                for scope in (
                    "development_yakov_2020_2022", "validation_yakov_2023",
                    "all_oats_context",
                )
            }
            for result in results[1:]
        },
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "interpretation_limits": [
            "Export of all newly added cereal harvest residue is an extreme straw-export endpoint.",
            "Complete surface-pool removal is a separate upper-bound endpoint.",
            "The source data contain 2023 residue mass but do not state sampling dates or field-year management.",
            "Only modeled Wheat-to-Oats transitions are changed; other oat cases are controls.",
            "No optimization bound or working APSIMX parameter is changed.",
        ],
    })
    archive = ROOT / f"oats_residue_history_{stamp}.tar.gz"
    create_archive(archive, root)
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
