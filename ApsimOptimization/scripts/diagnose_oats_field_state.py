#!/usr/bin/env python3
"""Separate 2023 oat effects of inherited field state and observed N labels."""
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

from src.historical_case import load_observations, parse_technology_nutrients, records_for_year
from src.multifield_model import fingerprint, inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_field_state import (paired_oat_metrics, swap_observed_technology,
                                  swap_soil_initial_state)
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports


YEAR = 2023
FIELD_A = "Field2"
FIELD_B = "Field7"
PROFILES = (
    {"name": "control", "swap_initial_state": False, "swap_n_labels": False},
    {"name": "swap_initial_state", "swap_initial_state": True, "swap_n_labels": False},
    {"name": "swap_2023_n_labels", "swap_initial_state": False, "swap_n_labels": True},
    {"name": "swap_both", "swap_initial_state": True, "swap_n_labels": True},
)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "profile_metrics.csv", "case_predictions.csv",
        "profile_summary.json", "oats_2023.csv", "daily_oats_2023.csv",
        "report_schema.json", "state_change.json", "replay_observations.json",
        "config_snapshot.json", "model_inventory.json", "preparation.json", "apsim.log",
    }
    files = sorted(
        file for file in root.rglob("*") if file.is_file() and file.name in allowed
    )
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "output_dir": root.relative_to(ROOT).as_posix(),
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


def _replay_n_by_field(observations: dict) -> dict[str, float]:
    records = {row["field"]: row for row in records_for_year(observations, YEAR)}
    return {
        field: float(parse_technology_nutrients(records[field]["technology"])["N"])
        for field in (FIELD_A, FIELD_B)
    }


def run_profile(config, observations, profile, output):
    output.mkdir(parents=True, exist_ok=False)
    model = read_json(config["model"])
    report_changes = patch_oats_diagnostic_reports(model, (FIELD_A, FIELD_B))
    state_change = None
    if profile["swap_initial_state"]:
        state_change = swap_soil_initial_state(model, FIELD_A, FIELD_B)

    replay_observations = copy.deepcopy(observations)
    technology_change = None
    if profile["swap_n_labels"]:
        technology_change = swap_observed_technology(
            replay_observations, YEAR, FIELD_A, FIELD_B
        )
    replay_n = _replay_n_by_field(replay_observations)

    source_candidate = output / "source_candidate.apsimx"
    replay_path = output / "replay_observations.json"
    write_json(source_candidate, model)
    write_json(replay_path, replay_observations)
    write_json(output / "state_change.json", {
        "profile": profile,
        "soil_initial_state": state_change,
        "technology_label": technology_change,
        "replayed_n_label_kg_ha": replay_n,
        "source_model_sha256": fingerprint(config["model"]),
        "candidate_model_sha256": fingerprint(source_candidate),
        "source_observations_sha256": fingerprint(config["historical_observations"]),
        "replay_observations_sha256": fingerprint(replay_path),
    })

    case = copy.deepcopy(config)
    case["model"] = str(source_candidate.resolve())
    case["model_provenance"] = None
    case["historical_observations"] = str(replay_path.resolve())
    case["historical_replay"] = dict(config["historical_replay"], enabled=True)
    case["scenario"] = {
        "kind": "diagnostic",
        "name": profile["name"],
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)

    daily, rows, schema = collect_oats_diagnostics(
        database, meta, records_for_year(observations, YEAR), year=YEAR,
        scenario=profile["name"],
    )
    for row in rows:
        row["replayed_n_label_kg_ha"] = replay_n[row["field"]]
    metrics = paired_oat_metrics(rows, YEAR, FIELD_A, FIELD_B)
    write_csv(output / "daily_oats_2023.csv", daily)
    write_csv(output / "oats_2023.csv", rows)
    write_json(output / "report_schema.json", schema)
    result = {
        "name": profile["name"],
        "design": profile,
        "replayed_n_label_kg_ha": replay_n,
        "soil_initial_state_change": state_change,
        "technology_label_change": technology_change,
        "report_changes": report_changes,
        "metrics": metrics,
    }
    write_json(output / "profile_summary.json", result)
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
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "outputs" / "multifield" / "OATS_FIELD_STATE" / stamp
    root.mkdir(parents=True, exist_ok=False)

    profiles, case_rows = [], []
    for index, profile in enumerate(PROFILES, 1):
        print(f"[{index}/{len(PROFILES)}] {profile['name']}", flush=True)
        result, rows = run_profile(
            config, observations, profile, root / profile["name"]
        )
        profiles.append(result)
        case_rows.extend({"profile": profile["name"], **row} for row in rows)
        metrics = result["metrics"]
        print(
            f"  RMSE={metrics['rmse_t_ha']:.3f}; "
            f"bias={metrics['bias_t_ha']:.3f}; "
            f"pair delta={metrics['simulated_yield_b_minus_a_t_ha']:.3f}",
            flush=True,
        )

    control = profiles[0]["metrics"]
    metric_rows = []
    for result in profiles:
        metrics = result["metrics"]
        metric_rows.append({
            "profile": result["name"],
            "swap_initial_state": result["design"]["swap_initial_state"],
            "swap_n_labels": result["design"]["swap_n_labels"],
            "field2_replayed_n_kg_ha": result["replayed_n_label_kg_ha"][FIELD_A],
            "field7_replayed_n_kg_ha": result["replayed_n_label_kg_ha"][FIELD_B],
            "mae_t_ha": metrics["mae_t_ha"],
            "rmse_t_ha": metrics["rmse_t_ha"],
            "rmse_change_from_control_t_ha": metrics["rmse_t_ha"] - control["rmse_t_ha"],
            "bias_t_ha": metrics["bias_t_ha"],
            "observed_pair_delta_t_ha": metrics["observed_yield_b_minus_a_t_ha"],
            "simulated_pair_delta_t_ha": metrics["simulated_yield_b_minus_a_t_ha"],
            "ordering_matches_observations": metrics["ordering_matches_observations"],
            "field2_simulated_yield_t_ha": metrics["fields_detail"][FIELD_A]["simulated_yield_t_ha"],
            "field7_simulated_yield_t_ha": metrics["fields_detail"][FIELD_B]["simulated_yield_t_ha"],
        })
    write_csv(root / "profile_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", case_rows)
    write_json(root / "summary.json", {
        "experiment": "oats_2023_field_state_x_n_label_factorial",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "year": YEAR,
        "fields": [FIELD_A, FIELD_B],
        "design": PROFILES,
        "profiles": profiles,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "interpretation_limits": [
            "The soil-state swap exchanges only existing model-start values between Field2 and Field7.",
            "Because APSIM runs continuously from 2019, the swapped state propagates through the full rotation before 2023.",
            "The N-label swap exchanges only the observed 2023 N30 and N60 labels; P and K are not replayed.",
            "The experiment diagnoses attribution and does not define optimization bounds.",
            "No diagnostic profile is applied to the calibrated model automatically.",
        ],
    })
    archive = ROOT / f"oats_field_state_{stamp}.tar.gz"
    create_archive(archive, root)
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
