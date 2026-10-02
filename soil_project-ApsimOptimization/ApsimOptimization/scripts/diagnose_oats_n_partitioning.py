#!/usr/bin/env python3
"""Compare grain formation at the observed oat N30 and N60 sowing rates."""
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
from src.multifield_model import fingerprint, inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_results import read_results
from src.oats_n_partitioning import compare_doses, patch_fertilizer_amount_on_date
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports


YEAR = 2023


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_dose(config, observations, records, fields, dose, output):
    output.mkdir(parents=True, exist_ok=False)
    data = read_json(config["model"])
    report_changes = patch_oats_diagnostic_reports(data, fields)
    sow_dates = {row["observed_sow_date"] for row in records if row["field"] in fields}
    if len(sow_dates) != 1 or None in sow_dates:
        raise ValueError(f"Expected one confirmed oat sowing date, found {sorted(sow_dates)}")
    sow_date = dt.date.fromisoformat(next(iter(sow_dates)))
    fertilizer_changes = patch_fertilizer_amount_on_date(data, fields, sow_date, dose)
    source = output / "source_candidate.apsimx"
    write_json(source, data)
    case = dict(config)
    case["model"] = str(source.resolve())
    case["model_provenance"] = None
    case["historical_replay"] = dict(config["historical_replay"], enabled=False)
    case["scenario"] = {
        "kind": "diagnostic",
        "name": f"oats_n{dose:g}_partitioning_2023",
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)
    result = read_results(database, meta, YEAR, case, audit_applications=False)
    comparison = compare_historical(result, meta, observations, YEAR)
    write_json(output / "historical_comparison.json", comparison)
    daily, summaries, schema = collect_oats_diagnostics(
        database, meta, records, year=YEAR, scenario=f"N{dose:g}"
    )
    for row in daily:
        row["diagnostic_n_kg_ha"] = dose
    for row in summaries:
        row["diagnostic_n_kg_ha"] = dose
    write_csv(output / "daily_oats_2023.csv", daily)
    write_csv(output / "field_summary.csv", summaries)
    write_json(output / "report_schema.json", schema)
    write_json(output / "scenario_summary.json", {
        "diagnostic_n_kg_ha": dose,
        "sowing_date": sow_date.isoformat(),
        "fertilizer_changes": fertilizer_changes,
        "report_changes": report_changes,
        "fields": summaries,
    })
    return summaries


def create_archive(path: Path, root: Path) -> None:
    names = {
        "summary.json", "dose_response.csv", "dose_deltas.csv",
        "config_snapshot.json", "model_inventory.json", "preparation.json",
        "historical_comparison.json", "daily_oats_2023.csv", "field_summary.csv",
        "report_schema.json", "scenario_summary.json", "apsim.log",
    }
    files = sorted(
        file for file in root.rglob("*") if file.is_file() and file.name in names
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
    records = records_for_year(observations, YEAR)
    oat_records = [row for row in records if "Oats" in row.get("yield_components", [])]
    fields = tuple(row["field"] for row in oat_records)
    doses = sorted({
        parse_technology_nutrients(row.get("technology"))["N"] for row in oat_records
        if parse_technology_nutrients(row.get("technology"))["N"] is not None
    })
    if doses != [30.0, 60.0]:
        raise ValueError(f"Expected observed oat N labels [30, 60], found {doses}")

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "outputs" / "multifield" / "OATS_N_PARTITIONING_DIAGNOSTIC" / stamp
    root.mkdir(parents=True, exist_ok=False)
    all_rows = []
    for index, dose in enumerate(doses, 1):
        print(f"[{index}/2] APSIM physiology at observed rate N{dose:g}", flush=True)
        all_rows.extend(run_dose(
            config, observations, records, fields, dose, root / f"n_{dose:g}"
        ))
    deltas = compare_doses(all_rows, doses[0], doses[1])
    write_csv(root / "dose_response.csv", all_rows)
    write_csv(root / "dose_deltas.csv", deltas)
    payload = {
        "experiment": "observed_range_oats_n_partitioning_2023",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "tested_n_kg_ha": doses,
        "dose_source": "explicit N30 and N60 labels in supplied 2023 oat observations",
        "application_event": "existing model sowing event on 2023-05-04",
        "fertilizer_type": "preserved from APSIM field manager",
        "base_model_changed": False,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "fields": all_rows,
        "n60_minus_n30": deltas,
        "limitations": [
            "The two rates are mechanism tests taken from observed labels, not optimization bounds.",
            "P and K response is not simulated.",
            "No cultivar or soil parameter is changed.",
        ],
    }
    write_json(root / "summary.json", payload)
    archive = ROOT / f"oats_n_partitioning_{stamp}.tar.gz"
    create_archive(archive, root)
    for row in deltas:
        print(
            f"{row['field']}: N60-N30 yield="
            f"{row['simulated_yield_t_ha_change']:+.3f} t/ha; "
            f"grain size={row['final_grain_size_g_change']:+.5f} g; "
            f"Fn stress days={row['nitrogen_stress_days_fn_below_0_8_change']:+.0f}",
            flush=True,
        )
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

