"""Strict, event-based multi-field APSIM SQLite reader and objective calculations."""
from __future__ import annotations

import datetime as dt
import copy
import math
import sqlite3
from pathlib import Path


def finite(value, label):
    if value is None or isinstance(value, bool):
        raise ValueError(f'{label}: missing numeric value')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{label}: non-finite value')
    return value


def table_rows(db, table):
    name = '"' + table.replace('"', '""') + '"'
    return [dict(r) for r in db.execute(f'SELECT * FROM {name}')]


def read_results(path, meta, year, config=None, audit_applications=True):
    if not Path(path).is_file():
        raise ValueError(f'Missing APSIM database: {path}')
    result = {'year': year, 'fields': {}}
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if '_Messages' in tables:
            # Models.Core.MessageType: Error=0, Warning=1, Information=2.
            errors = [dict(r) for r in db.execute('SELECT * FROM _Messages WHERE MessageType = 0')]
            if errors:
                raise ValueError(f'APSIM errors: {errors[:3]}')
        sims = table_rows(db, '_Simulations')
        ids = {s['ID'] for s in sims if s['Name'] == meta['simulation']}
        if len(ids) != 1:
            raise ValueError('Missing or ambiguous simulation identity')
        simulation_id = next(iter(ids))

        def rows(table):
            if table not in tables:
                raise ValueError(f'Missing Report table: {table}')
            values = [r for r in table_rows(db, table) if r['SimulationID'] == simulation_id]
            return [r for r in values if dt.date.fromisoformat(str(r['Date'])[:10]).year == year]

        for field, fm in meta['fields'].items():
            soil = sorted(rows(f'OptSoil_{field}'), key=lambda r: r['Date'])
            dates = [str(r['Date'])[:10] for r in soil]
            expected = [(dt.date(year, 1, 1) + dt.timedelta(days=i)).isoformat()
                        for i in range((dt.date(year+1, 1, 1)-dt.date(year, 1, 1)).days)]
            if dates != expected:
                raise ValueError(f'{field}: incomplete or duplicate daily output for {year}')
            yields, events = {}, {}
            rotation = fm['rotation']['RotationCrops'].split(',')
            expected_crops = rotation[year - int(meta['start'][:4])].strip().split('+')
            for crop in fm['crops']:
                table = f'OptHarvest_{field}_{crop}'
                # APSIM does not create a table if its event never occurs.
                # An absent non-rotation crop is valid; expected crops still fail closed.
                harvested = rows(table) if table in tables or crop in expected_crops else []
                values = [finite(r['YieldDM'], f'{field}/{crop} harvest') for r in harvested]
                if any(v < 0 for v in values):
                    raise ValueError('Negative harvest mass')
                if crop in expected_crops and not harvested:
                    raise ValueError(f'{field}/{crop}: no harvest event in {year}')
                yields[crop] = sum(values)
                events[crop] = len(values)
            area = config['fields'][field]['area_ha'] if config is not None else fm['area_ha']
            fr = {'area_ha': finite(area, f'{field}.area_ha'), 'model_area_ha': fm['area_ha'],
                  'yield_dm_kg_ha': yields, 'harvest_events': events,
                  'mineral_n_end_kg_ha': finite(soil[-1]['MineralN_NO3_NH4'], field),
                  'urea_n_end_kg_ha': finite(soil[-1]['UreaN'], field), 'nutrients': []}
            if config is not None:
                fs = config['fields'][field]
                if audit_applications:
                    fr['applied_product_kg_ha'] = finite(soil[-1]['AppliedProduct'], field)
                    fr['application_passes'] = finite(soil[-1]['ApplicationPasses'], field)
                for i, n in enumerate(fs.get('nutrients') or []):
                    sample = soil if n['period'] == 'daily' else [r for r in soil if str(r['Date'])[:10] == n['date']]
                    if not sample:
                        raise ValueError(f'{field}: no soil observation at configured date')
                    vals = [finite(r[f'Nutrient{i}'], f'{field}/{n["name"]}') for r in sample]
                    fr['nutrients'].append({'name': n['name'], 'unit': n['unit'],
                        'observed_min': min(vals), 'observed_max': max(vals),
                        'lower': n.get('min'), 'upper': n.get('max'),
                        'normalization_scale': n.get('normalization_scale')})
            result['fields'][field] = fr
    return result


