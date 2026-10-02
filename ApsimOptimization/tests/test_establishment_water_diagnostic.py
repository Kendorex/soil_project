import pytest

from scripts.diagnose_2019_establishment_water import add_rainfall, parse_pulse


def test_add_rainfall_changes_only_selected_weather_row(tmp_path):
    path = tmp_path/'weather.met'
    path.write_text(
        '[weather.met.weather]\n'
        'year day radn maxt mint rain\n'
        '() () (MJ/m2/d) (oC) (oC) (mm)\n'
        '2019 126 10 12 2 1.00\n'
        '2019 127 11 13 3 2.50\n', encoding='utf-8')
    change = add_rainfall(path, 2019, 127, 10)
    assert change == {'year': 2019, 'day_of_year': 127, 'rain_before_mm': 2.5,
                      'rain_added_mm': 10.0, 'rain_after_mm': 12.5}
    assert '2019 126 10 12 2 1.00' in path.read_text(encoding='utf-8')
    assert '2019 127 11 13 3 12.50' in path.read_text(encoding='utf-8')


def test_parse_pulse():
    assert parse_pulse('emergence_20:136:20') == ('emergence_20', 136, 20.0, 1)
    assert parse_pulse('wet_period:136:10:14') == ('wet_period', 136, 10.0, 14)
    with pytest.raises(Exception):
        parse_pulse('../bad:136:20')
