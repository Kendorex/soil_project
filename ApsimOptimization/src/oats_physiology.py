"""Physiology diagnostics for historical multi-field oat simulations."""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
from pathlib import Path
from typing import Any

from .multifield_model import child, kind, walk


DIAGNOSTIC_EXPRESSIONS = (
    "[Weather].Rain as Rain",
    "[Weather].MaxT as MaxT",
    "[Weather].MinT as MinT",
    "[Weather].Radn as Radn",
    "[Oats].Leaf.Fw as OatsLeafFw",
    "[Oats].Leaf.Fn as OatsLeafFn",
    "[Oats].Leaf.LAIDead as OatsLeafLAIDead",
    "[Oats].Leaf.WaterDemand as OatsLeafWaterDemand",
    "[Oats].Leaf.WaterAllocation as OatsLeafWaterAllocation",
    "[Oats].Leaf.PotentialEP as OatsLeafPotentialEP",
    "[Oats].Leaf.Transpiration as OatsLeafTranspiration",
    "[Oats].Leaf.DMSupplyPhotosynthesis as OatsLeafPhotosynthesis",
    "[Oats].Leaf.RadiationIntercepted as OatsLeafRadiationIntercepted",
    "[Oats].Root.Depth as OatsRootDepth",
    "[Oats].Root.FASW as OatsRootFASW",
    "[Oats].Root.WaterTensionFactor as OatsRootWaterTensionFactor",
    "[Oats].Root.WaterUptake as OatsRootWaterUptake",
    "[Oats].Root.NUptake as OatsRootNUptake",
    "[Oats].Root.NUptakeLayered as OatsRootNUptakeLayered",
    "[Oats].Root.LengthDensity as OatsRootLengthDensity",
    "[Oats].Arbitrator.N.TotalPlantDemand as OatsNitrogenDemandGm2",
    "[Oats].Arbitrator.N.TotalUptakeSupply as OatsNitrogenSupplyGm2",
    "[Oats].Population as OatsPopulation",
    "[Oats].Structure.TotalStemPopn as OatsTotalStemPopulation",
    "[Oats].Structure.MainStemPopn as OatsMainStemPopulation",
    "[Oats].Grain.Number as OatsGrainNumber",
    "[Oats].Grain.Size as OatsGrainSize",
    "[Oats].Grain.DMDemand.Total as OatsGrainDMDemand",
    "[Oats].Grain.potentialDMAllocation.Total as OatsGrainPotentialDMAllocation",
    "[Oats].Grain.Allocated.StructuralWt as OatsGrainDMAllocation",
    "[Oats].Grain.NDemand.Total as OatsGrainNDemand",
    "[Oats].Grain.Allocated.StructuralN as OatsGrainNAllocation",
    "[Oats].Stem.Live.Wt as OatsStemLiveWt",
    "[Oats].Leaf.Live.Wt as OatsLeafLiveWt",
    "[NO3].kgha as NO3kgHa",
    "[NH4].kgha as NH4kgHa",
    "[Urea].kgha as UreakgHa",
    "[Nutrient].MineralisedN as MineralisedN",
    "[Nutrient].TotalOrganicN as TotalOrganicN",
    "[SurfaceOrganicMatter].Wt as SurfaceResidueWt",
    "[SurfaceOrganicMatter].N as SurfaceResidueN",
    "[SurfaceOrganicMatter].IncorporatedN as SurfaceResidueIncorporatedN",
    "[Nutrient].Natm as NutrientNatm",
    "[Nutrient].DenitrifiedN as NutrientDenitrifiedN",
    "[Wheat].Root.NUptake as WheatRootNUptake",
    "[RedClover].Root.NUptake as RedCloverRootNUptake",
    "[PlantainForage].Root.NUptake as PlantainForageRootNUptake",
    "[Swim3].LeachNO3 as LeachNO3",
    "[Swim3].LeachNH4 as LeachNH4",
    "[Swim3].LeachUrea as LeachUrea",
    "[NO3].AmountLostInRunoff as NO3RunoffLoss",
    "[NH4].AmountLostInRunoff as NH4RunoffLoss",
    "[Urea].AmountLostInRunoff as UreaRunoffLoss",
)

