import copy

from src.oats_field_state import (paired_oat_metrics, swap_observed_technology,
                                  swap_soil_initial_state)


def model(initial_a, initial_b):
    def zone(name, initial):
        return {
            "$type": "Models.Core.Zone, Models", "Name": name, "Enabled": True,
            "Children": [{
                "$type": "Models.Soils.Soil, Models", "Name": "Soil",
                "Children": [{
                    "Name": "Water", "InitialValues": initial, "DUL": [0.4, 0.4],
                    "Children": [],
                }],
            }],
        }
    return {"Name": "Root", "Children": [{
        "$type": "Models.Core.Simulation, Models", "Name": "Sim", "Enabled": True,
        "Children": [zone("Field2", initial_a), zone("Field7", initial_b)],
    }]}


def test_initial_state_swap_is_exact_and_keeps_static_soil():
    data = model([0.2, 0.3], [0.3, 0.4])
    result = swap_soil_initial_state(data, "Field2", "Field7")
    assert result["static_soil_profile_unchanged"] is True
    assert len(result["changed_values"]) == 1
    simulation = data["Children"][0]
    assert simulation["Children"][0]["Children"][0]["Children"][0]["InitialValues"] == [0.3, 0.4]
    assert simulation["Children"][1]["Children"][0]["Children"][0]["InitialValues"] == [0.2, 0.3]


def test_initial_state_swap_refuses_different_static_profiles():
    data = model([0.2], [0.3])
    data["Children"][0]["Children"][1]["Children"][0]["Children"][0]["DUL"] = [0.5]
    try:
        swap_soil_initial_state(data, "Field2", "Field7")
    except ValueError as error:
        assert "Static soil profiles differ" in str(error)
    else:
        raise AssertionError("different static soils must be rejected")


def test_technology_swap_uses_existing_labels_only():
    observations = {"years": {"2023": [
        {"field": "Field2", "technology": "N30"},
        {"field": "Field7", "technology": "N60P60K60"},
    ]}}
    result = swap_observed_technology(observations, 2023, "Field2", "Field7")
    assert result["n_label_kg_ha_before"] == {"Field2": 30.0, "Field7": 60.0}
    assert observations["years"]["2023"][0]["technology"] == "N60P60K60"
    assert observations["years"]["2023"][1]["technology"] == "N30"


def test_pair_metrics_reports_accuracy_and_ordering():
    rows = [
        {"year": 2023, "field": "Field2", "observed_yield_t_ha": 3.4,
         "simulated_yield_t_ha": 3.0, "error_t_ha": -0.4},
        {"year": 2023, "field": "Field7", "observed_yield_t_ha": 3.57,
         "simulated_yield_t_ha": 3.2, "error_t_ha": -0.37},
    ]
    metrics = paired_oat_metrics(copy.deepcopy(rows), 2023, "Field2", "Field7")
    assert round(metrics["rmse_t_ha"], 6) == 0.385292
    assert metrics["ordering_matches_observations"] is True
