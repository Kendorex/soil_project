import pytest

from src.historical_bias import (blocked_year_selection, error_metrics,
                                 fit_group_correction, predict)


def rows():
    return [
        {'year': year, 'field': field, 'crop_group': crop,
         'simulated': simulated, 'observed': simulated+offset}
        for year, field, crop, simulated, offset in [
            (2019, 'Field1', 'oats', 1.0, 1.0),
            (2020, 'Field2', 'oats', 2.0, 1.0),
            (2021, 'Field1', 'wheat', 3.0, -0.5),
            (2022, 'Field2', 'wheat', 2.0, -0.5),
        ]]


def test_group_correction_is_shrunk_and_nonnegative():
    model = fit_group_correction(rows(), ['crop_group'], shrinkage=1)
    assert predict(model, {'crop_group': 'oats', 'simulated': 1}) > 1
    assert predict(model, {'crop_group': 'unknown', 'simulated': -10}) == 0


def test_blocked_year_selection_returns_ranked_cv_and_fitted_model():
    model, evaluated = blocked_year_selection(rows())
    assert evaluated == sorted(evaluated,
                               key=lambda row: (row['rmse_t_ha'], row['mae_t_ha'], row['name']))
    assert predict(model, rows()[0]) >= 0
    assert error_metrics([{**rows()[0], 'prediction': 2.0}])['rmse_t_ha'] == pytest.approx(0)
