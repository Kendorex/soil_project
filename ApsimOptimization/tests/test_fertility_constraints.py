import pytest

from src.fertility_constraints import evaluate, validate_config


def settings(mode="objective_only"):
    return {
        "mode": mode,
        "mineral_n_end": {
            "enabled": True,
            "reference": "fixed_map_baseline",
            "min_fraction_of_reference": 0.8,
        },
        "unassessed_indicators": ["mobile_phosphorus", "soil_organic_carbon_change"],
    }


def test_objective_only_reports_but_does_not_reject():
    result = evaluate(
        [{"year": 2023, "field": "Field_1", "mineral_n_end_kg_ha": 30.0}],
        {"2023": {"Field_1": 50.0}},
        settings(),
    )
    assert result["status"] == "checked_failed"
    assert result["normalized_violation"] == pytest.approx(0.2)
    assert result["aggregate_g"] == 0.0
    assert result["is_feasible"] is None
    assert result["unassessed_indicators"] == [
        "mobile_phosphorus", "soil_organic_carbon_change"
    ]


def test_hard_mode_rejects_by_normalized_total_violation():
    result = evaluate(
        [{"year": 2023, "field": "Field_1", "mineral_n_end_kg_ha": 30.0}],
        {"2023": {"Field_1": 50.0}},
        settings("hard"),
    )
    assert result["aggregate_g"] == pytest.approx(0.2)
    assert result["is_feasible"] is False


def test_hard_mode_fails_closed_when_reference_is_missing():
    result = evaluate(
        [{"year": 2023, "field": "Field_1", "mineral_n_end_kg_ha": 30.0}],
        {},
        settings("hard"),
    )
    assert result["status"] == "missing_data"
    assert result["aggregate_g"] > 0
    assert result["is_feasible"] is False


def test_invalid_or_implicit_threshold_is_rejected():
    config = settings()
    config["mineral_n_end"].pop("min_fraction_of_reference")
    assert validate_config(config) == [
        "fertility_constraints.mineral_n_end requires at least one explicit threshold"
    ]
