import math

from src.economy import calculate_economics, fertilizer_cost, validate_numeric_identity


CONSTANTS = {
    "grain_price": 14300.0, "constant_C": 23000.0, "application_cost": 480.0,
    "harvest_variable_cost": 0.0, "cleaning_cost": 500.0, "handling_cost": 350.0,
    "finance_rate": 0.18, "finance_days": 240.0,
}


def test_economic_formula_matches_numeric_form():
    scheme = {"passes": 1, "events": [{"type_apsimx": "UreaN", "amount": 30}]}
    result = calculate_economics(5.0, scheme, {"UreaN": 77.17}, CONSTANTS)
    material = 30 * 77.17
    c_f = material + 480
    assert math.isclose(result["fertilizer_cost"], 30 * 77.17)
    assert math.isclose(result["application_cost"], 480)
    assert math.isclose(result["financing_cost"], material * 0.18 * 240 / 365)
    assert math.isclose(result["controllable_cost"], c_f + material * 0.18 * 240 / 365)
    assert math.isclose(result["profit"], 13450 * 5 - 23000 - result["controllable_cost"])


def test_control_scheme_has_zero_fertilizer_cost():
    assert fertilizer_cost({"passes": 0, "events": []}, {}, 480) == 0


def test_numeric_identity_validation():
    validate_numeric_identity(CONSTANTS)
