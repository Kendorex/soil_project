#!/usr/bin/env python3
"""Screen APSIM-reference oat N uptake coefficients with held-out 2023."""
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

from src.cultivar_yield_calibration import upsert_named_cultivar_commands
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint, inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_n_balance import build_oats_n_balance
from src.oats_phenology import phenology_candidate_metrics
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports
from src.oats_source import source_candidate_comparison


YEARS = tuple(range(2019, 2024))
KNO3 = "[Root].KNO3.FixedValue"
KNH4 = "[Root].KNH4.FixedValue"

# Current values are shared by installed Oats, Wheat, Barley and WinterCereal.
# Alternative values are copied verbatim from installed Maize.json.  These are
# discrete mechanism tests, not inferred oat parameters or calibration bounds.
PROFILES = (
    {"name": "current_oats", "source": "installed Oats/Wheat/Barley/WinterCereal",
     "values": {}},
    {"name": "maize_kno3_only", "source": "installed Maize KNO3",
     "values": {KNO3: 0.03}},
    {"name": "maize_knh4_only", "source": "installed Maize KNH4",
     "values": {KNH4: 0.02}},
    {"name": "maize_coefficient_pair", "source": "installed Maize KNO3/KNH4",
     "values": {KNO3: 0.03, KNH4: 0.02}},
)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "candidate_metrics.csv", "case_predictions.csv",
        "n_supply_cases.csv", "profile_summary.json", "oats_history.csv",
        "n_balance_history.csv", "report_schema.json", "config_snapshot.json",
        "model_inventory.json", "preparation.json", "apsim.log",
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
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for file in files:
            archive.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)


def run_profile(config, observations, year_records, oat_fields, profile, output):
    output.mkdir(parents=True, exist_ok=False)
    model = read_json(config["model"])
    changes = upsert_named_cultivar_commands(
        model, "Yakov", profile["values"], expected_count=10
    ) if profile["values"] else []
    report_changes = patch_oats_diagnostic_reports(model, oat_fields)
    source_candidate = output / "source_candidate.apsimx"
    write_json(source_candidate, model)
    case = copy.deepcopy(config)
    case["model"] = str(source_candidate.resolve())
    case["model_provenance"] = None
    case["scenario"] = {
        "kind": "diagnostic", "name": profile["name"],
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)

    summaries_all, balances_all, schemas = [], [], {}
    for year in YEARS:
        daily, summaries, schema = collect_oats_diagnostics(
            database, meta, year_records[year], year=year,
            scenario=profile["name"],
        )
        summaries_all.extend(summaries)
        balances_all.extend({"year": year, **row}
                            for row in build_oats_n_balance(daily, summaries))
        schemas[str(year)] = schema
    metrics = phenology_candidate_metrics(summaries_all)
    write_csv(output / "oats_history.csv", summaries_all)
    write_csv(output / "n_balance_history.csv", balances_all)
    write_json(output / "report_schema.json", schemas)
    result = {
        "name": profile["name"], "source": profile["source"],
        "values": profile["values"], "changes": changes,
        "report_changes": report_changes, "metrics": metrics,
    }
    write_json(output / "profile_summary.json", result)
    return result, summaries_all, balances_all


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
    year_records = {year: records_for_year(observations, year) for year in YEARS}
    oat_fields = tuple(dict.fromkeys(
        row["field"] for year in YEARS for row in year_records[year]
        if "Oats" in row.get("yield_components", [])
    ))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "outputs" / "multifield" / "OATS_N_UPTAKE_FACTORS" / stamp
    root.mkdir(parents=True, exist_ok=False)

    candidates, case_rows, balance_rows, metric_rows = [], [], [], []
    for index, profile in enumerate(PROFILES, 1):
        print(f"[{index}/{len(PROFILES)}] {profile['name']}", flush=True)
        result, summaries, balances = run_profile(
            config, observations, year_records, oat_fields, profile,
            root / profile["name"],
        )
        candidates.append(result)
        case_rows.extend({"candidate": profile["name"], **row} for row in summaries)
        balance_rows.extend({"candidate": profile["name"], **row} for row in balances)
        for scope, metrics in result["metrics"].items():
            metric_rows.append({
                "candidate": profile["name"], "scope": scope,
                "reference_source": profile["source"],
                "KNO3": profile["values"].get(KNO3, 0.02),
                "KNH4": profile["values"].get(KNH4, 0.01),
                "yield_mae_t_ha": metrics["yield_mae_t_ha"],
                "yield_rmse_t_ha": metrics["yield_rmse_t_ha"],
                "yield_bias_t_ha": metrics["yield_bias_t_ha"],
                "tillering_mae_days": metrics["tillering"]["mae_days"],
                "heading_mae_days": metrics["heading"]["mae_days"],
            })
        dev = result["metrics"]["development_yakov_2020_2022"]
        val = result["metrics"]["validation_yakov_2023"]
        print(
            f"  development RMSE={dev['yield_rmse_t_ha']:.3f}; "
            f"2023 RMSE={val['yield_rmse_t_ha']:.3f}", flush=True,
        )

    comparison = source_candidate_comparison([
        {**candidate, "name": (
            "current_yakov" if candidate["name"] == "current_oats"
            else candidate["name"]
        )} for candidate in candidates
    ])
    write_csv(root / "candidate_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", case_rows)
    write_csv(root / "n_supply_cases.csv", balance_rows)
    write_json(root / "summary.json", {
        "experiment": "oats_n_uptake_coefficient_screen",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only", "base_model_changed": False,
        "historical_n_labels_replayed": True,
        "candidate_values_are_exact_local_apsim_references": True,
        "candidate_values_are_not_calibration_bounds": True,
        "apsim_reference_sources": {
            "control": "Oats/Wheat/Barley/WinterCereal KNO3=0.02, KNH4=0.01",
            "alternative": "Maize KNO3=0.03, KNH4=0.02",
        },
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"],
            "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "profiles": PROFILES, "comparison": comparison,
        "interpretation_limits": [
            "2019 Argamak is context only and excluded from Yakov development metrics.",
            "Maize coefficients are mechanism references, not accepted oat parameters.",
            "Candidates are compared without weighting or normalization.",
            "No candidate is applied to the calibrated model automatically.",
        ],
    })
    archive = ROOT / f"oats_n_uptake_factors_{stamp}.tar.gz"
    create_archive(archive, root)
    print(
        "DOMINATES CURRENT: "
        + (", ".join(comparison["candidates_dominating_current"]) or "none"),
        flush=True,
    )
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
