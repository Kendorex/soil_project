from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ALL_RESULTS_FIELDS = [
    "run_id", "technology_id", "scenario_id", "weather_file", "cultivar", "sowing_date", "population", "depth", "row", "fertilizer_scheme_id",
    "fertilizer_type", "fertilizer_amount", "fertilizer_passes", "yield_t_ha", "yield_raw", "yield_status",
    "grain_revenue", "fertilizer_material_cost", "fertilizer_cost", "application_cost", "finance_cost", "financing_cost",
    "fertilizer_controllable_cost", "seed_rate_kg_ha", "seed_cost_total", "seed_cost_adjustment",
    "seed_economics_status", "controllable_cost", "profit",
    "MineralN_end", "MineralN_source", "MineralNWithUrea_end", "NO3kgHa_end", "NH4kgHa_end", "OrganicC_end",
    "DenitrifiedN", "DenitrifiedN_status", "NAtmosphereLoss", "NAtmosphereLoss_status",
    "NO3Leaching", "NO3Leaching_status", "nitrogen_balance_outputs_status", "soil_output_status",
    "soil_output_timing", "soil_output_date", "crop_stage_end", "zadok_stage_end", "lai_end",
    "crop_sown", "sowing_event_count", "sowing_detected_date",
    "harvest_reached", "harvest_event_count", "harvest_detected_date", "peak_lai", "peak_biomass",
    "cache_hit", "actual_apsim_run",
    "N_NO3", "P_2O5", "SOC", "SOC_0", "soil_constraints_source", "constraints_status", "is_feasible", "rejection_reason", "run_status", "warning", "error_message",
]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] = ALL_RESULTS_FIELDS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_solution_outputs(outputs: Path, run_rows: list[dict[str, Any]], summaries: list[dict[str, Any]]) -> None:
    write_csv(outputs / "all_results.csv", run_rows)
    fields = list(summaries[0]) if summaries else ["fertilizer_scheme_id", "profit_mean", "profit_min", "constraints_status", "is_feasible", "rejection_reason"]
    write_csv(outputs / "feasible_results.csv", [row for row in summaries if row["is_feasible"] is True], fields)
    write_csv(outputs / "rejected_results.csv", [row for row in summaries if row["is_feasible"] is False], fields)
    ranked = [row for row in summaries if row["is_feasible"] is not False]
    write_csv(outputs / "top_20_solutions.csv", ranked[:20], fields)
    write_json(outputs / "best_solution.json", ranked[0] if ranked else None)
