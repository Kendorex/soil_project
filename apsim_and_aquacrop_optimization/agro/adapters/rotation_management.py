"""Management experiments on the authoritative fixed-rotation APSIM model.

The original model is never changed. Annual cereal policies are shared across
all fields and evaluation years; crop sequence, dates and perennial stands stay
as supplied. Outputs are native harvests and daily soil stocks.
"""
from __future__ import annotations

from copy import deepcopy
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3

from agro.adapters.apsim import run_apsim
from agro.contracts import FieldYearResult, SimulationResult


def walk(node):
    yield node
    for child in node.get('Children', []):
        yield from walk(child)


def manager(zone, name):
    return next(node for node in walk(zone) if node.get('Name') == name)


def parameters(node):
    return {row['Key']: row['Value'] for row in node['Parameters']}


def group(crop, cultivar):
    if crop == 'Oats':
        return 'oats_with_underseed'
    if crop == 'Wheat':
        return 'winter_rye' if 'Winter_rye' in cultivar else 'spring_wheat'
    return None


def apply_policies(document, policies, years):
    """Apply explicit policies to source manager parameters, retaining units."""
    result = deepcopy(document)
    clock = next(node for node in walk(result) if node.get('$type') == 'Models.Clock, Models')
    first_year = int(clock['Start'][:4])
    changes = []
    for zone in [node for node in walk(result) if node.get('$type') == 'Models.Core.Zone, Models']:
        rotation = manager(zone, 'RotationManager')
        rp = parameters(rotation)
        arrays = {key: [value.strip() for value in rp[key].split(',')]
                  for key in ('RotationCrops', 'RotationCultivars', 'RotationPopulations', 'RotationSowingDepths',
                              'RotationRowSpacings')}
        fertilizer = manager(zone, 'Fertilise at sowing')
        fp = parameters(fertilizer)
        doses = [value.strip() for value in fp['FertiliserAmounts'].split(',')]
        forms = [value.strip() for value in fp['FertiliserTypes'].split(',')]
        for index, crops in enumerate(arrays['RotationCrops']):
            year = first_year + index
            if year not in years:
                continue
            cultivars = arrays['RotationCultivars'][index].split(';')
            populations = arrays['RotationPopulations'][index].split(';')
            depths = arrays['RotationSowingDepths'][index].split(';')
            spacings = arrays['RotationRowSpacings'][index].split(';')
            for component, crop in enumerate(crops.split('+')):
                state = group(crop.strip(), cultivars[component].strip())
                if state is None or state not in policies:
                    continue
                policy = policies[state]
                old = {'population_m2': float(populations[component]), 'depth_mm': float(depths[component]),
                       'dose_kg_n_ha': float(doses[index]), 'form': forms[index]}
                if 'population_m2' in policy:
                    populations[component] = f"{float(policy['population_m2']):g}"
                if 'depth_mm' in policy:
                    depths[component] = f"{float(policy['depth_mm']):g}"
                if 'row_spacing_mm' in policy:
                    spacings[component] = f"{float(policy['row_spacing_mm']):g}"
                if policy.get('cultivar') not in (None, 'historical'):
                    cultivars[component] = policy['cultivar']
                if 'dose_kg_n_ha' in policy:
                    doses[index] = f"{float(policy['dose_kg_n_ha']):g}"
                    forms[index] = policy['form']
                for value in (float(populations[component]), float(depths[component]), float(spacings[component])):
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError('Population and sowing depth must be finite and positive')
                if not math.isfinite(float(doses[index])) or float(doses[index]) < 0:
                    raise ValueError('Nitrogen dose must be finite and nonnegative')
                changes.append({'field': zone['Name'], 'year': year, 'crop': crop, 'policy': state,
                                'before': old, 'after': {'population_m2': float(populations[component]),
                                'depth_mm': float(depths[component]), 'dose_kg_n_ha': float(doses[index]),
                                'form': forms[index], 'cultivar': cultivars[component],
                                'row_spacing_mm': float(spacings[component])}})
            arrays['RotationPopulations'][index] = ';'.join(populations)
            arrays['RotationSowingDepths'][index] = ';'.join(depths)
            arrays['RotationRowSpacings'][index] = ';'.join(spacings)
            arrays['RotationCultivars'][index] = ';'.join(cultivars)
        for param in rotation['Parameters']:
            if param['Key'] in ('RotationPopulations', 'RotationSowingDepths', 'RotationCultivars',
                                'RotationRowSpacings'):
                if param['Value'].split(',') == arrays[param['Key']]:
                    continue
                # Preserve formatting when the selected component values are unchanged.
                if [part.strip() for part in param['Value'].split(',')] == arrays[param['Key']]:
                    continue
                param['Value'] = ', '.join(arrays[param['Key']])
        for param in fertilizer['Parameters']:
            if param['Key'] == 'FertiliserAmounts':
                param['Value'] = ', '.join(doses)
            elif param['Key'] == 'FertiliserTypes':
                param['Value'] = ', '.join(forms)
    return result, changes


