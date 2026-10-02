#!/usr/bin/env python3
"""Screen exact APSIM oat source/sink factors with blocked years."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.screen_oats_phenology_profiles import (YEARS, create_archive, run_profile,
                                                     write_csv)
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint, write_json
from src.multifield_optimizer import ROOT, load_config
from src.oats_source import source_candidate_comparison


LEAF_AREA = "[Leaf].CohortParameters.MaxArea.AreaLargestLeaves.FixedValue"
SENESCENCE = "[Leaf].CohortParameters.SenescenceDuration.Multiplier.FixedValue"
EXTINCTION = "[Leaf].ExtinctionCoeff.FixedValue"
BRANCH_COVER = "[Structure].BranchingRate.StressFactors.CoverEffect.XYPairs.X"
BRANCH_X = (
    "[Structure].BranchingRate.PotentialBranchingRate.Vegetative."
    "PotentialBranchingRate.XYPairs.X"
)
BRANCH_Y = (
    "[Structure].BranchingRate.PotentialBranchingRate.Vegetative."
    "PotentialBranchingRate.XYPairs.Y"
)
GRAIN_SIZE = "[Grain].MaximumPotentialGrainSize.FixedValue"
GRAIN_NUMBER = "[Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue"


# Values are copied verbatim from Drummond or Milton in the installed
# Models/Resources/Oats.json. They are discrete mechanism tests, not bounds.
PROFILES = (
    {"name": "current_yakov", "source": "current Yakov control", "values": {}},
    {"name": "leaf_area_4000", "source": "Drummond and Milton",
     "values": {LEAF_AREA: 4000}},
    {"name": "senescence_duration_3", "source": "Milton",
     "values": {SENESCENCE: 3}},
    {"name": "extinction_0_6", "source": "Milton",
     "values": {EXTINCTION: 0.6}},
    {"name": "drummond_branching", "source": "Drummond branching mechanism",
     "values": {
         BRANCH_COVER: "0,0.2,0.35",
         BRANCH_X: "1,2,3.99,4,5,6,7,8,9",
         BRANCH_Y: "0,0,0,1,2,4,7,12,20",
     }},
    {"name": "maximum_grain_size_0_05", "source": "Drummond and Milton",
     "values": {GRAIN_SIZE: 0.05}},
    {"name": "grains_per_stem_40", "source": "Drummond",
     "values": {GRAIN_NUMBER: 40}},
    {"name": "grains_per_stem_50", "source": "Milton",
     "values": {GRAIN_NUMBER: 50}},
)


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
    year_records = {year: records_for_year(observations, year) for year in YEARS}
    oat_fields = tuple(dict.fromkeys(
        row["field"] for year in YEARS for row in year_records[year]
        if "Oats" in row.get("yield_components", [])
    ))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = ROOT / "outputs" / "multifield" / "OATS_SOURCE_FACTORS" / stamp
    root.mkdir(parents=True, exist_ok=False)

    candidates, metric_rows, case_rows = [], [], []
    for index, profile in enumerate(PROFILES, 1):
        print(f"[{index}/{len(PROFILES)}] {profile['name']}", flush=True)
        candidate, rows = run_profile(
            config, observations, year_records, oat_fields, profile,
            root / profile["name"],
        )
        candidates.append(candidate)
        for row in rows:
            case_rows.append({"candidate": profile["name"], **row})
        for scope, metrics in candidate["metrics"].items():
            metric_rows.append({
                "candidate": profile["name"], "scope": scope,
                "changed_commands": ";".join(profile["values"]),
                "reference_source": profile["source"],
                "yield_mae_t_ha": metrics["yield_mae_t_ha"],
                "yield_rmse_t_ha": metrics["yield_rmse_t_ha"],
                "yield_bias_t_ha": metrics["yield_bias_t_ha"],
                "tillering_mae_days": metrics["tillering"]["mae_days"],
                "heading_mae_days": metrics["heading"]["mae_days"],
            })
        dev = candidate["metrics"]["development_yakov_2020_2022"]
        val = candidate["metrics"]["validation_yakov_2023"]
        print(
            f"  development RMSE={dev['yield_rmse_t_ha']:.3f}; "
            f"2023 RMSE={val['yield_rmse_t_ha']:.3f}", flush=True,
        )

    comparison = source_candidate_comparison(candidates)
    write_csv(root / "candidate_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", case_rows)
    write_json(root / "summary.json", {
        "experiment": "oats_source_sink_factor_screen",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "historical_n_labels_replayed": True,
        "candidate_values_are_exact_local_apsim_references": True,
        "candidate_values_are_not_calibration_bounds": True,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"],
            "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "profiles": PROFILES,
        "comparison": comparison,
        "interpretation_limits": [
            "2019 Argamak remains context only and is excluded from Yakov development metrics.",
            "No observed LAI or biomass series are available; those outputs diagnose mechanisms only.",
            "Selection is based on yield RMSE in development and validation periods without weights.",
            "No candidate is applied to the calibrated model automatically.",
        ],
    })
    archive = ROOT / f"oats_source_factors_{stamp}.tar.gz"
    create_archive(archive, root)
    print(
        "DOMINATES CURRENT: "
        + (", ".join(comparison["candidates_dominating_current"]) or "none"),
        flush=True,
    )
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
