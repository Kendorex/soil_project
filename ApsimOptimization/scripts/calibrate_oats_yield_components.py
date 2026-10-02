#!/usr/bin/env python3
"""Screen APSIM-reference oat yield profiles using 2019-2022/2023 blocking."""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cultivar_yield_calibration import (crop_scopes, patch_named_cultivar,
                                             select_by_calibration)
from src.historical_case import compare_historical, load_observations, write_comparison_csv
from src.multifield_model import inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_quality import record_checkpoint
from src.multifield_results import read_results


QUALITY = ROOT/'outputs'/'multifield'/'quality_history'
GRAIN_SIZE = '[Grain].MaximumPotentialGrainSize.FixedValue'
GRAIN_NUMBER = '[Grain].NumberFunction.GrainNumber.GrainsPerGramOfStem.FixedValue'
PROFILES = [
    {'name': 'current_yakov', 'values': {GRAIN_SIZE: 0.04, GRAIN_NUMBER: 45},
     'source': 'current supplied multi-field model'},
    {'name': 'apsim_oats_drummond_yield',
     'values': {GRAIN_SIZE: 0.05, GRAIN_NUMBER: 40},
     'source': 'Models/Resources/Oats.json cultivar Drummond'},
    {'name': 'apsim_oats_milton_yield',
     'values': {GRAIN_SIZE: 0.05, GRAIN_NUMBER: 50},
     'source': 'Models/Resources/Oats.json cultivar Milton'},
]


def write_experiment_tables(output, candidates, decision):
    metric_rows = []
    field_rows = []
    for candidate in candidates:
        values = candidate['profile']['values']
        for scope, values_by_scope in candidate['metrics'].items():
            metric_rows.append({
                'candidate': candidate['name'],
                'scope': scope,
                'maximum_potential_grain_size': values[GRAIN_SIZE],
                'grains_per_gram_of_stem': values[GRAIN_NUMBER],
                'selected_on_calibration':
                    candidate['name'] == decision['selected_on_calibration'],
                **values_by_scope,
            })
        checkpoint = Path(candidate['checkpoint'])
        with (checkpoint/'field_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if 'Oats' in str(row.get('yield_components') or '').split('+'):
                    field_rows.append({
                        'candidate': candidate['name'],
                        'year': row['year'],
                        'field': row['field'],
                        'observed_yield_t_ha': row['observed_yield_t_ha'],
                        'simulated_yield_t_ha': row['simulated_yield_t_ha'],
                        'error_t_ha': row['error_t_ha'],
                        'evidence_tier': row['evidence_tier'],
                    })
    for name, rows in (('candidate_metrics.csv', metric_rows),
                       ('oats_field_predictions.csv', field_rows)):
        with (Path(output)/name).open('w', encoding='utf-8-sig', newline='') as stream:
            fields = list(dict.fromkeys(key for row in rows for key in row))
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_profile(config, observations, output, profile):
    output.mkdir(parents=True, exist_ok=False)
    experimental_model = output/'source_candidate.apsimx'
    model = read_json(config['model'])
    changes = patch_named_cultivar(model, 'Yakov', profile['values'], expected_count=10)
    write_json(experimental_model, model)
    case = copy.deepcopy(config)
    case['model'] = str(experimental_model.resolve())
    case['model_provenance'] = None
    case['historical_replay']['enabled'] = False
    meta = inventory(case)
    write_json(output/'config_snapshot.json', case)
    write_json(output/'model_inventory.json', meta)
    prepared = prepare(case, output)
    database = run_apsim(case, prepared)
    result = read_results(database, meta, case['year'], case, audit_applications=False)
    write_json(output/'baseline.json', result)
    comparison = compare_historical(result, meta, observations, case['year'])
    write_json(output/'historical_comparison.json', comparison)
    write_comparison_csv(output/'historical_comparison.csv', comparison)
    checkpoint, _ = record_checkpoint(
        output, case['historical_observations'], QUALITY,
        f'oats_yield_{profile["name"]}_{output.parent.name}',
        'Blocked-year oat yield-component calibration candidate from APSIM reference values')
    with (checkpoint/'field_metrics.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    write_json(output/'candidate_provenance.json', {
        'status': 'engineering_calibration_candidate',
        'profile': profile,
        'base_model': config['model'],
        'base_model_sha256': file_sha256(config['model']),
        'candidate_model_sha256': file_sha256(experimental_model),
        'changes': changes,
        'checkpoint': str(checkpoint),
    })
    return {'name': profile['name'], 'profile': profile, 'changes': changes,
            'checkpoint': str(checkpoint), 'metrics': crop_scopes(rows, 'Oats')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', type=Path,
                        default=ROOT/'configs'/'multifield_historical_replay.yaml')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    observations = load_observations(config['historical_observations'])
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    output = ROOT/'outputs'/'multifield'/'OATS_YIELD_CALIBRATION'/stamp
    output.mkdir(parents=True, exist_ok=False)
    candidates = []
    def display(value):
        return f'{value:.3f}' if value is not None else 'NA'

    for index, profile in enumerate(PROFILES, 1):
        print(f'[{index}/{len(PROFILES)}] {profile["name"]}', flush=True)
        candidate = run_profile(config, observations, output/profile['name'], profile)
        candidates.append(candidate)
        print('  calibration RMSE='
              f'{display(candidate["metrics"]["calibration"]["rmse_t_ha"])}; '
              'validation RMSE='
              f'{display(candidate["metrics"]["validation"]["rmse_t_ha"])}; '
              'validation Spearman='
              f'{candidate["metrics"]["validation"]["spearman_r"]}', flush=True)
    decision = select_by_calibration(candidates)
    payload = {
        'experiment': 'blocked_year_oats_yield_component_calibration',
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'profiles_are_exact_apsim_reference_values_not_agronomic_bounds': True,
        'candidates': candidates,
        'decision': decision,
    }
    write_json(output/'summary.json', payload)
    write_experiment_tables(output, candidates, decision)
    print(f'STATUS: {decision["status"]}', flush=True)
    print(f'SELECTED ON 2019-2022: {decision["selected_on_calibration"]}', flush=True)
    print(f'OUTPUT: {output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