def prepare(document, model_root, workdir):
    result = deepcopy(document)
    for node in list(walk(result)):
        if node.get('$type') == 'Models.Climate.Weather, Models':
            node['FileName'] = str((model_root / 'configs/weather_rotation.met').resolve())
        if node.get('Name') == 'Synchroniser':
            node['Enabled'] = False
        # Only native harvests and explicit optimization soil reports are required.
        node['Children'] = [child for child in node.get('Children', [])
                            if child.get('$type') != 'Models.Report, Models' or child['Name'] == 'HarvestReport']
    for zone in [node for node in walk(result) if node.get('$type') == 'Models.Core.Zone, Models']:
        zone['Children'].append({'$type': 'Models.Report, Models', 'Name': 'ManagementSoil',
            'VariableNames': ['[Clock].Today as Date', '[Zone].Name as Zone',
                'sum([NO3].kgha) + sum([NH4].kgha) as MineralN',
                'sum([Nutrient].Humic.C) as HumicCKgHa'],
            'EventNames': ['[Clock].EndOfDay'], 'Children': [], 'Enabled': True, 'ReadOnly': False})
    workdir.mkdir(parents=True, exist_ok=False)
    path = workdir / 'candidate.apsimx'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def read_outputs(database, source, years):
    # OrganicPool.C is already kg C/ha. Support normalization of early study
    # reports that explicitly multiplied this quantity by ten in the expression.
    model = json.loads(database.with_suffix('.apsimx').read_text(encoding='utf-8'))
    expressions = {value for node in walk(model) if node.get('Name') == 'ManagementSoil'
                   for value in node['VariableNames'] if value.endswith(' as HumicCKgHa')}
    if expressions == {'sum([Nutrient].Humic.C) as HumicCKgHa'}:
        carbon_scale = 1.
    elif expressions == {'sum([Nutrient].Humic.C) * 10 as HumicCKgHa'}:
        carbon_scale = 10.
    else:
        raise ValueError(f'Unverified Humic reporting expression: {expressions}')
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        errors = list(conn.execute('SELECT * FROM _Messages WHERE MessageType=0'))
        if errors:
            raise RuntimeError(f'APSIM simulation errors: {[dict(row) for row in errors[:2]]}')
        harvests = [dict(row) for row in conn.execute('SELECT * FROM HarvestReport ORDER BY Zone,Date')]
        soil = [dict(row) for row in conn.execute('SELECT * FROM ManagementSoil ORDER BY Zone,Date')]
    fields, humic = [], {}
    clock = next(node for node in walk(source) if node.get('$type') == 'Models.Clock, Models')
    first_year = int(clock['Start'][:4])
    for zone in [node for node in walk(source) if node.get('$type') == 'Models.Core.Zone, Models']:
        name, area = zone['Name'], float(zone['Area'])
        rp = parameters(manager(zone, 'RotationManager'))
        dates = [value.strip() for value in rp['RotationHarvestDates'].split(',')]
        stock = {row['Date'][:10]: row for row in soil if row['Zone'] == name}
        expected = (dt.date(max(years)+1, 1, 1)-dt.date(first_year, 1, 1)).days
        if len(stock) != expected:
            raise ValueError(f'{name}: incomplete daily soil outputs: {len(stock)} vs {expected}')
        for year in years:
            events = [row for row in harvests if row['Zone'] == name and int(row['Year']) == year]
            if len(events) != 1:
                raise ValueError(f'{name}/{year}: expected one native harvest')
            event = events[0]
            expected_date = dt.datetime.strptime(f'{year}-{dates[year-first_year]}', '%Y-%d-%b').date().isoformat()
            if event['Date'][:10] != expected_date:
                raise ValueError(f'{name}/{year}: harvest date shifted')
            end = stock[f'{year}-12-31']
            fields.append(FieldYearResult(name, year, area, {event['Crop']: float(event['YieldDMKgHa'])},
                                           {'mineral_n_end_kg_ha': float(end['MineralN'])}))
        start, end = stock[f'{min(years)-1}-12-31'], stock[f'{max(years)}-12-31']
        humic[name] = {'start_kg_ha': float(start['HumicCKgHa'])/carbon_scale,
                       'end_kg_ha': float(end['HumicCKgHa'])/carbon_scale}
    return SimulationResult('apsim', tuple(fields), {'database': str(database)}), humic


def read_applications(database, source, years):
    """Read actual applications, assigning autumn rye to its harvest year."""
    zones = {node['Name']: node for node in walk(source) if node.get('$type') == 'Models.Core.Zone, Models'}
    clock = next(node for node in walk(source) if node.get('$type') == 'Models.Clock, Models')
    first_year = int(clock['Start'][:4])
    applications = []
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM _Messages WHERE Message LIKE 'Applied %'"):
            match = re.fullmatch(r'Applied ([0-9.eE+-]+) kg/ha of (\S+) on .+', row['Message'])
            if not match:
                raise ValueError('Unrecognized fertilizer application message')
            field_id = row['ComponentName'].split('.')[0]
            when = dt.date.fromisoformat(row['Date'][:10])
            year = when.year
            rp = parameters(manager(zones[field_id], 'RotationManager'))
            cultivars = [item.strip() for item in rp['RotationCultivars'].split(',')]
            index = year + 1 - first_year
            if when.month >= 7 and index < len(cultivars) and 'Winter_rye' in cultivars[index]:
                year += 1
            if year in years:
                applications.append({'field': field_id, 'year': year, 'date': when.isoformat(),
                                     'dose_kg_n_ha': float(match[1]), 'form': match[2]})
    return applications


def execute(model_root, policies, years, command, workdir, timeout=1200):
    source_path = model_root / 'configs/rotation_fixed.apsimx'
    source = json.loads(source_path.read_text(encoding='utf-8-sig'))
    candidate, changes = apply_policies(source, policies, years)
    path = prepare(candidate, model_root, workdir)
    database = run_apsim({'runtime': {'command': command, 'timeout_seconds': timeout}}, path)
    result, humic = read_outputs(database, source, years)
    audit = {'policies': policies, 'changes': changes, 'humic': humic,
             'humic_units': 'kg C/ha',
             'applications': read_applications(database, source, years),
             'source_model_sha256': hashlib.sha256(source_path.read_bytes()).hexdigest()}
    (workdir/'management.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    return result, audit
