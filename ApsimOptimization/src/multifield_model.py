"""Non-destructive adapter for the calibrated rotation model."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import datetime as dt
from pathlib import Path

from .historical_case import load_observations, parse_technology_nutrients

ROTATION_KEYS = ('RotationCrops', 'RotationCultivars', 'RotationSowingDepths',
                 'RotationRowSpacings', 'RotationPopulations',
                 'RotationSowingDates', 'RotationHarvestDates')
CROP_ORGANS = {'Wheat': 'Grain.Total.Wt', 'Oats': 'Grain.Total.Wt',
               'Potato': 'Tuber.Total.Wt', 'Rape': 'Grain.Total.Wt',
               'Mustard': 'Grain.Total.Wt', 'RedClover': 'AboveGround.Wt',
               'PlantainForage': 'AboveGround.Wt'}

PERENNIAL_TRANSITION_OLD = '''            if (next < RotationCrops.Length && IsWinterCrop(next) && DateMatches(RotationSowingDates[next]))
            {
                EndPlantsNotIn(next);
                SowGroup(next, "autumn sowing for next harvest year");
            }'''
PERENNIAL_TRANSITION_HARVEST = '''            if (next < RotationCrops.Length && IsWinterCrop(next) && DateMatches(RotationSowingDates[next]))
            {
                if (IsPurePerennial(index))
                    HarvestGroup(index);
                EndPlantsNotIn(next);
                SowGroup(next, "autumn sowing for next harvest year");
            }'''


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def walk(node, path=''):
    path = path + '/' + node.get('Name', '')
    yield path, node
    for child in node.get('Children', []):
        yield from walk(child, path)


def kind(node):
    return node.get('$type', '').split(',')[0]


def parameters(node):
    return {p['Key']: p['Value'] for p in node.get('Parameters', [])}


def child(node, name):
    matches = [c for c in node.get('Children', []) if c['Name'] == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one {name} in {node['Name']}, found {len(matches)}")
    return matches[0]


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_model(config):
    data = read_json(config['model'])
    restored = []
    source_path = config.get('rotation_parameter_reference')
    reference = dict(walk(read_json(source_path))) if source_path else {}
    for path, node in walk(data):
        if node.get('Name') != 'Sow using a variable rule':
            continue
        if not parameters(node):
            source = reference.get(path)
            if source is None or source.get('CodeArray') != node.get('CodeArray'):
                raise ValueError(f'{path}: missing rotation parameters; matching reference required')
            node['Parameters'] = copy.deepcopy(source['Parameters'])
            restored.append(path)
        ps = parameters(node)
        if any(not ps.get(key) for key in ROTATION_KEYS):
            raise ValueError(f'{path}: incomplete rotation parameters')
        lengths = {len(ps[k].split(',')) for k in ROTATION_KEYS}
        if len(lengths) != 1:
            raise ValueError(f'{path}: rotation array lengths differ')
    simulations = [n for _, n in walk(data) if kind(n) == 'Models.Core.Simulation' and n.get('Enabled', True)]
    if len(simulations) != 1:
        raise ValueError('Select a template containing exactly one enabled Simulation')
    sim = simulations[0]
    zones = [n for n in sim['Children'] if kind(n) == 'Models.Core.Zone' and n.get('Enabled', True)]
    if not zones or len({z['Name'] for z in zones}) != len(zones):
        raise ValueError('Missing or duplicate fields')
    for z in zones:
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', z['Name']):
            raise ValueError('Field names must be safe APSIM report identifiers')
        child(z, 'Fertilise at sowing')
        child(z, 'Sow using a variable rule')
        plants = [n['Name'] for n in z['Children'] if kind(n) == 'Models.PMF.Plant']
        if set(plants) - CROP_ORGANS.keys():
            raise ValueError(f"Unsupported yield organs: {set(plants) - CROP_ORGANS.keys()}")
    return data, sim, zones, restored


def inventory(config):
    _, sim, zones, restored = load_model(config)
    clock = child(sim, 'Clock')
    model_sha = fingerprint(config['model'])
    weather_sha = fingerprint(config['weather'])

    def verified_provenance(config_key, expected_key, artifact_sha):
        path = config.get(config_key)
        if not path:
            return None
        provenance = read_json(path)
        expected = str(provenance.get(expected_key, '')).lower()
        if expected != artifact_sha.lower():
            raise ValueError(
                f'{config_key}: {expected_key} does not match configured artifact')
        return {'path': str(Path(path).resolve()), 'sha256': fingerprint(path),
                'artifact_sha256_verified': True}

    return {'simulation': sim['Name'], 'start': clock['Start'], 'end': clock['End'],
            'model_sha256': model_sha,
            'weather_sha256': weather_sha,
            'model_provenance': verified_provenance(
                'model_provenance', 'output_model_sha256', model_sha),
            'weather_provenance': verified_provenance(
                'weather_provenance', 'output_sha256', weather_sha),
            'restored_rotation_managers': restored,
            'rotation_reference_sha256': fingerprint(config['rotation_parameter_reference'])
            if config.get('rotation_parameter_reference') else None,
            'fields': {z['Name']: {'area_ha': z['Area'],
                'rotation': parameters(child(z, 'Sow using a variable rule')),
                'baseline_fertilizer': parameters(child(z, 'Fertilise at sowing')),
                'crops': [n['Name'] for n in z['Children'] if kind(n) == 'Models.PMF.Plant'],
                'reports': {n['Name']: {'variables': n['VariableNames'], 'events': n['EventNames']}
                            for n in z['Children'] if kind(n) == 'Models.Report'}} for z in zones}}


def report(name, variables, events):
    return {'$type': 'Models.Report, Models', 'Name': name, 'Enabled': True,
            'VariableNames': variables, 'EventNames': events, 'Children': []}


def fertilizer_code(original, config, field, decision):
    """Keep original applications outside the decision period and replace those inside it."""
    period_start = config['decision_period']['start']
    period_end = config['decision_period']['end']
    start = [int(v) for v in period_start.split('-')]
    end = [int(v) for v in period_end.split('-')]
    within_period = (f'Clock.Today.Date >= new DateTime({start[0]}, {start[1]}, {start[2]}) && '
                     f'Clock.Today.Date <= new DateTime({end[0]}, {end[1]}, {end[2]})')
    marker = 'private void OnSowing(object sender, EventArgs e)\n        {'
    if original.count(marker) != 1:
        raise ValueError('Unrecognized fertilizer manager; refusing ambiguous patch')
    code = original.replace(marker, marker + f'\n            if ({within_period}) return;')
    fs = config['fields'][field]
    option = config['fertilizer_options'][decision['option']]
    calls = []
    for component in option['components']:
        amount = decision['dose_kg_product_ha'] * component['mass_fraction'] * component['apsim_amount_per_kg_product']
        calls.append(f'Fertiliser.Apply(amount: {amount:.17g}, type: {json.dumps(component["apsim_type"])}, depth: {fs["application_depth_mm"]:.17g});')
    conditions = ' || '.join(
        f'(Clock.Today.Date == new DateTime({int(d[:4])}, {int(d[5:7])}, {int(d[8:10])}))'
        for d in fs['application_dates'])
    block = f'''
        public double OptimizerAppliedProduct {{ get; private set; }}
        public int OptimizerPasses {{ get; private set; }}
        [EventSubscribe("DoManagement")]
        private void OptimizerApply(object sender, EventArgs e)
        {{
            if (!({conditions})) return;
            if ({decision['dose_kg_product_ha']:.17g} <= 0) return;
            {' '.join(calls)}
            OptimizerAppliedProduct += {decision['dose_kg_product_ha']:.17g};
            OptimizerPasses++;
        }}
'''
    pos = code.rfind('    }')
    if pos < 0:
        raise ValueError('Invalid fertilizer manager class')
    return code[:pos] + block + code[pos:]


def historical_fertilizer_code(original, entries):
    """Use observed N labels on matching sowing events and preserve model type.

    Each entry is tied to an exact sowing date.  All other events continue to
    use the manager's original Amount and FertiliserType parameters.
    """
    marker = '            Fertiliser.Apply(amount: Amount, type: FertiliserType);'
    if original.count(marker) != 1:
        raise ValueError('Unrecognized fertilizer manager; refusing ambiguous historical patch')
    conditions = []
    for entry in sorted(entries, key=lambda row: row['sowing_date']):
        date = dt.date.fromisoformat(entry['sowing_date'])
        amount = float(entry['amount_kg_n_ha'])
        conditions.append(
            f'            if (Clock.Today.Date == new DateTime({date.year}, {date.month}, {date.day}))\n'
            f'                historicalReplayAmount = {amount:.17g};')
    replacement = (
        '            double historicalReplayAmount = Amount;\n' +
        '\n'.join(conditions) + '\n' +
        '            Fertiliser.Apply(amount: historicalReplayAmount, type: FertiliserType);')
    return original.replace(marker, replacement, 1)


def historical_replay_plan(config, sim, zones):
    """Build a reviewable N-only replay plan from the observation file."""
    settings = config.get('historical_replay') or {}
    if not settings.get('enabled'):
        return {'enabled': False, 'entries': [], 'skipped': []}
    expected = {
        'enabled': True,
        'nitrogen_source': 'technology_labels',
        'application_event': 'model_sowing_event_on_observed_date',
        'fertilizer_type': 'preserve_model',
    }
    if settings != expected:
        raise ValueError('historical_replay: use the explicit supported N-label replay settings')
    source = config.get('historical_observations')
    if not source:
        raise ValueError('historical_replay requires historical_observations')
    observations = load_observations(source)
    by_field = {zone['Name']: zone for zone in zones}
    clock_start = dt.date.fromisoformat(child(sim, 'Clock')['Start'][:10])
    clock_end = dt.date.fromisoformat(child(sim, 'Clock')['End'][:10])
    start_year = clock_start.year
    entries, skipped = [], []
    seen = set()
    for year_text, records in sorted((observations.get('years') or {}).items()):
        harvest_year = int(year_text)
        for record in records:
            field = record.get('field')
            nutrients = parse_technology_nutrients(record.get('technology'))
            amount = nutrients['N']
            sowing_text = record.get('observed_sow_date')
            if amount is None:
                skipped.append({'harvest_year': harvest_year, 'field': field,
                                'reason': 'technology_has_no_explicit_n_rate'})
                continue
            if not sowing_text:
                skipped.append({'harvest_year': harvest_year, 'field': field,
                                'amount_kg_n_ha': amount,
                                'reason': 'observed_sowing_date_missing'})
                continue
            if field not in by_field:
                raise ValueError(f'historical_replay: unknown field {field}')
            sowing_date = dt.date.fromisoformat(sowing_text)
            if not clock_start <= sowing_date <= clock_end:
                skipped.append({'harvest_year': harvest_year, 'field': field,
                                'sowing_date': sowing_text, 'amount_kg_n_ha': amount,
                                'reason': 'sowing_date_outside_model_clock'})
                continue
            rotation = parameters(child(by_field[field], 'Sow using a variable rule'))
            index = harvest_year - start_year
            rotation_dates = [value.strip() for value in
                              rotation['RotationSowingDates'].split(',')]
            if not 0 <= index < len(rotation_dates):
                raise ValueError(f'historical_replay: no rotation entry for {field}/{harvest_year}')
            # %-d is unavailable on Windows, so format the day explicitly.
            expected_day_month = f'{sowing_date.day}-{sowing_date:%b}'
            if rotation_dates[index].lower() != expected_day_month.lower():
                raise ValueError(
                    f'historical_replay: observed/model sowing date differs for {field}/{harvest_year} '
                    f'({sowing_text} vs {rotation_dates[index]})')
            key = (field, sowing_text)
            if key in seen:
                raise ValueError(f'historical_replay: duplicate sowing event {field}/{sowing_text}')
            seen.add(key)
            manager_values = parameters(child(by_field[field], 'Fertilise at sowing'))
            entries.append({
                'harvest_year': harvest_year,
                'field': field,
                'crop_group': record.get('crop_group'),
                'technology_label': record.get('technology'),
                'sowing_date': sowing_text,
                'amount_kg_n_ha': float(amount),
                'source_amount_interpretation': 'explicit N rate in technology label',
                'fertilizer_type': manager_values['FertiliserType'],
                'original_static_amount_kg_ha': float(manager_values['Amount']),
                'application_event': 'existing model sowing event',
            })
    return {
        **expected,
        'observations': str(Path(source).resolve()),
        'entries': entries,
        'skipped': skipped,
        'limitations': [
            'Technology labels do not identify fertilizer product, placement, or independent application date.',
            'The model fertilizer type is preserved; only its amount is replaced on the exact sowing event.',
            'P and K labels are recorded by the quality audit but are not simulated by this N-only replay.',
        ],
    }


def rotation_transition_code(original, policy):
    """Apply an explicit policy for a perennial-to-winter-crop overlap."""
    if policy in (None, 'preserve_source'):
        return original, False
    if policy != 'harvest_on_winter_sowing_proxy':
        raise ValueError(f'Unsupported rotation_transition_policy: {policy}')
    if original.count(PERENNIAL_TRANSITION_OLD) != 1:
        raise ValueError('Unrecognized rotation transition manager; refusing ambiguous patch')
    return original.replace(PERENNIAL_TRANSITION_OLD,
                            PERENNIAL_TRANSITION_HARVEST, 1), True


def prepare(config, directory, decisions=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    data, sim, zones, restored = load_model(config)
    shutil.copyfile(config['weather'], directory / 'weather.met')
    child(sim, 'Weather')['FileName'] = 'weather.met'
    # Preserve the full calibrated clock: rotations are indexed from its start year.
    removed = []
    transition_patched = []
    replay = historical_replay_plan(config, sim, zones)
    for path, node in list(walk(data)):
        node['Children'] = [c for c in node.get('Children', []) if c.get('Name') != 'Synchroniser']
        if node.get('Name') == 'Synchroniser':
            removed.append(path)
        if kind(node) == 'Models.Report':
            node['VariableNames'] = [
                v for v in node.get('VariableNames', [])
                if v.strip() and '[NFlow].Natm' not in v
            ]
        if node.get('Name') == 'Sow using a variable rule':
            code, changed = rotation_transition_code(
                '\n'.join(node.get('CodeArray') or []),
                config.get('rotation_transition_policy'))
            if changed:
                node['CodeArray'] = code.splitlines()
                transition_patched.append(path)
    for zone in zones:
        field = zone['Name']
        replay_entries = [entry for entry in replay['entries'] if entry['field'] == field]
        if replay_entries:
            manager = child(zone, 'Fertilise at sowing')
            manager['CodeArray'] = historical_fertilizer_code(
                '\n'.join(manager['CodeArray']), replay_entries).splitlines()
        for crop in [n['Name'] for n in zone['Children'] if kind(n) == 'Models.PMF.Plant']:
            zone['Children'].append(report(f'OptHarvest_{field}_{crop}',
                ['[Clock].Today as Date', f'[{crop}].{CROP_ORGANS[crop]} * 10 as YieldDM'],
                [f'[{crop}].Harvesting']))
        variables = ['[Clock].Today as Date',
                     'sum([NO3].kgha) + sum([NH4].kgha) as MineralN_NO3_NH4',
                     'sum([Urea].kgha) as UreaN']
        for i, nutrient in enumerate(config.get('fields', {}).get(field, {}).get('nutrients') or []):
            variables.append(f'{nutrient["expression"]} as Nutrient{i}')
        if decisions is not None:
            manager = child(zone, 'Fertilise at sowing')
            manager['CodeArray'] = fertilizer_code('\n'.join(manager['CodeArray']), config, field, decisions[field]).splitlines()
            variables += ['[Fertilise at sowing].Script.OptimizerAppliedProduct as AppliedProduct',
                          '[Fertilise at sowing].Script.OptimizerPasses as ApplicationPasses']
        zone['Children'].append(report(f'OptSoil_{field}', variables, ['[Clock].EndOfDay']))
    path = directory / 'candidate.apsimx'
    write_json(path, data)
    write_json(directory / 'preparation.json', {
        'restored': restored,
        'removed_cli_synchronisers': removed,
        'rotation_transition_policy': config.get('rotation_transition_policy',
                                                 'preserve_source'),
        'transition_managers_patched': transition_patched,
        'historical_replay': replay,
        'decisions': decisions,
        'source_sha256': fingerprint(config['model']),
    })
    return path
