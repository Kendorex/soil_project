#!/usr/bin/env python3
"""Screen one oat phenology command at a time using exact APSIM references."""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.screen_oats_phenology_profiles import (EARLY_BASE, EARLY_PP, MIN_LEAVES,
                                                     PP, PHYLLOCHRON, VRN, YEARS,
                                                     create_archive, run_profile,
                                                     write_csv)
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint, write_json
from src.multifield_optimizer import ROOT, load_config
from src.oats_phenology import pareto_comparison


# Each candidate changes exactly one Yakov command to a value copied verbatim
# from Drummond or Milton in the installed Models/Resources/Oats.json.
PROFILES = (
    {"name": "current_yakov", "source": "current Yakov control", "values": {}},
    {"name": "minimum_leaf_number_8_5", "source": "Drummond and Milton",
     "values": {MIN_LEAVES: 8.5}},
    {"name": "vernalisation_sensitivity_7", "source": "Drummond and Milton",
     "values": {VRN: 7}},
    {"name": "photoperiod_sensitivity_3", "source": "Drummond and Milton",
     "values": {PP: 3}},
    {"name": "early_reproductive_base_3", "source": "Drummond",
     "values": {EARLY_BASE: 3}},
    {"name": "early_reproductive_base_2", "source": "Milton",
     "values": {EARLY_BASE: 2}},
    {"name": "early_reproductive_pp_2", "source": "Drummond",
     "values": {EARLY_PP: 2}},
    {"name": "early_reproductive_pp_0", "source": "Milton",
     "values": {EARLY_PP: 0}},
    {"name": "phyllochron_75", "source": "Drummond",
     "values": {PHYLLOCHRON: 75}},
    {"name": "phyllochron_105", "source": "Milton",
     "values": {PHYLLOCHRON: 105}},
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
    root = ROOT / "outputs" / "multifield" / "OATS_PHENOLOGY_FACTORS" / stamp
    root.mkdir(parents=True, exist_ok=False)

    candidates, case_rows, metric_rows = [], [], []
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
                "changed_command": next(iter(profile["values"]), None),
                "changed_value": next(iter(profile["values"].values()), None),
                "reference_source": profile["source"],
                "yield_mae_t_ha": metrics["yield_mae_t_ha"],
                "yield_rmse_t_ha": metrics["yield_rmse_t_ha"],
                "yield_bias_t_ha": metrics["yield_bias_t_ha"],
                "tillering_mae_days": metrics["tillering"]["mae_days"],
                "tillering_bias_days": metrics["tillering"]["bias_days"],
                "heading_mae_days": metrics["heading"]["mae_days"],
                "heading_bias_days": metrics["heading"]["bias_days"],
            })
        dev = candidate["metrics"]["development_yakov_2020_2022"]
        val = candidate["metrics"]["validation_yakov_2023"]
        print(
            f"  dev RMSE={dev['yield_rmse_t_ha']:.3f}; "
            f"tillering={dev['tillering']['mae_days']:.2f} d; "
            f"heading={dev['heading']['mae_days']:.2f} d; "
            f"2023 RMSE={val['yield_rmse_t_ha']:.3f}", flush=True,
        )

    comparison = pareto_comparison(candidates)
    write_csv(root / "candidate_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", case_rows)
    write_json(root / "summary.json", {
        "experiment": "oats_one_factor_phenology_screen",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "one_command_changed_per_candidate": True,
        "candidate_values_are_exact_local_apsim_references": True,
        "candidate_values_are_not_calibration_bounds": True,
        "historical_n_labels_replayed": True,
        "inputs": {
            "model": config["model"],
            "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"],
            "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "profiles": PROFILES,
        "comparison": comparison,
        "interpretation_limits": [
            "2019 Argamak is context only and is excluded from Yakov development metrics.",
            "Effects are screened one at a time; interactions are not inferred from this run.",
            "Yield and date errors are not combined into a normalized or weighted score.",
            "No candidate is applied to the calibrated model automatically.",
        ],
    })
    archive = ROOT / f"oats_phenology_factors_{stamp}.tar.gz"
    create_archive(archive, root)
    print(
        "NONDOMINATED: " + ", ".join(comparison["nondominated_candidates"]),
        flush=True,
    )
    print(f"OUTPUT: {root}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
