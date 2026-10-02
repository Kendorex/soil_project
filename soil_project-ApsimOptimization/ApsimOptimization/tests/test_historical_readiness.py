import pytest

from src.historical_readiness import evaluate_historical_readiness, spearman_r


def test_spearman_uses_average_ranks_for_ties():
    assert spearman_r([1, 2, 2, 4], [10, 20, 20, 40]) == pytest.approx(1.0)
    assert spearman_r([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_readiness_passes_only_with_held_out_accuracy_and_ranking():
    rows = [
        {'year': 2021, 'field': 'Field1', 'observed_yield_t_ha': 1,
         'simulated_yield_t_ha': 1, 'diagnostic_flags': ''},
        {'year': 2022, 'field': 'Field1', 'observed_yield_t_ha': 3,
         'simulated_yield_t_ha': 3, 'diagnostic_flags': ''},
        {'year': 2023, 'field': 'Field1', 'observed_yield_t_ha': 1,
         'simulated_yield_t_ha': 1.1, 'diagnostic_flags': ''},
        {'year': 2023, 'field': 'Field2', 'observed_yield_t_ha': 3,
         'simulated_yield_t_ha': 2.9, 'diagnostic_flags': ''},
    ]
    result = evaluate_historical_readiness(rows)
    assert result['recommendation_allowed'] is True
    assert result['validation_model_metrics']['spearman_r'] == pytest.approx(1.0)
    assert result['validation_training_mean_metrics']['rmse_t_ha'] == pytest.approx(1.0)


def test_readiness_rejects_missing_harvest_and_bad_ranking():
    rows = [
        {'year': 2022, 'field': 'Field1', 'observed_yield_t_ha': 2,
         'simulated_yield_t_ha': 2, 'diagnostic_flags': ''},
        {'year': 2023, 'field': 'Field1', 'observed_yield_t_ha': 1,
         'simulated_yield_t_ha': 3, 'diagnostic_flags': ''},
        {'year': 2023, 'field': 'Field2', 'observed_yield_t_ha': 3,
         'simulated_yield_t_ha': None, 'diagnostic_flags': 'missing_harvest_event'},
    ]
    result = evaluate_historical_readiness(rows)
    assert result['recommendation_allowed'] is False
    assert 'complete_validation_coverage' in result['failed_checks']
    assert 'all_validation_harvest_events_present' in result['failed_checks']
    assert 'positive_validation_field_ranking' in result['failed_checks']
