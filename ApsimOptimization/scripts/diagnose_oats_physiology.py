#!/usr/bin/env python3
"""Run a compact 2023 oat physiology diagnostic using observed N labels."""
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

from src.historical_case import compare_historical, load_observations, records_for_year
from src.multifield_model import fingerprint, inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_results import read_results
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def compact_archive(path: Path, output: Path) -> dict:
    allowed = {
        "apsim.log", "config_snapshot.json", "model_inventory.json",
        "preparation.json", "historical_comparison.json",
        "historical_comparison.csv", "daily_oats_2023.csv",
        "field_summary.csv", "report_schema.json", "summary.json",
    }
    files = sorted(file for file in output.iterdir() if file.is_file() and file.name in allowed)
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "output_dir": output.relative_to(ROOT).as_posix(),
        "files": [
            {
                "path": file.name,
                "size": file.stat().st_size,
                "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
            for file in files
        ],
    }
    with tarfile.open(path, "w:gz") as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for file in files:
            archive.add(file, arcname=file.name, recursive=False)
    return manifest


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
    records = records_for_year(observations, 2023)
    oat_fields = tuple(
        row["field"] for row in records if "Oats" in row.get("yield_components", [])
    )
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "outputs" / "multifield" / "OATS_PHYSIOLOGY_DIAGNOSTIC" / stamp
    output.mkdir(parents=True, exist_ok=False)

    print(f"[1/3] Preparing temporary diagnostic reports for {', '.join(oat_fields)}", flush=True)
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
        "name": "oats_physiology_observed_n_labels_2023",
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)

    print("[2/3] Running APSIM historical state and 2023 oat diagnostic", flush=True)
    database = run_apsim(case, prepared)
    result = read_results(database, meta, 2023, case, audit_applications=False)
    comparison = compare_historical(result, meta, observations, 2023)
    write_json(output / "historical_comparison.json", comparison)
    fields = [key for key in comparison["fields"][0] if key != "yield_components"]
    with (output / "historical_comparison.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in comparison["fields"])

    print("[3/3] Exporting daily physiology and compact evidence archive", flush=True)
    daily, summaries, schema = collect_oats_diagnostics(
        database, meta, records, year=2023, scenario="observed_n_labels"
    )
    write_csv(output / "daily_oats_2023.csv", daily)
    write_csv(output / "field_summary.csv", summaries)
    write_json(output / "report_schema.json", schema)
    payload = {
        "experiment": "oats_physiology_diagnostic_2023",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": schema["status"],
        "base_model_changed": False,
        "historical_n_labels_replayed": True,
        "fertilizer_limitations": [
            "APSIM fertilizer product is preserved from the model.",
            "P and K response is not simulated.",
            "Observed technology labels do not establish placement or an independent date.",
        ],
        "inputs": {
            "config": str(args.config.resolve()),
            "base_model": config["model"],
            "base_model_sha256": fingerprint(config["model"]),
            "weather": config["weather"],
            "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "diagnostic_report_changes": report_changes,
        "fields": summaries,
        "interpretation": (
            "Mechanism diagnostic only. No cultivar, soil, fertilizer bound, or production "
            "recommendation is changed by this run."
        ),
    }
    write_json(output / "summary.json", payload)
    archive = ROOT / f"oats_physiology_{stamp}.tar.gz"
    compact_archive(archive, output)
    print(f"STATUS: {schema['status']}", flush=True)
    for row in summaries:
        print(
            f"{row['field']}: observed={row['observed_yield_t_ha']:.3f}; "
            f"simulated={row['simulated_yield_t_ha']:.3f}; "
            f"Fw<0.8 days={row['water_stress_days_fw_below_0_8']}; "
            f"Fn<0.8 days={row['nitrogen_stress_days_fn_below_0_8']}; "
            f"max root={row['max_root_depth_mm']:.1f} mm",
            flush=True,
        )
    print(f"OUTPUT: {output}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

