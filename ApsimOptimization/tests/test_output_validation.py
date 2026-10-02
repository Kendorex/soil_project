import csv
import json

from src.output_validation import validate_output_files, write_validation_report


SCENARIOS = ["s1_dojdey", "s2_holod", "s3_norm", "s4_zasuha"]


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_validate_outputs_on_small_complete_dataset(tmp_path):
    run_fields = [
        "run_id", "technology_id", "scenario_id", "yield_t_ha", "profit",
        "fertilizer_controllable_cost", "seed_cost_adjustment", "controllable_cost",
        "run_status", "NO3kgHa_end", "NH4kgHa_end", "MineralN_end",
    ]
    runs = [
        {"run_id": str(i), "technology_id": "T1", "scenario_id": scenario, "yield_t_ha": 1,
         "profit": i * 10, "fertilizer_controllable_cost": 90, "seed_cost_adjustment": 10,
         "controllable_cost": 100, "run_status": "completed",
         "NO3kgHa_end": 2, "NH4kgHa_end": 1, "MineralN_end": 3}
        for i, scenario in enumerate(SCENARIOS, start=1)
    ]
    write_csv(tmp_path / "results_runs.csv", run_fields, runs)
    write_csv(tmp_path / "all_results.csv", run_fields, runs)

    profit_fields = [f"profit_{scenario}" for scenario in SCENARIOS]
    technology = {"technology_id": "T1", "profit_avg": 25, "profit_min": 10, "controllable_cost": 100, "run_status": "completed"}
    technology.update({field: i * 10 for i, field in enumerate(profit_fields, start=1)})
    tech_fields = ["technology_id", "profit_avg", "profit_min", "controllable_cost", "run_status", *profit_fields]
    write_csv(tmp_path / "results_technologies.csv", tech_fields, [technology])
    write_csv(tmp_path / "top_20_solutions.csv", tech_fields, [technology])

    nsga_fields = [
        "generation", "evaluation_id", "technology_id", "fertilizer_scheme_id",
        "profit_avg", "profit_min", "controllable_cost", "seed_cost_adjustment",
        "f1", "f2", "f3", "cache_hit_count", "actual_apsim_runs",
    ]
    write_csv(tmp_path / "nsga2_evaluations.csv", nsga_fields, [{field: 1 for field in nsga_fields}])
    pareto_fields = ["technology_id", "profit_avg", "profit_min", "controllable_cost", "f1", "f2", "f3"]
    write_csv(tmp_path / "pareto_nsga2.csv", pareto_fields, [{field: 1 for field in pareto_fields}])
    (tmp_path / "optimizer_log.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "best_solution.json").write_text(json.dumps({
        "best_by_profit_avg": {}, "best_by_lowest_controllable_cost": {}, "best_compromise_solution": {}
    }), encoding="utf-8")

    report = validate_output_files(tmp_path, 1, SCENARIOS)
    assert report["status"] == "passed"
    text_path, json_path = write_validation_report(tmp_path, report)
    assert text_path.is_file() and json_path.is_file()