SCALAR_COLUMNS = (
    "Rain", "MaxT", "MinT", "Radn", "OatsLAI", "OatsZadok",
    "OatsAGWt", "OatsAGN", "OatsYield", "OatsLeafFw", "OatsLeafFn",
    "OatsLeafLAIDead", "OatsLeafWaterDemand", "OatsLeafWaterAllocation",
    "OatsLeafPotentialEP", "OatsLeafTranspiration",
    "OatsLeafPhotosynthesis", "OatsLeafRadiationIntercepted",
    "OatsRootDepth", "OatsRootFASW", "OatsRootWaterTensionFactor",
    "OatsPopulation", "OatsTotalStemPopulation", "OatsMainStemPopulation",
    "OatsGrainNumber", "OatsGrainSize", "OatsGrainDMDemand",
    "OatsGrainPotentialDMAllocation", "OatsGrainDMAllocation",
    "OatsGrainNDemand", "OatsGrainNAllocation", "OatsStemLiveWt",
    "OatsLeafLiveWt",
    "OatsNitrogenDemandGm2", "OatsNitrogenSupplyGm2",
    "SurfaceResidueWt", "SurfaceResidueN", "SurfaceResidueIncorporatedN",
    "WheatRootNUptake", "RedCloverRootNUptake", "PlantainForageRootNUptake",
    "LeachNO3", "LeachNH4", "LeachUrea",
    "WaterStored", "MineralN", "Drainage", "Runoff", "PondDepth",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def patch_oats_diagnostic_reports(
    data: dict[str, Any], fields: tuple[str, ...] = ("Field2", "Field7")
) -> dict[str, Any]:
    """Add source-verified oat outputs to selected temporary field reports.

    Reports on other fields are disabled to keep the diagnostic database small.
    The caller owns ``data``; the calibrated source file is never modified.
    """
    simulations = [
        node for _, node in walk(data)
        if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)
    ]
    if len(simulations) != 1:
        raise ValueError(f"Expected one enabled Simulation, found {len(simulations)}")
    simulation = simulations[0]
    zones = {
        node["Name"]: node for node in simulation.get("Children", [])
        if kind(node) == "Models.Core.Zone" and node.get("Enabled", True)
    }
    missing = set(fields) - set(zones)
    if missing:
        raise ValueError(f"Unknown diagnostic fields: {sorted(missing)}")

    changes: dict[str, Any] = {"selected_fields": list(fields), "reports": {}}
    for field, zone in zones.items():
        reports = [node for node in zone.get("Children", []) if kind(node) == "Models.Report"]
        if field not in fields:
            for report in reports:
                report["Enabled"] = False
            continue
        candidates = [
            report for report in reports
            if any("OatsLAI" in expression for expression in report.get("VariableNames", []))
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"{field}: expected one daily report containing OatsLAI, found {len(candidates)}"
            )
        report = candidates[0]
        report["Enabled"] = True
        variables = report.setdefault("VariableNames", [])
        added = []
        for expression in DIAGNOSTIC_EXPRESSIONS:
            if expression not in variables:
                variables.append(expression)
                added.append(expression)
        changes["reports"][field] = {
            "report_name": report["Name"],
            "added_expressions": added,
            "event_names": list(report.get("EventNames", [])),
        }
    return changes


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def _prefix_values(row: sqlite3.Row, columns: list[str], prefix: str) -> list[float]:
    result = []
    for column in columns:
        if column == prefix or column.startswith(prefix + "("):
            value = _number(row[column])
            if value is not None:
                result.append(value)
    return result


def _sum_prefix(row: sqlite3.Row, columns: list[str], prefix: str) -> float | None:
    values = _prefix_values(row, columns, prefix)
    return sum(values) if values else None


def _first_threshold(rows: list[dict[str, Any]], column: str, threshold: float) -> str | None:
    for row in rows:
        value = _number(row.get(column))
        if value is not None and value >= threshold:
            return str(row["date"])
    return None


def _stats(rows: list[dict[str, Any]], column: str) -> dict[str, float | int | None]:
    values = [_number(row.get(column)) for row in rows]
    usable = [value for value in values if value is not None]
    return {
        f"{column}_n": len(usable),
        f"{column}_min": min(usable) if usable else None,
        f"{column}_mean": sum(usable) / len(usable) if usable else None,
        f"{column}_max": max(usable) if usable else None,
    }


def _sum(rows: list[dict[str, Any]], column: str) -> float | None:
    values = [_number(row.get(column)) for row in rows]
    usable = [value for value in values if value is not None]
    return sum(usable) if usable else None


def _stress_days(rows: list[dict[str, Any]], column: str, threshold: float) -> int | None:
    values = [_number(row.get(column)) for row in rows]
    usable = [value for value in values if value is not None]
    return sum(value < threshold for value in usable) if usable else None


