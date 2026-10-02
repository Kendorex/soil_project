from src.historical_management_audit import (build_management_audit,
                                              inject_missing_n_records)


def test_audit_separates_observed_replay_fallback_and_carry_over():
    inventory = {
        "start": "2019-01-01T00:00:00", "end": "2021-12-31T00:00:00",
        "fields": {"Field2": {
            "rotation": {
                "RotationCrops": "Mustard, Oats+RedClover+PlantainForage, RedClover+PlantainForage",
                "RotationCultivars": "M, Yakov;C;T, C;T",
                "RotationSowingDates": "10-May, 12-May, -",
                "RotationHarvestDates": "31-Aug, 31-Aug, 30-Sep",
            },
            "baseline_fertilizer": {"FertiliserType": "NO3N", "Amount": "30"},
        }},
    }
    observations = {"years": {
        "2020": [{"field": "Field2", "technology": "N60P60K60",
                  "observed_sow_date": "2020-05-12"}],
    }}
    replay = {"entries": [{
        "harvest_year": 2020, "field": "Field2", "amount_kg_n_ha": 60,
        "fertilizer_type": "NO3N",
    }], "skipped": []}
    rows, fields, summary = build_management_audit(inventory, observations, replay)
    assert [row["effective_application_source"] for row in rows] == [
        "model_static_fallback", "observed_n_label", "no_sowing_event"
    ]
    assert rows[0]["fallback_reason"] == "historical_record_missing"
    assert fields[0]["model_static_fallback_events"] == 1
    assert summary["observed_n_label_events"] == 1


def test_first_year_winter_crop_is_startup_approximation():
    inventory = {
        "start": "2019-01-01T00:00:00", "end": "2019-12-31T00:00:00",
        "fields": {"Field5": {
            "rotation": {
                "RotationCrops": "Wheat", "RotationCultivars": "Winter_rye_smoke",
                "RotationSowingDates": "4-Sep", "RotationHarvestDates": "31-Aug",
            },
            "baseline_fertilizer": {"FertiliserType": "NO3N", "Amount": "30"},
        }},
    }
    observations = {"years": {"2019": [{
        "field": "Field5", "technology": "N30", "observed_sow_date": "2018-09-04"
    }]}}
    replay = {"entries": [], "skipped": [{
        "harvest_year": 2019, "field": "Field5", "reason": "sowing_date_outside_model_clock"
    }]}
    rows, _, _ = build_management_audit(inventory, observations, replay)
    assert rows[0]["sowing_event_kind"] == "startup_approximation"
    assert rows[0]["model_sowing_event_date"] == "2019-01-01"
    assert rows[0]["effective_application_source"] == "model_static_fallback"


def test_injects_only_missing_history_for_selected_fields():
    observations = {"years": {"2020": []}}
    audit_rows = [
        {"harvest_year": 2020, "field": "Field7", "rotation_crop": "Rape",
         "model_sowing_event_date": "2020-05-10",
         "effective_application_source": "model_static_fallback",
         "fallback_reason": "historical_record_missing"},
        {"harvest_year": 2020, "field": "Field8", "rotation_crop": "Wheat",
         "model_sowing_event_date": "2020-05-10",
         "effective_application_source": "model_static_fallback",
         "fallback_reason": "historical_record_missing"},
    ]
    result, changes = inject_missing_n_records(
        observations, audit_rows, 60, {"Field7"}, before_year=2023
    )
    assert observations["years"]["2020"] == []
    assert result["years"]["2020"][0]["technology"].endswith("(N60)")
    assert [row["field"] for row in changes] == ["Field7"]
