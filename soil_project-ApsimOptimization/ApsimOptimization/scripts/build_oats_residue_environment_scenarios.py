#!/usr/bin/env python3
"""Build two traceable residue-management model scenarios for robust NSGA-II."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import fingerprint, read_json, write_json
from src.multifield_optimizer import ROOT, load_config
from src.oats_residue_sensitivity import (
    defer_wheat_auto_harvest_for_field_years,
    patch_export_new_residue_at_cereal_to_oats_harvest,
)


FIELDS = ("Field2", "Field7")
TARGET_YEARS = {field: {2022} for field in FIELDS}
DEFAULT_CONFIG = ROOT / "configs" / "multifield_2023_n_diagnostic.yaml"
DEFAULT_MODEL_DIR = ROOT / "simulation" / "scenarios" / "oats_2023_residue"
DEFAULT_OUTPUT_CONFIG = ROOT / "configs" / "multifield_2023_n_residue_ensemble.yaml"


def relative_to_config(path: Path, config_path: Path) -> str:
    import os
    return Path(os.path.relpath(path.resolve(), config_path.parent.resolve())).as_posix()


def build_variant(source: dict, name: str, export_fresh_straw: bool,
                  source_model: Path, output_dir: Path) -> dict:
    model = copy.deepcopy(source)
    lifecycle = defer_wheat_auto_harvest_for_field_years(model, TARGET_YEARS)
    residue = (
        patch_export_new_residue_at_cereal_to_oats_harvest(
            model, FIELDS, TARGET_YEARS
        ) if export_fresh_straw else []
    )
    model_path = output_dir / f"{name}.apsimx"
    provenance_path = output_dir / f"{name}.provenance.json"
    write_json(model_path, model)
    provenance = {
        "status": "explicit_historical_management_scenario",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "recommendation_status": "uncertainty_scenario_not_confirmed_management",
        "source_model": str(source_model.resolve()),
        "source_model_sha256": fingerprint(source_model),
        "output_model": str(model_path.resolve()),
        "output_model_sha256": fingerprint(model_path),
        "scenario": name,
        "fields": list(FIELDS),
        "wheat_harvest_year": 2022,
        "documented_harvest_date": "2022-08-17",
        "lifecycle_changes": lifecycle,
        "fresh_harvest_residue_export_changes": residue,
        "interpretation": (
            "documented wheat harvest with export of only the new residue increment"
            if export_fresh_straw else
            "documented wheat harvest with all generated residue retained"
        ),
    }
    write_json(provenance_path, provenance)
    return {
        "name": name,
        "model": model_path,
        "model_provenance": provenance_path,
        "model_sha256": provenance["output_model_sha256"],
        "interpretation": provenance["interpretation"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output-config", type=Path, default=DEFAULT_OUTPUT_CONFIG)
    args = parser.parse_args(argv)
    base_config_path = args.base_config.resolve()
    config = load_config(base_config_path)
    source_model = Path(config["model"])
    source = read_json(source_model)
    args.model_dir.mkdir(parents=True, exist_ok=True)

    variants = [
        build_variant(
            source, "documented_harvest_residue_retained", False,
            source_model, args.model_dir,
        ),
        build_variant(
            source, "documented_harvest_fresh_straw_export", True,
            source_model, args.model_dir,
        ),
    ]
    raw_config = yaml.safe_load(base_config_path.read_text(encoding="utf-8"))
    raw_config["environment_scenarios"] = [{
        "name": row["name"],
        "model": relative_to_config(row["model"], args.output_config),
        "model_provenance": relative_to_config(
            row["model_provenance"], args.output_config
        ),
    } for row in variants]
    raw_config["scenario_aggregation"] = {
        "yield": "worst_case", "profit": "worst_case", "soil": "worst_case"
    }
    raw_config["output_dir"] = "../outputs/multifield/DIAGNOSTIC_2023_N_RESIDUE_ENSEMBLE"
    scenario = dict(raw_config.get("scenario") or {})
    scenario.update({
        "kind": "diagnostic",
        "name": "historical_2023_n_with_residue_uncertainty",
        "recommendation_status": "prohibited",
        "diagnostic_subtype": "uncertainty_ensemble",
    })
    limitations = list(scenario.get("limitations") or [])
    limitations.append(
        "Residue retention versus fresh-straw export is unresolved; all objectives use the worst scenario."
    )
    scenario["limitations"] = limitations
    raw_config["scenario"] = scenario
    args.output_config.parent.mkdir(parents=True, exist_ok=True)
    args.output_config.write_text(
        yaml.safe_dump(raw_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "base_config": str(base_config_path),
        "base_config_sha256": fingerprint(base_config_path),
        "source_model": str(source_model),
        "source_model_sha256": fingerprint(source_model),
        "generated_config": str(args.output_config.resolve()),
        "generated_config_sha256": fingerprint(args.output_config),
        "variants": [{**row, "model": str(row["model"]),
                      "model_provenance": str(row["model_provenance"])}
                     for row in variants],
        "aggregation": raw_config["scenario_aggregation"],
        "base_model_changed": False,
    }
    write_json(args.model_dir / "scenario_models_manifest.json", manifest)
    print(f"SCENARIOS: {len(variants)}")
    print(f"CONFIG: {args.output_config.resolve()}")
    print(f"MANIFEST: {(args.model_dir / 'scenario_models_manifest.json').resolve()}")
    print("NEXT: python3 run_multifield.py validate --config "
          f"{args.output_config.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
