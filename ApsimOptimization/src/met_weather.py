"""Read and audit the subset of APSIM .met used by the multi-field model."""
from __future__ import annotations

import datetime as dt
import math
import statistics
from collections import defaultdict
from pathlib import Path


def read_met(path):
    path = Path(path)
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    header_index = next((index for index, line in enumerate(lines)
                         if line.strip().lower().startswith('year day ')), None)
    if header_index is None or header_index+1 >= len(lines):
        raise ValueError(f'{path}: missing weather column header')
    columns = lines[header_index].split()
    units = lines[header_index+1].split()
    if len(columns) != len(units):
        raise ValueError(f'{path}: weather columns/units differ in length')
    rows = []
    for line_number, line in enumerate(lines[header_index+2:], header_index+3):
        if not line.strip() or line.lstrip().startswith('!'):
            continue
        values = line.split()
        if len(values) != len(columns):
            raise ValueError(f'{path}:{line_number}: expected {len(columns)} values')
        row = {name: float(value) for name, value in zip(columns, values)}
        year, day = int(row['year']), int(row['day'])
        row['date'] = dt.date(year, 1, 1)+dt.timedelta(days=day-1)
        rows.append(row)
    metadata = {}
    for line in lines[:header_index]:
        if '=' in line and not line.lstrip().startswith('!'):
            key, value = line.split('=', 1)
            metadata[key.strip().lower()] = value.strip()
    return {'path': str(path.resolve()), 'lines': lines, 'header_index': header_index,
            'columns': columns, 'units': dict(zip(columns, units)),
            'metadata': metadata, 'rows': rows}


def calculate_tav_amp(rows):
    means = [(row['maxt']+row['mint'])/2 for row in rows]
    monthly = defaultdict(list)
    for row, value in zip(rows, means):
        monthly[(row['date'].year, row['date'].month)].append(value)
    years = sorted({row['date'].year for row in rows})
    amplitudes = []
    for year in years:
        if any((year, month) not in monthly for month in range(1, 13)):
            raise ValueError(f'incomplete monthly temperature coverage for {year}')
        values = [statistics.fmean(monthly[(year, month)]) for month in range(1, 13)]
        amplitudes.append(max(values)-min(values))
    return statistics.fmean(means), statistics.fmean(amplitudes)


def audit_met(path):
    data = read_met(path)
    rows, issues = data['rows'], []
    if not rows:
        issues.append('no_daily_rows')
    dates = [row['date'] for row in rows]
    if len(dates) != len(set(dates)):
        issues.append('duplicate_dates')
    if any((b-a).days != 1 for a, b in zip(dates, dates[1:])):
        issues.append('date_gaps')
    for column in ('radn', 'maxt', 'mint', 'rain'):
        if column not in data['columns']:
            issues.append(f'missing_{column}')
    pressure = [row['sp'] for row in rows] if 'sp' in data['columns'] else []
    if data['units'].get('sp') == '(Pa)' and pressure:
        median = statistics.median(pressure)
        if 500 <= median <= 1500:
            issues.append('surface_pressure_hpa_values_declared_as_pa')
        elif not 50000 <= median <= 120000:
            issues.append('surface_pressure_outside_pa_screen')
    tav, amp = calculate_tav_amp(rows)
    for key, calculated in (('tav', tav), ('amp', amp)):
        try:
            stored = float(data['metadata'][key])
            if not math.isclose(stored, calculated, abs_tol=0.02):
                issues.append(f'{key}_metadata_differs_from_daily_temperature')
        except (KeyError, TypeError, ValueError):
            issues.append(f'{key}_metadata_missing_or_invalid')
    return data, {'issues': issues, 'rows': len(rows),
                  'start': dates[0].isoformat() if dates else None,
                  'end': dates[-1].isoformat() if dates else None,
                  'calculated_tav': tav, 'calculated_amp': amp,
                  'stored_tav': data['metadata'].get('tav'),
                  'stored_amp': data['metadata'].get('amp'),
                  'surface_pressure_unit': data['units'].get('sp'),
                  'surface_pressure_median': statistics.median(pressure) if pressure else None}


def write_units_corrected(source, destination):
    data, audit = audit_met(source)
    required = {'surface_pressure_hpa_values_declared_as_pa',
                'tav_metadata_differs_from_daily_temperature',
                'amp_metadata_differs_from_daily_temperature'}
    if not required <= set(audit['issues']):
        raise ValueError(f'weather does not match the supported correction signature: {audit["issues"]}')
    columns = data['columns']
    output = list(data['lines'][:data['header_index']+2])
    output.insert(1, '! units correction: sp values converted from hPa to declared Pa')
    output.insert(2, '! tav and amp recalculated from the unchanged daily temperature series')
    for index, line in enumerate(output):
        stripped = line.strip().lower()
        if stripped.startswith('tav ='):
            output[index] = f'tav = {audit["calculated_tav"]:.3f}'
        elif stripped.startswith('amp ='):
            output[index] = f'amp = {audit["calculated_amp"]:.3f}'
    for row in data['rows']:
        values = []
        for column in columns:
            value = row[column]*100.0 if column == 'sp' else row[column]
            if column in {'year', 'day'}:
                values.append(str(int(value)))
            elif column == 'sp':
                values.append(f'{value:.1f}')
            else:
                values.append(f'{value:.2f}')
        output.append(' '.join(values))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text('\n'.join(output)+'\n', encoding='utf-8', newline='\n')
    _, corrected_audit = audit_met(destination)
    if corrected_audit['issues']:
        raise ValueError(f'corrected weather still fails audit: {corrected_audit["issues"]}')
    return audit, corrected_audit
