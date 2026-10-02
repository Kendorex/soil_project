#!/usr/bin/env python3
"""Export evidence coverage for every modeled historical sowing application."""
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

from src.historical_case import load_observations
from src.historical_management_audit import build_management_audit
from src.multifield_model import (fingerprint, historical_replay_plan, inventory,
                                  load_model, write_json)
from src.multifield_optimizer import ROOT, load_config


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, output: Path) -> None:
    files = sorted(file for file in output.iterdir() if file.is_file())
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [{
            "path": file.name, "size": file.stat().st_size,
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
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "configs" / "multifield_historical_replay.yaml",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    observations = load_observations(config["historical_observations"])
    _, simulation, zones, _ = load_model(config)
    model_inventory = inventory(config)
    replay = historical_replay_plan(config, simulation, zones)
    rows, field_rows, audit = build_management_audit(
        model_inventory, observations, replay
    )

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "outputs" / "multifield" / "HISTORICAL_MANAGEMENT_AUDIT" / stamp
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "management_history.csv", rows)
    write_csv(output / "field_coverage.csv", field_rows)
    write_csv(
        output / "model_fallback_events.csv",
        [row for row in rows if row["effective_application_source"] == "model_static_fallback"],
    )
    write_json(output / "summary.json", {
        "experiment": "historical_management_evidence_audit",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "input_evidence_audit",
        "base_model_changed": False,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "audit": audit,
        "interpretation_limits": [
            "A model fallback is an unverified input, not evidence that the application was wrong.",
            "The Amount value is retained in native APSIM manager terms; product semantics are not inferred.",
            "P and K labels are recorded but the historical replay changes only the N application amount.",
            "Perennial carry-over years have no sowing event and therefore no sowing fertilizer event.",
        ],
    })
    archive = ROOT / f"historical_management_audit_{stamp}.tar.gz"
    create_archive(archive, output)
    print(f"ROTATION-YEAR ROWS: {audit['rotation_year_rows']}", flush=True)
    print(f"SOWING EVENTS: {audit['sowing_events']}", flush=True)
    print(f"OBSERVED N LABEL EVENTS: {audit['observed_n_label_events']}", flush=True)
    print(f"MODEL FALLBACK EVENTS: {audit['model_static_fallback_events']}", flush=True)
    print(f"OUTPUT: {output}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
