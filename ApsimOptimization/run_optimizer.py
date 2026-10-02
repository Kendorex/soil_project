from __future__ import annotations

# New multi-field commands bypass the single-crop legacy configuration entirely.
if __name__ == '__main__':
    import sys as _sys
    if len(_sys.argv) == 1 or _sys.argv[1] in {'inspect', 'validate', 'prepare', 'baseline', 'historical', 'optimize', '--help', '-h'}:
        from src.multifield_optimizer import main as _multifield_main
        raise SystemExit(_multifield_main(_sys.argv[1:] or ['inspect']))

import argparse
import csv
import hashlib
import logging
import os
import platform
import math
import sys
from pathlib import Path

from src.apsim_runner import ensure_runtime, runtime_diagnostics
from src.config import load_config, path_from_config
from src.cultivar_calibration import (
    build_calibration_plan,
    add_calibration_diagnostic_report_expressions,
    collect_calibration_diagnostics,
    load_calibration_config,
    run_calibration_plan,
    write_calibrated_candidate_template,
    write_rows as write_calibration_rows,
)
from src.execution import execute_rows, summarize_solutions
from src.initial_water_calibration import (
    build_initial_water_plan,
    run_initial_water_plan,
    write_rows as write_initial_water_rows,
)
from src.nsga2_optimizer import run_nsga2
from src.optimizer_grid import (
    active_weather_scenarios,
    build_plan,
    build_technology_plan,
    disabled_weather_scenario_ids,
    validate_inputs,
)
from src.output_validation import validate_output_files, write_validation_report
from src.reporting import ALL_RESULTS_FIELDS, write_csv, write_json, write_solution_outputs
from src.run_preflight import validate_prepared_apsimx
from src.search_space import build_search_space, search_space_summary
from src.soil_diagnostics import (
    run_soil_candidate_test,
    scan_soil_related_nodes,
    write_candidate_test_csv,
    write_soil_diagnosis,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _best_solution_payload(pareto: list[dict]) -> dict | None:
    valid = [
        row for row in pareto
        if row.get("run_status") != "failed"
        and row.get("profit_avg") is not None
        and row.get("profit_min") is not None
        and row.get("controllable_cost") is not None
    ]
    if not valid:
        return None
    by_profit = sorted(valid, key=lambda row: (-float(row["profit_avg"]), -float(row["profit_min"]), float(row["controllable_cost"]), row["technology_id"]))[0]
    by_cost = sorted(valid, key=lambda row: (float(row["controllable_cost"]), -float(row["profit_avg"]), -float(row["profit_min"]), row["technology_id"]))[0]

    def normalized(value: float, values: list[float], maximize: bool) -> float:
        low, high = min(values), max(values)
        if math.isclose(low, high):
            return 0.0
        return (high - value) / (high - low) if maximize else (value - low) / (high - low)

    avg_values = [float(row["profit_avg"]) for row in valid]
    min_values = [float(row["profit_min"]) for row in valid]
    cost_values = [float(row["controllable_cost"]) for row in valid]
    compromise = sorted(
        valid,
        key=lambda row: (
            normalized(float(row["profit_avg"]), avg_values, True) ** 2
            + normalized(float(row["profit_min"]), min_values, True) ** 2
            + normalized(float(row["controllable_cost"]), cost_values, False) ** 2,
            row["technology_id"],
        ),
    )[0]
    # Preserve the previous flat best-solution fields while adding explicit views.
    return {
        **compromise,
        "best_by_profit_avg": by_profit,
        "best_by_lowest_controllable_cost": by_cost,
        "best_compromise_solution": compromise,
    }


def _logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("optimizer")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(logging.StreamHandler())
    return logger


def _select_technology(space, scheme: str, config: dict, args):
    defaults = config["apsim"]["defaults"]
    cultivar = args.cultivar or config.get("cultivar", {}).get("default") or space.cultivars[0]
    requested = {
        "sowing_rule": args.sowing_date or str(defaults["sowing_date"]),
        "cultivar": cultivar,
        "population": float(args.population if args.population is not None else defaults["population"]),
        "depth": float(args.depth if args.depth is not None else defaults["depth"]),
        "row": float(args.row_spacing if args.row_spacing is not None else defaults["row"]),
        "fertilizer_scheme_id": scheme,
    }
    for technology in space.technologies():
        if all(getattr(technology, name) == value for name, value in requested.items()):
            return technology
    available = {
        "sowing_dates": list(space.sowing_rules),
        "cultivars": list(space.cultivars),
        "populations": list(space.populations),
        "depths": list(space.depths),
        "rows": list(space.rows),
        "fertilizer_schemes": list(space.fertilizer_scheme_ids),
    }
    raise ValueError(f"Requested technology is outside configured search space: {requested}; available={available}")


def main() -> int:
    parser = argparse.ArgumentParser(description="APSIMX wheat grid-search optimizer")
    parser.add_argument("--config", default="configs/optimizer_config.yaml")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true", help="validate and stage runs without starting APSIMX")
    modes.add_argument("--check-env", action="store_true", help="check APSIMX runtime and workspace permissions")
    modes.add_argument("--single-run", action="store_true", help="run one scenario/scheme combination")
    modes.add_argument(
        "--prepare-gui-run", action="store_true",
        help="prepare one portable APSIMX file and manifest for running through the GUI",
    )
    modes.add_argument("--evaluate-technology", action="store_true", help="run one technology in all weather scenarios")
    modes.add_argument("--full-grid", action="store_true", help="run the complete configured grid")
    modes.add_argument("--optimize-nsga2", action="store_true", help="run discrete NSGA-II optimization")
    modes.add_argument("--diagnose-soil-outputs", action="store_true", help="scan standart.apsimx for soil-related APSIMX nodes")
    modes.add_argument("--test-soil-output-candidates", action="store_true", help="test candidate soil Reporter expressions through APSIMX")
    modes.add_argument("--validate-outputs", action="store_true", help="validate full-grid and NSGA-II output files")
    modes.add_argument("--calibration-dry-run", action="store_true", help="stage cultivar calibration runs without APSIMX")
    modes.add_argument("--calibrate-cultivar", action="store_true", help="calibrate cultivar phenology through APSIMX")
    modes.add_argument(
        "--diagnose-cultivar-season", action="store_true",
        help="run selected cultivar years and export exact daily APSIMX Report data",
    )
    modes.add_argument(
        "--initial-water-dry-run", action="store_true",
        help="stage initial soil-water calibration without APSIMX",
    )
    modes.add_argument(
        "--calibrate-initial-water", action="store_true",
        help="fit initial soil-water scenarios to field measurements through APSIMX",
    )
    parser.add_argument("--scenario", default="s3_norm", help="scenario id for --single-run")
    parser.add_argument("--scheme", default="F0_no_fertilizer", help="fertilizer scheme id for --single-run")
    parser.add_argument("--sowing-date", help="technology sowing date/rule for single/evaluate modes")
    parser.add_argument("--cultivar", help="technology cultivar for single/evaluate modes")
    parser.add_argument("--population", type=float, help="plants/m2 for single/evaluate modes")
    parser.add_argument("--depth", type=float, help="sowing depth, mm, for single/evaluate modes")
    parser.add_argument("--row-spacing", type=float, help="row spacing, mm, for single/evaluate modes")
    parser.add_argument("--soil-candidates", default="configs/soil_output_candidates.yaml", help="candidate soil Reporter expressions")
    parser.add_argument(
        "--calibration-config",
        default="configs/cultivar_calibration_zlata.yaml",
        help="cultivar calibration settings and official observations",
    )
    parser.add_argument("--expanded-space", action="store_true", help="preview configured alternative search-space values in --dry-run")
    parser.add_argument(
        "--ignore-cache",
        action="store_true",
        help="evaluate APSIMX runs without reading existing cache; successful results are still saved",
    )
    parser.add_argument(
        "--allow-large-grid",
        action="store_true",
        help="allow --full-grid to exceed optimization.safety.max_full_grid_runs",
    )
    args = parser.parse_args()
    if args.expanded_space and not args.dry_run:
        parser.error("--expanded-space can only be used together with --dry-run")
    execution_mode = (
        args.single_run or args.evaluate_technology or args.full_grid or args.optimize_nsga2
        or args.calibrate_cultivar or args.diagnose_cultivar_season or args.calibrate_initial_water
    )
    if args.ignore_cache and not execution_mode:
        parser.error("--ignore-cache can only be used with an APSIMX execution mode")
    if args.allow_large_grid and not args.full_grid:
        parser.error("--allow-large-grid can only be used together with --full-grid")
    config = load_config(args.config)
    outputs = path_from_config(config, "outputs_dir")
    logger = _logger(outputs / "optimizer_log.txt")
    logger.info("Optimizer command started: %s", " ".join(sys.argv))
    if args.ignore_cache:
        logger.info("APSIMX result cache reads are disabled for this command; new successful results will be saved")
    try:
        if args.initial_water_dry_run or args.calibrate_initial_water:
            calibration_path = Path(args.calibration_config)
            if not calibration_path.is_absolute():
                calibration_path = config["_root"] / calibration_path
            calibration_config = load_calibration_config(calibration_path, config["_root"])
            plan = build_initial_water_plan(config, calibration_config, stage_files=True)
            water_config = calibration_config["initial_water_calibration"]
            output_dir = calibration_config["_root"] / water_config["output_dir"]
            write_initial_water_rows(output_dir / "initial_water_plan.csv", plan)
            if args.initial_water_dry_run:
                print(
                    f"INITIAL-WATER DRY-RUN OK: {len(plan)} planned APSIMX runs; "
                    f"years={sorted({row['year'] for row in plan})}"
                )
                print("Measured moisture is treated as gravimetric percent and converted using field bulk density.")
                print("Only temporary APSIMX copies are changed; standart.apsimx is unchanged.")
                print(f"Saved: {output_dir / 'initial_water_plan.csv'}")
                return 0
            ensure_runtime(config)
            rows, best = run_initial_water_plan(plan, config, calibration_config, logger)
            write_initial_water_rows(output_dir / "initial_water_results.csv", rows)
            write_initial_water_rows(output_dir / "best_initial_water_by_year.csv", best)
            print(
                f"INITIAL-WATER CALIBRATION OK: {len(rows)} APSIMX runs; "
                f"best years={len(best)}"
            )
            print("Results are sensitivity estimates and are not written to standart.apsimx automatically.")
            print(f"Saved: {output_dir / 'initial_water_results.csv'}")
            print(f"Saved: {output_dir / 'best_initial_water_by_year.csv'}")
            return 0

        if args.calibration_dry_run or args.calibrate_cultivar or args.diagnose_cultivar_season:
            calibration_path = Path(args.calibration_config)
            if not calibration_path.is_absolute():
                calibration_path = config["_root"] / calibration_path
            calibration_config = load_calibration_config(calibration_path, config["_root"])
            plan = build_calibration_plan(config, calibration_config, stage_files=True)
            calibration_outputs = config["_root"] / calibration_config["calibration"]["output_dir"]
            write_calibration_rows(calibration_outputs / "calibration_plan.csv", plan)
            parameter_count = len({row["candidate_id"] for row in plan})
            year_count = len({row["year"] for row in plan})
            logger.info(
                "Cultivar calibration plan: candidates=%d years=%d runs=%d",
                parameter_count, year_count, len(plan),
            )
            if args.calibration_dry_run:
                print(f"CALIBRATION DRY-RUN OK: {parameter_count} candidates x {year_count} years = {len(plan)} runs")
                calibration_years = ", ".join(map(str, calibration_config["calibration"].get("calibration_years", [])))
                validation_years = ", ".join(map(str, calibration_config["calibration"].get("validation_years", [])))
                print(f"Calibration years: {calibration_years or 'none'}; validation years: {validation_years or 'none'}")
                selection_mode = calibration_config["calibration"].get("selection", {}).get("mode", "heading_rmse")
                if selection_mode == "maturity_upper_bound_then_heading":
                    print("Targets: maturity no later than observed harvest; heading accuracy is the tie-breaker")
                else:
                    print("Primary target: observed start of heading; yield is diagnostic only")
                print(f"Saved: {calibration_outputs / 'calibration_plan.csv'}")
                print(f"First temporary APSIMX: {config['_root'] / plan[0]['working_apsimx']}")
                return 0

            ensure_runtime(config)
            if args.diagnose_cultivar_season:
                for planned in plan:
                    working = config["_root"] / planned["working_apsimx"]
                    added = add_calibration_diagnostic_report_expressions(working, calibration_config)
                    logger.info(
                        "Cultivar diagnostic Reporter expressions added: file=%s count=%d expressions=%s",
                        working, len(added), added,
                    )
            run_rows, summaries = run_calibration_plan(plan, config, calibration_config, logger)
            write_calibration_rows(calibration_outputs / "calibration_runs.csv", run_rows)
            write_calibration_rows(calibration_outputs / "calibration_summary.csv", summaries)
            if args.diagnose_cultivar_season:
                daily_rows, diagnostic_summaries = collect_calibration_diagnostics(plan, calibration_config)
                write_calibration_rows(calibration_outputs / "daily_report.csv", daily_rows)
                write_calibration_rows(calibration_outputs / "diagnostic_summary.csv", diagnostic_summaries)
                print(
                    f"CULTIVAR SEASON DIAGNOSIS OK: {len(plan)} APSIMX runs, "
                    f"{len(daily_rows)} daily Report rows"
                )
                print("Direct water/nitrogen stress indices are not reported; no expressions were guessed.")
                print(f"Saved: {calibration_outputs / 'daily_report.csv'}")
                print(f"Saved: {calibration_outputs / 'diagnostic_summary.csv'}")
                print(f"Saved: {calibration_outputs / 'calibration_runs.csv'}")
                return 0
            best = summaries[0] if summaries and summaries[0].get("calibration_heading_rmse_days") is not None else None
            write_json(calibration_outputs / "best_cultivar_parameters.json", best)
            if best is None:
                print("CULTIVAR CALIBRATION FAILED: no candidate produced a usable heading date")
                return 1
            parameter_names = list(calibration_config["calibration"].get("parameter_grid", {}))
            candidate_template = write_calibrated_candidate_template(
                calibration_config,
                {name: float(best[name]) for name in parameter_names},
                calibration_outputs / f"standart_{str(calibration_config['calibration']['cultivar']).casefold()}_calibrated_candidate.apsimx",
            )
            print(
                "CULTIVAR CALIBRATION OK: "
                f"best={best['candidate_id']} calibration_RMSE={best['calibration_heading_rmse_days']:.2f} days"
            )
            if best.get("calibration_maturity_late_rmse_days") is not None:
                print(
                    "Calibration maturity upper-bound: "
                    f"missing={best['calibration_maturity_missing_count']}, "
                    f"late_RMSE={best['calibration_maturity_late_rmse_days']:.2f} days, "
                    f"on_time={best['calibration_maturity_on_time_count']}"
                )
            if best.get("validation_heading_rmse_days") is not None:
                validation_years = ", ".join(
                    map(str, calibration_config["calibration"].get("validation_years", []))
                )
                print(
                    f"Independent validation error ({validation_years or 'configured years'}): "
                    f"{best['validation_heading_rmse_days']:.2f} days"
                )
            print(f"Saved: {calibration_outputs / 'calibration_runs.csv'}")
            print(f"Saved: {calibration_outputs / 'calibration_summary.csv'}")
            print(f"Saved: {calibration_outputs / 'best_cultivar_parameters.json'}")
            print(f"Candidate template for review: {candidate_template}")
            return 0

        if args.diagnose_soil_outputs:
            template = config["_root"] / config["apsim"]["base_template"]
            diagnosis = scan_soil_related_nodes(template)
            json_path, text_path = write_soil_diagnosis(outputs, diagnosis)
            logger.info("Soil output diagnosis saved: %s, %s", json_path, text_path)
            print(f"SOIL DIAGNOSIS OK: {diagnosis['match_count']} matched nodes")
            print("Report.VariableNames:")
            for expression in diagnosis.get("report_variable_names", []):
                print(f"  {expression}")
            for issue in diagnosis.get("report_variable_issues", []):
                print(f"REPORT WARNING: {issue}")
            print(f"Saved: {json_path}")
            print(f"Saved: {text_path}")
            return 0

        if args.test_soil_output_candidates:
            rows = run_soil_candidate_test(config, args.scenario, config["_root"] / args.soil_candidates, logger)
            write_candidate_test_csv(outputs / "soil_output_candidate_test.csv", rows)
            works = sum(1 for row in rows if row["status"] == "works_with_values")
            apsim_errors = sum(1 for row in rows if row["status"] == "apsim_error")
            all_null = sum(1 for row in rows if row["status"] == "works_all_null")
            not_configured = sum(1 for row in rows if row["status"] == "not_configured")
            print(
                f"SOIL CANDIDATE TEST COMPLETE: works_with_values={works}, works_all_null={all_null}, "
                f"apsim_error={apsim_errors}, not_configured={not_configured}, total={len(rows)}"
            )
            print("Candidate expression works technically, but agronomic validity must be confirmed.")
            print("Do not treat phosphorus pools as P_2O5 by default; P_2O5 remains unchecked until confirmed.")
            print("Saved: outputs/soil_output_candidate_test.csv")
            return 0

        inputs = validate_inputs(config, logger.info)
        if args.validate_outputs:
            space = build_search_space(config, inputs)
            scenarios = active_weather_scenarios(config)
            expected_technologies = search_space_summary(space, len(scenarios))["technology_count"]
            report = validate_output_files(outputs, expected_technologies, list(scenarios))
            text_path, json_path = write_validation_report(outputs, report)
            print(f"OUTPUT VALIDATION {report['status'].upper()}")
            print(f"Saved: {text_path}")
            print(f"Saved: {json_path}")
            return 0 if report["status"] == "passed" else 1
        if args.check_env:
            diagnosis = runtime_diagnostics(config)
            write_json(outputs / "environment_diagnostics.json", diagnosis)
            logger.info("Environment diagnostics: %s", diagnosis)
            if diagnosis["available"]:
                print("ENVIRONMENT OK: APSIMX runtime is available")
                return 0
            if platform.system() == "Windows" or not os.environ.get("WSL_DISTRO_NAME"):
                print("APSIMX runtime is not available in this environment. Run this command inside WSL.")
            print("Missing: " + ", ".join(diagnosis["missing"]))
            print("Set apsim.dotnet and apsim.models_dll in configs/optimizer_config.yaml")
            return 0
        if args.dry_run:
            space = build_search_space(config, inputs, expanded=args.expanded_space)
            scenarios = active_weather_scenarios(config)
            disabled_scenarios = disabled_weather_scenario_ids(config)
            summary = search_space_summary(space, len(scenarios))
            max_staged = int(config["optimization"].get("safety", {}).get("max_staged_dry_run_files", 500))
            stage_files = not args.expanded_space and summary["apsim_run_count"] <= max_staged
            # Expanded mode is a size preview and deliberately does not create a
            # potentially large set of temporary APSIMX files.
            plans = build_plan(
                config, inputs, stage_files=stage_files, log=logger.info,
                stage_name="dry_run_expanded" if args.expanded_space else "dry_run",
                expanded=args.expanded_space,
            )
            fields = list(plans[0])
            with (outputs / "dry_run_plan.csv").open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(plans)
            logger.info("Dry-run completed successfully; plan saved to outputs/dry_run_plan.csv")
            print(f"DRY-RUN OK: {len(plans)} planned runs")
            print("SEARCH SPACE:")
            for name, details in summary["parameters"].items():
                values = ", ".join(map(str, details["values"]))
                print(f"  {details['status']} parameter: {name} = {values} (count={details['count']})")
            print(f"  technologies = {summary['technology_count']}")
            print(f"  weather scenarios = {summary['scenario_count']}")
            print(f"  active scenario ids = {', '.join(scenarios)}")
            if disabled_scenarios:
                print(f"  disabled scenario ids = {', '.join(disabled_scenarios)}")
                print("  WARNING: exploratory result; disabled scenarios are excluded from profit_avg and profit_min")
            print(f"  planned APSIMX runs = {summary['apsim_run_count']}")
            if not stage_files:
                print(
                    f"  preview only: temporary APSIMX files were not created "
                    f"(planned runs exceed staging limit {max_staged})"
                )
            if config.get("seed_economics", {}).get("values_status") == "provisional":
                print("  WARNING: seed economics values are provisional and must be replaced with seed-lot data")
            config_name = config["_config_path"].relative_to(config["_root"]).as_posix()
            config_option = f" --config {config_name}"
            print("WSL validation commands:")
            print(f"  python3 run_optimizer.py{config_option} --check-env")
            print(f"  python3 run_optimizer.py{config_option} --single-run --scenario s3_norm --scheme F0_no_fertilizer")
            print(f"  python3 run_optimizer.py{config_option} --evaluate-technology --scheme F0_no_fertilizer")
            return 0

        if args.single_run:
            space = build_search_space(config, inputs)
            technology = _select_technology(space, args.scheme, config, args)
            rows = build_technology_plan(config, inputs, technology, "single_run", logger.info)
            selected = [row for row in rows if row["scenario_id"] == args.scenario]
            if not selected:
                raise ValueError(f"Unknown scenario {args.scenario!r}")
            ensure_runtime(config)
            results = execute_rows(selected, config, inputs, logger, use_cache=not args.ignore_cache)
            write_csv(outputs / "results_runs.csv", results)
            logger.info("Single-run result: %s", results[0])
            print(f"SINGLE-RUN OK: {selected[0]['run_id']}")
            return 0

        if args.prepare_gui_run:
            space = build_search_space(config, inputs)
            technology = _select_technology(space, args.scheme, config, args)
            rows = build_technology_plan(config, inputs, technology, "gui_run", logger.info)
            selected = [row for row in rows if row["scenario_id"] == args.scenario]
            if not selected:
                raise ValueError(f"Unknown scenario {args.scenario!r}")
            row = selected[0]
            apsimx = config["_root"] / row["working_apsimx"]
            preflight = validate_prepared_apsimx(apsimx, config["run_quality"])
            package_dir = apsimx.parent
            config_name = config["_config_path"].relative_to(config["_root"]).as_posix()
            validation_command = (
                f"python3 scripts/validate_apsim_run.py {row['working_apsimx']} "
                f"--config {config_name}"
            )
            runtime = runtime_diagnostics(config)
            models_dll = Path(runtime["models_dll"]) if runtime.get("models_dll") else None
            runtime_manifest = {
                "models_dll_sha256": _sha256_file(models_dll) if models_dll and models_dll.is_file() else None,
                "models_dll_configured": config.get("apsim", {}).get("models_dll"),
                "dotnet": runtime.get("dotnet"),
            }
            manifest = {
                "purpose": "portable_gui_run",
                "config": config_name,
                "apsimx": apsimx.name,
                "run": row,
                "preflight": preflight,
                "runtime": runtime_manifest,
                "post_run_validation": validation_command,
            }
            manifest_path = package_dir / "run_manifest.json"
            write_json(manifest_path, manifest)
            print("GUI RUN PACKAGE OK")
            print(f"Open in APSIMX GUI: {apsimx}")
            print(f"Manifest: {manifest_path}")
            print("After Run execute:")
            print(f"  {validation_command}")
            return 0

        if args.evaluate_technology:
            space = build_search_space(config, inputs)
            technology = _select_technology(space, args.scheme, config, args)
            rows = build_technology_plan(config, inputs, technology, "evaluate", logger.info)
            ensure_runtime(config)
            results = execute_rows(
                rows, config, inputs, logger, continue_on_error=True, use_cache=not args.ignore_cache
            )
            summary = summarize_solutions(results)[0]
            write_csv(outputs / "results_runs.csv", results)
            write_csv(outputs / "results_technologies.csv", [summary], list(summary))
            write_json(outputs / "evaluated_technology.json", summary)
            logger.info("Technology result: %s", summary)
            if summary.get("run_status") == "failed":
                print(f"TECHNOLOGY FAILED: {summary.get('error_message')}")
                return 1
            print(f"TECHNOLOGY OK: profit_avg={summary['profit_mean']:.2f}, profit_min={summary['profit_min']:.2f}")
            return 0

        if args.full_grid:
            ensure_runtime(config)
            space = build_search_space(config, inputs)
            summary = search_space_summary(space, len(active_weather_scenarios(config)))
            max_runs = int(config["optimization"].get("safety", {}).get("max_full_grid_runs", 2000))
            if summary["apsim_run_count"] > max_runs and not args.allow_large_grid:
                raise RuntimeError(
                    f"Configured full-grid contains {summary['apsim_run_count']} APSIMX runs, "
                    f"which exceeds safety limit {max_runs}. Use NSGA-II or explicitly add "
                    "--allow-large-grid after checking the runtime estimate."
                )
            plans = build_plan(config, inputs, stage_files=True, log=logger.info, stage_name="full_grid")
            all_results = []
            technology_ids = list(dict.fromkeys(row["technology_id"] for row in plans))
            for technology_id in technology_ids:
                group = [row for row in plans if row["technology_id"] == technology_id]
                logger.info("Running technology %s (%d scenarios)", technology_id, len(group))
                all_results.extend(execute_rows(
                    group, config, inputs, logger, continue_on_error=True, use_cache=not args.ignore_cache
                ))
            summaries = summarize_solutions(all_results)
            write_solution_outputs(outputs, all_results, summaries)
            write_csv(outputs / "results_runs.csv", all_results)
            if summaries:
                write_csv(outputs / "results_technologies.csv", summaries, list(summaries[0]))
            else:
                write_csv(outputs / "results_technologies.csv", [], ["technology_id"])
            logger.info("Full grid completed: %d APSIMX runs", len(all_results))
            print(f"FULL GRID OK: {len(all_results)} runs")
            return 0

        ensure_runtime(config)
        space = build_search_space(config, inputs)

        def evaluator(technology):
            rows = build_technology_plan(config, inputs, technology, "nsga2", logger.info)
            return summarize_solutions(execute_rows(
                rows, config, inputs, logger, continue_on_error=True, use_cache=not args.ignore_cache
            ))[0]

        nsga2_settings = dict(config["optimization"]["nsga2"])
        nsga2_settings["logger"] = logger
        nsga2_settings["fertilizer_passes_by_scheme"] = {item["id"]: item["passes"] for item in inputs["schemes"]}
        nsga2_result = run_nsga2(space, evaluator, nsga2_settings)
        pareto = nsga2_result["pareto"]
        evaluations = nsga2_result["evaluations"]
        write_csv(outputs / "nsga2_evaluations.csv", evaluations, list(evaluations[0]) if evaluations else [
            "generation", "generation_individual", "evaluation_id", "evaluation_index", "candidate_id", "technology_id", "d_sow", "cultivar", "population", "depth", "row",
            "fertilizer_scheme_id", "profit_s1", "profit_s2", "profit_s3", "profit_s4", "profit_avg", "profit_min",
            "fertilizer_cost", "financing_cost", "application_cost", "fertilizer_controllable_cost",
            "seed_rate_kg_ha", "seed_cost_total", "seed_cost_adjustment", "seed_economics_status",
            "controllable_cost", "fertilizer_passes",
            "scenario_count", "scenario_ids", "cache_hit_count", "actual_apsim_runs",
            "constraints_status", "run_status", "error_message", "f1", "f2", "f3",
        ])
        fields = list(pareto[0]) if pareto else ["technology_id", "profit_mean", "profit_min", "fertilizer_passes"]
        write_csv(outputs / "pareto_nsga2.csv", pareto, fields)
        write_json(outputs / "best_solution.json", _best_solution_payload(pareto))
        logger.info("NSGA-II completed: %d Pareto solutions", len(pareto))
        print(f"NSGA-II OK: {len(pareto)} Pareto solutions")
        return 0
    except Exception as exc:
        logger.exception("Optimizer command failed")
        print(f"ERROR: {exc}")
        print("Check apsim.dotnet, apsim.models_dll, apsim.base_template and weather_scenarios in configs/optimizer_config.yaml")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
