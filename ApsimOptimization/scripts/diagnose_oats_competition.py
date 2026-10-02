#!/usr/bin/env python3
"""Factorial diagnostic of companion-crop competition and N for 2023 oats."""
from __future__ import annotations

import argparse
import copy
import csv
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations, records_for_year
from src.multifield_model import inventory, parameters, prepare, read_json, walk, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_results import read_results


FIELDS = ('Field2', 'Field7')
ROTATION_COMPONENT_KEYS = (
    'RotationCrops', 'RotationCultivars', 'RotationSowingDepths',
    'RotationRowSpacings', 'RotationPopulations')


def patch_oats_only(model_path, year, start_year):
    """Replace only the selected year's mixture entry with its Oats component."""
    data = read_json(model_path)
    changed = []
    index = year-start_year
    for path, node in walk(data):
        field = next((field for field in FIELDS if f'/{field}/' in path), None)
        if field is None or node.get('Name') != 'Sow using a variable rule':
            continue
        values = parameters(node)
        for key in ROTATION_COMPONENT_KEYS:
            entries = [entry.strip() for entry in values[key].split(',')]
            original = entries[index]
            entries[index] = original.split(';')[0] if key != 'RotationCrops' else 'Oats'
            for item in node['Parameters']:
                if item['Key'] == key:
                    item['Value'] = ', '.join(entries)
                    break
            changed.append({'field': field, 'key': key, 'before': original,
                            'after': entries[index]})
    if len(changed) != len(FIELDS)*len(ROTATION_COMPONENT_KEYS):
        raise ValueError(f'Expected {len(FIELDS)*len(ROTATION_COMPONENT_KEYS)} patches, got {len(changed)}')
    write_json(model_path, data)
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', default=str(ROOT/'configs'/'multifield_2023_n_diagnostic.yaml'))
    parser.add_argument('--doses', nargs='+', type=float, default=[30.0, 60.0])
    args = parser.parse_args(argv)
    if any(dose < 0 for dose in args.doses):
        parser.error('doses must be nonnegative')

    config = load_config(args.config)
    meta = inventory(config)
    option = next(name for name, value in config['fertilizer_options'].items()
                  if len(value.get('components') or []) == 1 and
                  value['components'][0].get('apsim_type') == 'NO3N')
    observed = {row['field']: row for row in records_for_year(
        load_observations(config['historical_observations']), config['year'])}
    output = Path(config['output_dir']).parent/('oats_competition_diagnostic_'+uuid.uuid4().hex[:12])
    rows, patch_log = [], []
    for dose in args.doses:
        for system in ('mixture', 'oats_only'):
            decisions = copy.deepcopy(config['reference_decision'])
            for field in FIELDS:
                decisions[field] = {'option': option, 'dose_kg_product_ha': dose}
            work = output/f'{system}_n_{dose:g}'
            model = prepare(config, work, decisions)
            if system == 'oats_only':
                patch_log.extend({'scenario': f'{system}_n_{dose:g}', **row}
                                 for row in patch_oats_only(
                                     model, config['year'], int(meta['start'][:4])))
            run_config = dict(config, model=str(model), rotation_parameter_reference=None)
            run_meta = inventory(run_config)
            db = run_apsim(config, model)
            results = read_results(db, run_meta, config['year'], config)
            for field in FIELDS:
                result = results['fields'][field]
                oats = result['yield_dm_kg_ha'].get('Oats', 0.0)/1000.0
                companion = sum(result['yield_dm_kg_ha'].get(crop, 0.0)
                                for crop in ('RedClover', 'PlantainForage'))/1000.0
                rows.append({
                    'scenario': f'{system}_n_{dose:g}',
                    'system': system,
                    'dose_apsim_no3n_kg_ha': dose,
                    'field': field,
                    'observed_technology': observed[field]['technology'],
                    'observed_oats_yield_t_ha': observed[field]['observed_yield_t_ha'],
                    'simulated_oats_yield_t_ha': oats,
                    'oats_error_t_ha': oats-observed[field]['observed_yield_t_ha'],
                    'companion_harvest_dm_t_ha': companion,
                    'mineral_n_end_kg_ha': result['mineral_n_end_kg_ha'],
                })
            print(f'{system} N{dose:g}: ' + ', '.join(
                f'{row["field"]}={row["simulated_oats_yield_t_ha"]:.3f}'
                for row in rows[-len(FIELDS):]), flush=True)

    contrasts = []
    for dose in args.doses:
        for field in FIELDS:
            mixture = next(row for row in rows if row['field'] == field and
                           row['dose_apsim_no3n_kg_ha'] == dose and row['system'] == 'mixture')
            oats_only = next(row for row in rows if row['field'] == field and
                             row['dose_apsim_no3n_kg_ha'] == dose and row['system'] == 'oats_only')
            contrasts.append({
                'field': field,
                'dose_apsim_no3n_kg_ha': dose,
                'mixture_oats_yield_t_ha': mixture['simulated_oats_yield_t_ha'],
                'oats_only_yield_t_ha': oats_only['simulated_oats_yield_t_ha'],
                'competition_effect_t_ha': (oats_only['simulated_oats_yield_t_ha'] -
                                            mixture['simulated_oats_yield_t_ha']),
            })
    output.mkdir(parents=True, exist_ok=True)
    with (output/'results.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (output/'contrasts.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(contrasts[0]))
        writer.writeheader()
        writer.writerows(contrasts)
    write_json(output/'summary.json', {
        'status': 'engineering_diagnostic_only',
        'question': 'Does simulated companion-crop competition explain low 2023 Oats grain yield?',
        'year': config['year'], 'fields': list(FIELDS), 'doses': args.doses,
        'rows': rows, 'contrasts': contrasts, 'patches': patch_log,
        'warning': ('Oats-only is a structural sensitivity test, not a proposed crop plan. '
                    'APSIM NO3N levels are diagnostic and are not approved fertilizer bounds.'),
    })
    print(f'OATS COMPETITION DIAGNOSTIC: {output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
