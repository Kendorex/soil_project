#!/usr/bin/env python3
"""Prepare or analyse a multi-year APSIM package without launching APSIM.

The user runs the prepared ``candidate.apsimx`` in APSIM Next Generation.  The
``analyse`` action only reads the resulting SQLite database.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.fertility_constraints import validate_config as validate_fertility_config
from src.fertility_constraints import humic_enabled
from src.carbon_diagnostics import read_humic_period
from src.multifield_model import inventory, prepare, write_json
from src.multifield_results import read_results
from src.multiyear_rotation_optimizer import (
    baseline_fertilizer_by_field,
    decode_vector,
    patch_candidate_model,
    summarize_period,
    variable_spec,
)
from src.rotation_apsim_replay import (
    load_rotation_map,
    patch_selected_rotation,
    write_combined_weather,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "multiyear_rotation_nsga2_2020_2023.yaml"


def parse_vector(text: str, dimensions: list[int]) -> list[int]:
    try:
        values = [int(value.strip()) for value in text.split(",") if value.strip()]
    except ValueError as exc:
        raise ValueError("--vector must contain comma-separated integer indices") from exc
    if len(values) != len(dimensions):
        raise ValueError(
            f"--vector contains {len(values)} values; expected {len(dimensions)}"
        )
    for index, (value, size) in enumerate(zip(values, dimensions)):
        if not 0 <= value < size:
            raise ValueError(
                f"--vector index {index} is {value}; allowed range is 0..{size - 1}"
            )
    return values


def _relative(path: Path, parent: Path) -> str:
    return path.resolve().relative_to(parent.resolve()).as_posix()


def prepare_package(
    config_path: Path,
    kind: str,
    vector_text: str | None,
    output: Path | None,
) -> Path:
    # Preparation needs configuration dependencies; analysis of saved JSON/DB
    # packages does not import the optimizer or require numpy/yaml/pymoo.
    from scripts.run_multiyear_rotation_optimizer import load_config, load_replay_config

    config_path = config_path.resolve()
    config = load_config(config_path)
    errors = validate_fertility_config(config.get("fertility_constraints"))
    if errors:
        raise ValueError("Invalid fertility constraints: " + "; ".join(errors))
    replay_path = Path(config["replay_config"])
    replay = load_replay_config(replay_path)
    rotation_map = load_rotation_map(replay["rotation_map"])
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    package = (output or (
        ROOT / "outputs" / "manual_multiyear_apsim" / f"{kind}_{stamp}"
    )).resolve()
    package.mkdir(parents=True, exist_ok=False)

    selected_source = package / "selected_rotation_source.apsimx"
    rotation_patch = patch_selected_rotation(
        replay["base_model"], rotation_map, replay["profiles"], selected_source,
        warmup=replay.get("warmup"),
    )
    combined_weather = package / "weather_2018_2023_warmup.met"
    write_combined_weather(replay["warmup"]["weather"], replay["weather"], combined_weather)
    specs = variable_spec(config)
    dimensions = [len(options) for _, _, options in specs]

    policies = None
    management_changes: list[dict] = []
    vector = None
    source_model = selected_source
    if kind == "candidate":
        if vector_text is None:
            raise ValueError("--vector is required for a candidate package")
        vector = parse_vector(vector_text, dimensions)
        policies = decode_vector(config, vector)
        source_model = package / "candidate_source.apsimx"
        management_changes = patch_candidate_model(
            selected_source, rotation_patch, config, policies, source_model
        )
    elif vector_text is not None:
        raise ValueError("--vector is only valid with prepare-candidate")

    run_config = {
        "schema_version": 1,
        "model": str(source_model),
        "weather": str(combined_weather),
        "rotation_parameter_reference": None,
        "runtime": replay["runtime"],
        "rotation_transition_policy": replay["rotation_transition_policy"],
        "fields": replay["fields"],
    }
    model_inventory = inventory(run_config)
    prepared_dir = package / "prepared"
    prepared_apsimx = prepare(run_config, prepared_dir)
    from src.carbon_reporting import add_carbon_reports
    instrumented, carbon_report_changes = add_carbon_reports(
        json.loads(prepared_apsimx.read_text(encoding="utf-8-sig")))
    write_json(prepared_apsimx, instrumented)
    manifest = {
        "schema_version": 1,
        "status": "awaiting_user_apsim_run",
        "kind": kind,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": str(config_path),
        "replay_config": str(replay_path),
        "prepared_apsimx": _relative(prepared_apsimx, package),
        "expected_database": _relative(prepared_apsimx.with_suffix(".db"), package),
        "evaluation_years": config["evaluation_years"],
        "vector": vector,
        "variable_space": [
            {"policy": policy, "variable": key, "options": options}
            for policy, key, options in specs
        ],
        "policies": policies,
        "management_changes": management_changes,
        "rotation_patch": rotation_patch,
        "model_inventory": model_inventory,
        "baseline_fertilizer_by_field": baseline_fertilizer_by_field(selected_source),
        "carbon_reporting": {"schema_version": 1, "added_reports": carbon_report_changes,
                             "changes_processes_or_management": False},
        "instructions": [
            "Open prepared/candidate.apsimx in APSIM Next Generation.",
            "Run the simulation and keep candidate.db beside candidate.apsimx.",
            "Do not rename package directories or candidate identifiers.",
            "Return the whole package, including the database and APSIM log.",
        ],
    }
    write_json(package / "manual_run_manifest.json", manifest)
    write_json(package / "config_snapshot.json", config)
    write_json(package / "replay_config_snapshot.json", replay)
    print("MANUAL APSIM PACKAGE READY")
    print(f"Open in APSIM Next Generation: {prepared_apsimx}")
    print(f"Manifest: {package / 'manual_run_manifest.json'}")
    print("APSIM was not started by this script.")
    return package


def analyse_package(
    package: Path, baseline_reference: Path | None,
    *, with_carbon: bool = False, output_dir: Path | None = None,
) -> Path:
    package = package.resolve()
    manifest_path = package / "manual_run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    config = json.loads((package / "config_snapshot.json").read_text(encoding="utf-8-sig"))
    prepared = package / manifest["prepared_apsimx"]
    database = package / manifest["expected_database"]
    if not database.is_file():
        raise ValueError(f"APSIM database not found: {database}")
    run_config = {
        "fields": json.loads(
            (package / "replay_config_snapshot.json").read_text(encoding="utf-8-sig")
        )["fields"]
    }
    results = {
        int(year): read_results(
            database, manifest["model_inventory"], int(year), run_config,
            audit_applications=False,
        )
        for year in manifest["evaluation_years"]
    }
    humic = (read_humic_period(prepared, database, prepared.parent / "weather.met",
                               manifest["evaluation_years"], run_config["fields"])
             if humic_enabled(config.get("fertility_constraints")) else None)
    baseline = None
    if manifest["kind"] == "baseline":
        baseline_n = {
            str(year): {
                field: values["mineral_n_end_kg_ha"]
                for field, values in result["fields"].items()
            }
            for year, result in results.items()
        }
        summary = summarize_period(
            results, manifest["rotation_patch"], config, None, None,
            baseline_fertilizer=manifest["baseline_fertilizer_by_field"],
            humic_period=humic,
        )
        summary["mineral_n_reference_kg_ha"] = baseline_n
        output = package / "baseline_reference.json"
    else:
        if baseline_reference is None:
            raise ValueError("--baseline-reference is required for candidate analysis")
        baseline = json.loads(baseline_reference.read_text(encoding="utf-8-sig"))
        summary = summarize_period(
            results, manifest["rotation_patch"], config, manifest["policies"],
            baseline["mineral_n_reference_kg_ha"],
            humic_period=humic,
        )
        output = package / "candidate_analysis.json"
    summary.update({
        "manual_run_package": str(package),
        "source_database": str(database),
        "analysis_status": "completed",
    })
    if with_carbon:
        from src.carbon_diagnostics import diagnose_carbon
        summary["carbon_diagnostics"] = diagnose_carbon(
            prepared, database, prepared.parent / "weather.met",
            manifest["evaluation_years"], run_config["fields"], baseline,
        )
        carbon = summary["carbon_diagnostics"]
        print(f'CARBON DIAGNOSTICS: {carbon["status"]}; '
              f'comparison={(carbon.get("comparison") or {}).get("status", "not_assessed")}')
    if output_dir is not None:
        output = output_dir.resolve() / output.name
    write_json(output, summary)
    print(f"ANALYSIS READY: {output}")
    print("APSIM was not started by this script.")
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare-baseline", "prepare-candidate"):
        child = subparsers.add_parser(action)
        child.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        child.add_argument("--output", type=Path)
        if action == "prepare-candidate":
            child.add_argument("--vector", required=True)
    analyse = subparsers.add_parser("analyse")
    analyse.add_argument("package", type=Path)
    analyse.add_argument("--baseline-reference", type=Path)
    analyse.add_argument("--with-carbon", action="store_true",
                         help="Include model carbon diagnostics; does not alter objectives/constraints")
    analyse.add_argument("--output-dir", type=Path,
                         help="Write analysis JSON here instead of modifying the source package")
    args = parser.parse_args(argv)
    if args.action == "analyse":
        analyse_package(args.package, args.baseline_reference,
                        with_carbon=args.with_carbon, output_dir=args.output_dir)
    else:
        prepare_package(
            args.config,
            "baseline" if args.action == "prepare-baseline" else "candidate",
            getattr(args, "vector", None),
            args.output,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
