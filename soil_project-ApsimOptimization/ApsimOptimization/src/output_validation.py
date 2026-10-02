from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        return list(reader.fieldnames or []), list(reader)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_output_files(
    outputs: Path,
    expected_technologies: int,
    scenario_ids: list[str],
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, message: str, severity: str = "error") -> None:
        checks.append({"name": name, "status": "passed" if passed else "failed", "severity": severity, "message": message})

    required_files = (
        "results_runs.csv", "results_technologies.csv", "top_20_solutions.csv", "all_results.csv",
        "optimizer_log.txt", "pareto_nsga2.csv", "nsga2_evaluations.csv", "best_solution.json",
    )
    for name in required_files:
        add(f"file:{name}", (outputs / name).is_file(), f"required output {'exists' if (outputs / name).is_file() else 'is missing'}")

    runs: list[dict[str, str]] = []
    technologies: list[dict[str, str]] = []
    runs_path = outputs / "results_runs.csv"
    technologies_path = outputs / "results_technologies.csv"
    if runs_path.is_file():
        fields, runs = _read_csv(runs_path)
        required = {
            "technology_id", "scenario_id", "yield_t_ha", "profit", "controllable_cost",
            "fertilizer_controllable_cost", "seed_cost_adjustment", "run_status",
            "NO3kgHa_end", "NH4kgHa_end", "MineralN_end",
        }
        missing = sorted(required - set(fields))
        add("results_runs:columns", not missing, f"missing columns: {missing}" if missing else "required columns are present")
        expected_runs = expected_technologies * len(scenario_ids)
        add("results_runs:row_count", len(runs) == expected_runs, f"rows={len(runs)}, expected={expected_runs}")
        add("results_runs:yield_not_empty", any(row.get("yield_t_ha") not in (None, "") for row in runs), "yield_t_ha contains data")
        add("results_runs:profit_not_empty", any(row.get("profit") not in (None, "") for row in runs), "profit contains data")
        failed_runs = [row.get("run_id", "") for row in runs if row.get("run_status") == "failed"]
        add("results_runs:no_failed_runs", not failed_runs, f"failed runs: {failed_runs}")

        grouped: dict[str, set[str]] = {}
        for row in runs:
            grouped.setdefault(row.get("technology_id", ""), set()).add(row.get("scenario_id", ""))
        invalid = {key: sorted(value) for key, value in grouped.items() if value != set(scenario_ids)}
        add("results_runs:four_scenarios", not invalid and len(grouped) == expected_technologies, f"invalid technology scenarios: {invalid}")

        mineral_mismatches = []
        for row in runs:
            mineral = _number(row.get("MineralN_end"))
            no3 = _number(row.get("NO3kgHa_end"))
            nh4 = _number(row.get("NH4kgHa_end"))
            if mineral is not None and no3 is not None and nh4 is not None and not math.isclose(mineral, no3 + nh4, rel_tol=tolerance, abs_tol=tolerance):
                mineral_mismatches.append(row.get("run_id", ""))
        add("results_runs:mineral_n_identity", not mineral_mismatches, f"mismatched runs: {mineral_mismatches}")
        cost_mismatches = []
        for row in runs:
            controllable = _number(row.get("controllable_cost"))
            fertilizer = _number(row.get("fertilizer_controllable_cost"))
            seed = _number(row.get("seed_cost_adjustment"))
            if (
                controllable is not None and fertilizer is not None and seed is not None
                and not math.isclose(controllable, fertilizer + seed, rel_tol=tolerance, abs_tol=tolerance)
            ):
                cost_mismatches.append(row.get("run_id", ""))
        add("results_runs:controllable_cost_identity", not cost_mismatches, f"mismatched runs: {cost_mismatches}")

    if technologies_path.is_file():
        fields, technologies = _read_csv(technologies_path)
        profit_columns = [f"profit_{scenario}" for scenario in scenario_ids]
        required = {"technology_id", "profit_avg", "profit_min", "controllable_cost", "run_status", *profit_columns}
        missing = sorted(required - set(fields))
        add("results_technologies:columns", not missing, f"missing columns: {missing}" if missing else "required columns are present")
        add("results_technologies:row_count", len(technologies) == expected_technologies, f"rows={len(technologies)}, expected={expected_technologies}")
        add("results_technologies:profit_avg_not_empty", any(row.get("profit_avg") not in (None, "") for row in technologies), "profit_avg contains data")
        add("results_technologies:controllable_cost_not_empty", any(row.get("controllable_cost") not in (None, "") for row in technologies), "controllable_cost contains data")
        failed_technologies = [row.get("technology_id", "") for row in technologies if row.get("run_status") == "failed"]
        add("results_technologies:no_failed_runs", not failed_technologies, f"failed technologies: {failed_technologies}")
        mismatches = []
        for row in technologies:
            profits = [_number(row.get(column)) for column in profit_columns]
            reported = _number(row.get("profit_avg"))
            if reported is not None and all(value is not None for value in profits):
                calculated = sum(profits) / len(profits)
                if not math.isclose(reported, calculated, rel_tol=tolerance, abs_tol=tolerance):
                    mismatches.append(row.get("technology_id", ""))
        add("results_technologies:profit_avg_identity", not mismatches, f"mismatched technologies: {mismatches}")

    all_results_path = outputs / "all_results.csv"
    if all_results_path.is_file():
        fields, all_rows = _read_csv(all_results_path)
        missing = sorted({"technology_id", "scenario_id", "yield_t_ha", "profit", "controllable_cost"} - set(fields))
        add("all_results:columns", not missing, f"missing columns: {missing}" if missing else "required columns are present")
        expected_runs = expected_technologies * len(scenario_ids)
        add("all_results:row_count", len(all_rows) == expected_runs, f"rows={len(all_rows)}, expected={expected_runs}")

    top_path = outputs / "top_20_solutions.csv"
    if top_path.is_file():
        _fields, top_rows = _read_csv(top_path)
        add("top_20_solutions:row_count", 0 < len(top_rows) <= 20, f"rows={len(top_rows)}, expected between 1 and 20")

    evaluations_path = outputs / "nsga2_evaluations.csv"
    if evaluations_path.is_file():
        fields, _rows = _read_csv(evaluations_path)
        required = {
            "generation", "evaluation_id", "technology_id", "fertilizer_scheme_id",
            "profit_avg", "profit_min", "controllable_cost", "seed_cost_adjustment",
            "f1", "f2", "f3", "cache_hit_count", "actual_apsim_runs",
        }
        missing = sorted(required - set(fields))
        add("nsga2_evaluations:columns", not missing, f"missing columns: {missing}" if missing else "required columns are present")

    pareto_path = outputs / "pareto_nsga2.csv"
    if pareto_path.is_file():
        fields, pareto_rows = _read_csv(pareto_path)
        required = {"technology_id", "profit_avg", "profit_min", "controllable_cost", "f1", "f2", "f3"}
        missing = sorted(required - set(fields))
        add("pareto_nsga2:columns", not missing, f"missing columns: {missing}" if missing else "required columns are present")
        add("pareto_nsga2:not_empty", bool(pareto_rows), f"rows={len(pareto_rows)}")

    log_path = outputs / "optimizer_log.txt"
    if log_path.is_file():
        add("optimizer_log:not_empty", log_path.stat().st_size > 0, f"bytes={log_path.stat().st_size}")

    best_path = outputs / "best_solution.json"
    if best_path.is_file():
        try:
            best = json.loads(best_path.read_text(encoding="utf-8"))
            required = {"best_by_profit_avg", "best_by_lowest_controllable_cost", "best_compromise_solution"}
            missing = sorted(required - set(best or {}))
            add("best_solution:keys", not missing, f"missing keys: {missing}" if missing else "required keys are present")
        except (OSError, ValueError, TypeError) as exc:
            add("best_solution:json", False, str(exc))

    failed = [check for check in checks if check["status"] == "failed" and check["severity"] == "error"]
    return {
        "status": "passed" if not failed else "failed",
        "expected_technologies": expected_technologies,
        "expected_runs": expected_technologies * len(scenario_ids),
        "scenario_ids": scenario_ids,
        "checks": checks,
    }


def write_validation_report(outputs: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    outputs.mkdir(parents=True, exist_ok=True)
    json_path = outputs / "validation_report.json"
    text_path = outputs / "validation_report.txt"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"Output validation: {report['status']}",
        f"Expected technologies: {report['expected_technologies']}",
        f"Expected APSIMX runs: {report['expected_runs']}",
        "",
    ]
    for check in report["checks"]:
        lines.append(f"[{check['status'].upper()}] {check['name']}: {check['message']}")
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return text_path, json_path
