#!/usr/bin/env python3
"""Trace N mineralisation and residues from 2022 wheat to 2023 oats."""
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
from src.multifield_model import fingerprint, inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_field_state import paired_oat_metrics
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports
from src.oats_preseason_n import collect_preseason_rows, build_preseason_n_summary


FIELDS = ("Field2", "Field7")


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, output: Path) -> None:
    allowed = {
        "summary.json", "daily_preseason_n.csv", "phase_snapshots.csv",
        "interval_balance.csv", "oats_2023.csv", "report_schema.json",
        "config_snapshot.json", "model_inventory.json", "preparation.json", "apsim.log",
    }
    files = sorted(file for file in output.iterdir()
                   if file.is_file() and file.name in allowed)
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [{
            "path": file.name, "size": file.stat().st_size,
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
    previous = {row["field"]: row for row in records_for_year(observations, 2022)}
    current = {row["field"]: row for row in records_for_year(observations, 2023)}
    windows = {}
    predecessor_evidence = {}
    for field in FIELDS:
        previous_row = previous[field]
        current_row = current[field]
        harvest = previous_row.get("observed_harvest_date")
        sowing = current_row.get("observed_sow_date")
        if not harvest or not sowing:
            raise ValueError(f"{field}: exact 2022 harvest and 2023 sowing dates required")
        windows[field] = {
            "previous_harvest_date": harvest,
            "sowing_date": sowing,
            "end_date": "2023-12-31",
        }
        predecessor_evidence[field] = {
            "crop_2022": previous_row.get("crop"),
            "technology_2022": previous_row.get("technology"),
            "observed_yield_2022_t_ha": previous_row.get("observed_yield_t_ha"),
            "harvest_2022": harvest,
            "crop_2023": current_row.get("crop"),
            "technology_2023": current_row.get("technology"),
            "sowing_2023": sowing,
        }

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "outputs" / "multifield" / "OATS_PRESEASON_N" / stamp
    output.mkdir(parents=True, exist_ok=False)
    model = read_json(config["model"])
    report_changes = patch_oats_diagnostic_reports(model, FIELDS)
    source_candidate = output / "source_candidate.apsimx"
    write_json(source_candidate, model)
    case = dict(config)
    case["model"] = str(source_candidate.resolve())
    case["model_provenance"] = None
    case["scenario"] = {
        "kind": "diagnostic", "name": "oats_2022_2023_n_lineage",
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)

    daily_rows = collect_preseason_rows(database, meta, windows)
    snapshots, intervals = build_preseason_n_summary(daily_rows, windows)
    _, oat_rows, schema = collect_oats_diagnostics(
        database, meta, records_for_year(observations, 2023), year=2023,
        scenario=case["scenario"]["name"],
    )
    metrics = paired_oat_metrics(oat_rows, 2023, *FIELDS)
    write_csv(output / "daily_preseason_n.csv", daily_rows)
    write_csv(output / "phase_snapshots.csv", snapshots)
    write_csv(output / "interval_balance.csv", intervals)
    write_csv(output / "oats_2023.csv", oat_rows)
    write_json(output / "report_schema.json", schema)
    write_json(output / "summary.json", {
        "experiment": "oats_2022_2023_preseason_n_lineage",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only", "base_model_changed": False,
        "windows": windows, "predecessor_evidence": predecessor_evidence,
        "report_changes": report_changes, "yield_metrics": metrics,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"],
            "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "interpretation_limits": [
            "Reported terms are a partial lineage, not a closed nitrogen balance.",
            "Net mineralisation and organic pools are direct APSIM outputs in kg/ha.",
            "No residue-management observation was supplied for an external check.",
            "The run does not alter soil, crop, fertiliser, or optimization parameters.",
        ],
    })
    archive = ROOT / f"oats_preseason_n_{stamp}.tar.gz"
    create_archive(archive, output)
    for row in intervals:
        print(
            f"{row['field']} {row['interval']}: mineralised="
            f"{row['net_mineralised_n_sum_kg_ha']:.2f} kg/ha; "
            f"mineral N change={row['mineral_n_change_kg_ha']:+.2f} kg/ha",
            flush=True,
        )
    print(f"OUTPUT: {output}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
