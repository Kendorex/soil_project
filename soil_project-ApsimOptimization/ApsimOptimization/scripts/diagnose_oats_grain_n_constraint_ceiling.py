#!/usr/bin/env python3
"""Test the mathematical yield ceiling with near-zero grain minimum N."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_oats_yield_components import source_grain_sizes
from scripts.diagnose_oats_grain_fill_budget import build_case_budget
from scripts.diagnose_oats_photosynthesis_ceiling import (
    CASES, YEARS, make_archive, run_profile, write_csv,
)
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint
from src.multifield_optimizer import ROOT, load_config
from src.oats_physiology import collect_oats_diagnostics


MINIMUM_GRAIN_N = "[Grain].MinimumNConc.FixedValue"
MINIMUM_GRAIN_N_EPSILON = 1e-6
PROFILE = {
    "name": "grain_minimum_n_epsilon_ceiling",
    "values": {MINIMUM_GRAIN_N: MINIMUM_GRAIN_N_EPSILON},
    "meaning": "mathematical upper bound with a positive near-zero grain N constraint",
}
BASELINE_ROOT = ROOT / "outputs" / "multifield" / "OATS_PHOTOSYNTHESIS_CEILING"


def latest_control() -> Path:
    candidates = [
        run / "transition_control"
        for run in BASELINE_ROOT.glob("*") if run.is_dir()
        and (run / "transition_control" / "candidate.db").exists()
        and (run / "transition_control" / "profile_summary.json").exists()
    ]
    if not candidates:
        raise FileNotFoundError(
            "No saved transition_control run found; run "
            "scripts/diagnose_oats_photosynthesis_ceiling.py first"
        )
    return max(candidates, key=lambda path: path.parent.stat().st_mtime)


def read_saved_control(control: Path, year_records: dict):
    inventory = json.loads(
        (control / "model_inventory.json").read_text(encoding="utf-8")
    )
    daily, summaries = [], []
    for year in YEARS:
        year_daily, year_summaries, _ = collect_oats_diagnostics(
            control / "candidate.db", inventory, year_records[year],
            year=year, scenario="transition_control",
        )
        daily.extend(year_daily)
        summaries.extend(year_summaries)
    profile = json.loads(
        (control / "profile_summary.json").read_text(encoding="utf-8")
    )
    return profile, daily, summaries


def metric_rows(candidate: str, metrics: dict) -> list[dict]:
    rows = []
    for scope, values in metrics.items():
        rows.append({
            "candidate": candidate, "scope": scope,
            "yield_mae_t_ha": values["yield_mae_t_ha"],
            "yield_rmse_t_ha": values["yield_rmse_t_ha"],
            "yield_bias_t_ha": values["yield_bias_t_ha"],
            "tillering_mae_days": values["tillering"]["mae_days"],
            "heading_mae_days": values["heading"]["mae_days"],
        })
    return rows


def budgets(candidate: str, daily: list[dict], summaries: list[dict],
            grain_source: dict) -> tuple[list[dict], list[dict]]:
    rows, selected = [], []
    for year, field, reference_scope in CASES:
        budget, chosen = build_case_budget(
            candidate, year, field, daily, summaries, grain_source
        )
        budget["reference_scope"] = reference_scope
        rows.append(budget)
        selected.extend(chosen)
    return rows, selected


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--control", type=Path)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "configs" / "multifield_historical_replay.yaml",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    observations = load_observations(config["historical_observations"])
    grain_source = source_grain_sizes(observations)
    year_records = {year: records_for_year(observations, year) for year in YEARS}
    oat_fields = tuple(dict.fromkeys(
        row["field"] for year in YEARS for row in year_records[year]
        if "Oats" in row.get("yield_components", [])
    ))

    control = args.control or latest_control()
    control_profile, control_daily, control_summaries = read_saved_control(
        control, year_records
    )
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    root = ROOT / "outputs" / "multifield" / "OATS_GRAIN_N_CEILING" / stamp
    root.mkdir(parents=True, exist_ok=False)

    if PROFILE["values"][MINIMUM_GRAIN_N] <= 0:
        raise ValueError(
            "MinimumNConc must remain positive: APSIM assigns zero constrained "
            "growth when MinNConc is zero"
        )
    print("[1/1] grain_minimum_n_epsilon_ceiling", flush=True)
    candidate, candidate_daily, candidate_summaries = run_profile(
        config, observations, year_records, oat_fields, PROFILE,
        root / PROFILE["name"],
    )
    control_budget, control_selected = budgets(
        "transition_control", control_daily, control_summaries, grain_source
    )
    candidate_budget, candidate_selected = budgets(
        PROFILE["name"], candidate_daily, candidate_summaries, grain_source
    )

    write_csv(
        root / "candidate_metrics.csv",
        metric_rows("transition_control", control_profile["metrics"])
        + metric_rows(PROFILE["name"], candidate["metrics"]),
    )
    write_csv(
        root / "case_predictions.csv",
        [{"candidate": "transition_control", **row} for row in control_summaries]
        + [{"candidate": PROFILE["name"], **row} for row in candidate_summaries],
    )
    write_csv(root / "grain_fill_budget.csv", control_budget + candidate_budget)
    write_csv(root / "daily_grain_fill.csv", control_selected + candidate_selected)
    (root / "summary.json").write_text(json.dumps({
        "experiment": "oats_near_zero_minimum_grain_n_upper_bound",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "profile": PROFILE,
        "control_run": str(control),
        "control_database_sha256": fingerprint(control / "candidate.db"),
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "interpretation_limits": [
            "MinimumNConc=1e-6 is physiologically impossible and is only a numerical ceiling.",
            "Exact zero is prohibited because APSIM explicitly sets constrained growth to zero when MinNConc is zero.",
            "The source workbook contains no measured grain protein or grain N concentration.",
            "The 2023 transition assumes complete export of newly created 2022 wheat harvest residue.",
            "The candidate must not be copied into the production model.",
        ],
        "control": control_profile,
        "candidate": candidate,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = ROOT / f"oats_grain_n_ceiling_{stamp}.tar.gz"
    make_archive(archive, root)
    dev = candidate["metrics"]["development_yakov_2020_2022"]
    val = candidate["metrics"]["validation_yakov_2023"]
    print(
        f"  development RMSE={dev['yield_rmse_t_ha']:.3f}; "
        f"2023 RMSE={val['yield_rmse_t_ha']:.3f}", flush=True,
    )
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    print(f"ARCHIVE SHA256: {fingerprint(archive)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
