from scripts.evaluate_hydrology_v2_ensemble import rmse


def test_rmse():
    assert rmse([3, 4]) == (12.5) ** 0.5
    assert rmse([]) is None
