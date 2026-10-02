#!/usr/bin/env python3
"""Compare modeled wheat termination dates with documented rotation harvests."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import sqlite3
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import child, fingerprint, load_model, write_json
from src.multifield_optimizer import ROOT, load_config
from src.oats_residue_sensitivity import cereal_to_oats_targets


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


def audit(database: Path, simulation: str, targets: list[dict]) -> tuple[list[dict], list[dict]]:
    summaries = []
    event_windows = []
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        simulation_id = connection.execute(
            'SELECT ID FROM "_Simulations" WHERE Name=?', (simulation,)
        ).fetchone()[0]
        for target in targets:
            field = target["field"]
            harvest_date = dt.date.fromisoformat(target["cereal_harvest_date"])
            start = harvest_date.replace(month=1, day=1).isoformat()
            end = (harvest_date + dt.timedelta(days=30)).isoformat()
            table = f"Report{field.removeprefix('Field')}"
            rows = [dict(row) for row in connection.execute(
                f'''SELECT substr("Clock.Today",1,10) AS Date,
                           WheatAGWt, WheatYield, WheatZadok, WheatStage,
                           SurfaceResidueWt, SurfaceResidueN
                    FROM "{table}" WHERE SimulationID=?
                    AND substr("Clock.Today",1,10) BETWEEN ? AND ?
                    ORDER BY "Clock.Today"''',
                (simulation_id, start, end),
            )]
            active_indices = [
                index for index, row in enumerate(rows)
                if float(row["WheatAGWt"] or 0.0) > 0.0
            ]
            last_active_index = active_indices[-1] if active_indices else None
            first_zero = None
            if last_active_index is not None:
                first_zero = next(
                    (row for row in rows[last_active_index + 1:]
                     if float(row["WheatAGWt"] or 0.0) <= 0.0),
                    None,
                )
            jumps = []
            for previous, current in zip(rows, rows[1:]):
                jump = float(current["SurfaceResidueWt"] or 0.0) - float(
                    previous["SurfaceResidueWt"] or 0.0
                )
                jumps.append((jump, previous, current))
            largest = max(jumps, key=lambda item: item[0]) if jumps else None
            modeled_date = first_zero["Date"] if first_zero else None
            summaries.append({
                **target,
                "last_active_wheat_date": (
                    rows[last_active_index]["Date"] if last_active_index is not None else None
                ),
                "modeled_wheat_termination_date": modeled_date,
                "termination_minus_documented_days": (
                    (dt.date.fromisoformat(modeled_date) - harvest_date).days
                    if modeled_date else None
                ),
                "largest_surface_residue_jump_date": (
                    largest[2]["Date"] if largest else None
                ),
                "largest_surface_residue_jump_kg_ha": (
                    largest[0] if largest else None
                ),
                "residue_before_largest_jump_kg_ha": (
                    float(largest[1]["SurfaceResidueWt"] or 0.0) if largest else None
                ),
                "residue_after_largest_jump_kg_ha": (
                    float(largest[2]["SurfaceResidueWt"] or 0.0) if largest else None
                ),
            })
            if modeled_date:
                center = dt.date.fromisoformat(modeled_date)
                for row in rows:
                    day = dt.date.fromisoformat(row["Date"])
                    if abs((day - center).days) <= 3:
                        event_windows.append({
                            "field": field,
                            "oat_year": target["oat_year"],
                            "documented_harvest_date": harvest_date.isoformat(),
                            **row,
                        })
    return summaries, event_windows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "configs" / "multifield_historical_replay.yaml",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    model, simulation, _, _ = load_model(config)
    start_year = int(child(simulation, "Clock")["Start"][:4])
    targets = cereal_to_oats_targets(model, start_year)
    summaries, windows = audit(args.database, simulation["Name"], targets)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = ROOT / "outputs" / "multifield" / "CEREAL_HARVEST_TIMING_AUDIT" / stamp
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "harvest_timing.csv", summaries)
    write_csv(output / "harvest_event_windows.csv", windows)
    payload = {
        "experiment": "cereal_harvest_timing_before_oats_audit",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "input_and_lifecycle_audit",
        "base_model_changed": False,
        "database": str(args.database.resolve()),
        "database_sha256": fingerprint(args.database),
        "model": config["model"],
        "model_sha256": fingerprint(config["model"]),
        "events": summaries,
    }
    write_json(output / "summary.json", payload)
    archive = ROOT / f"cereal_harvest_timing_{stamp}.tar.gz"
    create_archive(archive, output)
    for row in summaries:
        print(
            f"{row['field']} -> oats {row['oat_year']}: documented "
            f"{row['cereal_harvest_date']}, modeled {row['modeled_wheat_termination_date']}, "
            f"delta={row['termination_minus_documented_days']}",
            flush=True,
        )
    print(f"OUTPUT: {output}")
    print(f"ARCHIVE: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
