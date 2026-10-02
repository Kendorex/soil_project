#!/usr/bin/env python3
"""Test a development-derived oat grain-number setting with and without N ceiling.

Only 2020-2022 cases are used to derive the grain-number command.  The 2023
cases remain an independent validation block.  The near-zero grain N profile
is a mathematical feasibility ceiling and can never be selected for the model.
"""
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
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_oats_biomass_source import (
    load_source_biomass_rows,
    model_phase_comparison,
)
from scripts.audit_oats_yield_components import source_grain_sizes
from scripts.diagnose_oats_grain_n_constraint_ceiling import latest_control, read_saved_control
from scripts.diagnose_oats_photosynthesis_ceiling import YEARS, run_profile
from scripts.evaluate_oats_component_validation import flatten_metrics
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint
from src.multifield_optimizer import ROOT, load_config
from src.oats_component_validation import (
    SCOPES,
    add_observed_grain_components,
    compare_candidate,
    evaluate_candidate,
)


GRAIN_NUMBER = "[Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue"
MINIMUM_GRAIN_N = "[Grain].MinimumNConc.FixedValue"
CURRENT_GRAINS_PER_GRAM_STEM = 45.0
MINIMUM_GRAIN_N_EPSILON = 1e-6
KNOWN_LIFECYCLE_FAILURE = (2021, "Field1")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def derive_development_grain_number(
    control_rows: list[dict[str, Any]], grain_source: dict[tuple[int, str], dict]
) -> tuple[float, list[dict[str, Any]]]:
    """Derive a robust command from development cases with measured TKW."""
    derivation = []
    usable_values = []
    for row in control_rows:
        year, field = int(row["year"]), str(row["field"])
        if not 2020 <= year <= 2022 or str(row.get("observed_cultivar", "")).casefold() != "яков":
            continue
        source = grain_source.get((year, field), {})
        observed_tkw = source.get("observed_thousand_grain_mass_g")
        observed_yield = float(row["observed_yield_t_ha"])
        model_number = float(row.get("final_grain_number_m2") or 0.0)
        exclusion = None
        if (year, field) == KNOWN_LIFECYCLE_FAILURE:
            exclusion = "known_2021_field1_lifecycle_failure"
        elif observed_tkw is None:
            exclusion = "missing_observed_tkw"
        elif model_number <= 0:
            exclusion = "missing_model_grain_number"
        if exclusion is None:
            implied_number = observed_yield * 100000.0 / float(observed_tkw)
            value = CURRENT_GRAINS_PER_GRAM_STEM * implied_number / model_number
            usable_values.append(value)
        else:
            implied_number, value = None, None
        derivation.append({
            "year": year,
            "field": field,
            "observed_yield_t_ha": observed_yield,
            "observed_thousand_grain_mass_g": observed_tkw,
            "observed_yield_implied_grain_number_m2": implied_number,
            "control_model_grain_number_m2": model_number,
            "current_grains_per_gram_stem": CURRENT_GRAINS_PER_GRAM_STEM,
            "derived_grains_per_gram_stem": value,
            "included_in_median": exclusion is None,
            "exclusion_reason": exclusion,
        })
    if not usable_values:
        raise ValueError("No usable 2020-2022 cases for grain-number derivation")
    return float(median(usable_values)), derivation


