from src.constraints import evaluate


LIMITS = {"N_NO3_min": 4, "P_2O5_min": 51, "SOC_0": 1.5, "strict": False}


def test_missing_soil_non_strict_is_unchecked():
    status, feasible, reason = evaluate({"N_NO3": 5, "P_2O5": None, "SOC": None}, LIMITS)
    assert status == "soil_constraints_unchecked"
    assert feasible is None
    assert "missing exact soil data" in reason


def test_missing_soil_strict_is_infeasible():
    status, feasible, _ = evaluate({"N_NO3": None, "P_2O5": None, "SOC": None}, {**LIMITS, "strict": True})
    assert status == "soil_constraints_unchecked"
    assert feasible is False


def test_all_satisfied():
    assert evaluate({"N_NO3": 4, "P_2O5": 51, "SOC": 1.5}, LIMITS)[:2] == ("checked_passed", True)
