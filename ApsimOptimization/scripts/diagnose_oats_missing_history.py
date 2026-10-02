#!/usr/bin/env python3
"""Measure 2023 oat sensitivity to unobserved pre-2023 N applications."""
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

from src.historical_case import load_observations, records_for_year
from src.historical_management_audit import (build_management_audit,
                                              inject_missing_n_records)
from src.multifield_model import (child, fingerprint, historical_replay_plan,
                                  inventory, load_model, prepare, read_json,
                                  write_json)
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_field_state import paired_oat_metrics
from src.oats_n_balance import build_oats_n_balance
from src.oats_n_layers import build_oats_n_layers, build_oats_n_uptake_layers
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports


YEAR = 2023
FIELDS = ("Field2", "Field7")
# All levels occur explicitly in the supplied historical technology labels.
LEVELS = (30.0, 0.0, 60.0)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "profile_metrics.csv", "case_predictions.csv",
        "n_balance.csv", "n_layer_summary.csv", "n_layers.csv",
        "n_uptake_layers.csv",
        "profile_summary.json", "oats_2023.csv", "daily_oats_2023.csv",
        "history_changes.json", "replay_observations.json", "report_schema.json",
        "config_snapshot.json", "model_inventory.json", "preparation.json", "apsim.log",
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


def run_level(config, observations, audit_rows, thickness_by_field, level, output):
    output.mkdir(parents=True, exist_ok=False)
    replay_observations, changes = inject_missing_n_records(
        observations, audit_rows, level, set(FIELDS), before_year=YEAR
    )
    # N30 is the current static fallback. Keeping the source observations intact
    # makes this profile an exact control rather than a synthetic replay.
    if level == 30.0:
        replay_observations = observations
        changes = [{**row, "control_uses_model_static_fallback": True}
                   for row in changes]

    model = read_json(config["model"])
    report_changes = patch_oats_diagnostic_reports(model, FIELDS)
    source_candidate = output / "source_candidate.apsimx"
    replay_path = output / "replay_observations.json"
    write_json(source_candidate, model)
    write_json(replay_path, replay_observations)
    write_json(output / "history_changes.json", changes)
    case = dict(config)
    case["model"] = str(source_candidate.resolve())
    case["model_provenance"] = None
    case["historical_observations"] = str(replay_path.resolve())
    case["scenario"] = {
        "kind": "diagnostic", "name": f"missing_history_n_{level:g}",
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)
    daily, rows, schema = collect_oats_diagnostics(
        database, meta, records_for_year(observations, YEAR), year=YEAR,
        scenario=case["scenario"]["name"],
    )
    metrics = paired_oat_metrics(rows, YEAR, *FIELDS)
    n_balance = build_oats_n_balance(daily, rows)
    n_layer_summary, n_layers = build_oats_n_layers(daily, thickness_by_field)
    n_uptake_layers = build_oats_n_uptake_layers(daily, thickness_by_field)
    write_csv(output / "daily_oats_2023.csv", daily)
    write_csv(output / "oats_2023.csv", rows)
    write_csv(output / "n_balance.csv", n_balance)
    write_csv(output / "n_layer_summary.csv", n_layer_summary)
    write_csv(output / "n_layers.csv", n_layers)
    write_csv(output / "n_uptake_layers.csv", n_uptake_layers)
    write_json(output / "report_schema.json", schema)
    result = {
        "name": case["scenario"]["name"], "missing_history_n_model_value": level,
        "history_changes": changes, "report_changes": report_changes,
        "metrics": metrics, "n_balance": n_balance,
        "n_layer_summary": n_layer_summary,
        "n_uptake_layers": n_uptake_layers,
    }
    write_json(output / "profile_summary.json", result)
    return result, rows, n_layers


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
    _, simulation, zones, _ = load_model(config)
    thickness_by_field = {
        zone["Name"]: list(child(child(zone, "Soil"), "Physical")["Thickness"])
        for zone in zones if zone["Name"] in FIELDS
    }
    meta = inventory(config)
    replay = historical_replay_plan(config, simulation, zones)
    audit_rows, _, audit = build_management_audit(meta, observations, replay)
    gaps = audit["field2_field7_pre_2023_fallbacks"]
    if not gaps:
        raise ValueError("No target missing-history events were found")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "outputs" / "multifield" / "OATS_MISSING_HISTORY" / stamp
    root.mkdir(parents=True, exist_ok=False)
    profiles, case_rows, balance_rows = [], [], []
    layer_summary_rows, layer_rows, uptake_layer_rows = [], [], []
    for index, level in enumerate(LEVELS, 1):
        print(f"[{index}/{len(LEVELS)}] missing historical events N{level:g}", flush=True)
        result, rows, profile_layers = run_level(
            config, observations, audit_rows, thickness_by_field,
            level, root / f"n_{level:g}"
        )
        profiles.append(result)
        case_rows.extend({"profile": result["name"], **row} for row in rows)
        balance_rows.extend({"profile": result["name"], **row}
                            for row in result["n_balance"])
        layer_summary_rows.extend({"profile": result["name"], **row}
                                  for row in result["n_layer_summary"])
        layer_rows.extend({"profile": result["name"], **row}
                          for row in profile_layers)
        uptake_layer_rows.extend({"profile": result["name"], **row}
                                 for row in result["n_uptake_layers"])
        print(
            f"  Field2={result['metrics']['fields_detail']['Field2']['simulated_yield_t_ha']:.3f}; "
            f"Field7={result['metrics']['fields_detail']['Field7']['simulated_yield_t_ha']:.3f}; "
            f"RMSE={result['metrics']['rmse_t_ha']:.3f}", flush=True,
        )

    control = profiles[0]["metrics"]
    metric_rows = []
    for profile in profiles:
        metrics = profile["metrics"]
        metric_rows.append({
            "profile": profile["name"],
            "missing_history_n_model_value": profile["missing_history_n_model_value"],
            "field2_simulated_yield_t_ha": metrics["fields_detail"]["Field2"]["simulated_yield_t_ha"],
            "field7_simulated_yield_t_ha": metrics["fields_detail"]["Field7"]["simulated_yield_t_ha"],
            "mae_t_ha": metrics["mae_t_ha"], "rmse_t_ha": metrics["rmse_t_ha"],
            "rmse_change_from_control_t_ha": metrics["rmse_t_ha"] - control["rmse_t_ha"],
            "bias_t_ha": metrics["bias_t_ha"],
            "simulated_pair_delta_t_ha": metrics["simulated_yield_b_minus_a_t_ha"],
            "ordering_matches_observations": metrics["ordering_matches_observations"],
        })
    write_csv(root / "profile_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", case_rows)
    write_csv(root / "n_balance.csv", balance_rows)
    write_csv(root / "n_layer_summary.csv", layer_summary_rows)
    write_csv(root / "n_layers.csv", layer_rows)
    write_csv(root / "n_uptake_layers.csv", uptake_layer_rows)
    write_json(root / "summary.json", {
        "experiment": "oats_2023_missing_preseason_history_sensitivity",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only", "base_model_changed": False,
        "target_gaps": gaps, "levels": LEVELS, "profiles": profiles,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "interpretation_limits": [
            "N0 and N60 are observed labels elsewhere in the supplied data and are diagnostic scenarios, not inferred true history.",
            "Only the three missing Field2/Field7 pre-2023 sowing events are changed.",
            "Observed 2022 and 2023 applications remain unchanged.",
            "The experiment does not create fertilizer bounds or update the calibrated model.",
        ],
    })
    archive = ROOT / f"oats_missing_history_{stamp}.tar.gz"
    create_archive(archive, root)
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
