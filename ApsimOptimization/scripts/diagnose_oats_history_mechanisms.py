#!/usr/bin/env python3
"""Run one APSIM simulation and compare oat mechanisms across 2019-2023."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import (compare_historical, load_observations,
                                 parse_technology_nutrients, records_for_year)
from src.multifield_model import (child, fingerprint, inventory, load_model,
                                  prepare, read_json, write_json)
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_results import read_results
from src.oats_history import summarize_oats_history
from src.oats_n_balance import build_oats_n_balance
from src.oats_n_layers import build_oats_n_layers, build_oats_n_uptake_layers
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports


YEARS = tuple(range(2019, 2024))


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, output: Path) -> None:
    allowed = {
        "apsim.log", "config_snapshot.json", "model_inventory.json",
        "preparation.json", "history_summary.json", "oats_history.csv",
        "daily_oats_2019_2023.csv", "report_schema.json",
        "historical_comparisons.json", "historical_comparisons.csv",
        "oats_n_supply_history.csv", "n_balance_history.csv",
        "n_layer_summary_history.csv", "n_uptake_layers_history.csv",
    }
    files = sorted(file for file in output.iterdir() if file.is_file() and file.name in allowed)
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "output_dir": output.relative_to(ROOT).as_posix(),
        "files": [{
            "path": file.name,
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
            archive.add(file, arcname=file.name, recursive=False)


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
    oat_records = [
        row for year in YEARS for row in year_records[year]
        if "Oats" in row.get("yield_components", [])
    ]
    oat_fields = tuple(dict.fromkeys(row["field"] for row in oat_records))
    _, _, zones, _ = load_model(config)
    thickness_by_field = {
        zone["Name"]: list(child(child(zone, "Soil"), "Physical")["Thickness"])
        for zone in zones if zone["Name"] in oat_fields
    }

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "outputs" / "multifield" / "OATS_HISTORY_MECHANISMS" / stamp
    output.mkdir(parents=True, exist_ok=False)

    print(
        f"[1/4] Preparing oat reports for {len(oat_records)} cases in "
        f"{len(oat_fields)} fields", flush=True,
    )
    data = read_json(config["model"])
    report_changes = patch_oats_diagnostic_reports(data, oat_fields)
    source_candidate = output / "source_candidate.apsimx"
    write_json(source_candidate, data)
    case = dict(config)
    case["model"] = str(source_candidate.resolve())
    case["model_provenance"] = None
    case["historical_replay"] = dict(config["historical_replay"], enabled=True)
    case["scenario"] = {
        "kind": "diagnostic",
        "name": "oats_history_mechanisms_2019_2023",
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)

    print("[2/4] Running the full 2019-2023 APSIM history once", flush=True)
    database = run_apsim(case, prepared)

    print("[3/4] Reading yield checks and physiology for every oat case", flush=True)
    comparisons = []
    comparison_rows = []
    daily_rows = []
    summary_rows = []
    balance_rows = []
    layer_summary_rows = []
    uptake_layer_rows = []
    schemas = {}
    for year in YEARS:
        result = read_results(database, meta, year, case, audit_applications=False)
        comparison = compare_historical(result, meta, observations, year)
        comparisons.append(comparison)
        for row in comparison["fields"]:
            comparison_rows.append({"year": year, **{
                key: value for key, value in row.items() if key != "yield_components"
            }})
        if not any("Oats" in row.get("yield_components", []) for row in year_records[year]):
            continue
        daily, summaries, schema = collect_oats_diagnostics(
            database, meta, year_records[year], year=year,
            scenario="historical_observed_n_labels",
        )
        for row in daily:
            row["year"] = year
        for row in summaries:
            row["observed_n_label_kg_ha"] = parse_technology_nutrients(
                row.get("technology")
            )["N"]
        daily_rows.extend(daily)
        summary_rows.extend(summaries)
        for row in build_oats_n_balance(daily, summaries):
            balance_rows.append({"year": year, **row})
        phase_rows, _ = build_oats_n_layers(daily, thickness_by_field)
        for row in phase_rows:
            layer_summary_rows.append({"year": year, **row})
        for row in build_oats_n_uptake_layers(daily, thickness_by_field):
            uptake_layer_rows.append({"year": year, **row})
        schemas[str(year)] = schema

    history = summarize_oats_history(summary_rows, validation_year=2023)
    write_csv(output / "oats_history.csv", history.pop("rows"))
    write_csv(output / "daily_oats_2019_2023.csv", daily_rows)
    write_csv(output / "n_balance_history.csv", balance_rows)
    write_csv(output / "n_layer_summary_history.csv", layer_summary_rows)
    write_csv(output / "n_uptake_layers_history.csv", uptake_layer_rows)
    balance_by_case = {(int(row["year"]), row["field"]): row for row in balance_rows}
    grain_fill_by_case = {
        (int(row["year"]), row["field"]): row for row in layer_summary_rows
        if row["phase"] == "grain_fill_start"
    }
    supply_history = []
    for row in summary_rows:
        key = (int(row["year"]), row["field"])
        balance = balance_by_case.get(key, {})
        grain_fill = grain_fill_by_case.get(key, {})
        supply_history.append({
            "year": key[0],
            "split": "validation" if key[0] == 2023 else "development",
            "field": key[1],
            "technology": row.get("technology"),
            "observed_n_label_kg_ha": row.get("observed_n_label_kg_ha"),
            "observed_yield_t_ha": row.get("observed_yield_t_ha"),
            "simulated_yield_t_ha": row.get("simulated_yield_t_ha"),
            "error_t_ha": row.get("error_t_ha"),
            "absolute_error_t_ha": row.get("absolute_error_t_ha"),
            "grain_fill_days": row.get("grain_fill_days"),
            "grain_fill_n_stress_days": balance.get("grain_fill_n_stress_days"),
            "root_n_uptake_active_kg_n_ha": balance.get(
                "root_n_uptake_magnitude_if_native_nonpositive"
            ),
            "soil_n_supply_to_demand_active_ratio": balance.get(
                "soil_n_supply_to_demand_active_ratio"
            ),
            "days_soil_n_supply_below_plant_demand": balance.get(
                "days_soil_n_supply_below_plant_demand"
            ),
            "soil_n_supply_to_demand_grain_fill_ratio": balance.get(
                "soil_n_supply_to_demand_grain_fill_ratio"
            ),
            "grain_fill_days_soil_n_supply_below_plant_demand": balance.get(
                "grain_fill_days_soil_n_supply_below_plant_demand"
            ),
            "mineral_n_at_grain_fill_start_kg_n_ha": balance.get(
                "mineral_n_at_grain_fill_start_model_value"
            ),
            "root_depth_at_grain_fill_start_mm": grain_fill.get("root_depth_mm"),
            "no3_nh4_inside_root_depth_at_grain_fill_start_kg_n_ha": grain_fill.get(
                "no3_nh4_inside_root_depth_proxy_kg_n_ha"
            ),
            "no3_nh4_below_root_depth_at_grain_fill_start_kg_n_ha": grain_fill.get(
                "no3_nh4_below_root_depth_proxy_kg_n_ha"
            ),
        })
    write_csv(output / "oats_n_supply_history.csv", supply_history)
    write_csv(output / "historical_comparisons.csv", comparison_rows)
    write_json(output / "historical_comparisons.json", comparisons)
    write_json(output / "report_schema.json", schemas)
    write_json(output / "history_summary.json", {
        "experiment": "oats_history_mechanisms_2019_2023",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "historical_n_labels_replayed": True,
        "inputs": {
            "config": str(args.config.resolve()),
            "model": config["model"],
            "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"],
            "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "diagnostic_report_changes": report_changes,
        "metrics": history,
        "interpretation_limits": [
            "Error groups use only the sign of simulated minus observed yield; no quality threshold is invented.",
            "The 2019 observed oat cultivar is Argamak while the APSIM rotation uses Yakov.",
            "Observed N labels are replayed once at the model sowing event; P and K response is not simulated.",
            "This run diagnoses mechanisms and does not change cultivar, soil, or optimization bounds.",
        ],
    })

    print("[4/4] Writing reusable CSV evidence and compact archive", flush=True)
    archive = ROOT / f"oats_history_mechanisms_{stamp}.tar.gz"
    create_archive(archive, output)
    all_metrics = history["all_years"]
    validation = history["validation_year"]
    print(
        f"Oats all years: n={all_metrics['cases']}; MAE={all_metrics['mae_t_ha']:.3f}; "
        f"RMSE={all_metrics['rmse_t_ha']:.3f}; bias={all_metrics['mean_bias_t_ha']:+.3f}",
        flush=True,
    )
    print(
        f"Oats 2023 validation: n={validation['cases']}; "
        f"MAE={validation['mae_t_ha']:.3f}; bias={validation['mean_bias_t_ha']:+.3f}",
        flush=True,
    )
    print(f"OUTPUT: {output}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
