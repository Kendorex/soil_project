from src.constraints import evaluate


def test_mineral_n_kg_ha_does_not_activate_no3_mg_kg_constraint():
    values = {"MineralN_end": 100, "OrganicC_end": 1000, "N_NO3": None, "P_2O5": None, "SOC": None}
    status, feasible, _ = evaluate(values, {"strict": False, "N_NO3_min": 4, "P_2O5_min": 51, "SOC_0": 1})
    assert status == "soil_constraints_unchecked"
    assert feasible is None


def test_disabled_constraint_source_is_always_unchecked():
    status, feasible, reason = evaluate(
        {"N_NO3": 10, "P_2O5": 100, "SOC": 2, "SOC_0": 1},
        {"source": "disabled", "strict": False, "N_NO3_min": 4, "P_2O5_min": 51},
    )
    # evaluate receives a source marker from soil_values_for_run in production;
    # this direct assertion checks that the safe marker cannot become feasible.
    from src.constraints import soil_values_for_run
    values = soil_values_for_run({}, {"source": "disabled"}, {"technology_id": "T", "scenario_id": "S"})
    status, feasible, reason = evaluate(values, {"source": "disabled", "strict": False})
    assert status == "soil_constraints_unchecked"
    assert feasible is None
    assert "disabled" in reason
