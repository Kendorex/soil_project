#!/usr/bin/env python3
"""Build one component-aware oat validation report from saved APSIM runs.

This script is intentionally analysis-only: it reads existing ``candidate.db``
and ``oats_history.csv`` files and does not start APSIM or edit the base model.
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
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.audit_oats_biomass_source import (
    load_source_biomass_rows,
    model_phase_comparison,
)
from scripts.audit_oats_yield_components import source_grain_sizes
from src.historical_case import load_observations
from src.multifield_optimizer import ROOT
from src.oats_component_validation import (
    OBJECTIVE_PATHS,
    SCOPES,
    add_observed_grain_components,
    compare_candidate,
    evaluate_candidate,
)


DEFAULT_OBSERVATIONS = ROOT / "data" / "observations" / "multifield_2019_2023.json"
DEFAULT_EXPERIMENT_ROOT = ROOT / "outputs" / "multifield" / "OATS_GRAIN_N_CEILING"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) or ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def portable_path(value: str | Path) -> Path:
    """Resolve a stored WSL/Windows project path on the current host."""
    path = Path(value)
    if path.exists():
        return path.resolve()
    normalized = str(value).replace("\\", "/")
    marker = f"/{ROOT.name}/"
    if marker in normalized:
        candidate = ROOT / normalized.split(marker, 1)[1]
        if candidate.exists():
            return candidate.resolve()
    return path


def parse_run_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected NAME=RUN_DIRECTORY")
    name, raw_path = value.split("=", 1)
    if not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("expected non-empty NAME=RUN_DIRECTORY")
    return name.strip(), portable_path(raw_path.strip())


def latest_default_runs() -> tuple[tuple[str, Path], list[tuple[str, Path]], Path]:
    summaries = sorted(
        DEFAULT_EXPERIMENT_ROOT.glob("*/summary.json"),
        key=lambda path: path.parent.stat().st_mtime,
        reverse=True,
    )
    if not summaries:
        raise FileNotFoundError(
            "No OATS_GRAIN_N_CEILING result found; provide --reference and --candidate"
        )
    summary_path = summaries[0]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    reference = ("transition_control", portable_path(summary["control_run"]))
    candidate_name = summary["profile"]["name"]
    candidate = (candidate_name, summary_path.parent / candidate_name)
    return reference, [candidate], summary_path


def validate_run_directory(name: str, path: Path) -> dict[str, Path]:
    files = {
        "history": path / "oats_history.csv",
        "database": path / "candidate.db",
    }
    missing = [str(file) for file in files.values() if not file.is_file()]
    if missing:
        raise FileNotFoundError(f"{name}: missing required files: {missing}")
    return files


def flatten_metrics(candidate: str, metrics: dict) -> list[dict[str, Any]]:
    rows = []
    for scope in SCOPES:
        values = metrics[scope]
        rows.append({
            "candidate": candidate,
            "scope": scope,
            "cases": values["cases"],
            "yield_n": values["yield"]["n"],
            "yield_mae_t_ha": values["yield"]["mae_t_ha"],
            "yield_rmse_t_ha": values["yield"]["rmse_t_ha"],
            "yield_bias_t_ha": values["yield"]["bias_t_ha"],
            **values["phenology"],
            "tkw_n": values["thousand_grain_mass"]["n"],
            "tkw_mae_g": values["thousand_grain_mass"]["mae_g"],
            "tkw_rmse_g": values["thousand_grain_mass"]["rmse_g"],
            "tkw_bias_g": values["thousand_grain_mass"]["bias_g"],
            "grain_number_n": values["grain_number"]["n"],
            "grain_number_mean_absolute_relative_error": (
                values["grain_number"]["mean_absolute_relative_error"]
            ),
            "grain_number_mean_relative_bias": (
                values["grain_number"]["mean_relative_bias"]
            ),
            "biomass_n": values["biomass"]["n"],
            "biomass_mae_g_m2": values["biomass"]["mae_g_m2"],
            "biomass_rmse_g_m2": values["biomass"]["rmse_g_m2"],
            "biomass_bias_g_m2": values["biomass"]["bias_g_m2"],
        })
    return rows


def create_archive(path: Path, output: Path) -> None:
    files = sorted(file for file in output.iterdir() if file.is_file())
    manifest = {
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [{
            "path": file.name,
            "size": file.stat().st_size,
            "sha256": sha256(file),
        } for file in files],
    }
    with tarfile.open(path, "w:gz") as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for file in files:
            archive.add(file, arcname=file.name, recursive=False)


def markdown_summary(reference: str, results: list[dict], decisions: list[dict]) -> str:
    lines = [
        "# Component-aware oat validation",
        "",
        f"Reference: `{reference}`.",
        "",
        "Metrics are kept in their physical units. No normalization, weighting, "
        "or aggregate score is used. Grain number is inferred from observed yield "
        "and thousand-grain mass and is not an independent observation.",
        "",
    ]
    for result in results:
        lines.append(f"## {result['name']}")
        lines.append("")
        for scope in SCOPES:
            values = result["metrics"][scope]
            lines.append(
                f"- `{scope}`: yield RMSE={values['yield']['rmse_t_ha']}; "
                f"TKW RMSE={values['thousand_grain_mass']['rmse_g']}; "
                f"grain-number MARE={values['grain_number']['mean_absolute_relative_error']}; "
                f"biomass RMSE={values['biomass']['rmse_g_m2']}; "
                f"tillering MAE={values['phenology']['tillering_mae_days']}; "
                f"heading MAE={values['phenology']['heading_mae_days']}."
            )
        decision = next(
            (item for item in decisions if item["candidate"] == result["name"]), None
        )
        if decision:
            lines.append("")
            lines.append(f"Decision versus reference: `{decision['overall_status']}`.")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--reference", type=parse_run_spec)
    parser.add_argument("--candidate", type=parse_run_spec, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    default_source = None
    if args.reference is None and not args.candidate:
        reference, candidates, default_source = latest_default_runs()
    elif args.reference is not None and args.candidate:
        reference, candidates = args.reference, args.candidate
    else:
        parser.error("provide both --reference and at least one --candidate, or neither")

    observations = load_observations(args.observations)
    grain_source = source_grain_sizes(observations)
    biomass_source_rows, biomass_source = load_source_biomass_rows(observations)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = args.output or (
        ROOT / "outputs" / "multifield" / "OATS_COMPONENT_VALIDATION" / stamp
    )
    output.mkdir(parents=True, exist_ok=False)

    run_specs = [reference, *candidates]
    results, all_cases, all_biomass, metric_rows = [], [], [], []
    seen = set()
    for name, directory in run_specs:
        if name in seen:
            parser.error(f"duplicate run name: {name}")
        seen.add(name)
        directory = portable_path(directory)
        files = validate_run_directory(name, directory)
        history = add_observed_grain_components(read_csv(files["history"]), grain_source)
        biomass = model_phase_comparison(
            files["database"], observations, biomass_source_rows
        )
        for row in history:
            row["candidate"] = name
        for row in biomass:
            row["candidate"] = name
        metrics = evaluate_candidate(history, biomass)
        result = {
            "name": name,
            "run_directory": str(directory),
            "history": str(files["history"]),
            "history_sha256": sha256(files["history"]),
            "database": str(files["database"]),
            "database_sha256": sha256(files["database"]),
            "metrics": metrics,
        }
        results.append(result)
        all_cases.extend(history)
        all_biomass.extend(biomass)
        metric_rows.extend(flatten_metrics(name, metrics))

    reference_name = reference[0]
    reference_metrics = results[0]["metrics"]
    decisions = []
    for result in results[1:]:
        decision = compare_candidate(result["metrics"], reference_metrics)
        decisions.append({"candidate": result["name"], **decision})

    write_csv(output / "case_component_errors.csv", all_cases)
    write_csv(output / "biomass_phase_errors.csv", all_biomass)
    write_csv(output / "scope_metrics.csv", metric_rows)
    decision_rows = []
    for decision in decisions:
        for scope, comparison in decision["scope_comparisons"].items():
            for metric in comparison["metrics_compared"]:
                decision_rows.append({
                    "candidate": decision["candidate"],
                    "overall_status": decision["overall_status"],
                    "scope": scope,
                    "scope_status": comparison["status"],
                    "metric": metric["metric"],
                    "reference_value": metric["reference"],
                    "candidate_value": metric["candidate"],
                    "delta": metric["delta"],
                })
    write_csv(output / "pareto_comparison.csv", decision_rows)
    summary = {
        "experiment": "component_aware_oat_historical_validation",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "analysis_only",
        "base_model_changed": False,
        "reference": reference_name,
        "objective_paths": list(OBJECTIVE_PATHS),
        "time_blocks": list(SCOPES),
        "comparison_method": "strict Pareto dominance; lower error is better",
        "normalization": "none",
        "weights": "none",
        "default_experiment_summary": str(default_source) if default_source else None,
        "observations": str(args.observations.resolve()),
        "observations_sha256": sha256(args.observations),
        "biomass_source": biomass_source,
        "results": results,
        "decisions": decisions,
        "interpretation_limits": [
            "Observed yield moisture basis is not stated.",
            "Observed grain number is inferred from observed yield and TKW; it is not independently measured.",
            "The transition reference assumes complete export of newly created 2022 wheat residue.",
            "Strict Pareto comparison uses no tolerance; scientific acceptance still requires interpretation.",
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "summary.md").write_text(
        markdown_summary(reference_name, results, decisions), encoding="utf-8"
    )
    archive = ROOT / f"oats_component_validation_{stamp}.tar.gz"
    create_archive(archive, output)
    print(f"REFERENCE: {reference_name}")
    for decision in decisions:
        print(f"{decision['candidate']}: {decision['overall_status']}")
    print(f"OUTPUT: {output}")
    print(f"SEND THIS ARCHIVE: {archive}")
    print(f"ARCHIVE SHA256: {sha256(archive)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
