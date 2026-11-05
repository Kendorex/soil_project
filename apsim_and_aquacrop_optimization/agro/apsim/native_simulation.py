"""Run an exported APSIM model without legacy management rewrites."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from agro.adapters.apsim import run_apsim
from agro.apsim.met_weather import read_met


def run(config, output, source_config):
    root = Path(config['model_root'])
    model = root / 'simulation.apsimx'
    weather = root / 'weather.met'
    document = json.loads(model.read_text(encoding='utf-8-sig'))
    from agro.apsim.field_area import apply_field_area
    area_changes=apply_field_area(document,config.get('field_area_ha'))

    def walk(node):
        yield node
        for child in node.get('Children', []):
            yield from walk(child)

    nodes = list(walk(document))
    simulations = [n for n in nodes if n.get('$type', '').split(',')[0] == 'Models.Core.Simulation'
                   and n.get('Enabled', True)]
    clocks = [n for n in nodes if n.get('$type', '').split(',')[0] == 'Models.Clock']
    weather_nodes = [n for n in nodes if n.get('$type', '').split(',')[0] == 'Models.Climate.Weather']
    if len(simulations) != 1 or len(clocks) != 1 or not weather_nodes:
        raise ValueError('Expected one enabled simulation, one clock and weather input')
    if any(n['FileName'] != 'weather.met' for n in weather_nodes):
        raise ValueError('Exported model must reference its accompanying weather.met')
    stores = [n for n in nodes if n.get('$type', '').split(',')[0] == 'Models.Storage.DataStore']
    custom_names = {n['CustomFileName'] for n in stores if n.get('CustomFileName')}
    if len(custom_names) > 1:
        raise ValueError('Expected a single output database')
    database_name = next(iter(custom_names), 'simulation.db')
    if Path(database_name).name != database_name or '/' in database_name or '\\' in database_name:
        raise ValueError('Output database must be a local filename')
    start = dt.date.fromisoformat(clocks[0]['Start'][:10])
    end = dt.date.fromisoformat(clocks[0]['End'][:10])
    dates = [row['date'] for row in read_met(weather)['rows']]
    required = {start + dt.timedelta(days=i) for i in range((end-start).days + 1)}
    if end < start or len(dates) != len(set(dates)) or required - set(dates):
        raise ValueError('Weather must cover every simulation day without duplicates')

    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')

    save('config.json', config)
    execution = {'source_config': str(source_config), 'mode': 'native_full_simulation',
                 'area_changes':area_changes,
                 'status': 'running', 'period_start': str(start), 'period_end': str(end),
                 'fields': [n['Name'] for n in simulations[0].get('Children', [])
                            if n.get('$type', '').split(',')[0] == 'Models.Core.Zone'
                            and n.get('Enabled', True)],
                 'input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (model, weather)}}
    save('execution.json', execution)
    try:
        for name in ('simulation.apsimx', 'weather.met', 'provenance.json'):
            shutil.copy2(root / name, output / name)
        if area_changes:
            save('simulation.apsimx',document)
        execution['executed_model_sha256']=hashlib.sha256((output/'simulation.apsimx').read_bytes()).hexdigest()
        database = run_apsim({'runtime': {'command': config['command'],
                                         'timeout_seconds': config.get('timeout_seconds', 1800)}},
                             output / 'simulation.apsimx', database_path=output / database_name)
        with sqlite3.connect(database) as connection:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            if '_Messages' in tables:
                errors = connection.execute(
                    'SELECT Message FROM _Messages WHERE MessageType = 0').fetchall()
                if errors:
                    raise RuntimeError('APSIM reported errors: ' + str(errors[:5]))
            counts = {name: connection.execute(
                'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                      for name in tables if not name.startswith('_')}
        if not any(counts.values()):
            raise RuntimeError('APSIM produced no report rows')
        save('simulation.json', {'engine': 'apsim', 'mode': 'native_full_simulation',
                                'period_start': str(start), 'period_end': str(end),
                                'fields': execution['fields'], 'database': str(database),
                                'report_rows': counts})
        execution['status'] = 'completed'
    except Exception as exc:
        execution.update(status='failed', error=str(exc))
        raise
    finally:
        save('execution.json', execution)
    return output
