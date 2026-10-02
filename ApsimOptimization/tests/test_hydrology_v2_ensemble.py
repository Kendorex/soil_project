from scripts.prepare_hydrology_v2_ensemble import quantile


def test_quantile_linear_interpolation():
    assert quantile([0, 10], 0.25) == 2.5
    assert quantile([3], 0.95) == 3