def _first_below(rows: list[dict[str, Any]], column: str, threshold: float) -> str | None:
    for row in rows:
        value = _number(row.get(column))
        if value is not None and value < threshold:
            return str(row["date"])
    return None


def _maximum_with_date(
    rows: list[dict[str, Any]], column: str
) -> tuple[float | None, str | None]:
    values = [(_number(row.get(column)), str(row["date"])) for row in rows]
    usable = [(value, date) for value, date in values if value is not None]
    return max(usable, default=(None, None))


def _iso_day(value: Any) -> str:
    return str(value)[:10]


def collect_oats_diagnostics(
    database: Path,
    inventory: dict[str, Any],
    records: list[dict[str, Any]],
    year: int = 2023,
    scenario: str = "observed_n_labels",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Export active-crop daily rows and field summaries from an APSIM database."""
    oat_records = [row for row in records if "Oats" in row.get("yield_components", [])]
    if not oat_records:
        raise ValueError(f"No oat observations for {year}")

    daily_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    schema: dict[str, Any] = {"year": year, "scenario": scenario, "fields": {}}
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        simulation_rows = connection.execute(
            'SELECT ID, Name FROM "_Simulations" WHERE Name=?',
            (inventory["simulation"],),
        ).fetchall()
        if len(simulation_rows) != 1:
            raise ValueError("Missing or ambiguous simulation in diagnostic database")
        simulation_id = simulation_rows[0]["ID"]

        for record in oat_records:
            field = record["field"]
            reports = inventory["fields"][field]["reports"]
            candidates = [
                name for name, report in reports.items()
                if any("OatsLAI" in expression for expression in report["variables"])
            ]
            if len(candidates) != 1:
                raise ValueError(f"{field}: cannot identify the daily oat report")
            table = candidates[0]
            available = set(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ))
            if table not in available:
                raise ValueError(f"{field}: report table {table} is absent")
            columns = _table_columns(connection, table)
            quoted = ", ".join('"' + value.replace('"', '""') + '"' for value in columns)
            raw_rows = connection.execute(
                f'SELECT {quoted} FROM "{table}" WHERE SimulationID=? '
                'AND substr("Clock.Today",1,4)=? ORDER BY "Clock.Today"',
                (simulation_id, str(year)),
            ).fetchall()
            converted = []
            for raw in raw_rows:
                item: dict[str, Any] = {
                    "scenario": scenario,
                    "field": field,
                    "date": _iso_day(raw["Clock.Today"]),
                    "OatsStage": raw["OatsStage"] if "OatsStage" in columns else None,
                }
                for column in SCALAR_COLUMNS:
                    item[column] = _number(raw[column]) if column in columns else None
                # SWIM3 PAW is an array of layer values. Its unit is intentionally
                # not asserted here; summing layers is only a within-model diagnostic.
                item["PAW_total"] = _sum_prefix(raw, columns, "PAW")
                paw_layers = _prefix_values(raw, columns, "PAW")
                item["PAW_top_layer"] = paw_layers[0] if paw_layers else None
                item["SW_total"] = _sum_prefix(raw, columns, "SW")
                sw_layers = _prefix_values(raw, columns, "SW")
                item["SW_top_layer"] = sw_layers[0] if sw_layers else None
                item["OatsRootWaterUptake_total"] = _sum_prefix(
                    raw, columns, "OatsRootWaterUptake"
                )
                item["OatsRootNUptake_total"] = _sum_prefix(
                    raw, columns, "OatsRootNUptake"
                )
                item["Denitrification_total_native"] = _sum_prefix(
                    raw, columns, "Denitrification"
                )
                n_flow = _prefix_values(raw, columns, "NFlow")
                item["NFlow_bottom_native"] = n_flow[-1] if n_flow else None
                # Preserve individual soil layers for a later comparison with
                # root depth.  APSIM's kgha property supplies kg N/ha per layer.
                for prefix in (
                    "NO3kgHa", "NH4kgHa", "UreakgHa",
                    "MineralisedN", "TotalOrganicN",
                    "NutrientNatm", "NutrientDenitrifiedN",
                    "NO3RunoffLoss", "NH4RunoffLoss", "UreaRunoffLoss",
                    "OatsRootNUptakeLayered", "OatsRootLengthDensity",
                ):
                    values = _prefix_values(raw, columns, prefix)
                    item[f"{prefix}_total"] = sum(values) if values else None
                    for index, value in enumerate(values, 1):
                        item[f"{prefix}_layer_{index}"] = value
                converted.append(item)

            sow_date = dt.date.fromisoformat(record["observed_sow_date"])
            eligible = [row for row in converted if row["date"] >= sow_date.isoformat()]
            active = [row for row in eligible if (_number(row.get("OatsAGWt")) or 0.0) > 0]
            harvest_table = f"OptHarvest_{field}_Oats"
            harvest_columns = (
                _table_columns(connection, harvest_table) if harvest_table in available else []
            )
            harvest_rows = []
            if {"Date", "YieldDM"} <= set(harvest_columns):
                harvest_rows = connection.execute(
                    f'SELECT Date, YieldDM FROM "{harvest_table}" WHERE SimulationID=? '
                    'AND substr(Date,1,4)=? ORDER BY Date',
                    (simulation_id, str(year)),
                ).fetchall()
            simulated_yield = (
                sum(float(row["YieldDM"]) for row in harvest_rows) / 1000.0
                if harvest_rows else None
            )

            observed = float(record["observed_yield_t_ha"])
            required_scalars = {
                "OatsLeafFw", "OatsLeafFn", "OatsLeafWaterDemand",
                "OatsLeafWaterAllocation", "OatsRootDepth", "OatsRootFASW",
                "OatsPopulation", "OatsTotalStemPopulation", "OatsGrainNumber",
                "OatsGrainSize", "OatsGrainDMDemand",
                "OatsGrainPotentialDMAllocation", "OatsGrainDMAllocation",
                "OatsNitrogenDemandGm2", "OatsNitrogenSupplyGm2",
            }
            required_prefixes = {
                "PAW", "SW", "OatsRootWaterUptake", "OatsRootNUptake",
                "Denitrification", "NFlow", "NO3kgHa", "NH4kgHa", "UreakgHa",
                "OatsRootNUptakeLayered", "OatsRootLengthDensity",
                "MineralisedN", "TotalOrganicN",
                "NutrientNatm", "NutrientDenitrifiedN",
                "NO3RunoffLoss", "NH4RunoffLoss", "UreaRunoffLoss",
            }
            present_scalars = required_scalars & set(columns)
            present_prefixes = {
                prefix for prefix in required_prefixes
                if any(name == prefix or name.startswith(prefix + "(") for name in columns)
            }
            requested_count = len(required_scalars) + len(required_prefixes)
            present_count = len(present_scalars) + len(present_prefixes)
            missing_outputs = ";".join(sorted(
                (required_scalars - present_scalars) | (required_prefixes - present_prefixes)
            ))

            # A failed establishment is a useful historical result rather than a
            # reason to discard the whole multi-year diagnostic. Preserve it as
            # an explicit zero-yield case when APSIM produced no harvest row.
            if not active:
                simulated_yield = simulated_yield if simulated_yield is not None else 0.0
                summary = {
                    "scenario": scenario,
                    "year": year,
                    "field": field,
                    "crop": record.get("crop"),
                    "observed_cultivar": record.get("cultivar"),
                    "model_cultivar": record.get("model_cultivar"),
                    "technology": record.get("technology"),
                    "diagnostic_status": "no_active_crop_rows",
                    "observed_yield_t_ha": observed,
                    "simulated_yield_t_ha": simulated_yield,
                    "error_t_ha": simulated_yield - observed,
                    "absolute_error_t_ha": abs(simulated_yield - observed),
                    "active_days": 0,
                    "first_active_date": None,
                    "last_active_date": None,
                    "harvest_event_present": bool(harvest_rows),
                    "harvest_dates": ";".join(_iso_day(row["Date"]) for row in harvest_rows),
                    "observed_tillering_date": record.get("observed_tillering_date"),
                    "observed_heading_date": record.get("observed_heading_date"),
                    "diagnostic_completeness_percent": 100.0 * present_count / requested_count,
                    "missing_requested_outputs": missing_outputs,
                }
                summaries.append(summary)
                schema["fields"][field] = {
                    "report_table": table,
                    "available_columns": columns,
                    "active_row_count": 0,
                    "diagnostic_status": "no_active_crop_rows",
                    "missing_requested_outputs": missing_outputs.split(";")
                    if missing_outputs else [],
                }
                continue

            daily_rows.extend(active)

            demand = _sum(active, "OatsLeafWaterDemand")
            allocation = _sum(active, "OatsLeafWaterAllocation")
            vegetative = [
                row for row in active
                if _number(row.get("OatsZadok")) is not None
                and float(row["OatsZadok"]) < 50
            ]
            grain_fill = [
                row for row in active
                if _number(row.get("OatsZadok")) is not None
                and 71 <= float(row["OatsZadok"]) < 90
            ]
            max_lai, max_lai_date = _maximum_with_date(active, "OatsLAI")
            max_biomass, max_biomass_date = _maximum_with_date(active, "OatsAGWt")
            max_root_depth, max_root_depth_date = _maximum_with_date(active, "OatsRootDepth")
            max_stems, max_stems_date = _maximum_with_date(active, "OatsTotalStemPopulation")
            last = active[-1]
            final_population = _number(last.get("OatsPopulation"))
            final_grain_number = _number(last.get("OatsGrainNumber"))
            final_grain_size = _number(last.get("OatsGrainSize"))
            grain_dm_demand = _sum(active, "OatsGrainDMDemand")
            grain_potential_allocation = _sum(active, "OatsGrainPotentialDMAllocation")
            grain_actual_allocation = _sum(active, "OatsGrainDMAllocation")
            grain_n_demand = _sum(active, "OatsGrainNDemand")
            grain_n_allocation = _sum(active, "OatsGrainNAllocation")
            summary: dict[str, Any] = {
                "scenario": scenario,
                "year": year,
                "field": field,
                "crop": record.get("crop"),
                "observed_cultivar": record.get("cultivar"),
                "model_cultivar": record.get("model_cultivar"),
                "technology": record.get("technology"),
                "diagnostic_status": "complete_active_crop",
                "observed_yield_t_ha": observed,
                "simulated_yield_t_ha": simulated_yield,
                "error_t_ha": simulated_yield - observed if simulated_yield is not None else None,
                "absolute_error_t_ha": (
                    abs(simulated_yield - observed) if simulated_yield is not None else None
                ),
                "active_days": len(active),
                "first_active_date": active[0]["date"],
                "last_active_date": active[-1]["date"],
                "harvest_event_present": bool(harvest_rows),
                "harvest_dates": ";".join(_iso_day(row["Date"]) for row in harvest_rows),
                "simulated_tillering_date": _first_threshold(active, "OatsZadok", 21),
                "simulated_heading_date": _first_threshold(active, "OatsZadok", 50),
                "simulated_maturity_date": _first_threshold(active, "OatsZadok", 90),
                "observed_tillering_date": record.get("observed_tillering_date"),
                "observed_heading_date": record.get("observed_heading_date"),
                "max_lai": max_lai,
                "max_lai_date": max_lai_date,
                "max_aboveground_biomass_g_m2": max_biomass,
                "max_aboveground_biomass_date": max_biomass_date,
                "max_root_depth_mm": max_root_depth,
                "max_root_depth_date": max_root_depth_date,
                "final_plant_population_m2": final_population,
                "max_stem_population_m2": max_stems,
                "max_stem_population_date": max_stems_date,
                "max_stems_per_plant": (
                    max_stems / final_population
                    if max_stems is not None and final_population not in (None, 0) else None
                ),
                "final_grain_number_m2": final_grain_number,
                "final_grain_size_g": final_grain_size,
                "final_grains_per_stem": (
                    final_grain_number / max_stems
                    if final_grain_number is not None and max_stems not in (None, 0) else None
                ),
                "grain_dm_demand_sum_g_m2": grain_dm_demand,
                "grain_potential_dm_allocation_sum_g_m2": grain_potential_allocation,
                "grain_actual_dm_allocation_sum_g_m2": grain_actual_allocation,
                "grain_potential_allocation_to_demand_ratio": (
                    grain_potential_allocation / grain_dm_demand
                    if grain_potential_allocation is not None and grain_dm_demand not in (None, 0)
                    else None
                ),
                "grain_n_demand_sum_g_m2": grain_n_demand,
                "grain_n_allocation_sum_g_m2": grain_n_allocation,
                "grain_n_allocation_to_demand_ratio": (
                    grain_n_allocation / grain_n_demand
                    if grain_n_allocation is not None and grain_n_demand not in (None, 0)
                    else None
                ),
                "grain_dm_demand_unmet_days": sum(
                    1 for row in active
                    if (_number(row.get("OatsGrainDMDemand")) or 0) >
                    (_number(row.get("OatsGrainPotentialDMAllocation")) or 0) + 1e-9
                ),
                "simulated_harvest_index": (
                    simulated_yield * 100.0 / max_biomass
                    if simulated_yield is not None and max_biomass not in (None, 0) else None
                ),
                "water_stress_days_fw_below_0_8": _stress_days(active, "OatsLeafFw", 0.8),
                "severe_water_stress_days_fw_below_0_5": _stress_days(
                    active, "OatsLeafFw", 0.5
                ),
                "nitrogen_stress_days_fn_below_0_8": _stress_days(active, "OatsLeafFn", 0.8),
                "severe_nitrogen_stress_days_fn_below_0_5": _stress_days(
                    active, "OatsLeafFn", 0.5
                ),
                "first_water_stress_date_fw_below_0_8": _first_below(
                    active, "OatsLeafFw", 0.8
                ),
                "first_nitrogen_stress_date_fn_below_0_8": _first_below(
                    active, "OatsLeafFn", 0.8
                ),
                "vegetative_days": len(vegetative),
                "vegetative_water_stress_days_fw_below_0_8": _stress_days(
                    vegetative, "OatsLeafFw", 0.8
                ),
                "vegetative_nitrogen_stress_days_fn_below_0_8": _stress_days(
                    vegetative, "OatsLeafFn", 0.8
                ),
                "grain_fill_days": len(grain_fill),
                "grain_fill_water_stress_days_fw_below_0_8": _stress_days(
                    grain_fill, "OatsLeafFw", 0.8
                ),
                "grain_fill_nitrogen_stress_days_fn_below_0_8": _stress_days(
                    grain_fill, "OatsLeafFn", 0.8
                ),
                "leaf_water_demand_sum": demand,
                "leaf_water_allocation_sum": allocation,
                "leaf_water_allocation_to_demand_ratio": (
                    allocation / demand if allocation is not None and demand not in (None, 0) else None
                ),
                "root_water_uptake_sum": _sum(active, "OatsRootWaterUptake_total"),
                # APSIM reports root uptake with its native sign convention.
                "root_n_uptake_native_sum": _sum(active, "OatsRootNUptake_total"),
                "denitrification_native_sum": _sum(
                    active, "Denitrification_total_native"
                ),
                "bottom_n_flow_native_sum": _sum(active, "NFlow_bottom_native"),
                "mineral_n_first_active": _number(active[0].get("MineralN")),
                "mineral_n_last_active": _number(active[-1].get("MineralN")),
                "oats_aboveground_n_first_native": _number(active[0].get("OatsAGN")),
                "oats_aboveground_n_last_native": _number(active[-1].get("OatsAGN")),
                "rain_sum_mm": _sum(active, "Rain"),
                "diagnostic_completeness_percent": 100.0 * present_count / requested_count,
                "missing_requested_outputs": missing_outputs,
            }
            for column in (
                "OatsLeafFw", "OatsLeafFn", "OatsRootFASW",
                "OatsRootWaterTensionFactor", "OatsRootDepth", "PAW_total",
                "PAW_top_layer", "SW_top_layer", "MineralN",
                "OatsTotalStemPopulation", "OatsGrainNumber", "OatsGrainSize",
                "OatsGrainDMDemand", "OatsGrainPotentialDMAllocation",
            ):
                summary.update(_stats(active, column))
            for observed_key, simulated_key, error_key in (
                ("observed_tillering_date", "simulated_tillering_date", "tillering_error_days"),
                ("observed_heading_date", "simulated_heading_date", "heading_error_days"),
            ):
                summary[error_key] = (
                    (dt.date.fromisoformat(summary[simulated_key]) -
                     dt.date.fromisoformat(summary[observed_key])).days
                    if summary.get(observed_key) and summary.get(simulated_key) else None
                )
            summaries.append(summary)
            schema["fields"][field] = {
                "report_table": table,
                "available_columns": columns,
                "active_row_count": len(active),
                "diagnostic_status": "complete_active_crop",
                "missing_requested_outputs": summary["missing_requested_outputs"].split(";")
                if summary["missing_requested_outputs"] else [],
            }

    if any(row["diagnostic_status"] == "no_active_crop_rows" for row in summaries):
        schema["status"] = "complete_with_failed_establishment_cases"
    elif all(not row["missing_requested_outputs"] for row in summaries):
        schema["status"] = "complete"
    else:
        schema["status"] = "partial_missing_outputs"
    return daily_rows, summaries, schema
