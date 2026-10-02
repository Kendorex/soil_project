from __future__ import annotations

from typing import Any


REQUIRED_CONSTANTS = {
    "grain_price", "constant_C", "application_cost", "harvest_variable_cost",
    "cleaning_cost", "handling_cost", "finance_rate", "finance_days",
}


def fertilizer_cost(scheme: dict[str, Any], prices: dict[str, float], application_cost: float) -> float:
    """Return fertilizer material cost; application is reported separately."""
    material = 0.0
    for event in scheme.get("events", []):
        fertilizer_type = event["type_apsimx"]
        if fertilizer_type not in prices:
            raise ValueError(f"No fertilizer price for {fertilizer_type}")
        material += float(event["amount"]) * prices[fertilizer_type]
    return material


def finance_cost(fertilizer_material_cost: float, finance_rate: float, finance_days: float) -> float:
    return finance_rate * finance_days / 365.0 * fertilizer_material_cost


def calculate_seed_economics(
    population: float | None,
    seed_config: dict[str, Any] | None,
) -> dict[str, float | str | None]:
    config = seed_config or {}
    if not config.get("enabled", False) or population is None:
        return {
            "seed_rate_kg_ha": None,
            "seed_cost_total": None,
            "seed_cost_adjustment": 0.0,
            "seed_economics_status": "disabled",
        }

    required = (
        "baseline_population", "thousand_seed_weight_g", "germination_fraction",
        "purity_fraction", "field_establishment_fraction", "seed_price_per_kg",
    )
    missing = [name for name in required if config.get(name) is None]
    if missing:
        raise ValueError(f"Missing seed economics parameters: {missing}")
    fractions = [
        float(config["germination_fraction"]),
        float(config["purity_fraction"]),
        float(config["field_establishment_fraction"]),
    ]
    if any(value <= 0 or value > 1 for value in fractions):
        raise ValueError("Seed germination, purity and field establishment fractions must be in (0, 1]")
    efficiency = fractions[0] * fractions[1] * fractions[2]
    thousand_seed_weight = float(config["thousand_seed_weight_g"])
    seed_price = float(config["seed_price_per_kg"])

    def rate(target_population: float) -> float:
        return float(target_population) * thousand_seed_weight / (100.0 * efficiency)

    seed_rate = rate(float(population))
    baseline_rate = rate(float(config["baseline_population"]))
    total_cost = seed_rate * seed_price
    mode = str(config.get("mode", "incremental_from_baseline"))
    if mode == "incremental_from_baseline":
        adjustment = (seed_rate - baseline_rate) * seed_price
    elif mode == "full_cost":
        adjustment = total_cost
    else:
        raise ValueError(f"Unsupported seed economics mode: {mode}")
    return {
        "seed_rate_kg_ha": seed_rate,
        "seed_cost_total": total_cost,
        "seed_cost_adjustment": adjustment,
        "seed_economics_status": str(config.get("values_status", "configured")),
    }


def calculate_economics(
    yield_t_ha: float,
    scheme: dict[str, Any],
    prices: dict[str, float],
    constants: dict[str, float],
    population: float | None = None,
    seed_config: dict[str, Any] | None = None,
) -> dict[str, float | str | None]:
    missing = REQUIRED_CONSTANTS - constants.keys()
    if missing:
        raise ValueError(f"Missing economic constants: {sorted(missing)}")
    material = sum(float(event["amount"]) * prices[event["type_apsimx"]] for event in scheme.get("events", []))
    application = float(scheme["passes"]) * constants["application_cost"]
    financing = finance_cost(material, constants["finance_rate"], constants["finance_days"])
    fertilizer_controllable = material + application + financing
    seed = calculate_seed_economics(population, seed_config)
    controllable = fertilizer_controllable + float(seed["seed_cost_adjustment"] or 0.0)
    revenue = yield_t_ha * constants["grain_price"]
    yield_cost = yield_t_ha * (
        constants["harvest_variable_cost"] + constants["cleaning_cost"] + constants["handling_cost"]
    )
    profit = revenue - constants["constant_C"] - controllable - yield_cost
    return {
        "grain_revenue": revenue,
        "fertilizer_material_cost": material,
        "application_cost": application,
        "fertilizer_cost": material,
        "finance_cost": financing,
        "financing_cost": financing,
        "fertilizer_controllable_cost": fertilizer_controllable,
        **seed,
        "controllable_cost": controllable,
        "profit": profit,
    }


def validate_numeric_identity(constants: dict[str, float], tolerance: float = 0.02) -> None:
    coefficient = constants["grain_price"] - constants["harvest_variable_cost"] - constants["cleaning_cost"] - constants["handling_cost"]
    fertilizer_multiplier = 1.0 + constants["finance_rate"] * constants["finance_days"] / 365.0
    if abs(coefficient - 13450.0) > tolerance or abs(fertilizer_multiplier - 1.11836) > tolerance:
        raise ValueError(
            f"Economic constants fail numeric check: yield coefficient={coefficient}, fertilizer multiplier={fertilizer_multiplier}"
        )
