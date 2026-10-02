#!/usr/bin/env python3
"""Test whether 2019 spring-crop failures are sensitive to surface rainfall.

Added rainfall is an engineering perturbation. It is never written to the source
weather file and must not be interpreted as a corrected weather record.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import inventory, prepare, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_quality import build_checkpoint


YEAR = 2019
TARGETS = ('Field3', 'Field7')
DEFAULT_PULSES = (
    ('unmodified', 127, 0.0, 1),
    ('sowing_plus_50_mm', 127, 50.0, 1),
    ('emergence_plus_30_mm', 136, 30.0, 1),
    ('emergence_10_mm_for_14d', 136, 10.0, 14),
)


def add_rainfall(path, year, doy, added_mm):
    """Add rain to exactly one .met row and return an auditable change record."""
    path = Path(path)
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    header = next((line.split() for line in lines
                   if line.strip().lower().startswith('year day ')), None)
    if not header or 'rain' not in [value.lower() for value in header]:
        raise ValueError(f'Cannot locate year/day/rain header in {path}')
    rain_index = [value.lower() for value in header].index('rain')
    matches = 0
    before = after = None
    for index, line in enumerate(lines):
        parts = line.split()
        if len(parts) <= rain_index:
            continue
        try:
            row_year, row_doy = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if row_year == year and row_doy == doy:
            before = float(parts[rain_index])
            after = before+float(added_mm)
            parts[rain_index] = f'{after:.2f}'
            lines[index] = ' '.join(parts)
            matches += 1
    if matches != 1:
        raise ValueError(f'Expected one weather row for {year}/{doy}, found {matches}')
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return {'year': year, 'day_of_year': doy, 'rain_before_mm': before,
            'rain_added_mm': float(added_mm), 'rain_after_mm': after}


def add_rainfall_period(path, year, first_doy, added_mm_per_day, days):
    """Apply a bounded daily pulse series, retaining every edited source value."""
    return [add_rainfall(path, year, first_doy+offset, added_mm_per_day)
            for offset in range(days)]


def write_csv(path, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_pulse(value):
    """Parse LABEL:DOY:MM[:DAYS] while keeping scenario names filesystem-safe."""
    try:
        parts = value.split(':')
        if len(parts) not in (3, 4):
            raise ValueError()
        label, doy_text, rain_text = parts[:3]
        doy, rain = int(doy_text), float(rain_text)
        days = int(parts[3]) if len(parts) == 4 else 1
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError('pulse must be LABEL:DAY_OF_YEAR:MM[:DAYS]') from exc
    if (not label.replace('_', '').isalnum() or not 1 <= doy <= 365 or rain < 0 or
            days < 1 or doy+days-1 > 365):
        raise argparse.ArgumentTypeError('pulse has invalid label, day, rainfall, or duration')
    return label, doy, rain, days


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', default=str(
        ROOT/'configs'/'multifield_2023_n_diagnostic.yaml'))
    parser.add_argument('--pulse', action='append', type=parse_pulse,
                        help=('repeatable LABEL:DAY_OF_YEAR:MM[:DAYS]; defaults test '
                              'single pulses and sustained post-emergence moisture'))
    args = parser.parse_args(argv)
    pulses = tuple(args.pulse or DEFAULT_PULSES)
    if sum(rain == 0 for _, _, rain, _ in pulses) != 1:
        parser.error('include exactly one zero-rain reference pulse')
    if len({label for label, _, _, _ in pulses}) != len(pulses):
        parser.error('pulse labels must be unique')

    config = load_config(args.config)
    observations_path = ROOT/'data'/'observations'/'multifield_2019_2023.json'
    observations = json.loads(
        observations_path.read_text(encoding='utf-8-sig'))
    output = (Path(config['output_dir']).parent /
              ('establishment_water_diagnostic_'+uuid.uuid4().hex[:12]))
    rows, weather_changes = [], []
    for scenario, pulse_doy, added_mm, days in pulses:
        work = output/scenario
        model = prepare(config, work)
        daily_changes = add_rainfall_period(
            work/'weather.met', YEAR, pulse_doy, added_mm, days)
        weather_changes.append({
            'scenario': scenario,
            'first_day_of_year': pulse_doy,
            'days': days,
            'rain_added_mm_per_day': float(added_mm),
            'rain_added_total_mm': float(added_mm)*days,
            'daily_changes': daily_changes,
        })
        run_config = dict(config, model=str(model), weather=str(work/'weather.met'),
                          rotation_parameter_reference=None)
        write_json(work/'config_snapshot.json', run_config)
        write_json(work/'model_inventory.json', inventory(run_config))
        run_apsim(config, model)
        _, _, field_rows, traces = build_checkpoint(
            work, observations, label=scenario,
            notes='2019 engineering rainfall sensitivity')
        by_field = {(row['year'], row['field']): row for row in field_rows}
        trace_by_field = {(row['year'], row['field']): row for row in traces}
        for field in TARGETS:
            row = by_field[(YEAR, field)]
            trace = trace_by_field[(YEAR, field)]
            rows.append({
                'scenario': scenario,
                'rain_added_mm': float(added_mm),
                'pulse_day_of_year': pulse_doy,
                'pulse_date': (dt.date(YEAR, 1, 1)+dt.timedelta(days=pulse_doy-1)).isoformat(),
                'pulse_days': days,
                'rain_added_total_mm': float(added_mm)*days,
                'field': field,
                'crop_group': row['crop_group'],
                'observed_yield_t_ha': row['observed_yield_t_ha'],
                'simulated_yield_t_ha': row['simulated_yield_t_ha'],
                'error_t_ha': row['error_t_ha'],
                'surface_paw_at_model_sowing': row['surface_paw_at_model_sowing'],
                'max_lai': trace['max_lai'],
                'max_aboveground_biomass_g_m2': trace['max_aboveground_biomass_g_m2'],
                'first_active_date': trace['first_active_date'],
                'diagnostic_flags': row['diagnostic_flags'],
            })
        print(f'{scenario}: ' + ', '.join(
            f'{row["field"]}={row["simulated_yield_t_ha"]:.3f} t/ha'
            for row in rows[-len(TARGETS):]), flush=True)

    reference = {row['field']: row for row in rows if row['rain_added_mm'] == 0.0}
    contrasts = [{
        **row,
        'yield_change_from_unmodified_t_ha': (
            row['simulated_yield_t_ha']-reference[row['field']]['simulated_yield_t_ha']),
    } for row in rows]
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output/'results.csv', rows)
    write_csv(output/'contrasts.csv', contrasts)
    write_json(output/'summary.json', {
        'status': 'engineering_diagnostic_only',
        'question': ('Does an artificial rainfall pulse at sowing or emergence recover '
                     'the failed 2019 spring crops?'),
        'year': YEAR,
        'fields': list(TARGETS),
        'pulses': [{'scenario': label, 'day_of_year': doy,
                    'rain_added_mm_per_day': rain, 'days': days,
                    'rain_added_total_mm': rain*days,
                    'date': (dt.date(YEAR, 1, 1)+dt.timedelta(days=doy-1)).isoformat()}
                   for label, doy, rain, days in pulses],
        'weather_changes': weather_changes,
        'rows': rows,
        'contrasts': contrasts,
        'warning': ('Artificial rain is a sensitivity probe, not a weather correction, '
                    'irrigation recommendation, or optimization decision bound.'),
    })
    print(f'ESTABLISHMENT WATER DIAGNOSTIC: {output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
