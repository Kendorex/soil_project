#!/usr/bin/env python3
"""Screen exact APSIM oat phenology profiles with 2019-2022/2023 blocking."""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cultivar_yield_calibration import upsert_named_cultivar_commands
from src.historical_case import load_observations, records_for_year
from src.multifield_model import fingerprint, inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.oats_phenology import pareto_comparison, phenology_candidate_metrics
from src.oats_physiology import collect_oats_diagnostics, patch_oats_diagnostic_reports


MIN_LEAVES = "[Phenology].MinimumLeafNumber.FixedValue"
VRN = "[Phenology].VrnSensitivity.FixedValue"
PP = "[Phenology].PpSensitivity.FixedValue"
EARLY_BASE = "[Phenology].EarlyReproductiveLongDayBase.FixedValue"
EARLY_PP = "[Phenology].EarlyReproductivePpSensitivity.FixedValue"
PHYLLOCHRON = "[Structure].Phyllochron.BasePhyllochron.FixedValue"
YEARS = tuple(range(2019, 2024))

# Exact discrete profiles copied from the installed Models/Resources/Oats.json.
# Canopy and grain commands remain at the current Yakov values in every candidate.
PROFILES = (
    {
        "name": "current_yakov",
        "source": "current calibrated multi-field model",
        "values": {},
    },
    {
        "name": "apsim_drummond_phenology",
        "source": "installed Models/Resources/Oats.json cultivar Drummond",
        "values": {
            MIN_LEAVES: 8.5, VRN: 7, PP: 3,
            EARLY_BASE: 3, EARLY_PP: 2, PHYLLOCHRON: 75,
        },
    },
    {
        "name": "apsim_milton_phenology",
        "source": "installed Models/Resources/Oats.json cultivar Milton",
        "values": {
            MIN_LEAVES: 8.5, VRN: 7, PP: 3,
            EARLY_BASE: 2, EARLY_PP: 0, PHYLLOCHRON: 105,
        },
    },
)


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_profile(config, observations, year_records, oat_fields, profile, output):
    output.mkdir(parents=True, exist_ok=False)
    model = read_json(config["model"])
    changes = upsert_named_cultivar_commands(
        model, "Yakov", profile["values"], expected_count=10
    ) if profile["values"] else []
    report_changes = patch_oats_diagnostic_reports(model, oat_fields)
    source_candidate = output / "source_candidate.apsimx"
    write_json(source_candidate, model)
    case = copy.deepcopy(config)
    case["model"] = str(source_candidate.resolve())
    case["model_provenance"] = None
    case["historical_replay"] = dict(config["historical_replay"], enabled=True)
    case["scenario"] = {
        "kind": "diagnostic",
        "name": profile["name"],
        "recommendation_status": "prohibited",
    }
    meta = inventory(case)
    write_json(output / "config_snapshot.json", case)
    write_json(output / "model_inventory.json", meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)

    rows, schemas = [], {}
    for year in YEARS:
        _, summaries, schema = collect_oats_diagnostics(
            database, meta, year_records[year], year=year,
            scenario=profile["name"],
        )
        rows.extend(summaries)
        schemas[str(year)] = schema
    metrics = phenology_candidate_metrics(rows)
    write_csv(output / "oats_history.csv", rows)
    write_json(output / "report_schema.json", schemas)
    result = {
        "name": profile["name"],
        "source": profile["source"],
        "values": profile["values"],
        "changes": changes,
        "report_changes": report_changes,
        "metrics": metrics,
    }
    write_json(output / "profile_summary.json", result)
    return result, rows


def create_archive(path: Path, root: Path) -> None:
    allowed = {
        "summary.json", "candidate_metrics.csv", "case_predictions.csv",
        "profile_summary.json", "oats_history.csv", "report_schema.json",
        "config_snapshot.json", "model_inventory.json", "preparation.json", "apsim.log",
    }
    files = sorted(
        file for file in root.rglob("*") if file.is_file() and file.name in allowed
    )
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "output_dir": root.relative_to(ROOT).as_posix(),
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
    root = ROOT / "outputs" / "multifield" / "OATS_PHENOLOGY_SCREEN" / stamp
    root.mkdir(parents=True, exist_ok=False)

    candidates, case_rows = [], []
    for index, profile in enumerate(PROFILES, 1):
        print(f"[{index}/{len(PROFILES)}] {profile['name']}", flush=True)
        result, rows = run_profile(
            config, observations, year_records, oat_fields, profile,
            root / profile["name"],
        )
        candidates.append(result)
        for row in rows:
            case_rows.append({"candidate": profile["name"], **row})
        dev = result["metrics"]["development_yakov_2020_2022"]
        val = result["metrics"]["validation_yakov_2023"]
        print(
            f"  development yield RMSE={dev['yield_rmse_t_ha']:.3f}; "
            f"tillering MAE={dev['tillering']['mae_days']:.2f} d; "
            f"heading MAE={dev['heading']['mae_days']:.2f} d; "
            f"validation yield RMSE={val['yield_rmse_t_ha']:.3f}", flush=True,
        )

    comparison = pareto_comparison(candidates)
    metric_rows = []
    for candidate in candidates:
        for scope, metrics in candidate["metrics"].items():
            metric_rows.append({
                "candidate": candidate["name"], "scope": scope,
                "yield_mae_t_ha": metrics["yield_mae_t_ha"],
                "yield_rmse_t_ha": metrics["yield_rmse_t_ha"],
                "yield_bias_t_ha": metrics["yield_bias_t_ha"],
                "tillering_mae_days": metrics["tillering"]["mae_days"],
                "tillering_bias_days": metrics["tillering"]["bias_days"],
                "heading_mae_days": metrics["heading"]["mae_days"],
                "heading_bias_days": metrics["heading"]["bias_days"],
            })
    write_csv(root / "candidate_metrics.csv", metric_rows)
    write_csv(root / "case_predictions.csv", case_rows)
    write_json(root / "summary.json", {
        "experiment": "oats_reference_phenology_screen",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "engineering_diagnostic_only",
        "base_model_changed": False,
        "profile_values_are_exact_local_apsim_references": True,
        "profiles_are_discrete_tests_not_parameter_bounds": True,
        "historical_n_labels_replayed": True,
        "inputs": {
            "model": config["model"],
            "model_sha256": fingerprint(config["model"]),
            "weather": config["weather"],
            "weather_sha256": fingerprint(config["weather"]),
            "observations": config["historical_observations"],
            "observations_sha256": fingerprint(config["historical_observations"]),
        },
        "candidates": candidates,
        "comparison": comparison,
        "interpretation_limits": [
            "2019 Argamak is kept as context and excluded from Yakov development metrics.",
            "2019 also has a previously demonstrated early-water limitation.",
            "No normalized or weighted score combines yield and phenology metrics.",
            "No candidate is written into the calibrated production model automatically.",
        ],
    })
    archive = ROOT / f"oats_phenology_screen_{stamp}.tar.gz"
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
