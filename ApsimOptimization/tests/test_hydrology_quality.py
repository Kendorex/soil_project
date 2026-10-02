from src.hydrology_quality import evaluate_run_hydrology_quality


def test_rejects_near_zero_yield_after_early_water_stress():
    run = {"year": 2023, "simulated_yield_raw": 0, "run_status": "completed"}
    rows = [
        {"Clock.Today": "2023-05-14", "Wheat.Phenology.CurrentStageName": "Emergence", "LeafFw": "1", "Wheat.LAI": ".08"},
        {"Clock.Today": "2023-05-15", "LeafFw": "0", "Wheat.LAI": ".08"},
        {"Clock.Today": "2023-05-16", "LeafFw": "0", "Wheat.LAI": ".08"},
        {"Clock.Today": "2023-05-17", "LeafFw": "0", "Wheat.LAI": ".08"},
    ]
    result = evaluate_run_hydrology_quality(run, rows, retention_profile_validated=False)
    assert result["quality_status"] == "rejected_hydrology_failure"
    assert result["prediction_allowed"] is False
    assert result["establishment_severe_stress_streak_days"] == 3


def test_unvalidated_profile_is_diagnostic_even_when_crop_survives():
    run = {"year": 2020, "simulated_yield_raw": 1100, "run_status": "completed"}
    rows = [
        {"Clock.Today": "2020-05-20", "Wheat.Phenology.CurrentStageName": "Emergence", "LeafFw": "1", "Wheat.LAI": ".1"},
        {"Clock.Today": "2020-05-21", "LeafFw": ".8", "Wheat.LAI": ".2"},
    ]
    result = evaluate_run_hydrology_quality(run, rows, retention_profile_validated=False)
    assert result["quality_status"] == "diagnostic_only_unvalidated_soil"
    assert "soil_retention_profile_not_field_validated" in result["quality_reasons"]
