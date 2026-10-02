#!/usr/bin/env python3
"""Build a field-state report from an oat history diagnostic archive."""
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

from src.multifield_model import write_json
from src.multifield_optimizer import ROOT
from src.oats_state_lineage import build_state_lineage


def csv_rows(payload: bytes) -> list[dict]:
    text = payload.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_verified_archive(path: Path) -> tuple[list[dict], list[dict], dict]:
    with tarfile.open(path, "r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
        required = {"manifest.json", "daily_oats_2019_2023.csv", "oats_history.csv"}
        if not required <= names:
            raise ValueError(f"Archive is missing {sorted(required - names)}")
        manifest = json.load(archive.extractfile("manifest.json"))
        expected = {row["path"]: row["sha256"] for row in manifest["files"]}
        payloads = {}
        for name in required - {"manifest.json"}:
            payload = archive.extractfile(name).read()
            if hashlib.sha256(payload).hexdigest() != expected.get(name):
                raise ValueError(f"SHA-256 mismatch for {name}")
            payloads[name] = payload
    return (
        csv_rows(payloads["daily_oats_2019_2023.csv"]),
        csv_rows(payloads["oats_history.csv"]),
        manifest,
    )


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
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args(argv)
    daily, history, source_manifest = read_verified_archive(args.archive)
    lineage, contrasts = build_state_lineage(daily, history)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = ROOT / "outputs" / "multifield" / "OATS_STATE_LINEAGE" / stamp
    output.mkdir(parents=True, exist_ok=False)
    write_csv(output / "state_lineage.csv", lineage)
    write_csv(output / "paired_field_contrasts.csv", contrasts)
    write_json(output / "summary.json", {
        "experiment": "oats_field_state_lineage",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_archive": str(args.archive.resolve()),
        "source_archive_sha256": hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        "source_manifest_created_utc": source_manifest["created_utc"],
        "cases": len(lineage),
        "paired_years": [row["year"] for row in contrasts],
        "base_model_changed": False,
        "interpretation_limits": [
            "Mineral N and PAW values are APSIM model states, not independent measurements.",
            "The first active row is normally the day after sowing and may include sowing fertilizer.",
            "Native APSIM signs and units are retained where the report schema did not assert a unit.",
            "The report diagnoses missing field-state information and does not define calibration bounds.",
        ],
    })
    archive_path = ROOT / f"oats_state_lineage_{stamp}.tar.gz"
    create_archive(archive_path, output)
    print(f"CASES: {len(lineage)}", flush=True)
    print(f"PAIRED YEARS: {', '.join(str(row['year']) for row in contrasts)}", flush=True)
    print(f"OUTPUT: {output}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
