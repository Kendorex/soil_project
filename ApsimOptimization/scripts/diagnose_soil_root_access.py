#!/usr/bin/env python3
"""Bracket spring-crop root access using soil endpoints already stored in APSIMX.

Every scenario is an engineering sensitivity test. AirDry, LL15, and DUL come
from the model itself; none of the variants is an approved soil calibration.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import (child, inventory, kind, parameters, prepare,
                                  read_json, write_json)
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_quality import aggregate_summary, build_checkpoint, metrics


SCENARIOS = {
    'unmodified': {'crop_ll': None, 'initial_water': None, 'oats_only': False},
    'unmodified_oats_only': {'crop_ll': None, 'initial_water': None, 'oats_only': True},
    'crop_ll_midpoint_airdry': {
        'crop_ll': 'midpoint_airdry', 'initial_water': None, 'oats_only': False},
    'crop_ll_at_airdry': {'crop_ll': 'airdry', 'initial_water': None, 'oats_only': False},
    'initial_water_at_dul': {'crop_ll': None, 'initial_water': 'dul', 'oats_only': False},
    'crop_ll_airdry_and_initial_dul': {
        'crop_ll': 'airdry', 'initial_water': 'dul', 'oats_only': False},
    'crop_ll_airdry_initial_dul_oats_only': {
        'crop_ll': 'airdry', 'initial_water': 'dul', 'oats_only': True},
}
TARGET_CROPS = {'Овёс', 'Яровая пшеница'}


def patch_soil(model_path, scenario):
    """Patch top 300 mm using only AirDry/current-LL/DUL values in each field."""
    if scenario not in SCENARIOS:
        raise ValueError(f'Unknown scenario: {scenario}')
    settings = SCENARIOS[scenario]
    data = read_json(model_path)
    audit = []
    simulations = [node for node in data.get('Children', [])
                   if kind(node) == 'Models.Core.Simulation' and node.get('Enabled', True)]
    if len(simulations) != 1:
        raise ValueError('Expected one enabled simulation')
    zones = [node for node in simulations[0].get('Children', [])
             if kind(node) == 'Models.Core.Zone' and node.get('Enabled', True)]
    for zone in zones:
        soil = child(zone, 'Soil')
        physical = child(soil, 'Physical')
        water = child(soil, 'Water')
        if settings['crop_ll']:
            for crop_name in ('WheatSoil', 'OatsSoil'):
                crop = child(physical, crop_name)
                for layer in (0, 1):
                    before = float(crop['LL'][layer])
                    airdry = float(physical['AirDry'][layer])
                    after = (airdry if settings['crop_ll'] == 'airdry' else
                             (before+airdry)/2.0)
                    crop['LL'][layer] = after
                    audit.append({'field': zone['Name'], 'object': crop_name,
                                  'parameter': 'LL', 'layer': layer+1,
                                  'before': before, 'after': after,
                                  'source_endpoint': 'Physical.AirDry'})
        if settings['initial_water'] == 'dul':
            for layer in (0, 1):
                before = float(water['InitialValues'][layer])
                after = float(physical['DUL'][layer])
                water['InitialValues'][layer] = after
                audit.append({'field': zone['Name'], 'object': 'Water',
                              'parameter': 'InitialValues', 'layer': layer+1,
                              'before': before, 'after': after,
                              'source_endpoint': 'Physical.DUL'})
        if settings['oats_only'] and zone['Name'] == 'Field3':
            manager = child(zone, 'Sow using a variable rule')
            values = parameters(manager)
            for key in ('RotationCrops', 'RotationCultivars', 'RotationSowingDepths',
                        'RotationRowSpacings', 'RotationPopulations'):
                entries = [entry.strip() for entry in values[key].split(',')]
                before = entries[0]
                after = 'Oats' if key == 'RotationCrops' else before.split(';')[0]
                entries[0] = after
                next(item for item in manager['Parameters'] if item['Key'] == key)['Value'] = ', '.join(entries)
                audit.append({'field': zone['Name'], 'object': manager['Name'],
                              'parameter': key, 'layer': None,
                              'before': before, 'after': after,
                              'source_endpoint': 'first mixture component'})
    write_json(model_path, data)
    return audit


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(
            key for row in rows for key in row)))
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', default=str(
        ROOT/'configs'/'multifield_2023_n_diagnostic.yaml'))
    parser.add_argument('--scenarios', nargs='+', choices=list(SCENARIOS),
                        default=list(SCENARIOS))
    args = parser.parse_args(argv)
    if 'unmodified' not in args.scenarios:
        parser.error('include unmodified as the reference')

    config = load_config(args.config)
    observations_path = ROOT/'data'/'observations'/'multifield_2019_2023.json'
    observations = json.loads(observations_path.read_text(encoding='utf-8-sig'))
    output = (Path(config['output_dir']).parent /
              ('soil_root_access_diagnostic_'+uuid.uuid4().hex[:12]))
    summaries, field_rows_all, audit_all = [], [], []
    for scenario in args.scenarios:
        work = output/scenario
        model = prepare(config, work)
        audit = patch_soil(model, scenario)
        audit_all.extend({'scenario': scenario, **row} for row in audit)
        run_config = dict(config, model=str(model), weather=str(work/'weather.met'),
                          rotation_parameter_reference=None)
        write_json(work/'config_snapshot.json', run_config)
        write_json(work/'model_inventory.json', inventory(run_config))
        run_apsim(config, model)
        _, _, field_rows, _ = build_checkpoint(
            work, observations, label=scenario,
            notes='Engineering soil/root-access sensitivity')
        field_rows_all.extend({'scenario': scenario, **row} for row in field_rows)
        aggregate = aggregate_summary(field_rows)
        spring = [row for row in field_rows if row['crop_group'] in TARGET_CROPS]
        targets_2019 = [row for row in spring if row['year'] == 2019]
        summaries.append({
            'scenario': scenario,
            'patch_count': len(audit),
            'oats_only_2019': SCENARIOS[scenario]['oats_only'],
            'overall_rmse_t_ha': aggregate['overall']['rmse_t_ha'],
            'overall_mae_t_ha': aggregate['overall']['mae_t_ha'],
            'spring_annual_n': metrics(spring)['n'],
            'spring_annual_rmse_t_ha': metrics(spring)['rmse_t_ha'],
            'spring_annual_mae_t_ha': metrics(spring)['mae_t_ha'],
            'field3_2019_yield_t_ha': next(row['simulated_yield_t_ha'] for row in targets_2019
                                           if row['field'] == 'Field3'),
            'field7_2019_yield_t_ha': next(row['simulated_yield_t_ha'] for row in targets_2019
                                           if row['field'] == 'Field7'),
        })
        print(f'{scenario}: overall RMSE={summaries[-1]["overall_rmse_t_ha"]:.3f}; '
              f'spring RMSE={summaries[-1]["spring_annual_rmse_t_ha"]:.3f}; '
              f'2019 Field3={summaries[-1]["field3_2019_yield_t_ha"]:.3f}, '
              f'Field7={summaries[-1]["field7_2019_yield_t_ha"]:.3f}', flush=True)

    reference = next(row for row in summaries if row['scenario'] == 'unmodified')
    for row in summaries:
        row['overall_rmse_change_t_ha'] = (
            row['overall_rmse_t_ha']-reference['overall_rmse_t_ha'])
        row['spring_rmse_change_t_ha'] = (
            row['spring_annual_rmse_t_ha']-reference['spring_annual_rmse_t_ha'])
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output/'scenario_metrics.csv', summaries)
    write_csv(output/'field_metrics.csv', field_rows_all)
    write_csv(output/'patch_audit.csv', audit_all)
    write_json(output/'summary.json', {
        'status': 'engineering_diagnostic_only',
        'question': ('Are 2019 spring failures controlled by crop extraction lower limits '
                     'or by initial profile water?'),
        'soil_depth_tested_mm': 300,
        'scenarios': summaries,
        'patches': audit_all,
        'warning': ('AirDry, midpoint and DUL variants bracket mechanisms only. They are '
                    'not validated soil parameters or optimization bounds. Oats-only '
                    'changes later rotation carryover, so multi-year aggregate changes '
                    'must not be attributed to 2019 competition alone.'),
    })
    print(f'SOIL ROOT ACCESS DIAGNOSTIC: {output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
