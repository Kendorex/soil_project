from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .apsim_runner import find_db_for_apsimx, run_apsim_files
from .cache import ResultCache
from .constraints import evaluate, load_external_soil_data, soil_values_for_run
from .economy import calculate_economics
from .results_reader import read_report


def _failed_result(row: dict[str, Any], error: Exception, actual_apsim_run: int = 1) -> dict[str, Any]:
    return {
        **{key: row[key] for key in (
            "run_id", "technology_id", "scenario_id", "weather_file", "cultivar", "fertilizer_scheme_id",
            "fertilizer_type", "fertilizer_amount", "fertilizer_passes",
        )},
        "sowing_date": row["sowing_date"], "population": row["population"], "depth": row["depth"], "row": row["row"],
        "yield_t_ha": None, "yield_raw": None, "yield_status": "not_available",
        "grain_revenue": None, "fertilizer_material_cost": None, "application_cost": None,
        "fertilizer_cost": None, "finance_cost": None, "financing_cost": None,
        "fertilizer_controllable_cost": None, "seed_rate_kg_ha": None, "seed_cost_total": None,
        "seed_cost_adjustment": None, "seed_economics_status": "not_evaluated",
        "controllable_cost": None, "profit": None,
        "MineralN_end": None, "MineralN_source": "", "MineralNWithUrea_end": None,
        "NO3kgHa_end": None, "NH4kgHa_end": None, "OrganicC_end": None,
        "DenitrifiedN": None, "DenitrifiedN_status": "missing",
        "NAtmosphereLoss": None, "NAtmosphereLoss_status": "missing",
        "NO3Leaching": None, "NO3Leaching_status": "missing",
        "nitrogen_balance_outputs_status": "missing", "soil_output_status": "missing",
        "soil_output_timing": "not_available", "soil_output_date": "",
        "crop_stage_end": "", "zadok_stage_end": None, "lai_end": None,
        "crop_sown": None, "sowing_event_count": None, "sowing_detected_date": "",
        "harvest_reached": None, "harvest_event_count": None, "harvest_detected_date": "",
        "peak_lai": None, "peak_biomass": None,
        "N_NO3": None, "P_2O5": None, "SOC": None, "SOC_0": None,
        "soil_constraints_source": "not_evaluated",
        "constraints_status": "not_evaluated", "is_feasible": False,
        "rejection_reason": str(error), "run_status": "failed", "warning": "",
        "error_message": str(error),
        "cache_hit": False, "actual_apsim_run": actual_apsim_run,
    }


