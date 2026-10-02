#!/usr/bin/env python3
"""Evaluate the isolated topsoil ensemble against yield and measured water."""

from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.hydrology_quality import evaluate_run_hydrology_quality

V2 = ROOT / "experiments" / "hydrology_v2"
MANIFEST = V2 / "ensemble" / "ensemble_manifest.json"
WATER_SUMMARY = V2 / "audit" / "topsoil_water_summary.csv"
OUTPUT = V2 / "ensemble_evaluation"


def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def rmse(errors):
    errors = list(errors)
    return math.sqrt(sum(value * value for value in errors) / len(errors)) if errors else None


def date_only(value):
    return datetime.fromisoformat(str(value)).date().isoformat()


def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    measured_water = {
        row["measurement_date"]: float(row["volumetric_water_mean_m3_m3"])
        for row in read_csv(WATER_SUMMARY)
        if row["year"] == "2023" and row["field"] == "3"
    }
    run_rows = []
    water_rows = []
    candidate_rows = []
    for candidate in manifest["candidates"]:
        candidate_id = candidate["candidate_id"]
        result_dir = ROOT / "outputs" / f"hydrology_v2_{candidate_id}"
        runs = read_csv(result_dir / "calibration_runs.csv")
        daily = read_csv(result_dir / "daily_report.csv")
        daily_by_year = {
            year: [row for row in daily if row["year"] == year]
            for year in {row["year"] for row in daily}
        }
        yield_errors_calibration = []
        failed_years = []
        for run in runs:
            quality = evaluate_run_hydrology_quality(
                run,
                daily_by_year.get(run["year"], []),
                retention_profile_validated=False,
            )
            simulated = float(run["simulated_yield_raw"]) / 1000.0
            observed = float(run["observed_yield_t_ha"])
            if int(run["year"]) in (2020, 2021, 2022):
                yield_errors_calibration.append(simulated - observed)
            if quality["quality_status"] == "rejected_hydrology_failure":
                failed_years.append(int(run["year"]))
            run_rows.append(
                {
                    "candidate_id": candidate_id,
                    "year": run["year"],
                    "observed_yield_t_ha": observed,
                    "simulated_yield_t_ha": simulated,
                    "yield_error_t_ha": simulated - observed,
                    "max_lai": quality["max_lai"],
                    "establishment_severe_stress_streak_days": quality["establishment_severe_stress_streak_days"],
                    "quality_status": quality["quality_status"],
                }
            )

        simulated_by_date = {
            date_only(row["Clock.Today"]): row
            for row in daily_by_year.get("2023", [])
        }
        water_errors = []
        for measurement_date, observed in measured_water.items():
            row = simulated_by_date.get(measurement_date)
            if row is None:
                continue
            simulated = (15.0 * float(row["SW(1)"]) + 5.0 * float(row["SW(2)"])) / 20.0
            water_errors.append(simulated - observed)
            water_rows.append(
                {
                    "candidate_id": candidate_id,
                    "measurement_date": measurement_date,
                    "observed_sw_0_20_m3_m3": observed,
                    "simulated_sw_0_20_m3_m3": simulated,
                    "error_m3_m3": simulated - observed,
                }
            )
        validation = next(row for row in run_rows if row["candidate_id"] == candidate_id and row["year"] == "2023")
        candidate_rows.append(
            {
                "candidate_id": candidate_id,
                "top30_airdry_m3_m3": candidate["top30_airdry_m3_m3"],
                "top30_ll15_m3_m3": candidate["top30_ll15_m3_m3"],
                "top30_dul_m3_m3": candidate["top30_dul_m3_m3"],
                "calibration_yield_rmse_t_ha_2020_2022": rmse(yield_errors_calibration),
                "water_rmse_m3_m3_2023": rmse(water_errors),
                "simulated_yield_t_ha_2023": validation["simulated_yield_t_ha"],
                "yield_error_t_ha_2023": validation["yield_error_t_ha"],
                "hydrology_failure_years": "|".join(map(str, failed_years)),
                "deep_profile_status": candidate["deep_profile_status"],
                "prediction_allowed": False,
            }
        )

    candidate_rows.sort(key=lambda row: (row["calibration_yield_rmse_t_ha_2020_2022"], row["water_rmse_m3_m3_2023"]))
    for rank, row in enumerate(candidate_rows, 1):
        row["calibration_yield_rank"] = rank
    write_csv(OUTPUT / "ensemble_runs.csv", run_rows)
    write_csv(OUTPUT / "moisture_comparison_2023.csv", water_rows)
    write_csv(OUTPUT / "ensemble_candidates.csv", candidate_rows)
    best = candidate_rows[0]
    summary = {
        "candidate_count": len(candidate_rows),
        "run_count": len(run_rows),
        "best_calibration_yield_candidate": best["candidate_id"],
        "best_candidate_metrics": best,
        "prediction_candidate_selected": None,
        "reason_not_selected": (
            "The experiment changes only 0-30 cm; the 30-210 cm profile, Ks, "
            "and groundwater representation remain unvalidated."
        ),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "ensemble_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Evaluated {len(run_rows)} runs from {len(candidate_rows)} candidates")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
