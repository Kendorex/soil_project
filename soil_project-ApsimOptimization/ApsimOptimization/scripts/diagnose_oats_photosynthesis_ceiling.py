#!/usr/bin/env python3
"""Test whether removing the oat photosynthesis N penalty can close the grain gap."""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_oats_yield_components import source_grain_sizes
from scripts.diagnose_oats_grain_fill_budget import build_case_budget
from src.cultivar_yield_calibration import upsert_named_cultivar_commands
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint, inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_phenology import phenology_candidate_metrics
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports
from src.oats_residue_sensitivity import (
    defer_wheat_auto_harvest_for_field_years,
    patch_export_new_residue_at_cereal_to_oats_harvest,
)


YEARS = tuple(range(2019, 2024))
TRANSITION_FIELDS = ("Field2", "Field7")
FN_RESPONSE = "[Leaf].Photosynthesis.FN.XYPairs.Y"
PROFILES = (
    {
        "name": "transition_control",
        "values": {},
        "meaning": "documented 2022 wheat harvest and complete fresh-residue export",
    },
    {
        "name": "photosynthesis_n_unconstrained_ceiling",
        "values": {FN_RESPONSE: "1,1,1"},
        "meaning": "diagnostic upper bound with no N penalty on photosynthesis",
    },
)
CASES = (
    (2021, "Field6", "historical_near_match"),
    (2022, "Field10", "historical_near_match"),
    (2023, "Field2", "validation_transition"),
    (2023, "Field7", "validation_transition"),
)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def make_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "candidate_metrics.csv", "case_predictions.csv",
        "grain_fill_budget.csv", "daily_grain_fill.csv", "profile_summary.json",
        "oats_history.csv", "report_schema.json", "config_snapshot.json",
        "model_inventory.json", "preparation.json", "apsim.log",
    }
    files = sorted(
        file for file in root.rglob("*")
        if file.is_file() and file.name in allowed
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


def run_profile(config: dict, observations: dict, year_records: dict,
                oat_fields: tuple[str, ...], profile: dict, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    model = read_json(config["model"])
    lifecycle_changes = defer_wheat_auto_harvest_for_field_years(
        model, {field: {2022} for field in TRANSITION_FIELDS}
    )
    residue_changes = patch_export_new_residue_at_cereal_to_oats_harvest(
        model, TRANSITION_FIELDS, {field: {2022} for field in TRANSITION_FIELDS}
    )
    cultivar_changes = (
        upsert_named_cultivar_commands(
            model, "Yakov", profile["values"], expected_count=10
        ) if profile["values"] else []
    )
    report_changes = patch_oats_diagnostic_reports(model, oat_fields)
    source = output / "source_candidate.apsimx"
    write_json(source, model)

    case = copy.deepcopy(config)
    case["model"] = str(source.resolve())
    case["model_provenance"] = None
    case["historical_replay"] = dict(config["historical_replay"], enabled=True)
    case["scenario"] = {
        "kind": "diagnostic_ceiling",
        "name": profile["name"],
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)

    daily_rows, summaries, schemas = [], [], {}
    for year in YEARS:
        daily, summary, schema = collect_oats_diagnostics(
            database, meta, year_records[year], year=year, scenario=profile["name"]
        )
        daily_rows.extend(daily)
        summaries.extend(summary)
        schemas[str(year)] = schema
    metrics = phenology_candidate_metrics(summaries)
    write_csv(output / "oats_history.csv", summaries)
    write_json(output / "report_schema.json", schemas)
    result = {
        "name": profile["name"],
        "meaning": profile["meaning"],
        "values": profile["values"],
        "metrics": metrics,
        "lifecycle_changes": lifecycle_changes,
        "residue_rotation_manager_paths": residue_changes,
        "cultivar_changes": cultivar_changes,
        "report_changes": report_changes,
    }
    write_json(output / "profile_summary.json", result)
    return result, daily_rows, summaries


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
    grain_source = source_grain_sizes(observations)
    year_records = {year: records_for_year(observations, year) for year in YEARS}
    oat_fields = tuple(dict.fromkeys(
        row["field"] for year in YEARS for row in year_records[year]
        if "Oats" in row.get("yield_components", [])
    ))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    root = ROOT / "outputs" / "multifield" / "OATS_PHOTOSYNTHESIS_CEILING" / stamp
    root.mkdir(parents=True, exist_ok=False)

    results, prediction_rows, metric_rows = [], [], []
    budget_rows, selected_daily = [], []
    for index, profile in enumerate(PROFILES, 1):
        print(f"[{index}/{len(PROFILES)}] {profile['name']}", flush=True)
        result, daily, summaries = run_profile(
            config, observations, year_records, oat_fields, profile,
            root / profile["name"],
        )
        results.append(result)
        prediction_rows.extend({"candidate": profile["name"], **row} for row in summaries)
        for scope, metrics in result["metrics"].items():
            metric_rows.append({
                "candidate": profile["name"], "scope": scope,
                "yield_mae_t_ha": metrics["yield_mae_t_ha"],
                "yield_rmse_t_ha": metrics["yield_rmse_t_ha"],
                "yield_bias_t_ha": metrics["yield_bias_t_ha"],
                "tillering_mae_days": metrics["tillering"]["mae_days"],
                "heading_mae_days": metrics["heading"]["mae_days"],
            })
        for year, field, reference_scope in CASES:
            budget, chosen = build_case_budget(
                profile["name"], year, field, daily, summaries, grain_source
            )
            budget["reference_scope"] = reference_scope
            budget_rows.append(budget)
            selected_daily.extend(chosen)
        dev = result["metrics"]["development_yakov_2020_2022"]
        val = result["metrics"]["validation_yakov_2023"]
        print(
            f"  development RMSE={dev['yield_rmse_t_ha']:.3f}; "
            f"2023 RMSE={val['yield_rmse_t_ha']:.3f}", flush=True,
        )

    write_csv(root / "candidate_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", prediction_rows)
    write_csv(root / "grain_fill_budget.csv", budget_rows)
    write_csv(root / "daily_grain_fill.csv", selected_daily)
    write_json(root / "summary.json", {
        "experiment": "oats_photosynthesis_n_stress_upper_bound",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "profiles": PROFILES,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "interpretation_limits": [
            "The unity FN response is an intentionally unrealistic upper bound, not a calibration candidate.",
            "The response is disabled for the full crop cycle, so development and validation years must be compared together.",
            "The 2023 transition assumes complete export of newly created 2022 wheat harvest residue.",
            "Observed yield moisture basis and observed grain number remain unavailable.",
        ],
        "results": results,
    })
    archive = ROOT / f"oats_photosynthesis_ceiling_{stamp}.tar.gz"
    make_archive(archive, root)
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    print(f"ARCHIVE SHA256: {fingerprint(archive)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