def execute_rows(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    inputs: dict[str, Any],
    logger,
    continue_on_error: bool = False,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    cache = ResultCache(config["_root"] / config["paths"]["outputs_dir"] / "cache")
    schemes = {item["id"]: item for item in inputs["schemes"]}
    external_soil_rows, external_soil_error = load_external_soil_data(config["soil_constraints"], config["_root"])
    results = []
    for row in rows:
        cached = cache.load(row) if use_cache else None
        if cached is not None:
            cached = dict(cached)
            cached["cache_hit"] = True
            cached["actual_apsim_run"] = 0
            logger.info("APSIMX cache hit: technology_id=%s scenario=%s", row["technology_id"], row["scenario_id"])
            results.append(cached)
            continue
        if not use_cache:
            logger.info(
                "APSIMX cache bypassed: technology_id=%s scenario=%s",
                row["technology_id"], row["scenario_id"],
            )
        try:
            apsimx = config["_root"] / row["working_apsimx"]
            logger.info("APSIMX run requested: technology_id=%s, scenario=%s", row["technology_id"], row["scenario_id"])
            run_apsim_files([apsimx], config, logger)
            database = find_db_for_apsimx(apsimx)
            if database is None:
                raise RuntimeError(f"APSIMX database not found after successful run: {apsimx}")
            values, diagnosis = read_report(
                database,
                config["results"],
                config.get("nitrogen_balance", {}),
                config.get("run_quality", {}),
            )
            logger.info("SQLite diagnosis for %s: %s", row["run_id"], json.dumps(diagnosis, ensure_ascii=False))
            if values.get("warning"):
                logger.warning("%s: %s", row["run_id"], values["warning"])
            if not str(values.get("run_status", "")).startswith("completed"):
                raise RuntimeError(
                    f"APSIMX run rejected by run_quality: status={values.get('run_status')}; "
                    f"{values.get('warning', '')}"
                )
            economics = calculate_economics(
                values["yield_t_ha"],
                schemes[row["fertilizer_scheme_id"]],
                inputs["prices"],
                inputs["constants"],
                population=row["population"],
                seed_config=config.get("seed_economics", {}),
            )
            soil_values = soil_values_for_run(
                values, config["soil_constraints"], row, external_soil_rows, external_soil_error
            )
            status, feasible, reason = evaluate(soil_values, config["soil_constraints"])
            result = {
                **{key: row[key] for key in (
                    "run_id", "technology_id", "scenario_id", "weather_file", "cultivar", "fertilizer_scheme_id",
                    "fertilizer_type", "fertilizer_amount", "fertilizer_passes",
                )},
                "sowing_date": row["sowing_date"], "population": row["population"], "depth": row["depth"], "row": row["row"],
                "yield_t_ha": values["yield_t_ha"], "yield_raw": values["yield_raw"],
                "yield_status": values["yield_status"], **economics,
                **{key: values.get(key) for key in (
                    "MineralN_end", "MineralN_source", "MineralNWithUrea_end", "NO3kgHa_end", "NH4kgHa_end",
                    "OrganicC_end", "DenitrifiedN", "NAtmosphereLoss", "NO3Leaching",
                    "DenitrifiedN_status", "NAtmosphereLoss_status", "NO3Leaching_status",
                    "nitrogen_balance_outputs_status", "soil_output_status", "soil_output_timing", "soil_output_date",
                    "crop_stage_end", "zadok_stage_end", "lai_end",
                    "crop_sown", "sowing_event_count", "sowing_detected_date",
                    "harvest_reached", "harvest_event_count", "harvest_detected_date",
                    "peak_lai", "peak_biomass",
                )},
                "N_NO3": soil_values.get("N_NO3"), "P_2O5": soil_values.get("P_2O5"),
                "SOC": soil_values.get("SOC"), "SOC_0": soil_values.get("SOC_0", config["soil_constraints"].get("SOC_0")),
                "soil_constraints_source": config["soil_constraints"].get("source", "report"),
                "constraints_status": status, "is_feasible": feasible, "rejection_reason": reason,
                "run_status": values["run_status"], "warning": values["warning"], "error_message": "",
                "cache_hit": False, "actual_apsim_run": 1,
            }
            cache.save(row, database, result)
            results.append(result)
        except Exception as exc:
            if not continue_on_error:
                raise
            logger.exception("APSIMX run failed: technology_id=%s scenario=%s", row["technology_id"], row["scenario_id"])
            results.append(_failed_result(row, exc))
    return results


def summarize_solutions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["technology_id"], []).append(row)
    summaries = []
    for technology_id, group in grouped.items():
        profits = [float(row["profit"]) for row in group if row.get("profit") is not None]
        has_failed_run = any(row.get("run_status") == "failed" for row in group)
        statuses = {row["constraints_status"] for row in group}
        failed = any(row["is_feasible"] is False for row in group)
        feasible = all(row["is_feasible"] is True for row in group)
        unchecked = "soil_constraints_unchecked" in statuses
        if failed:
            aggregate_feasible = False
            aggregate_status = "soil_constraints_unchecked" if unchecked else "checked_failed"
        elif unchecked:
            aggregate_feasible = None
            aggregate_status = "soil_constraints_unchecked"
        else:
            aggregate_feasible = True
            aggregate_status = "checked_passed"
        scenario_profits = {f"profit_{row['scenario_id']}": row.get("profit") for row in group}
        scenario_mineral_n = {f"MineralN_end_{row['scenario_id']}": row.get("MineralN_end") for row in group}

        def numeric_values(field: str) -> list[float]:
            return [float(row[field]) for row in group if row.get(field) is not None]

        def mean(field: str) -> float | None:
            values = numeric_values(field)
            return sum(values) / len(values) if values else None

        def minimum(field: str) -> float | None:
            values = numeric_values(field)
            return min(values) if values else None

        nitrogen_statuses = [row.get("nitrogen_balance_outputs_status", "missing") for row in group]
        if nitrogen_statuses and all(value == "available" for value in nitrogen_statuses):
            nitrogen_status = "available"
        elif nitrogen_statuses and all(value == "missing" for value in nitrogen_statuses):
            nitrogen_status = "missing"
        else:
            nitrogen_status = "partial"
        summaries.append({
            "technology_id": technology_id,
            "fertilizer_scheme_id": group[0]["fertilizer_scheme_id"],
            "cultivar": group[0]["cultivar"],
            "sowing_date": group[0]["sowing_date"], "population": group[0]["population"],
            "depth": group[0]["depth"], "row": group[0]["row"],
            "fertilizer_passes": group[0]["fertilizer_passes"],
            "fertilizer_cost": group[0].get("fertilizer_cost"),
            "financing_cost": group[0].get("financing_cost", group[0].get("finance_cost")),
            "finance_cost": group[0].get("finance_cost"),
            "application_cost": group[0].get("application_cost"),
            "fertilizer_controllable_cost": group[0].get("fertilizer_controllable_cost"),
            "seed_rate_kg_ha": group[0].get("seed_rate_kg_ha"),
            "seed_cost_total": group[0].get("seed_cost_total"),
            "seed_cost_adjustment": group[0].get("seed_cost_adjustment"),
            "seed_economics_status": group[0].get("seed_economics_status"),
            "controllable_cost": group[0].get("controllable_cost"),
            "scenario_count": len(group),
            "scenario_ids": ",".join(row["scenario_id"] for row in group),
            "cache_hit_count": sum(1 for row in group if row.get("cache_hit") is True),
            "actual_apsim_runs": sum(int(row.get("actual_apsim_run", 0)) for row in group),
            "profit_mean": sum(profits) / len(profits) if profits and not has_failed_run else None,
            "profit_avg": sum(profits) / len(profits) if profits and not has_failed_run else None,
            "profit_min": min(profits) if profits and not has_failed_run else None,
            "constraints_status": aggregate_status,
            "is_feasible": False if has_failed_run else aggregate_feasible,
            "rejection_reason": "; ".join(sorted({row["rejection_reason"] for row in group if row["rejection_reason"]})),
            "run_status": "failed" if has_failed_run else "completed",
            "error_message": "; ".join(sorted({row.get("error_message", "") for row in group if row.get("error_message")})),
            **scenario_mineral_n,
            "MineralN_end_mean": mean("MineralN_end"),
            "MineralN_end_min": minimum("MineralN_end"),
            "OrganicC_end_mean": mean("OrganicC_end"),
            "OrganicC_end_min": minimum("OrganicC_end"),
            "DenitrifiedN_mean": mean("DenitrifiedN"),
            "NAtmosphereLoss_mean": mean("NAtmosphereLoss"),
            "NO3Leaching_mean": mean("NO3Leaching"),
            "nitrogen_balance_outputs_status": nitrogen_status,
            **scenario_profits,
        })
    return sorted(summaries, key=lambda row: (row["profit_mean"] is None, -(row["profit_mean"] or -1e99), -(row["profit_min"] or -1e99), row["fertilizer_passes"]))
