import math

from src.economy import calculate_economics, calculate_seed_economics


CONSTANTS = {
    "grain_price": 14300.0, "constant_C": 23000.0, "application_cost": 480.0,
    "harvest_variable_cost": 0.0, "cleaning_cost": 500.0, "handling_cost": 350.0,
    "finance_rate": 0.18, "finance_days": 240.0,
}


def test_controllable_cost_uses_material_application_and_material_financing():
    result = calculate_economics(
        2.0,
        {"passes": 2, "events": [{"type_apsimx": "UreaN", "amount": 30}]},
        {"UreaN": 10.0},
        CONSTANTS,
    )
    expected_financing = 300 * 0.18 * 240 / 365
    assert result["fertilizer_cost"] == 300
    assert result["application_cost"] == 960
    assert math.isclose(result["financing_cost"], expected_financing)
    assert math.isclose(result["controllable_cost"], 300 + 960 + expected_financing)
    assert math.isclose(result["profit"], 13450 * 2 - 23000 - result["controllable_cost"])


SEED_CONFIG = {
    "enabled": True,
    "mode": "incremental_from_baseline",
    "values_status": "provisional",
    "baseline_population": 250,
    "thousand_seed_weight_g": 45,
    "germination_fraction": 0.92,
    "purity_fraction": 0.98,
    "field_establishment_fraction": 0.90,
    "seed_price_per_kg": 30,
}


def test_seed_cost_increases_with_population_without_double_counting_baseline():
    baseline = calculate_seed_economics(250, SEED_CONFIG)
    increased = calculate_seed_economics(500, SEED_CONFIG)
    assert baseline["seed_cost_adjustment"] == 0
    assert increased["seed_rate_kg_ha"] > baseline["seed_rate_kg_ha"]
    assert math.isclose(
        increased["seed_cost_adjustment"],
        (increased["seed_rate_kg_ha"] - baseline["seed_rate_kg_ha"]) * 30,
    )


def test_controllable_cost_includes_incremental_seed_cost():
    result = calculate_economics(
        2.0,
        {"passes": 0, "events": []},
        {},
        CONSTANTS,
        population=500,
        seed_config=SEED_CONFIG,
    )
    assert result["fertilizer_controllable_cost"] == 0
    assert result["seed_cost_adjustment"] > 0
    assert result["controllable_cost"] == result["seed_cost_adjustment"]
    assert math.isclose(result["profit"], 13450 * 2 - 23000 - result["seed_cost_adjustment"])
