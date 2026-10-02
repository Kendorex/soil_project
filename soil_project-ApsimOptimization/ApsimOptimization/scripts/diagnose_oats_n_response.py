"""Engineering N-response diagnostic for 2023 oats on Field2 and Field7."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations, records_for_year
from src.multifield_model import (child, fertilizer_code, fingerprint, kind, prepare,
                                  read_json, walk, write_json)
from src.multifield_optimizer import ROOT, load_config, run_apsim


FIELDS = ('Field2', 'Field7')
DATE = '2023-05-04'


def patch_oats_fertilizer(model, config, dose):
    data = read_json(model)
    simulation = next(n for _, n in walk(data) if kind(n) == 'Models.Core.Simulation' and n.get('Enabled', True))
    for field in FIELDS:
        zone = child(simulation, field)
        manager = child(zone, 'Fertilise at sowing')
        local = dict(config)
        local['fertilizer_options'] = {'NO3N_DIAGNOSTIC': {'components': [
            {'apsim_type': 'NO3N', 'mass_fraction': 1, 'apsim_amount_per_kg_product': 1}]}}
        local['fields'] = dict(config['fields'])
        local['fields'][field] = dict(config['fields'][field], application_dates=[DATE],
                                      application_depth_mm=0)
        manager['CodeArray'] = fertilizer_code('\n'.join(manager['CodeArray']), local, field,
            {'option': 'NO3N_DIAGNOSTIC', 'dose_kg_product_ha': dose}).splitlines()
    write_json(model, data)


def observed_yields(config):
    observations = load_observations(config['historical_observations'])
    return {r['field']: r['observed_yield_t_ha'] for r in records_for_year(observations, 2023)
            if r['field'] in FIELDS}


def read_metrics(db_path, dose, observed):
    rows = []
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        for field in FIELDS:
            number = field.replace('Field', '')
            daily = [dict(r) for r in db.execute(
                f'''select "Clock.Today" as Date, OatsLAI, OatsAGWt, OatsAGN,
                    OatsYield, WaterStored, MineralN from "Report{number}"
                    where Zone=? and substr("Clock.Today",1,4)='2023'
                    order by "Clock.Today"''', (field,))]
            active = [r for r in daily if float(r['OatsLAI'] or 0) > 0 or float(r['OatsAGWt'] or 0) > 0]
            harvest = db.execute(f'''select sum(YieldDM) from "OptHarvest_{field}_Oats"
                                  where substr(Date,1,4)='2023' ''').fetchone()[0]
            simulated = float(harvest or 0)/1000.0
            rows.append({
                'dose_apsim_no3n_kg_ha': dose,
                'field': field,
                'observed_technology': 'N30' if field == 'Field2' else 'N60P60K60',
                'observed_yield_t_ha': observed[field],
                'simulated_yield_t_ha': simulated,
                'yield_error_t_ha': simulated-observed[field],
                'max_lai': max((float(r['OatsLAI'] or 0) for r in active), default=0),
                'max_agwt_g_m2': max((float(r['OatsAGWt'] or 0) for r in active), default=0),
                'max_crop_n_g_m2': max((float(r['OatsAGN'] or 0) for r in active), default=0),
                'min_water_stored_mm': min((float(r['WaterStored']) for r in active
                                            if r['WaterStored'] is not None), default=None),
                'min_mineral_n_reported': min((float(r['MineralN']) for r in active
                                               if r['MineralN'] is not None), default=None),
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', default=str(ROOT/'configs/multifield.yaml'))
    parser.add_argument('--doses', nargs='+', type=float, default=[0, 30, 60],
                        help='diagnostic APSIM NO3N amounts; these are not optimization bounds')
    args = parser.parse_args()
    config = load_config(args.config)
    observed = observed_yields(config)
    root = Path(config['output_dir'])/('oats_n_diagnostic_'+uuid.uuid4().hex[:12])
    all_rows = []
    for dose in args.doses:
        if dose < 0:
            parser.error('diagnostic doses must be nonnegative')
        work = root/f'n_{dose:g}'
        model = prepare(config, work)
        patch_oats_fertilizer(model, config, dose)
        db = run_apsim(config, model)
        rows = read_metrics(db, dose, observed)
        all_rows.extend(rows)
        print(f'NO3N {dose:g}: ' + ', '.join(
            f'{r["field"]}={r["simulated_yield_t_ha"]:.3f} t/ha' for r in rows), flush=True)
    with (root/'response.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)
    write_json(root/'summary.json', {
        'experiment': 'observed_range_oats_n_response',
        'status': 'engineering_diagnostic_only',
        'inputs': {
            'config': str(Path(args.config).resolve()),
            'model': config['model'],
            'model_sha256': fingerprint(config['model']),
            'weather': config['weather'],
            'weather_sha256': fingerprint(config['weather']),
            'historical_observations': config['historical_observations'],
            'historical_observations_sha256': fingerprint(config['historical_observations']),
        },
        'fertilizer_type': 'APSIM NO3N', 'application_date': DATE,
        'tested_doses_kg_ha': args.doses,
        'rows': all_rows,
        'warning': 'P and K in the observed N60P60K60 technology are not simulated.'})
    print(f'OATS N DIAGNOSTIC: {root}', flush=True)


if __name__ == '__main__':
    main()
