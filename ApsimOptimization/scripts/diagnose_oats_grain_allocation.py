#!/usr/bin/env python3
"""Compare oat grain allocation with historically well reproduced cases.

The script reuses saved physiology CSV files.  It does not run APSIM or change
the calibrated model.  The implied harvest index is a diagnostic ratio based
on observed yield and simulated maximum above-ground biomass; it is not a
calibration target because the source workbook does not state the yield
moisture basis.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import tarfile
from pathlib import Path
from statistics import fmean


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORICAL = (
    ROOT / "outputs" / "multifield" / "OATS_SOURCE_FACTORS"
    / "20260928_112031" / "current_yakov" / "oats_history.csv"
)
DEFAULT_CANDIDATE = (
    ROOT / "outputs" / "multifield" / "OATS_WHEAT_RESIDUE"
    / "20260929_120748" / "documented_2022_harvest_export_new_residue"
    / "oats_2023.csv"
)


def number(row: dict[str, str], name: str) -> float:
    value = row.get(name, "")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name}: expected a number, got {value!r}") from error
    if not math.isfinite(result):
        raise ValueError(f"{name}: expected a finite number, got {value!r}")
    return result


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def diagnostic_row(row: dict[str, str], scenario: str, source: Path) -> dict:
    observed = number(row, "observed_yield_t_ha")
    simulated = number(row, "simulated_yield_t_ha")
    biomass = number(row, "max_aboveground_biomass_g_m2")
    harvest_index = number(row, "simulated_harvest_index")
    implied_hi = observed * 100.0 / biomass if biomass > 0 else math.nan
    error = simulated - observed
    return {
        "scenario": scenario,
        "year": int(number(row, "year")),
        "field": row["field"],
        "observed_yield_t_ha": observed,
        "simulated_yield_t_ha": simulated,
        "error_t_ha": error,
        "absolute_error_t_ha": abs(error),
        "max_aboveground_biomass_g_m2": biomass,
        "simulated_harvest_index": harvest_index,
        "observed_yield_implied_hi_on_simulated_biomass": implied_hi,
        "additional_hi_needed_on_simulated_biomass": implied_hi - harvest_index,
        "final_grain_size_g": number(row, "final_grain_size_g"),
        "grain_potential_allocation_to_demand_ratio": number(
            row, "grain_potential_allocation_to_demand_ratio"
        ),
        "grain_n_allocation_to_demand_ratio": number(
            row, "grain_n_allocation_to_demand_ratio"
        ),
        "grain_fill_nitrogen_stress_days": int(number(
            row, "grain_fill_nitrogen_stress_days_fn_below_0_8"
        )),
        "source_csv": str(source),
    }


def mean(rows: list[dict], key: str) -> float | None:
    values = [float(row[key]) for row in rows if math.isfinite(float(row[key]))]
    return fmean(values) if values else None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def archive_output(output: Path) -> Path:
    archive_path = ROOT / f"oats_grain_allocation_{output.name}.tar.gz"
    files = sorted(file for file in output.iterdir() if file.is_file())
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [{
            "path": file.name,
            "size": file.stat().st_size,
            "sha256": sha256(file),
        } for file in files],
    }
    with tarfile.open(archive_path, "w:gz") as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for file in files:
            archive.add(file, arcname=file.name, recursive=False)
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-csv", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--candidate-csv", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--near-match-error", type=float, default=0.2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = args.output or (
        ROOT / "outputs" / "multifield" / "OATS_GRAIN_ALLOCATION" / timestamp
    )
    output.mkdir(parents=True, exist_ok=False)

    historical = [
        diagnostic_row(row, "current_historical_model", args.historical_csv)
        for row in read_csv(args.historical_csv)
        if number(row, "simulated_yield_t_ha") > 0
    ]
    candidate = [
        diagnostic_row(
            row,
            "documented_2022_harvest_and_fresh_straw_export_diagnostic",
            args.candidate_csv,
        )
        for row in read_csv(args.candidate_csv)
    ]
    rows = historical + candidate
    write_csv(output / "case_comparison.csv", rows)

    development = [row for row in historical if 2020 <= row["year"] <= 2022]
    near_matches = [
        row for row in development
        if row["absolute_error_t_ha"] <= args.near_match_error
    ]
    validation_baseline = [row for row in historical if row["year"] == 2023]
    summary = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "purpose": "separate 2023 biomass limitation from global oat grain-allocation error",
        "near_match_definition": (
            f"historical 2020-2022 cases with absolute yield error <= "
            f"{args.near_match_error:g} t/ha"
        ),
        "near_match_cases": [f"{row['field']}/{row['year']}" for row in near_matches],
        "groups": {
            "historical_near_matches": {
                "n": len(near_matches),
                "mean_simulated_harvest_index": mean(
                    near_matches, "simulated_harvest_index"
                ),
                "mean_observed_yield_implied_hi": mean(
                    near_matches, "observed_yield_implied_hi_on_simulated_biomass"
                ),
                "mean_grain_n_allocation_to_demand_ratio": mean(
                    near_matches, "grain_n_allocation_to_demand_ratio"
                ),
            },
            "validation_2023_baseline": {
                "n": len(validation_baseline),
                "mean_absolute_error_t_ha": mean(
                    validation_baseline, "absolute_error_t_ha"
                ),
                "mean_simulated_harvest_index": mean(
                    validation_baseline, "simulated_harvest_index"
                ),
            },
            "validation_2023_transition_candidate": {
                "n": len(candidate),
                "mean_absolute_error_t_ha": mean(candidate, "absolute_error_t_ha"),
                "mean_simulated_harvest_index": mean(
                    candidate, "simulated_harvest_index"
                ),
                "mean_observed_yield_implied_hi": mean(
                    candidate, "observed_yield_implied_hi_on_simulated_biomass"
                ),
                "mean_grain_n_allocation_to_demand_ratio": mean(
                    candidate, "grain_n_allocation_to_demand_ratio"
                ),
            },
        },
        "interpretation_limits": [
            "Observed yield moisture basis is not stated in the source workbook.",
            "Observed yield implied HI uses simulated maximum above-ground biomass.",
            "The transition candidate assumes complete export of newly created wheat harvest residue; this management is not confirmed.",
            "These ratios diagnose mechanisms and are not accepted calibration parameters.",
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    archive = archive_output(output)
    print(json.dumps({
        "output": str(output),
        "archive": str(archive),
        "archive_sha256": sha256(archive),
        "near_match_cases": summary["near_match_cases"],
        "groups": summary["groups"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
