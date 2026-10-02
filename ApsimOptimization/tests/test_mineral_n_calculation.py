import math

from src.nitrogen import calculate_mineral_n_end


def test_ready_mineral_n_has_priority():
    result = calculate_mineral_n_end({"MineralN": 12, "NO3kgHa": 3, "NH4kgHa": 4}, {})
    assert result == {"value": 12.0, "status": "available", "source": "MineralN"}


def test_mineral_n_falls_back_to_no3_plus_nh4():
    result = calculate_mineral_n_end({"NO3kgHa": 3, "NH4kgHa": 4}, {})
    assert result["value"] == 7
    assert result["status"] == "available"


def test_urea_is_not_used_without_permission():
    result = calculate_mineral_n_end({"MineralNWithUrea": 20}, {"allow_urea_in_mineral_n": False})
    assert math.isnan(result["value"])
    assert result["status"] == "partial"


def test_urea_can_only_be_used_with_explicit_permission():
    result = calculate_mineral_n_end({"MineralNWithUrea": 20}, {"allow_urea_in_mineral_n": True})
    assert result["value"] == 20
    assert result["source"] == "MineralNWithUrea"


def test_missing_mineral_n_is_reported():
    result = calculate_mineral_n_end({}, {})
    assert math.isnan(result["value"])
    assert result["status"] == "missing"
