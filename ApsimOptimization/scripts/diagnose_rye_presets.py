"""Run standard APSIM winter-wheat phenology presets against observed rye seasons.

The supplied calibrated model is never overwritten. Each preset gets its own
prepared APSIMX and SQLite database under outputs/multifield/rye_diagnostic_*.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import load_observations, records_for_year
from src.multifield_model import prepare, read_json, walk, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim


def patch_cultivar(path, cultivar, commands):
    data = read_json(path)
    nodes = [node for _, node in walk(data) if node.get('Name') == cultivar and
             node.get('$type', '').startswith('Models.PMF.Cultivar')]
    if not nodes:
        raise ValueError(f'Cultivar {cultivar} not found')
    for node in nodes:
        existing = list(node.get('Command', []))
        for command_path, value in commands.items():
            pattern = re.compile(r'^' + re.escape(command_path) + r'\s*=')
            matches = [i for i, command in enumerate(existing) if pattern.match(command)]
            replacement = f'{command_path} = {value}'
            if len(matches) > 1:
                raise ValueError(f'Ambiguous cultivar command {command_path}')
            if matches:
                existing[matches[0]] = replacement
            else:
                existing.append(replacement)
        node['Command'] = existing
    write_json(path, data)
    return len(nodes)


def date_value(value):
    return dt.date.fromisoformat(str(value)[:10])


def evaluate_database(path, observations, diagnostic):
    wanted_years = set(diagnostic['years'])
    records = [r for year in wanted_years for r in records_for_year(observations, year)
               if r['crop_group'] == diagnostic['observed_crop_group']]
    rows = []
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        for record in records:
            field = record['field']
            year = int(record['year'])
            report = 'Report' + field.replace('Field', '')
            sow = dt.date.fromisoformat(record['observed_sow_date'])
            end = dt.date(year, 12, 31)
            daily = [dict(r) for r in db.execute(
                f'''select "Clock.Today" as Date, WheatStage, WheatZadok, WheatLAI,
                    WheatAGWt, WheatYield, WaterStored, MineralN from "{report}"
                    where Zone=? and substr("Clock.Today",1,10) between ? and ?
                    order by "Clock.Today"''', (field, sow.isoformat(), end.isoformat()))]
            heading = next((date_value(r['Date']) for r in daily
                            if r['WheatStage'] in ('Heading', 'HeadEmergence') or
                            (r['WheatZadok'] is not None and float(r['WheatZadok']) >= 50)), None)
            maturity = next((date_value(r['Date']) for r in daily
                             if r['WheatStage'] in ('Maturity', 'HarvestRipe') or
                             (r['WheatZadok'] is not None and float(r['WheatZadok']) >= 90)), None)
            harvest_table = f'OptHarvest_{field}_Wheat'
            harvested = [] if harvest_table not in tables else [r[0] for r in db.execute(
                f'''select YieldDM from "{harvest_table}" where substr(Date,1,4)=?''', (str(year),))]
            simulated_yield = sum(float(v) for v in harvested) / 1000.0
            observed_heading = dt.date.fromisoformat(record['observed_heading_date'])
            rows.append({
                'year': year, 'field': field, 'technology': record['technology'],
                'observed_heading_date': observed_heading.isoformat(),
                'simulated_heading_date': heading.isoformat() if heading else None,
                'heading_error_days': (heading-observed_heading).days if heading else None,
                'simulated_maturity_date': maturity.isoformat() if maturity else None,
                'observed_yield_t_ha': record['observed_yield_t_ha'],
                'simulated_yield_t_ha': simulated_yield,
                'yield_error_t_ha': simulated_yield - record['observed_yield_t_ha'],
                'max_zadok': max((float(r['WheatZadok'] or 0) for r in daily), default=0),
                'max_lai': max((float(r['WheatLAI'] or 0) for r in daily), default=0),
                'max_agwt_g_m2': max((float(r['WheatAGWt'] or 0) for r in daily), default=0),
                'min_water_stored_mm': min((float(r['WaterStored']) for r in daily
                                            if r['WaterStored'] is not None), default=None),
                'min_mineral_n_reported': min((float(r['MineralN']) for r in daily
                                               if r['MineralN'] is not None), default=None),
            })
    return rows


def summarise(rows):
    heading_errors = [r['heading_error_days'] for r in rows if r['heading_error_days'] is not None]
    yield_errors = [r['yield_error_t_ha'] for r in rows]
    return {
        'seasons': len(rows),
        'heading_detected': len(heading_errors),
        'heading_mae_days': sum(abs(v) for v in heading_errors)/len(heading_errors) if heading_errors else None,
        'heading_rmse_days': math.sqrt(sum(v*v for v in heading_errors)/len(heading_errors)) if heading_errors else None,
        'yield_mae_t_ha': sum(abs(v) for v in yield_errors)/len(yield_errors),
        'yield_rmse_t_ha': math.sqrt(sum(v*v for v in yield_errors)/len(yield_errors)),
    }


def write_csv(path, rows):
    with Path(path).open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--config', default=str(ROOT/'configs/multifield.yaml'))
    parser.add_argument('--diagnostic', default=str(ROOT/'configs/rye_diagnostic.yaml'))
    parser.add_argument('--export-model', type=Path)
    parser.add_argument('--candidate')
    args = parser.parse_args()
    if not args.run and not args.export_model:
        parser.error('select --run and/or --export-model')
    import yaml
    config = load_config(args.config)
    diagnostic = yaml.safe_load(Path(args.diagnostic).read_text(encoding='utf-8'))
    observations = load_observations(config['historical_observations'])
    if args.export_model:
        if not args.candidate or args.candidate not in diagnostic['candidates']:
            parser.error('--export-model requires --candidate from the diagnostic configuration')
        destination = args.export_model if args.export_model.is_absolute() else ROOT/args.export_model
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(config['model'], destination)
        selected = diagnostic['candidates'][args.candidate]
        patched = patch_cultivar(destination, diagnostic['cultivar'], selected['commands'])
        def project_path(value):
            path = Path(value).resolve()
            try:
                return path.relative_to(ROOT).as_posix()
            except ValueError:
                return str(path)

        write_json(destination.with_suffix('.provenance.json'), {
            'source_model': project_path(config['model']),
            'diagnostic_config': project_path(args.diagnostic),
            'candidate': args.candidate, 'candidate_source': selected['source'],
            'commands': selected['commands'], 'patched_cultivar_nodes': patched,
            'note': 'Rye phenology calibration; source model preserved unchanged.'})
        print(f'EXPORTED RYE-CALIBRATED MODEL: {destination}', flush=True)
        if not args.run:
            return
    # Attach the year to each source record without changing the stored observations.
    for year in diagnostic['years']:
        for record in records_for_year(observations, year):
            record.setdefault('year', year)
    root = Path(config['output_dir'])/('rye_diagnostic_'+uuid.uuid4().hex[:12])
    all_rows, candidates = [], []
    for name, candidate in diagnostic['candidates'].items():
        work = root/name
        model = prepare(config, work)
        count = patch_cultivar(model, diagnostic['cultivar'], candidate['commands'])
        db = run_apsim(config, model)
        rows = evaluate_database(db, observations, diagnostic)
        for row in rows:
            row['candidate'] = name
            row['source'] = candidate['source']
        metrics = summarise(rows)
        candidates.append({'candidate': name, 'source': candidate['source'],
                           'patched_cultivar_nodes': count, **metrics})
        all_rows.extend(rows)
        print(name, json.dumps(metrics), flush=True)
    candidates.sort(key=lambda r: (-r['heading_detected'],
                    r['heading_rmse_days'] if r['heading_rmse_days'] is not None else float('inf'),
                    r['yield_rmse_t_ha'], r['candidate']))
    write_json(root/'summary.json', {'status': 'diagnostic_only', 'candidates': candidates,
                                    'selected_by_heading': candidates[0]})
    write_csv(root/'seasons.csv', all_rows)
    write_csv(root/'candidates.csv', candidates)
    print(f'RYE DIAGNOSTIC: {root}', flush=True)


if __name__ == '__main__':
    main()
