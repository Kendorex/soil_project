"""Historical field/crop observations and comparison with APSIM output."""
from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path


def load_observations(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def records_for_year(observations, year):
    records = (observations.get('years') or {}).get(str(year))
    if not isinstance(records, list) or not records:
        raise ValueError(f'Historical observations contain no records for {year}')
    return records


def parse_technology_nutrients(value):
    """Extract only nutrient rates explicitly written in a technology label.

    The result deliberately says nothing about product, timing, placement, or
    whether the written rate was actually applied.  Those facts are absent
    from the source workbook.
    """
    text = str(value or '')
    result = {'N': None, 'P': None, 'K': None}
    if 'без удобр' in text.lower():
        return {'N': 0.0, 'P': 0.0, 'K': 0.0}
    translations = {'Н': 'N', 'П': 'P', 'Р': 'P', 'К': 'K'}
    for symbol, number in re.findall(
            r'([NPKНПРК])\s*(\d+(?:[.,]\d+)?)', text.upper()):
        result[translations.get(symbol, symbol)] = float(number.replace(',', '.'))
    return result


def validate_historical_mapping(meta, observations, year):
    """Fail if observations are mapped to a different APSIM rotation."""
    records = records_for_year(observations, year)
    by_field = {}
    index = year - int(meta['start'][:4])
    for record in records:
        field = record.get('field')
        if field in by_field:
            raise ValueError(f'Duplicate historical record for {field}/{year}')
        if field not in meta['fields']:
            raise ValueError(f'Historical record uses unknown field {field}')
        fm = meta['fields'][field]
        crops = [v.strip() for v in fm['rotation']['RotationCrops'].split(',')]
        cultivars = [v.strip() for v in fm['rotation']['RotationCultivars'].split(',')]
        if not 0 <= index < len(crops):
            raise ValueError(f'{field}: no rotation entry for {year}')
        if record.get('model_crop') != crops[index] or record.get('model_cultivar') != cultivars[index]:
            raise ValueError(
                f'{field}/{year}: historical crop mapping differs from APSIM rotation '
                f'({record.get("model_crop")}/{record.get("model_cultivar")} vs '
                f'{crops[index]}/{cultivars[index]})')
        components = record.get('yield_components')
        if not isinstance(components, list) or not components or set(components) - set(fm['crops']):
            raise ValueError(f'{field}/{year}: invalid yield_components')
        value = record.get('observed_yield_t_ha')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f'{field}/{year}: invalid observed yield')
        by_field[field] = record
    expected = set(meta['fields'])
    if observations.get('complete_field_coverage_by_year', {}).get(str(year)) is True and set(by_field) != expected:
        raise ValueError(f'Historical observations marked complete for {year} but fields differ from APSIM')
    return by_field


def compare_historical(results, meta, observations, year):
    records = validate_historical_mapping(meta, observations, year)
    if results.get('year') != year:
        raise ValueError(f'APSIM results are for {results.get("year")}, expected {year}')
    rows = []
    for field in meta['fields']:
        if field not in records:
            continue
        record = records[field]
        simulated = sum(results['fields'][field]['yield_dm_kg_ha'][c]
                        for c in record['yield_components']) / 1000.0
        observed = float(record['observed_yield_t_ha'])
        error = simulated - observed
        area = float(results['fields'][field]['area_ha'])
        mineral_n = results['fields'][field].get('mineral_n_end_kg_ha')
        urea_n = results['fields'][field].get('urea_n_end_kg_ha')
        rows.append({
            'field': field,
            'crop_group': record['crop_group'],
            'technology_observed': record['technology'],
            'model_crop': record['model_crop'],
            'model_cultivar': record['model_cultivar'],
            'yield_components': record['yield_components'],
            'observed_yield_t_ha': observed,
            'simulated_yield_t_ha': simulated,
            'error_t_ha': error,
            'absolute_error_t_ha': abs(error),
            'area_ha_used': area,
            'apsim_zone_area_ha': results['fields'][field].get('model_area_ha', area),
            'observed_total_t': observed * area,
            'simulated_total_t': simulated * area,
            'simulated_mineral_n_end_kg_ha': mineral_n,
            'simulated_urea_n_end_kg_ha': urea_n,
        })
    if not rows:
        raise ValueError(f'No comparable historical fields for {year}')
    n = len(rows)
    observed_total = sum(r['observed_total_t'] for r in rows)
    simulated_total = sum(r['simulated_total_t'] for r in rows)
    metrics = {
        'fields_compared': n,
        'mae_t_ha': sum(r['absolute_error_t_ha'] for r in rows) / n,
        'rmse_t_ha': math.sqrt(sum(r['error_t_ha'] ** 2 for r in rows) / n),
        'mean_bias_t_ha': sum(r['error_t_ha'] for r in rows) / n,
        'observed_total_t': observed_total,
        'simulated_total_t': simulated_total,
        'total_error_percent':
            100.0 * (simulated_total - observed_total) / observed_total if observed_total else None,
    }
    return {
        'year': year,
        'decision_period': observations.get('decision_period'),
        'source': observations.get('source'),
        'metrics': metrics,
        'objective_status': {
            'yield': 'calculated against historical observations',
            'net_profit': 'not calculated: multi-crop prices, product conversions and field costs are unconfirmed',
            'soil_state': 'APSIM end-of-year mineral N is reported without pass/fail because approved intervals are missing',
        },
        'fields': rows,
        'interpretation_limits': observations.get('interpretation_limits', []),
    }


def write_comparison_csv(path, comparison):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [k for k in comparison['fields'][0] if k != 'yield_components']
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in comparison['fields']:
            writer.writerow({k: v for k, v in row.items() if k in fields})
