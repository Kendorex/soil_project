"""Engineering N-response curves for every 2023 field and its active product."""
from __future__ import annotations

import argparse
import csv
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations, records_for_year
from src.multifield_model import inventory, prepare, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_results import read_results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', default=str(ROOT/'configs/multifield_2023_n_diagnostic.yaml'))
    parser.add_argument('--doses', nargs='+', type=float, default=[0, 30, 60, 90, 120],
                        help='APSIM NO3N amounts; diagnostic levels, not optimization bounds')
    parser.add_argument('--fields', nargs='+', help='subset of Field names; default is all fields')
    args = parser.parse_args()

    config = load_config(args.config)
    meta = inventory(config)
    selected = args.fields or list(config['fields'])
    unknown = set(selected) - set(config['fields'])
    if unknown:
        parser.error(f'unknown fields: {sorted(unknown)}')
    if any(dose < 0 for dose in args.doses):
        parser.error('diagnostic doses must be nonnegative')
    option = next((name for name, value in config['fertilizer_options'].items()
                   if len(value.get('components') or []) == 1 and
                   value['components'][0].get('apsim_type') == 'NO3N' and
                   value['components'][0].get('mass_fraction') == 1.0 and
                   value['components'][0].get('apsim_amount_per_kg_product') == 1.0), None)
    if option is None:
        raise ValueError('configuration has no 1:1 diagnostic NO3N option')

    observed = {r['field']: r for r in records_for_year(
        load_observations(config['historical_observations']), config['year'])}
    root = Path(config['output_dir']).parent/('field_n_diagnostic_'+uuid.uuid4().hex[:12])
    rows = []
    for dose in args.doses:
        decisions = {field: dict(decision)
                     for field, decision in config['reference_decision'].items()}
        for field in selected:
            decisions[field] = {'option': option, 'dose_kg_product_ha': dose}
        work = root/f'n_{dose:g}'
        model = prepare(config, work, decisions)
        db = run_apsim(config, model)
        result = read_results(db, meta, config['year'], config)
        for field in selected:
            fs = config['fields'][field]
            field_result = result['fields'][field]
            simulated = sum(field_result['yield_dm_kg_ha'][crop]
                            for crop in fs['objective_yield_components'])/1000.0
            baseline_n = fs['nutrients'][0]['min']
            rows.append({
                'dose_apsim_no3n_kg_ha': dose,
                'field': field,
                'crop_group': observed[field]['crop_group'],
                'application_date': fs['application_dates'][0],
                'observed_technology': observed[field]['technology'],
                'observed_yield_t_ha': observed[field]['observed_yield_t_ha'],
                'simulated_yield_t_ha': simulated,
                'yield_error_t_ha': simulated-observed[field]['observed_yield_t_ha'],
                'mineral_n_end_kg_ha': field_result['mineral_n_end_kg_ha'],
                'baseline_mineral_n_end_kg_ha': baseline_n,
                'mineral_n_change_from_baseline_kg_ha':
                    field_result['mineral_n_end_kg_ha']-baseline_n,
            })
        print(f'NO3N {dose:g}: ' + ', '.join(
            f'{row["field"]}={row["simulated_yield_t_ha"]:.3f} t/ha'
            for row in rows[-len(selected):]), flush=True)

    zero = {row['field']: row['simulated_yield_t_ha'] for row in rows
            if row['dose_apsim_no3n_kg_ha'] == 0}
    for row in rows:
        row['yield_change_from_n0_t_ha'] = (
            row['simulated_yield_t_ha']-zero[row['field']] if row['field'] in zero else None)
    root.mkdir(parents=True, exist_ok=True)
    with (root/'response.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_json(root/'summary.json', {
        'status': 'engineering_diagnostic_only',
        'year': config['year'],
        'fertilizer_type': 'APSIM NO3N',
        'fields': selected,
        'doses': args.doses,
        'rows': rows,
        'warning': ('These levels are sensitivity probes, not fertilizer bounds. '
                    'P and K are not simulated; economics are not evaluated.'),
    })
    print(f'FIELD N DIAGNOSTIC: {root}', flush=True)


if __name__ == '__main__':
    main()
