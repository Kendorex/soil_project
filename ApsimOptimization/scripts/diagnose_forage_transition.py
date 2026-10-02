#!/usr/bin/env python3
"""Test harvesting a pure perennial stand before the following winter sowing.

The current rotation manager can end a forage stand to sow the following winter
crop before the forage's later scheduled cut date.  This diagnostic harvests the
stand on that known transition date.  It patches only a prepared working copy.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations
from src.multifield_model import inventory, prepare, read_json, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim


def event_rows(db_path, meta, observations):
    rows = []
    start_year = int(meta['start'][:4])
    simulation = meta['simulation']
    with sqlite3.connect(db_path) as db:
        simulation_id = db.execute('SELECT ID FROM _Simulations WHERE Name=?',
                                   (simulation,)).fetchone()[0]
        for year_text, records in sorted(observations['years'].items()):
            year = int(year_text)
            for record in records:
                if record['crop_group'] != 'Клевер + тимофеевка':
                    continue
                field = record['field']
                index = year-start_year
                rotation = meta['fields'][field]['rotation']
                crops = [value.strip() for value in rotation['RotationCrops'].split(',')]
                next_crop = crops[index+1] if index+1 < len(crops) else None
                values = []
                dates = []
                component_counts = {}
                for component in record['yield_components']:
                    table = f'OptHarvest_{field}_{component}'
                    events = db.execute(
                        f'SELECT Date, YieldDM FROM "{table}" WHERE SimulationID=? '
                        'AND substr(Date,1,4)=? ORDER BY Date',
                        (simulation_id, str(year))).fetchall()
                    component_counts[component] = len(events)
                    values.extend(float(event[1]) for event in events)
                    dates.extend(str(event[0])[:10] for event in events)
                complete = all(component_counts.get(component) for component in
                               record['yield_components'])
                simulated = sum(values)/1000.0 if complete else None
                rows.append({
                    'year': year,
                    'field': field,
                    'rotation_crop': crops[index],
                    'next_rotation_crop': next_crop,
                    'observed_yield_t_ha': record['observed_yield_t_ha'],
                    'simulated_yield_t_ha': simulated,
                    'yield_error_t_ha': (simulated-record['observed_yield_t_ha']
                                         if simulated is not None else None),
                    'harvest_dates': ';'.join(sorted(set(dates))),
                    'red_clover_events': component_counts.get('RedClover', 0),
                    'plantain_proxy_events': component_counts.get('PlantainForage', 0),
                    'transition_conflict_candidate': bool(
                        next_crop and 'Wheat' in next_crop and
                        'Winter_rye' in [v.strip() for v in
                            rotation['RotationCultivars'].split(',')][index+1]),
                })
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', default=str(
        ROOT/'configs'/'multifield_2023_n_diagnostic.yaml'))
    parser.add_argument('--observations', default=str(
        ROOT/'data'/'observations'/'multifield_2019_2023.json'))
    args = parser.parse_args(argv)

    config = load_config(args.config)
    config['rotation_transition_policy'] = 'harvest_on_winter_sowing_proxy'
    observations = load_observations(args.observations)
    meta = inventory(config)
    output = Path(config['output_dir']).parent/(
        'forage_transition_diagnostic_'+uuid.uuid4().hex[:12])
    model = prepare(config, output/'patched_model')
    preparation = read_json(model.parent/'preparation.json')
    patched = preparation['transition_managers_patched']
    db = run_apsim(config, model)
    rows = event_rows(db, meta, observations)
    conflicts = [row for row in rows if row['transition_conflict_candidate']]
    output.mkdir(parents=True, exist_ok=True)
    with (output/'forage_harvests.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    write_json(output/'summary.json', {
        'status': 'engineering_diagnostic_only',
        'question': ('Are forage harvest events lost because the next winter crop '
                     'ends the perennial stand before its scheduled cut?'),
        'patched_managers': patched,
        'conflicts': conflicts,
        'rows': rows,
        'warning': ('The transition date is imposed by the known next winter sowing. '
                    'Actual forage cut dates remain unconfirmed.'),
    })
    print('FORAGE TRANSITION CASES:', flush=True)
    for row in conflicts:
        print(f'{row["year"]}/{row["field"]}: yield={row["simulated_yield_t_ha"]} '
              f't/ha; harvest={row["harvest_dates"]}', flush=True)
    print(f'FORAGE TRANSITION DIAGNOSTIC: {output}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
