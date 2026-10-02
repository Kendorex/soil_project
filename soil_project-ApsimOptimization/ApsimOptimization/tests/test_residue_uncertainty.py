import pytest

from scripts.evaluate_residue_uncertainty import (
    build_field_ranges, envelope_metrics, verify_target_scope,
)


def _comparison(field2, field3):
    return {"year": 2023, "fields": [
        {
            "field": "Field2", "crop_group": "Овёс",
            "yield_components": ["Oats"], "observed_yield_t_ha": 3.4,
            "simulated_yield_t_ha": field2,
        },
        {
            "field": "Field3", "crop_group": "Яровая пшеница",
            "yield_components": ["Wheat"], "observed_yield_t_ha": 2.0,
            "simulated_yield_t_ha": field3,
        },
    ]}


def test_builds_reviewable_scenario_envelope_and_scope_check():
    comparisons = {
        "retained": _comparison(1.0, 2.1),
        "exported": _comparison(2.0, 2.1),
    }
    rows = build_field_ranges(comparisons, ["retained", "exported"])
    oats = next(row for row in rows if row["field"] == "Field2")
    assert oats["year"] == 2023
    assert oats["scenario_min_yield_t_ha"] == 1.0
    assert oats["scenario_max_yield_t_ha"] == 2.0
    assert oats["scenario_range_width_t_ha"] == 1.0
    assert oats["observed_inside_scenario_range"] is False
    assert oats["observed_distance_to_range_t_ha"] == pytest.approx(1.4)
    scope = verify_target_scope(rows, {"Field2"})
    assert scope["changed_target_fields"] == ["Field2"]
    assert scope["non_target_fields_unchanged"] is True


def test_rejects_residue_effect_outside_target_fields():
    comparisons = {
        "retained": _comparison(1.0, 2.1),
        "exported": _comparison(2.0, 2.2),
    }
    rows = build_field_ranges(comparisons, ["retained", "exported"])
    with pytest.raises(ValueError, match="non-target"):
        verify_target_scope(rows, {"Field2"})


def test_envelope_metrics_report_coverage_width_and_remaining_error():
    rows = [
        {"observed_inside_scenario_range": True, "scenario_range_width_t_ha": 1.0,
         "observed_distance_to_range_t_ha": 0.0},
        {"observed_inside_scenario_range": False, "scenario_range_width_t_ha": 0.5,
         "observed_distance_to_range_t_ha": 1.0},
    ]
    result = envelope_metrics(rows)
    assert result["inside_range_fraction"] == 0.5
    assert result["mean_range_width_t_ha"] == 0.75
    assert result["mean_distance_to_range_t_ha"] == 0.5
