#!/usr/bin/env python3
"""Run one APSIM replay for the selected 2019-2023 crop map."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import tarfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import inventory, prepare, write_json
from src.multifield_optimizer import run_apsim
from src.multifield_results import read_results
from src.rotation_apsim_replay import (
    load_rotation_map, patch_selected_rotation, write_combined_weather,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "multiyear_rotation_replay_2019_2023.yaml"


def resolve(config_path: Path, value: str) -> str:
    path = Path(value)
    return str(path.resolve() if path.is_absolute() else (config_path.parent / path).resolve())


def load_config(path: Path) -> dict:
    path = path.resolve()
    config = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    for key in ("base_model", "weather", "rotation_map", "output_dir"):
        config[key] = resolve(path, config[key])
    if config.get("warmup", {}).get("enabled", False):
        config["warmup"]["weather"] = resolve(path, config["warmup"]["weather"])
    if os.environ.get("APSIM_MODELS"):
        config.setdefault("runtime", {})["command"] = [os.environ["APSIM_MODELS"]]
    return config


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, output: Path, config_path: Path, map_path: Path) -> None:
    names = {
        "config_snapshot.json", "rotation_patch.json", "model_inventory.json",
        "annual_field_results.csv", "annual_summary.csv", "summary.json",
        "weather_2018_2023_warmup.met",
    }
    files = [file for file in output.iterdir() if file.is_file() and file.name in names]
    run_dir = output / "run"
    files.extend(
        file for file in run_dir.iterdir()
        if file.is_file() and file.name in {"preparation.json", "apsim.log"}
    )
    files.extend([config_path, map_path])
    with tarfile.open(path, "w:gz") as archive:
        for file in sorted(set(item.resolve() for item in files)):
            name = file.relative_to(ROOT).as_posix() if file.is_relative_to(ROOT) else file.name
            archive.add(file, arcname=name, recursive=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    config = load_config(config_path)
    rotation_map = load_rotation_map(config["rotation_map"])
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = Path(config["output_dir"]) / f"replay_{stamp}"
    output.mkdir(parents=True, exist_ok=False)

    source_model = output / "selected_rotation_source.apsimx"
    warmup = config.get("warmup")
    patch = patch_selected_rotation(
        config["base_model"], rotation_map, config["profiles"], source_model,
        warmup=warmup,
    )
    write_json(output / "rotation_patch.json", patch)

    weather = config["weather"]
    weather_meta = None
    if warmup and warmup.get("enabled", False):
        combined_weather = output / "weather_2018_2023_warmup.met"
        weather_meta = write_combined_weather(
            warmup["weather"], config["weather"], combined_weather,
        )
        weather = str(combined_weather)

    run_config = {
        "schema_version": 1,
        "model": str(source_model),
        "weather": weather,
        "rotation_parameter_reference": None,
        "runtime": config["runtime"],
        "rotation_transition_policy": config["rotation_transition_policy"],
        "fields": config["fields"],
        "scenario": config["scenario"],
        "limitations": config["limitations"],
        "warmup": warmup,
    }
    write_json(output / "config_snapshot.json", run_config)
    meta = inventory(run_config)
    public_meta = dict(meta)
    for key in list(public_meta):
        if "sha256" in key or key.endswith("provenance"):
            public_meta.pop(key)
    write_json(output / "model_inventory.json", public_meta)

    print("[1/3] Preparing selected rotation model", flush=True)
    prepared = prepare(run_config, output / "run")
    print("[2/3] Running one APSIM simulation", flush=True)
    database = run_apsim(run_config, prepared)

    print("[3/3] Reading annual field results", flush=True)
    field_rows = []
    annual_rows = []
    start_year = int(rotation_map["start_year"])
    end_year = int(rotation_map["end_year"])
    annual_results = {}
    for year in range(start_year, end_year + 1):
        result = read_results(
            database, meta, year, run_config, audit_applications=False,
        )
        annual_results[str(year)] = result
        total_area = 0.0
        total_product_kg = 0.0
        mineral_n_area_sum = 0.0
        for field, values in result["fields"].items():
            state = patch["fields"][field][str(year)]
            components = state["primary_yield_components"]
            primary_kg_ha = sum(values["yield_dm_kg_ha"][crop] for crop in components)
            area = float(values["area_ha"])
            total_area += area
            total_product_kg += primary_kg_ha * area
            mineral_n_area_sum += values["mineral_n_end_kg_ha"] * area
            field_rows.append({
                "year": year,
                "field": field,
                "state_id": state["state_id"],
                "product": state["product"],
                "area_ha": area,
                "primary_yield_dm_t_ha": primary_kg_ha / 1000.0,
                "primary_production_dm_t": primary_kg_ha * area / 1000.0,
                "mineral_n_end_kg_ha": values["mineral_n_end_kg_ha"],
                "urea_n_end_kg_ha": values["urea_n_end_kg_ha"],
                "harvest_events": sum(values["harvest_events"][crop] for crop in components),
            })
        annual_rows.append({
            "year": year,
            "fields": len(result["fields"]),
            "area_ha": total_area,
            "primary_production_dm_t": total_product_kg / 1000.0,
            "area_weighted_primary_yield_dm_t_ha": total_product_kg / total_area / 1000.0,
            "area_weighted_mineral_n_end_kg_ha": mineral_n_area_sum / total_area,
        })

    write_csv(output / "annual_field_results.csv", field_rows)
    write_csv(output / "annual_summary.csv", annual_rows)
    write_json(output / "summary.json", {
        "status": "completed",
        "scenario": config["scenario"],
        "rotation_map": rotation_map,
        "annual_summary": annual_rows,
        "limitations": config["limitations"],
        "warmup_weather": weather_meta,
        "interpretation": {
            "grain": "harvested grain dry matter",
            "forage_biomass": "harvested above-ground dry matter of the structural grass proxy",
            "profit": "not calculated",
            "soil": "end-of-year mineral N diagnostic, without confirmed target interval",
        },
    })
    archive = ROOT / f"rotation_apsim_replay_2019_2023_{stamp}.tar.gz"
    create_archive(archive, output, config_path, Path(config["rotation_map"]))
    print(f"ANNUAL RESULTS: {output / 'annual_summary.csv'}", flush=True)
    print(f"FIELD RESULTS: {output / 'annual_field_results.csv'}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
