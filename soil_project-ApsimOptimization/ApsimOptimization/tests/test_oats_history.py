from src.oats_history import (cultivar_mapping_status, prediction_direction,
                              summarize_oats_history)


def test_prediction_direction_uses_sign_without_quality_threshold():
    assert prediction_direction(-0.01) == "underpredicted"
    assert prediction_direction(0.01) == "overpredicted"
    assert prediction_direction(0.0) == "equal"
    assert prediction_direction(None) == "unavailable"


def test_cultivar_mapping_handles_yakov_transliteration():
    assert cultivar_mapping_status("Яков", "Yakov;First_RedClover") == "matched_yakov"
    assert cultivar_mapping_status("Аргамак", "Yakov;First_RedClover") == (
        "different_or_unconfirmed"
    )


def test_history_summary_separates_development_and_validation_years():
    rows = [
        {
            "year": 2019, "field": "Field3", "observed_yield_t_ha": 3.0,
            "simulated_yield_t_ha": 0.0, "error_t_ha": -3.0,
            "observed_cultivar": "Аргамак", "model_cultivar": "Yakov",
            "diagnostic_status": "no_active_crop_rows", "max_lai": 0.0,
        },
        {
            "year": 2022, "field": "Field4", "observed_yield_t_ha": 1.0,
            "simulated_yield_t_ha": 2.0, "error_t_ha": 1.0,
            "observed_cultivar": "Яков", "model_cultivar": "Yakov",
            "diagnostic_status": "complete_active_crop", "max_lai": 3.0,
        },
        {
            "year": 2023, "field": "Field2", "observed_yield_t_ha": 3.0,
            "simulated_yield_t_ha": 1.0, "error_t_ha": -2.0,
            "observed_cultivar": "Яков", "model_cultivar": "Yakov",
            "diagnostic_status": "complete_active_crop", "max_lai": 2.0,
        },
    ]
    summary = summarize_oats_history(rows)
    assert summary["all_years"]["mae_t_ha"] == 2.0
    assert summary["supported_cultivar_all_years"]["mae_t_ha"] == 1.5
    assert summary["development_years"]["mae_t_ha"] == 1.0
    assert summary["validation_year"]["mae_t_ha"] == 2.0
    assert summary["mechanism_groups_by_error_sign"]["underpredicted"]["case_ids"] == [
        "2023/Field2"
    ]
