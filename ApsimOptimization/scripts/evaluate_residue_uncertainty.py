#!/usr/bin/env python3
"""Evaluate the 2023 yield envelope for two explicit wheat-residue scenarios."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import math
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import compare_historical, load_observations, write_comparison_csv
from src.multifield_model import inventory, prepare, read_json, write_json
from src.multifield_optimizer import (
    ROOT, environment_config, environment_scenarios, load_config,
    require_compatible_environment, run_apsim,
)
from src.multifield_results import read_results


DEFAULT_CONFIG = ROOT / "configs" / "multifield_historical_residue_uncertainty.yaml"


def error_metrics(rows: list[dict]) -> dict:
    errors = [
        float(row["simulated_yield_t_ha"]) - float(row["observed_yield_t_ha"])
        for row in rows
    ]
    if not errors:
        return {"n": 0, "mae_t_ha": None, "rmse_t_ha": None, "bias_t_ha": None}
    return {
        "n": len(errors),
        "mae_t_ha": sum(abs(value) for value in errors) / len(errors),
        "rmse_t_ha": math.sqrt(sum(value * value for value in errors) / len(errors)),
        "bias_t_ha": sum(errors) / len(errors),
    }


def oat_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if "Oats" in (row.get("yield_components") or [])]


def build_field_ranges(comparisons: dict[str, dict], scenario_order: list[str]) -> list[dict]:
    if len(scenario_order) != 2:
        raise ValueError("Exactly two residue scenarios are required")
    indexed = {
        name: {row["field"]: row for row in comparisons[name]["fields"]}
        for name in scenario_order
    }
    if set(indexed[scenario_order[0]]) != set(indexed[scenario_order[1]]):
        raise ValueError("Residue scenarios returned different field sets")
    comparison_year = comparisons[scenario_order[0]].get("year")
    output = []
    for field in sorted(indexed[scenario_order[0]], key=lambda value: int(value.removeprefix("Field"))):
        first = indexed[scenario_order[0]][field]
        observed = float(first["observed_yield_t_ha"])
        predictions = {
            name: float(indexed[name][field]["simulated_yield_t_ha"])
            for name in scenario_order
        }
        low, high = min(predictions.values()), max(predictions.values())
        row_year = first.get("year", comparison_year)
        if row_year is None:
            raise ValueError("Historical comparison does not contain a year")
        output.append({
            "year": int(row_year),
            "field": field,
            "crop_group": first["crop_group"],
            "observed_yield_t_ha": observed,
            f"{scenario_order[0]}_yield_t_ha": predictions[scenario_order[0]],
            f"{scenario_order[1]}_yield_t_ha": predictions[scenario_order[1]],
            "scenario_min_yield_t_ha": low,
            "scenario_max_yield_t_ha": high,
            "scenario_range_width_t_ha": high - low,
            "observed_inside_scenario_range": low <= observed <= high,
            "observed_distance_to_range_t_ha": (
                0.0 if low <= observed <= high else min(abs(observed - low), abs(observed - high))
            ),
        })
    return output


def verify_target_scope(rows: list[dict], target_fields: set[str], tolerance: float = 1e-9) -> dict:
    unexpected = [
        {"field": row["field"], "range_width_t_ha": row["scenario_range_width_t_ha"]}
        for row in rows
        if row["field"] not in target_fields
        and abs(float(row["scenario_range_width_t_ha"])) > tolerance
    ]
    if unexpected:
        raise ValueError(f"Residue scenarios changed non-target fields: {unexpected}")
    changed_targets = [
        row["field"] for row in rows
        if row["field"] in target_fields
        and abs(float(row["scenario_range_width_t_ha"])) > tolerance
    ]
    return {
        "target_fields": sorted(target_fields),
        "changed_target_fields": sorted(changed_targets),
        "non_target_fields_unchanged": True,
        "comparison_tolerance_t_ha": tolerance,
    }


def envelope_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "inside_range_n": 0, "inside_range_fraction": None,
                "mean_range_width_t_ha": None, "mean_distance_to_range_t_ha": None}
    return {
        "n": len(rows),
        "inside_range_n": sum(bool(row["observed_inside_scenario_range"]) for row in rows),
        "inside_range_fraction": (
            sum(bool(row["observed_inside_scenario_range"]) for row in rows) / len(rows)
        ),
        "mean_range_width_t_ha": (
            sum(float(row["scenario_range_width_t_ha"]) for row in rows) / len(rows)
        ),
        "mean_distance_to_range_t_ha": (
            sum(float(row["observed_distance_to_range_t_ha"]) for row in rows) / len(rows)
        ),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "evaluation.json", "field_ranges.csv", "scenario_metrics.csv",
        "historical_comparison.json", "historical_comparison.csv",
        "baseline.json", "config_snapshot.json", "model_inventory.json",
        "preparation.json", "apsim.log",
    }
    files = sorted(file for file in root.rglob("*") if file.is_file() and file.name in allowed)
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [{"path": file.relative_to(root).as_posix(), "size": file.stat().st_size}
                  for file in files],
    }
    with tarfile.open(path, "w:gz") as archive:
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("archive_manifest.json")
        info.size = len(data)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(data))
        for file in files:
            archive.add(file, arcname=file.relative_to(root).as_posix(), recursive=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true")
    mode.add_argument(
        "--resume", type=Path, metavar="EVALUATION_DIR",
        help="Build reports from an evaluation directory whose APSIM runs already finished",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    scenarios = environment_scenarios(config)
    if len(scenarios) != 2:
        raise ValueError("The residue uncertainty evaluation requires two scenarios")
    names = [scenario["name"] for scenario in scenarios]

    comparisons = {}
    scenario_metrics = []

    if args.resume:
        output = args.resume.resolve()
        if not output.is_dir():
            raise FileNotFoundError(f"Evaluation directory not found: {output}")
        snapshot_path = output / "config_snapshot.json"
        if not snapshot_path.is_file():
            raise FileNotFoundError(f"Configuration snapshot not found: {snapshot_path}")
        snapshot = read_json(snapshot_path)
        snapshot_names = [item["name"] for item in environment_scenarios(snapshot)]
        if snapshot_names != names or int(snapshot["year"]) != int(config["year"]):
            raise ValueError("Current config does not match the saved evaluation")
        prefix = "evaluation_"
        stamp = output.name[len(prefix):] if output.name.startswith(prefix) else dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        for index, name in enumerate(names, 1):
            comparison_path = output / name / "historical_comparison.json"
            if not comparison_path.is_file():
                raise FileNotFoundError(f"Completed scenario result not found: {comparison_path}")
            print(f"[{index}/{len(names)}] reuse {name}", flush=True)
            comparisons[name] = read_json(comparison_path)
    else:
        observations = load_observations(config["historical_observations"])
        reference_meta = inventory(config)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        output = Path(config["output_dir"]) / f"evaluation_{stamp}"
        output.mkdir(parents=True, exist_ok=False)
        write_json(output / "config_snapshot.json", config)
        for index, scenario in enumerate(scenarios, 1):
            name = scenario["name"]
            print(f"[{index}/{len(scenarios)}] {name}", flush=True)
            case = environment_config(config, scenario)
            meta = inventory(case)
            require_compatible_environment(reference_meta, meta, name)
            work = output / name
            write_json(work / "model_inventory.json", meta)
            model = prepare(case, work)
            database = run_apsim(case, model)
            results = read_results(database, meta, config["year"], config, audit_applications=False)
            write_json(work / "baseline.json", results)
            comparison = compare_historical(results, meta, observations, config["year"])
            write_json(work / "historical_comparison.json", comparison)
            write_comparison_csv(work / "historical_comparison.csv", comparison)
            comparisons[name] = comparison

    for name in names:
        comparison = comparisons[name]
        scenario_metrics.append({
            "scenario": name,
            "scope": "all_fields_2023",
            **error_metrics(comparison["fields"]),
        })
        scenario_metrics.append({
            "scenario": name,
            "scope": "oats_2023",
            **error_metrics(oat_rows(comparison["fields"])),
        })

    ranges = build_field_ranges(comparisons, names)
    target_fields = set(config["residue_uncertainty"]["target_fields"])
    scope_check = verify_target_scope(ranges, target_fields)
    oat_ranges = [row for row in ranges if row["field"] in target_fields]
    evaluation = {
        "experiment": "historical_2023_wheat_residue_uncertainty_envelope",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "diagnostic_uncertainty_envelope",
        "recommendation_allowed": False,
        "year": config["year"],
        "scenarios": names,
        "nitrogen_management": "explicit N rates replayed from supplied technology labels",
        "scope_check": scope_check,
        "scenario_metrics": scenario_metrics,
        "envelope_metrics": {
            "all_fields_2023": envelope_metrics(ranges),
            "target_oats_fields_2023": envelope_metrics(oat_ranges),
        },
        "field_ranges": ranges,
        "interpretation": (
            "The min-max values are a two-endpoint management-scenario envelope, "
            "not a confidence or probability interval."
        ),
        "unknown_required_for_resolution": (
            "Whether fresh wheat straw from the 2022 harvest was retained or exported "
            "on Field2 and Field7."
        ),
        "base_model_changed": False,
    }
    write_json(output / "evaluation.json", evaluation)
    write_csv(output / "field_ranges.csv", ranges)
    write_csv(output / "scenario_metrics.csv", scenario_metrics)
    archive = ROOT / f"oats_residue_uncertainty_{stamp}.tar.gz"
    create_archive(archive, output)
    print(f"OUTPUT: {output}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
