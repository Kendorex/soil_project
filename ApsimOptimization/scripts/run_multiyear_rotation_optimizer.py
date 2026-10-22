#!/usr/bin/env python3
"""Run diagnostic multi-year NSGA-II on the selected 2019-2023 crop map."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import tarfile
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import inventory, prepare, read_json, write_json
from src.multifield_optimizer import run_apsim
from src.multifield_results import read_results
from src.fertility_constraints import validate_config as validate_fertility_config
from src.fertility_constraints import humic_enabled, inequality_constraint_count
from src.carbon_reporting import add_carbon_reports
from src.carbon_diagnostics import read_humic_period
from src.multiyear_rotation_optimizer import (
    baseline_fertilizer_by_field, decode_vector, patch_candidate_model,
    summarize_period, variable_spec,
)
from src.rotation_apsim_replay import (
    load_rotation_map, patch_selected_rotation, write_combined_weather,
)
from scripts.run_rotation_apsim_replay import load_config as load_replay_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "multiyear_rotation_nsga2_2020_2023.yaml"


def resolve(config_path: Path, value: str) -> str:
    path = Path(value)
    return str(path.resolve() if path.is_absolute() else (config_path.parent / path).resolve())


def load_config(path: Path) -> dict:
    path = path.resolve()
    config = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    for key in ("replay_config", "output_dir"):
        config[key] = resolve(path, config[key])
    return config


def write_csv(path: Path, rows: list[dict]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def result_row(result: dict) -> dict:
    fertility = result.get("fertility_constraints") or {}
    humic = fertility.get("humic_carbon") or {}
    row = {
        "candidate_id": result["candidate_id"], "status": result["status"],
        "yield_dm_kg": result.get("yield_dm_kg"),
        "net_profit_proxy": result.get("net_profit_proxy"),
        "soil_deviation": result.get("soil_deviation"),
        "fertility_constraints_mode": fertility.get("mode"),
        "fertility_constraints_status": fertility.get("status"),
        "fertility_is_feasible": fertility.get("is_feasible"),
        "fertility_hard_constraints_status": fertility.get("hard_constraints_status"),
        "fertility_normalized_violation": fertility.get("normalized_violation"),
        "fertility_missing_count": len(fertility.get("missing") or []),
        "humic_mode": humic.get("mode"),
        "humic_status": humic.get("status"),
        "humic_is_feasible": humic.get("is_feasible"),
        "humic_g": humic.get("aggregate_g"),
        "humic_failed_fields": ",".join(humic.get("failed_fields") or []),
        "humic_missing_count": len(humic.get("missing") or []),
        "error": result.get("error"),
    }
    for crop, policy in (result.get("policies") or {}).items():
        row[f"{crop}_cultivar"] = policy.get("cultivar")
        row[f"{crop}_population_m2"] = policy.get("population")
        row[f"{crop}_depth_mm"] = policy.get("depth")
        row[f"{crop}_row_spacing_mm"] = policy.get("row_spacing")
        row[f"{crop}_fertilizer"] = policy.get("fertilizer", {}).get("name")
        row[f"{crop}_dose_kg_n_ha"] = policy.get("fertilizer", {}).get("dose_kg_n_ha")
    return row


def archive_results(archive: Path, output: Path, config_path: Path, replay_path: Path, map_path: Path) -> None:
    keep = {
        "config_snapshot.json", "replay_config_snapshot.json", "rotation_patch.json",
        "baseline_reference.json", "evaluations.json", "evaluations_summary.csv",
        "pareto.json", "pareto_summary.csv", "optimization_summary.json",
        "variable_space.json", "run.log",
    }
    files = [path for path in output.iterdir() if path.is_file() and path.name in keep]
    files.extend([config_path, replay_path, map_path])
    with tarfile.open(archive, "w:gz") as stream:
        for path in sorted(set(item.resolve() for item in files)):
            arcname = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
            stream.add(path, arcname=arcname, recursive=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    config = load_config(config_path)
    n_constraints = inequality_constraint_count(config.get("fertility_constraints"))
    fertility_errors = validate_fertility_config(config.get("fertility_constraints"))
    if fertility_errors:
        raise ValueError("Invalid fertility constraints: " + "; ".join(fertility_errors))
    replay_path = Path(config["replay_config"])
    replay = load_replay_config(replay_path)
    if os.environ.get("APSIM_MODELS"):
        replay.setdefault("runtime", {})["command"] = [os.environ["APSIM_MODELS"]]
    rotation_map = load_rotation_map(replay["rotation_map"])
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = Path(config["output_dir"]) / f"optimize_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    log_path = output / "run.log"

    def log(message: str) -> None:
        print(message, flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")

    base_model = output / "selected_rotation_warmup_source.apsimx"
    patch = patch_selected_rotation(
        replay["base_model"], rotation_map, replay["profiles"], base_model,
        warmup=replay.get("warmup"),
    )
    write_json(output / "rotation_patch.json", patch)
    combined_weather = output / "weather_2018_2023_warmup.met"
    write_combined_weather(replay["warmup"]["weather"], replay["weather"], combined_weather)
    run_config = {
        "schema_version": 1,
        "model": str(base_model), "weather": str(combined_weather),
        "rotation_parameter_reference": None,
        "runtime": replay["runtime"],
        "rotation_transition_policy": replay["rotation_transition_policy"],
        "fields": replay["fields"],
    }
    write_json(output / "config_snapshot.json", config)
    write_json(output / "replay_config_snapshot.json", replay)
    specs = variable_spec(config)
    write_json(output / "variable_space.json", [
        {"policy": policy, "variable": key, "options": options}
        for policy, key, options in specs
    ])

    def simulate(model: Path, work: Path) -> tuple[dict[int, dict], dict, dict | None]:
        candidate_config = dict(run_config, model=str(model))
        meta = inventory(candidate_config)
        prepared = prepare(candidate_config, work)
        if humic_enabled(config.get("fertility_constraints")):
            instrumented, _ = add_carbon_reports(read_json(prepared))
            write_json(prepared, instrumented)
        database = run_apsim(candidate_config, prepared)
        results = {
            int(year): read_results(database, meta, int(year), candidate_config,
                                    audit_applications=False)
            for year in config["evaluation_years"]
        }
        humic = (read_humic_period(prepared, database, work / "weather.met",
                                   config["evaluation_years"], run_config["fields"])
                 if humic_enabled(config.get("fertility_constraints")) else None)
        return results, meta, humic

    log("[1/3] APSIM baseline reference for 2020-2023")
    baseline_results, _, baseline_humic = simulate(base_model, output / "baseline_run")
    if humic_enabled(config.get("fertility_constraints")) and baseline_humic.get("status") != "checked":
        raise RuntimeError("Baseline Humic data could not be verified: "
                           + str(baseline_humic.get("reason", "missing data")))
    baseline_n = {
        str(year): {field: values["mineral_n_end_kg_ha"]
                    for field, values in result["fields"].items()}
        for year, result in baseline_results.items()
    }
    baseline = summarize_period(
        baseline_results, patch, config, None, None,
        baseline_fertilizer=baseline_fertilizer_by_field(base_model),
        humic_period=baseline_humic,
    )
    baseline["mineral_n_reference_kg_ha"] = baseline_n
    baseline["interpretation"] = "Fixed-map APSIM reference; 2019 is warm-up only."
    write_json(output / "baseline_reference.json", baseline)

    cache: dict[str, dict] = {}
    evaluation_history = []

    def evaluate(vector) -> dict:
        policies = decode_vector(config, vector)
        key = json.dumps(policies, sort_keys=True, ensure_ascii=False)
        if key in cache:
            result = cache[key]
            evaluation_history.append({"candidate_id": result["candidate_id"], "cache_hit": True})
            return result
        candidate_id = f"candidate_{len(cache) + 1:03d}"
        work = output / "candidates" / candidate_id
        work.mkdir(parents=True, exist_ok=False)
        candidate_model = work / "source.apsimx"
        try:
            changes = patch_candidate_model(base_model, patch, config, policies, candidate_model)
            results, _, humic = simulate(candidate_model, work / "run")
            result = summarize_period(results, patch, config, policies, baseline_n,
                                      humic_period=humic)
            result.update({"candidate_id": candidate_id, "status": "ok",
                           "policies": policies, "management_changes": changes})
        except Exception as exc:
            result = {"candidate_id": candidate_id, "status": "failed",
                      "policies": policies, "error": str(exc),
                      "F": [1e30, 1e30, 1e30], "G": [1e30] * n_constraints}
        cache[key] = result
        evaluation_history.append({"candidate_id": candidate_id, "cache_hit": False})
        write_json(work / "result.json", result)
        log(f"{candidate_id}: {result['status']}; F={result['F']}; G={result['G']}")
        return result

    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.core.sampling import Sampling
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.operators.repair.rounding import RoundingRepair
    from pymoo.operators.sampling.rnd import IntegerRandomSampling
    from pymoo.optimize import minimize
    from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting

    class Problem(ElementwiseProblem):
        def __init__(self):
            super().__init__(n_var=len(specs), n_obj=3, n_ieq_constr=n_constraints,
                             xl=np.zeros(len(specs), dtype=int),
                             xu=np.asarray([len(options) - 1 for _, _, options in specs]),
                             vtype=int)

        def _evaluate(self, x, out, *unused, **kwargs):
            result = evaluate(x)
            out["F"] = np.asarray(result["F"], dtype=float)
            out["G"] = np.asarray(result["G"], dtype=float)

    class AnchoredIntegerSampling(Sampling):
        def _do(self, problem, n_samples, **kwargs):
            samples = IntegerRandomSampling()._do(problem, n_samples, **kwargs)
            if n_samples:
                samples[0] = np.zeros(len(specs), dtype=int)
            return samples

    log("[2/3] Dynamic-management preflight and multi-year NSGA-II")
    preflight = evaluate(np.zeros(len(specs), dtype=int))
    if preflight["status"] != "ok":
        raise RuntimeError(f"Dynamic-management preflight failed: {preflight['error']}")
    preflight_humic = (preflight.get("fertility_constraints") or {}).get("humic_carbon") or {}
    if preflight_humic.get("status") == "missing_data":
        raise RuntimeError(f"Dynamic-management Humic data could not be verified: {preflight_humic['missing']}")
    algorithm = NSGA2(
        pop_size=int(config["nsga2"]["population"]),
        sampling=AnchoredIntegerSampling(),
        crossover=SBX(prob=0.9, eta=15, vtype=float, repair=RoundingRepair()),
        mutation=PM(eta=20, vtype=float, repair=RoundingRepair()),
        eliminate_duplicates=True,
    )
    minimize(Problem(), algorithm, ("n_gen", int(config["nsga2"]["generations"])),
             seed=int(config["nsga2"]["seed"]), verbose=True)
    successful = [value for value in cache.values() if value["status"] == "ok"]
    feasible = [value for value in successful if all(float(g) <= 0 for g in value["G"])]
    pareto = []
    if feasible:
        front = NonDominatedSorting().do(
            np.asarray([value["F"] for value in feasible]),
            only_non_dominated_front=True,
        )
        pareto = [feasible[int(index)] for index in front]
    evaluations = list(cache.values())
    write_json(output / "evaluations.json", evaluations)
    write_csv(output / "evaluations_summary.csv", [result_row(row) for row in evaluations])
    write_json(output / "pareto.json", pareto)
    write_csv(output / "pareto_summary.csv", [result_row(row) for row in pareto])
    summary = {
        "status": (
            "completed" if feasible else
            "no_feasible_solution" if successful else
            "failed"
        ),
        "scenario": config["scenario"],
        "evaluation_years": config["evaluation_years"],
        "warmup_year": 2019,
        "evaluated_unique_candidates": len(evaluations),
        "successful_candidates": len(successful),
        "feasible_candidates": len(feasible),
        "infeasible_candidates": len(successful) - len(feasible),
        "failed_candidates": len(evaluations) - len(successful),
        "pareto_solutions": len(pareto),
        "baseline": {key: baseline[key] for key in ("yield_dm_kg", "net_profit_proxy", "soil_deviation")},
        "objectives": ["maximize total harvested dry matter",
                       "maximize diagnostic net profit proxy",
                       "minimize mineral-N shortfall relative to fixed-map baseline"],
        "fertility_constraints": config.get("fertility_constraints"),
        "limitations": config["limitations"],
    }
    write_json(output / "optimization_summary.json", summary)
    log("[3/3] Results and archive")
    archive = ROOT / f"multiyear_rotation_nsga2_2020_2023_{stamp}.tar.gz"
    archive_results(archive, output, config_path, replay_path, Path(replay["rotation_map"]))
    log(f"SUMMARY: {output / 'optimization_summary.json'}")
    log(f"PARETO: {output / 'pareto_summary.csv'}")
    log(f"SEND THIS ARCHIVE: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
