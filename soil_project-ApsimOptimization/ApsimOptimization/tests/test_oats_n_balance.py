import pytest

from src.oats_n_balance import build_oats_n_balance


def test_builds_phase_resolved_partial_balance_and_preserves_native_signs():
    daily = [
        {"scenario": "control", "field": "Field2", "date": "2023-05-01",
         "OatsZadok": 20, "MineralN": 50, "OatsRootNUptake_total": -2,
         "Denitrification_total_native": 0.2, "NFlow_bottom_native": -0.1,
         "OatsAGN": 1, "OatsGrainNDemand": 0, "OatsGrainNAllocation": 0},
        {"scenario": "control", "field": "Field2", "date": "2023-07-01",
         "OatsZadok": 50, "MineralN": 35, "OatsRootNUptake_total": -3,
         "Denitrification_total_native": 0.3, "NFlow_bottom_native": -0.2,
         "OatsAGN": 4, "OatsGrainNDemand": 1, "OatsGrainNAllocation": 0.5},
        {"scenario": "control", "field": "Field2", "date": "2023-08-01",
         "OatsZadok": 75, "MineralN": 20, "OatsRootNUptake_total": -1,
         "Denitrification_total_native": 0.1, "NFlow_bottom_native": -0.3,
         "OatsAGN": 5, "OatsGrainNDemand": 2, "OatsGrainNAllocation": 1,
         "OatsNitrogenDemandGm2": 0.5, "OatsNitrogenSupplyGm2": 0.3},
    ]
    summaries = [{
        "scenario": "control", "field": "Field2", "observed_yield_t_ha": 3.4,
        "simulated_yield_t_ha": 1.0,
        "grain_fill_nitrogen_stress_days_fn_below_0_8": 1,
        "OatsLeafFn_mean": 0.7,
    }]
    row = build_oats_n_balance(daily, summaries)[0]
    assert row["mineral_n_at_heading_model_value"] == 35
    assert row["mineral_n_at_grain_fill_start_model_value"] == 20
    assert row["root_n_uptake_active_native_sum"] == -6
    assert row["root_n_uptake_magnitude_if_native_nonpositive"] == 6
    assert row["denitrification_active_native_sum"] == 0.6
    assert row["bottom_n_flow_active_native_sum"] == pytest.approx(-0.6)
    assert row["plant_n_demand_active_sum_g_m2"] == pytest.approx(0.5)
    assert row["soil_n_supply_active_sum_g_m2"] == pytest.approx(0.3)
    assert row["soil_n_supply_to_demand_active_ratio"] == pytest.approx(0.6)
    assert row["days_soil_n_supply_below_plant_demand"] == 1
    assert row["balance_closure_status"] == "partial_reported_terms_only"
