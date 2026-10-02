#!/usr/bin/env python3
"""Engineering sensitivity test for 2023 winter-rye N amount and timing.

The spring date is an explicit numerical probe.  It is not an agronomic
recommendation and it does not change the calibrated source APSIMX file.
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import json
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations, records_for_year
from src.multifield_model import inventory, prepare, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_results import read_results


FIELDS = ('Field5', 'Field9')


def one_to_one_no3_option(config):
    for name, option in config['fertilizer_options'].items():
        components = option.get('components') or []
        if (len(components) == 1 and components[0].get('apsim_type') == 'NO3N' and
                components[0].get('mass_fraction') == 1.0 and
                components[0].get('apsim_amount_per_kg_product') == 1.0):
            return name
    raise ValueError('configuration has no 1:1 diagnostic NO3N option')


def phenology_trace(db_path, field, year):
    table = f'Report{int(field[5:])}'
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            f'''SELECT "Clock.Today" AS Date, WheatZadok, WheatLAI, WheatAGWt,
                       WheatAGN, MineralN, WaterStored
                FROM "{table}"
                WHERE substr("Clock.Today",1,4) IN (?, ?)
                ORDER BY "Clock.Today"''', (str(year-1), str(year))).fetchall()

    def first_zadok(threshold):
        return next((str(row['Date'])[:10] for row in rows
                     if row['WheatZadok'] is not None and
                     float(row['WheatZadok']) >= threshold), None)

    def maximum(column):
        values = [(float(row[column]), str(row['Date'])[:10])
                  for row in rows if row[column] is not None]
        return max(values, default=(None, None))

    max_lai, max_lai_date = maximum('WheatLAI')
    max_biomass, max_biomass_date = maximum('WheatAGWt')
    max_crop_n, max_crop_n_date = maximum('WheatAGN')
    return {
        'simulated_heading_date': first_zadok(50),
        'simulated_maturity_date': first_zadok(90),
        'max_lai': max_lai,
        'max_lai_date': max_lai_date,
        'max_aboveground_biomass_g_m2': max_biomass,
        'max_aboveground_biomass_date': max_biomass_date,
        'max_crop_n_g_m2': max_crop_n,
        'max_crop_n_date': max_crop_n_date,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', default=str(
        ROOT/'configs'/'multifield_2023_n_diagnostic.yaml'))
    parser.add_argument('--spring-date', default='2023-04-01',
                        help='engineering probe date; not a recommendation')
    args = parser.parse_args(argv)
    try:
        spring_date = dt.date.fromisoformat(args.spring_date)
    except ValueError as exc:
        parser.error(f'--spring-date must be ISO YYYY-MM-DD: {exc}')

    config = load_config(args.config)
    year = int(config['year'])
    if spring_date.year != year:
        parser.error('--spring-date must be inside the selected harvest year')
    observed = {row['field']: row for row in records_for_year(
        load_observations(config['historical_observations']), year)}
    option = one_to_one_no3_option(config)
    autumn_date = config['fields']['Field9']['application_dates'][0]
    scenarios = [
        {'scenario': 'autumn_n30', 'dose_per_application': 30.0,
         'dates': [autumn_date]},
        {'scenario': 'autumn_n60', 'dose_per_application': 60.0,
         'dates': [autumn_date]},
        {'scenario': 'spring_n30', 'dose_per_application': 30.0,
         'dates': [spring_date.isoformat()]},
        {'scenario': 'spring_n60', 'dose_per_application': 60.0,
         'dates': [spring_date.isoformat()]},
        {'scenario': 'split_n60', 'dose_per_application': 30.0,
         'dates': [autumn_date, spring_date.isoformat()]},
    ]
    output = Path(config['output_dir']).parent/(
        'rye_n_timing_diagnostic_'+uuid.uuid4().hex[:12])
    rows = []
    for scenario in scenarios:
        run_config = copy.deepcopy(config)
        run_config['fields']['Field9']['application_dates'] = scenario['dates']
        decisions = copy.deepcopy(run_config['reference_decision'])
        decisions['Field9'] = {
            'option': option,
            'dose_kg_product_ha': scenario['dose_per_application'],
        }
        work = output/scenario['scenario']
        model = prepare(run_config, work, decisions)
        db = run_apsim(run_config, model)
        result = read_results(db, inventory(run_config), year, run_config)
        for field in FIELDS:
            field_result = result['fields'][field]
            simulated = field_result['yield_dm_kg_ha']['Wheat']/1000.0
            trace = phenology_trace(db, field, year)
            heading = trace['simulated_heading_date']
            observed_heading = observed[field].get('observed_heading_date')
            rows.append({
                'scenario': scenario['scenario'],
                'field': field,
                'changed_field': field == 'Field9',
                'application_dates': ';'.join(
                    scenario['dates'] if field == 'Field9' else
                    run_config['fields'][field]['application_dates']),
                'dose_per_application_apsim_no3n_kg_ha': (
                    scenario['dose_per_application'] if field == 'Field9' else
                    decisions[field]['dose_kg_product_ha']),
                'application_count': (len(scenario['dates']) if field == 'Field9' else
                                      len(run_config['fields'][field]['application_dates'])),
                'total_apsim_no3n_kg_ha': (
                    scenario['dose_per_application']*len(scenario['dates'])
                    if field == 'Field9' else decisions[field]['dose_kg_product_ha']*
                    len(run_config['fields'][field]['application_dates'])),
                'observed_technology': observed[field]['technology'],
                'observed_yield_t_ha': observed[field]['observed_yield_t_ha'],
                'simulated_yield_t_ha': simulated,
                'yield_error_t_ha': simulated-observed[field]['observed_yield_t_ha'],
                'observed_heading_date': observed_heading,
                **trace,
                'heading_error_days': ((dt.date.fromisoformat(heading)-
                                        dt.date.fromisoformat(observed_heading)).days
                                       if heading and observed_heading else None),
                'mineral_n_end_kg_ha': field_result['mineral_n_end_kg_ha'],
            })
        field9 = rows[-1]
        print(f'{scenario["scenario"]}: Field9={field9["simulated_yield_t_ha"]:.3f} '
              f't/ha; end N={field9["mineral_n_end_kg_ha"]:.2f} kg/ha', flush=True)

    control = next(row for row in rows if row['scenario'] == 'autumn_n30' and
                   row['field'] == 'Field9')
    contrasts = [{
        'scenario': row['scenario'],
        'total_apsim_no3n_kg_ha': row['total_apsim_no3n_kg_ha'],
        'simulated_yield_t_ha': row['simulated_yield_t_ha'],
        'yield_change_from_autumn_n30_t_ha': (
            row['simulated_yield_t_ha']-control['simulated_yield_t_ha']),
        'yield_error_t_ha': row['yield_error_t_ha'],
        'mineral_n_end_kg_ha': row['mineral_n_end_kg_ha'],
        'heading_error_days': row['heading_error_days'],
    } for row in rows if row['field'] == 'Field9']

    output.mkdir(parents=True, exist_ok=True)
    for name, data in (('results.csv', rows), ('contrasts.csv', contrasts)):
        with (output/name).open('w', newline='', encoding='utf-8-sig') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)
    write_json(output/'summary.json', {
        'status': 'engineering_diagnostic_only',
        'question': ('Does application timing, at equal total APSIM NO3N, explain '
                     'the 2023 Field9 winter-rye yield error?'),
        'year': year,
        'changed_field': 'Field9',
        'unchanged_comparator_field': 'Field5',
        'scenarios': scenarios,
        'rows': rows,
        'contrasts': contrasts,
        'warning': ('Dates and APSIM NO3N amounts are sensitivity probes, not '
                    'fertilizer recommendations or approved optimization bounds.'),
    })
    print(f'RYE N TIMING DIAGNOSTIC: {output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
