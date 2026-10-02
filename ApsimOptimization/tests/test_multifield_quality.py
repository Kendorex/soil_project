import pytest

from src.multifield_quality import evidence_tier, metrics, parse_technology, write_csv


def test_quality_metrics_use_observed_vs_simulated_pairs():
    rows = [
        {'observed_yield_t_ha': 1.0, 'simulated_yield_t_ha': 2.0},
        {'observed_yield_t_ha': 3.0, 'simulated_yield_t_ha': 2.0},
        {'observed_yield_t_ha': 99.0, 'simulated_yield_t_ha': None},
    ]
    result = metrics(rows)
    assert result['n'] == 2
    assert result['mae_t_ha'] == pytest.approx(1.0)
    assert result['rmse_t_ha'] == pytest.approx(1.0)
    assert result['bias_t_ha'] == pytest.approx(0.0)
    assert result['r2_1to1'] == pytest.approx(0.0)
    assert result['pearson_r'] is None


@pytest.mark.parametrize(('label', 'expected'), [
    ('интенс N60P60K60', {'N': 60.0, 'P': 60.0, 'K': 60.0}),
    ('интенс Н60Р60К60', {'N': 60.0, 'P': 60.0, 'K': 60.0}),
    ('K70', {'N': None, 'P': None, 'K': 70.0}),
    ('без удобрений', {'N': 0.0, 'P': 0.0, 'K': 0.0}),
    ('Нет данных', {'N': None, 'P': None, 'K': None}),
])
def test_parse_technology(label, expected):
    assert parse_technology(label) == expected


def test_history_csv_accepts_columns_added_by_new_checkpoint(tmp_path):
    path = tmp_path/'history.csv'
    write_csv(path, [{'checkpoint_id': 'old'},
                     {'checkpoint_id': 'new', 'heading_error_days': 2}])
    content = path.read_text(encoding='utf-8-sig')
    assert content.splitlines()[0] == 'checkpoint_id,heading_error_days'
    assert 'old,' in content
    assert 'new,2' in content


@pytest.mark.parametrize(('flags', 'expected'), [
    ([], 'standard'),
    (['zero_surface_paw_at_model_sowing'], 'input_sensitive'),
    (['unsupported_observed_cultivar_proxy', 'zero_surface_paw_at_model_sowing'],
     'cultivar_proxy'),
    (['unresolved_hydrology_case'], 'input_sensitive'),
    (['timothy_represented_by_plantain'], 'structural_proxy'),
    (['observed_sowing_before_model_clock', 'zero_surface_paw_at_model_sowing'],
     'boundary_limited'),
])
def test_evidence_tier_uses_explicit_diagnostic_flags(flags, expected):
    assert evidence_tier(flags) == expected
