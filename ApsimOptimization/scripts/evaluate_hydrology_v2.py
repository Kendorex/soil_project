#!/usr/bin/env python3
"""Compare the isolated BD-only run with baseline and apply hydrology gates."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.hydrology_quality import evaluate_run_hydrology_quality


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-output",
        type=Path,
        default=ROOT / "outputs" / "hydrology_v2_zlata_bd_only",
    )
    parser.add_argument(
        "--baseline-output",
        type=Path,
        default=ROOT / "outputs" / "cultivar_calibration_zlata_final_evaluation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "experiments" / "hydrology_v2" / "evaluation",
    )
    args = parser.parse_args()

    candidate_runs = read_csv(args.candidate_output / "calibration_runs.csv")
    daily = read_csv(args.candidate_output / "daily_report.csv")
    baseline = {row["year"]: row for row in read_csv(args.baseline_output / "calibration_runs.csv")}

    # Candidate 01 still inherits AirDry/LL15/DUL/Ks; its retention profile is not validated.
    retention_profile_validated = False
    quality_rows = []
    comparison_rows = []
    for run in candidate_runs:
        year = str(run["year"])
        year_daily = [row for row in daily if str(row.get("year")) == year]
        quality_rows.append(
            evaluate_run_hydrology_quality(
                run, year_daily, retention_profile_validated=retention_profile_validated
            )
        )
        before = baseline.get(year, {})
        old = float(before["simulated_yield_raw"]) / 1000 if before else None
        new = float(run["simulated_yield_raw"]) / 1000
        comparison_rows.append(
            {
                "year": year,
                "observed_yield_t_ha": run["observed_yield_t_ha"],
                "baseline_yield_t_ha": old,
                "measured_bd_candidate_yield_t_ha": new,
                "candidate_minus_baseline_t_ha": new - old if old is not None else None,
            }
        )

    write_csv(args.output_dir / "prediction_quality.csv", quality_rows)
    write_csv(args.output_dir / "yield_comparison.csv", comparison_rows)
    summary = {
        "candidate": "01_measured_bd_top30",
        "retention_profile_validated": False,
        "prediction_allowed_years": [row["year"] for row in quality_rows if row["prediction_allowed"]],
        "rejected_hydrology_failure_years": [
            row["year"] for row in quality_rows if row["quality_status"] == "rejected_hydrology_failure"
        ],
        "conclusion": "Measured bulk density alone does not repair the water balance.",
    }
    (args.output_dir / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Saved: {args.output_dir / 'prediction_quality.csv'}")
    print(f"Saved: {args.output_dir / 'yield_comparison.csv'}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
