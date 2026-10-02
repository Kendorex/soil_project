#!/usr/bin/env python3
"""Compare supported N forms for the observed 2023 oat N rates."""
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
from src.multifield_model import (
    child, fingerprint, inventory, kind, prepare, read_json, walk, write_json,
)
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_results import read_results


DEFAULT_CONFIG = ROOT / "configs" / "oats_2023_fertilizer_form_screen.yaml"
HISTORICAL_APPLY = (
    "Fertiliser.Apply(amount: historicalReplayAmount, type: FertiliserType);"
)


def patch_2023_fertilizer_form(
    model_path: Path, fields: list[str], application_date: str, apsim_type: str,
) -> list[dict]:
    date = dt.date.fromisoformat(application_date)
    data = read_json(model_path)
    simulations = [
        node for _, node in walk(data)
        if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)
    ]
    if len(simulations) != 1:
        raise ValueError("Expected one enabled Simulation")
    changes = []
    for field in fields:
        manager = child(child(simulations[0], field), "Fertilise at sowing")
        code = "\n".join(manager.get("CodeArray") or [])
        if code.count(HISTORICAL_APPLY) != 1:
            raise ValueError(f"{field}: prepared historical fertilizer call not found")
        replacement = (
            "Fertiliser.Apply(amount: historicalReplayAmount, type: "
            f"(Clock.Today.Date == new DateTime({date.year}, {date.month}, {date.day}) "
            f"? {json.dumps(apsim_type)} : FertiliserType));"
        )
        manager["CodeArray"] = code.replace(HISTORICAL_APPLY, replacement, 1).splitlines()
        changes.append({
            "field": field,
            "application_date": application_date,
            "apsim_type": apsim_type,
            "scope": "only the matching 2023 sowing event",
        })
    write_json(model_path, data)
    return changes


def error_metrics(rows: list[dict]) -> dict:
    errors = [float(row["error_t_ha"]) for row in rows]
    return {
        "n": len(errors),
        "mae_t_ha": sum(abs(value) for value in errors) / len(errors),
        "rmse_t_ha": math.sqrt(sum(value * value for value in errors) / len(errors)),
        "bias_t_ha": sum(errors) / len(errors),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def verify_non_target_yields(results_by_type: dict[str, dict], target_fields: set[str]) -> dict:
    names = list(results_by_type)
    reference = results_by_type[names[0]]["fields"]
    mismatches = []
    for name in names[1:]:
        candidate = results_by_type[name]["fields"]
        for field, reference_row in reference.items():
            if field in target_fields:
                continue
            if candidate[field]["yield_dm_kg_ha"] != reference_row["yield_dm_kg_ha"]:
                mismatches.append({"apsim_type": name, "field": field})
    if mismatches:
        raise ValueError(f"Fertilizer form changed non-target yields: {mismatches}")
    return {"non_target_fields_unchanged": True, "target_fields": sorted(target_fields)}


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "field_results.csv", "form_metrics.csv", "config_snapshot.json",
        "historical_comparison.json", "historical_comparison.csv", "baseline.json",
        "model_inventory.json", "preparation.json", "fertilizer_form_patch.json", "apsim.log",
    }
    files = sorted(file for file in root.rglob("*") if file.is_file() and file.name in allowed)
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [
            {"path": file.relative_to(root).as_posix(), "size": file.stat().st_size}
            for file in files
        ],
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
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    settings = config.get("fertilizer_form_screen") or {}
    fields = list(settings.get("target_fields") or [])
    apsim_types = list(settings.get("apsim_types") or [])
    application_date = settings.get("application_date")
    doses = settings.get("dose_by_field_kg_n_ha") or {}
    if not fields or not apsim_types or not application_date:
        raise ValueError("Incomplete fertilizer_form_screen configuration")
    if set(fields) != set(doses):
        raise ValueError("Every target field requires an observed N dose")

    observations = load_observations(config["historical_observations"])
    meta = inventory(config)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = Path(config["output_dir"]) / f"evaluation_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "config_snapshot.json", config)

    comparisons = {}
    results_by_type = {}
    field_rows = []
    form_metrics = []
    for index, apsim_type in enumerate(apsim_types, 1):
        print(f"[{index}/{len(apsim_types)}] {apsim_type}", flush=True)
        work = output / apsim_type
        write_json(work / "model_inventory.json", meta)
        model = prepare(config, work)
        changes = patch_2023_fertilizer_form(model, fields, application_date, apsim_type)
        write_json(work / "fertilizer_form_patch.json", {
            "changes": changes,
            "dose_by_field_kg_n_ha": doses,
            "amount_basis": settings["amount_basis"],
            "model_after_patch_sha256": fingerprint(model),
        })
        database = run_apsim(config, model)
        results = read_results(database, meta, config["year"], config, audit_applications=False)
        results_by_type[apsim_type] = results
        write_json(work / "baseline.json", results)
        comparison = compare_historical(results, meta, observations, config["year"])
        comparisons[apsim_type] = comparison
        write_json(work / "historical_comparison.json", comparison)
        write_comparison_csv(work / "historical_comparison.csv", comparison)
        oats = [row for row in comparison["fields"] if row["field"] in fields]
        for row in oats:
            field_result = results["fields"][row["field"]]
            field_rows.append({
                "apsim_type": apsim_type,
                "field": row["field"],
                "observed_n_kg_ha": float(doses[row["field"]]),
                "observed_yield_t_ha": row["observed_yield_t_ha"],
                "simulated_yield_t_ha": row["simulated_yield_t_ha"],
                "error_t_ha": row["error_t_ha"],
                "mineral_n_end_kg_ha": field_result["mineral_n_end_kg_ha"],
                "urea_n_end_kg_ha": field_result["urea_n_end_kg_ha"],
            })
        form_metrics.append({"apsim_type": apsim_type, **error_metrics(oats)})

    scope_check = verify_non_target_yields(results_by_type, set(fields))
    ranked = sorted(form_metrics, key=lambda row: (row["rmse_t_ha"], row["mae_t_ha"]))
    summary = {
        "experiment": "oats_2023_fertilizer_form_screen",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "diagnostic_uncertainty_screen",
        "recommendation_allowed": False,
        "base_model_changed": False,
        "year": config["year"],
        "residue_endpoint": settings["residue_endpoint"],
        "application_date": application_date,
        "dose_by_field_kg_n_ha": doses,
        "amount_basis": settings["amount_basis"],
        "tested_apsim_types": apsim_types,
        "scope_check": scope_check,
        "form_metrics": form_metrics,
        "field_results": field_rows,
        "best_historical_fit": ranked[0],
        "interpretation": (
            "The best fit identifies sensitivity to an unresolved fertilizer product; "
            "it does not establish which product was actually applied."
        ),
        "p_and_k_limitation": (
            "The N60P60K60 label contains P and K, but this screen changes only the APSIM N form."
        ),
    }
    write_json(output / "summary.json", summary)
    write_csv(output / "field_results.csv", field_rows)
    write_csv(output / "form_metrics.csv", form_metrics)
    archive = ROOT / f"oats_fertilizer_form_{stamp}.tar.gz"
    create_archive(archive, output)
    print(f"OUTPUT: {output}", flush=True)
    print(f"SEND THIS ARCHIVE: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
