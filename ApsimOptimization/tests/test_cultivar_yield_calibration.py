import pytest

from src.cultivar_yield_calibration import (crop_scopes, patch_named_cultivar,
                                             select_by_calibration,
                                             upsert_named_cultivar_commands)


def test_patch_named_cultivar_requires_existing_commands_on_all_fields():
    model = {'Children': [{'$type': 'Models.PMF.Cultivar, Models', 'Name': 'Yakov',
                           'Command': ['[Grain].Size = 0.04']}]}
    changes = patch_named_cultivar(model, 'Yakov', {'[Grain].Size': 0.05},
                                   expected_count=1)
    assert model['Children'][0]['Command'] == ['[Grain].Size = 0.05']
    assert changes[0]['old'] == '0.04'
    with pytest.raises(ValueError, match='missing commands'):
        patch_named_cultivar(model, 'Yakov', {'[Grain].Unknown': 1})


def test_upsert_named_cultivar_commands_records_additions_and_replacements():
    model = {'Children': [{'$type': 'Models.PMF.Cultivar, Models', 'Name': 'Yakov',
                           'Command': ['[Leaf].Area = 3500']}]}
    changes = upsert_named_cultivar_commands(
        model, 'Yakov', {'[Leaf].Area': 4000, '[Structure].Branching.X': '0,1'},
        expected_count=1)
    assert model['Children'][0]['Command'] == [
        '[Leaf].Area = 4000', '[Structure].Branching.X = 0,1']
    assert [row['operation'] for row in changes] == ['replaced', 'added']
    assert changes[0]['old'] == '3500'
    assert changes[1]['old'] is None


def test_selection_uses_calibration_then_requires_held_out_gain_and_rank():
    candidates = [
        {'name': 'current_yakov', 'metrics': {
            'calibration': {'rmse_t_ha': 2.0},
            'validation': {'rmse_t_ha': 1.5, 'spearman_r': -1.0}}},
        {'name': 'reference_profile', 'metrics': {
            'calibration': {'rmse_t_ha': 1.5},
            'validation': {'rmse_t_ha': 1.2, 'spearman_r': 1.0}}},
    ]
    result = select_by_calibration(candidates)
    assert result['selected_on_calibration'] == 'reference_profile'
    assert result['accepted'] is True


def test_selection_rejects_calibration_winner_that_fails_validation():
    candidates = [
        {'name': 'current_yakov', 'metrics': {
            'calibration': {'rmse_t_ha': 2.0},
            'validation': {'rmse_t_ha': 1.0, 'spearman_r': 1.0}}},
        {'name': 'reference_profile', 'metrics': {
            'calibration': {'rmse_t_ha': 1.5},
            'validation': {'rmse_t_ha': 1.1, 'spearman_r': 1.0}}},
    ]
    assert select_by_calibration(candidates)['accepted'] is False


def test_crop_scopes_uses_explicit_yield_component_in_mixed_rotation_label():
    rows = [
        {'year': 2022, 'yield_components': 'Oats',
         'observed_yield_t_ha': 2, 'simulated_yield_t_ha': 1.5},
        {'year': 2023, 'yield_components': 'Oats',
         'observed_yield_t_ha': 3, 'simulated_yield_t_ha': 2.5},
        {'year': 2023, 'yield_components': 'Wheat',
         'observed_yield_t_ha': 3, 'simulated_yield_t_ha': 3},
    ]
    result = crop_scopes(rows)
    assert result['calibration']['n'] == 1
    assert result['validation']['n'] == 1
