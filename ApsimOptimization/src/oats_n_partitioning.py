"""Observed-range nitrogen mechanism tests for the multi-field oat model."""
from __future__ import annotations

import datetime as dt
from typing import Any

from .multifield_model import child, kind, walk


FERTILIZER_CALL = "            Fertiliser.Apply(amount: Amount, type: FertiliserType);"


def patch_fertilizer_amount_on_date(
    data: dict[str, Any], fields: tuple[str, ...], date: dt.date, amount_kg_ha: float
) -> list[dict[str, Any]]:
    """Replace only the amount on one known sowing date and preserve product/type."""
    simulations = [
        node for _, node in walk(data)
        if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)
    ]
    if len(simulations) != 1:
        raise ValueError(f"Expected one enabled Simulation, found {len(simulations)}")
    simulation = simulations[0]
    changes = []
    for field in fields:
        zone = child(simulation, field)
        manager = child(zone, "Fertilise at sowing")
        code = "\n".join(manager.get("CodeArray") or [])
        if code.count(FERTILIZER_CALL) != 1:
            raise ValueError(f"{field}: unrecognized fertilizer manager")
        replacement = (
            "            double diagnosticAmount = Amount;\n"
            f"            if (Clock.Today.Date == new DateTime({date.year}, {date.month}, {date.day}))\n"
            f"                diagnosticAmount = {float(amount_kg_ha):.17g};\n"
            "            Fertiliser.Apply(amount: diagnosticAmount, type: FertiliserType);"
        )
        manager["CodeArray"] = code.replace(FERTILIZER_CALL, replacement, 1).splitlines()
        values = {item["Key"]: item["Value"] for item in manager.get("Parameters", [])}
        changes.append({
            "field": field,
            "date": date.isoformat(),
            "diagnostic_amount_kg_ha": float(amount_kg_ha),
            "fertilizer_type_preserved": values.get("FertiliserType"),
            "original_default_amount_kg_ha": float(values["Amount"]),
        })
    return changes


def compare_doses(rows: list[dict[str, Any]], low: float, high: float) -> list[dict[str, Any]]:
    """Return transparent high-minus-low field deltas for key mechanism outputs."""
    by_key = {(float(row["diagnostic_n_kg_ha"]), row["field"]): row for row in rows}
    fields = sorted({row["field"] for row in rows})
    metrics = (
        "simulated_yield_t_ha", "max_lai", "max_aboveground_biomass_g_m2",
        "nitrogen_stress_days_fn_below_0_8", "grain_fill_nitrogen_stress_days_fn_below_0_8",
        "final_grain_number_m2", "final_grain_size_g", "simulated_harvest_index",
        "grain_potential_allocation_to_demand_ratio", "grain_n_allocation_to_demand_ratio",
    )
    result = []
    for field in fields:
        left, right = by_key[(float(low), field)], by_key[(float(high), field)]
        item: dict[str, Any] = {"field": field, "low_n_kg_ha": low, "high_n_kg_ha": high}
        for metric in metrics:
            a, b = left.get(metric), right.get(metric)
            item[f"{metric}_low"] = a
            item[f"{metric}_high"] = b
            item[f"{metric}_change"] = (
                float(b) - float(a) if a is not None and b is not None else None
            )
        result.append(item)
    return result

