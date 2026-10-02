from pathlib import Path

from src.met_weather import audit_met, write_units_corrected


def make_weather(path):
    rows = []
    import datetime as dt
    day = dt.date(2020, 1, 1)
    while day.year == 2020:
        seasonal = 10 if 4 <= day.month <= 9 else -10
        rows.append(f'{day.year} {day.timetuple().tm_yday} 10 {seasonal+2} {seasonal-2} 1 2 1000')
        day += dt.timedelta(days=1)
    Path(path).write_text('[weather.met.weather]\ntav = 99\namp = 99\n\n'
                          'year day radn maxt mint rain wind_speed sp\n'
                          '() () (MJ/m2/d) (oC) (oC) (mm) (m/s) (Pa)\n' +
                          '\n'.join(rows)+'\n', encoding='utf-8')


def test_weather_units_correction_is_explicit_and_complete(tmp_path):
    source, target = tmp_path/'bad.met', tmp_path/'fixed.met'
    make_weather(source)
    _, audit = audit_met(source)
    assert 'surface_pressure_hpa_values_declared_as_pa' in audit['issues']
    before, after = write_units_corrected(source, target)
    assert before['issues']
    assert after['issues'] == []
    assert after['surface_pressure_median'] == 100000