def comparison_rows(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for decision in decisions:
        for scope, comparison in decision["scope_comparisons"].items():
            for metric in comparison["metrics_compared"]:
                rows.append({
                    "candidate": decision["candidate"],
                    "overall_status": decision["overall_status"],
                    "scope": scope,
                    "scope_status": comparison["status"],
                    "metric": metric["metric"],
                    "reference_value": metric["reference"],
                    "candidate_value": metric["candidate"],
                    "delta": metric["delta"],
                })
    return rows


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "parameter_derivation.csv", "scope_metrics.csv",
        "component_case_predictions.csv", "biomass_phase_errors.csv",
        "pareto_comparison.csv", "profile_summary.json", "oats_history.csv",
        "report_schema.json", "config_snapshot.json", "model_inventory.json",
        "preparation.json", "apsim.log",
    }
    files = sorted(
        file for file in root.rglob("*")
        if file.is_file() and file.name in allowed
    )
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
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
    parser.add_argument("--control", type=Path)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "configs" / "multifield_historical_replay.yaml",
    )
    args = parser.parse_args(argv)
    config = load_config(args.config)
    observations = load_observations(config["historical_observations"])
    grain_source = source_grain_sizes(observations)
    biomass_source_rows, _ = load_source_biomass_rows(observations)
    year_records = {year: records_for_year(observations, year) for year in YEARS}
    oat_fields = tuple(dict.fromkeys(
        row["field"] for year in YEARS for row in year_records[year]
        if "Oats" in row.get("yield_components", [])
    ))

    control_path = args.control or latest_control()
    control_profile, _, control_rows = read_saved_control(control_path, year_records)
    derived_value, derivation = derive_development_grain_number(
        control_rows, grain_source
    )
    profiles = (
        {
            "name": "development_median_grain_number",
            "values": {GRAIN_NUMBER: derived_value},
            "meaning": "2020-2022 median data-derived grain number with current grain N constraint",
        },
        {
            "name": "development_median_grain_number_n_ceiling",
            "values": {
                GRAIN_NUMBER: derived_value,
                MINIMUM_GRAIN_N: MINIMUM_GRAIN_N_EPSILON,
            },
            "meaning": "same grain-number structure with an impossible near-zero grain N feasibility ceiling",
        },
    )
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    root = ROOT / "outputs" / "multifield" / "OATS_JOINT_GRAIN_STRUCTURE" / stamp
    root.mkdir(parents=True, exist_ok=False)
    write_csv(root / "parameter_derivation.csv", derivation)

    control_annotated = add_observed_grain_components(control_rows, grain_source)
    control_biomass = model_phase_comparison(
        control_path / "candidate.db", observations, biomass_source_rows
    )
    control_metrics = evaluate_candidate(control_annotated, control_biomass)
    results = [{
        "name": "transition_control",
        "profile": control_profile,
        "metrics": control_metrics,
        "run_directory": str(control_path),
    }]
    all_cases = [{"candidate": "transition_control", **row} for row in control_annotated]
    all_biomass = [{"candidate": "transition_control", **row} for row in control_biomass]
    metric_rows = flatten_metrics("transition_control", control_metrics)

    for index, profile in enumerate(profiles, 1):
        print(f"[{index}/{len(profiles)}] {profile['name']}", flush=True)
        run_result, _, summaries = run_profile(
            config, observations, year_records, oat_fields, profile,
            root / profile["name"],
        )
        annotated = add_observed_grain_components(summaries, grain_source)
        biomass = model_phase_comparison(
            root / profile["name"] / "candidate.db",
            observations, biomass_source_rows,
        )
        metrics = evaluate_candidate(annotated, biomass)
        results.append({
            "name": profile["name"],
            "profile": run_result,
            "metrics": metrics,
            "run_directory": str(root / profile["name"]),
        })
        all_cases.extend({"candidate": profile["name"], **row} for row in annotated)
        all_biomass.extend({"candidate": profile["name"], **row} for row in biomass)
        metric_rows.extend(flatten_metrics(profile["name"], metrics))
        dev = metrics["development_yakov_2020_2022"]["yield"]["rmse_t_ha"]
        val = metrics["validation_yakov_2023"]["yield"]["rmse_t_ha"]
        print(f"  development yield RMSE={dev:.3f}; 2023 RMSE={val:.3f}", flush=True)

    decisions = []
    for result in results[1:]:
        decision = compare_candidate(result["metrics"], control_metrics)
        decisions.append({"candidate": result["name"], **decision})

    write_csv(root / "scope_metrics.csv", metric_rows)
    write_csv(root / "component_case_predictions.csv", all_cases)
    write_csv(root / "biomass_phase_errors.csv", all_biomass)
    write_csv(root / "pareto_comparison.csv", comparison_rows(decisions))
    summary = {
        "experiment": "development_derived_oat_joint_grain_structure",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "control_run": str(control_path),
        "derived_grains_per_gram_stem": derived_value,
        "derivation_scope": "Yakov 2020-2022 excluding known Field1/2021 lifecycle failure",
        "profiles": profiles,
        "results": results,
        "decisions": decisions,
        "inputs": {
            "model": config["model"], "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"], "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "method": {
            "development_years": [2020, 2021, 2022],
            "validation_years": [2023],
            "selection": "strict Pareto comparison with no weights or normalization",
        },
        "interpretation_limits": [
            "Observed grain number is inferred from yield and TKW, not independently measured.",
            "Observed yield moisture basis is unknown.",
            "The transition control assumes complete export of new 2022 wheat residue.",
            "MinimumNConc=1e-6 is physiologically impossible and only tests structural feasibility.",
            "Neither candidate is applied to the production model automatically.",
        ],
    }
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    archive = ROOT / f"oats_joint_grain_structure_{stamp}.tar.gz"
    create_archive(archive, root)
    for decision in decisions:
        print(f"{decision['candidate']}: {decision['overall_status']}")
    print(f"DERIVED GRAINS PER GRAM STEM: {derived_value}")
    print(f"OUTPUT: {root}")
    print(f"SEND THIS ARCHIVE: {archive}")
    print(f"ARCHIVE SHA256: {fingerprint(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
