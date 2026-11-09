"""Replay archived APSIM parameter windows and test isolated management assumptions."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agro.adapters.apsim import run_apsim
from analyze_apsim_errors import fingerprint, read_json, valid, walk


GROUPS = [
    {'id': 'oats_2022', 'field': 'Field4', 'trained_through': 2020, 'end': 2022,
     'change_year': 2022, 'variants': ['baseline', 'underseed_30_days', 'underseed_density_25pct']},
    {'id': 'galega_2010_2011', 'field': 'Field1', 'trained_through': 2009, 'end': 2011,
     'change_year': 2010, 'variants': ['baseline', 'no_spring_reset', 'spring_reset_may']},
    {'id': 'grass3_wheat_2012_2013', 'field': 'Field6', 'trained_through': 2011, 'end': 2013,
     'change_year': 2012, 'variants': ['baseline', 'no_spring_reset', 'spring_reset_may']},
    {'id': 'barley_2007', 'field': 'Field8', 'trained_through': 2006, 'end': 2007,
     'change_year': 2007, 'variants': ['baseline', 'barley_final_phenology']},
]


def save(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def apply_archived_parameters(doc, definitions, values):
    overrides = {(r['plant'], r['cultivar'], r['path']): values[r['id']] for r in definitions}
    found = set()
    for node in walk(doc):
        if node.get('$type', '').startswith('Models.PMF.Plant,'):
            for cultivar in node.get('Children', []):
                for i, command in enumerate(cultivar.get('Command', [])):
                    key = (node['Name'], cultivar['Name'], command.split('=', 1)[0].strip())
                    if key in overrides:
                        cultivar['Command'][i] = key[2] + ' = ' + format(overrides[key], '.12g')
                        found.add(key)
    assert found == set(overrides), 'Archived parameter paths must exist in the exported model'


def build(source, definitions, values, group, variant):
    doc = deepcopy(source)
    values = dict(values)
    if variant == 'barley_final_phenology':
        final_values = read_json(ROOT/'models/apsim/simulation/calibrated_history/parameters.json')['parameters']
        for spec in definitions:
            if spec['plant']=='Barley' and spec['group']=='phenology':
                values[spec['id']] = final_values[spec['id']]
    apply_archived_parameters(doc, definitions, values)
    for node in walk(doc):
        if node.get('$type', '').startswith('Models.Clock,'):
            node['End'] = f"{group['end']}-12-31T00:00:00"
        if node.get('$type', '').startswith('Models.Climate.Weather,'):
            node['FileName'] = 'weather.met'
        if node.get('$type', '').startswith('Models.Storage.DataStore,'):
            node['CustomFileName'] = 'probe.db'
    # Retain all ten fields so shared model context matches the archived execution.
    zone = next(n for n in walk(doc) if n.get('Name') == group['field'])
    assert float(zone['Area']) == 1
    for node in list(walk(doc)):
        node['Children'] = [c for c in node.get('Children', []) if
                            not c.get('$type', '').startswith('Models.Report,') or c['Name'].startswith('HarvestReport_')]
    manager = next(n for n in zone['Children'] if n.get('Name') == 'RotationManager')
    code = '\n'.join(manager['CodeArray'])
    spring = 'if(Persistent(i) && Match("01-Mar"))'
    year = group['change_year']
    changes = []
    if variant == 'barley_final_phenology':
        changes.append('Replace only the six Barley phenology parameters by their final-fit values throughout the replay')
    if variant == 'no_spring_reset':
        replacement = f'if(Persistent(i) && Match("01-Mar") && Clock.Today.Year!={year})'
        assert code.count(spring) == 1
        code = code.replace(spring, replacement)
        changes.append(f'Disable the March reset in {year} only')
    if variant == 'spring_reset_may':
        replacement = f'if(Persistent(i) && Match(Clock.Today.Year=={year} ? "01-May" : "01-Mar"))'
        assert code.count(spring) == 1
        code = code.replace(spring, replacement)
        changes.append(f'Move the reset from March 1 to May 1 in {year} only')
    if variant == 'underseed_30_days':
        original = 'undersowDate=Clock.Today.AddDays(1);'
        assert code.count(original) == 1
        code = code.replace(original, f'undersowDate=Clock.Today.AddDays(Clock.Today.Year=={year} ? 30 : 1);')
        changes.append(f'Delay underseeding to 30 days after cereal sowing in {year} only')
    if variant == 'underseed_density_25pct':
        parameters = {p['Key']: p for p in manager['Parameters']}
        pops = [x.strip() for x in parameters['RotationPopulations']['Value'].split(',')]
        index = year - 2004
        species = parameters['RotationCrops']['Value'].split(',')[index].strip().split('+')
        densities = pops[index].split(';')
        for i, species_name in enumerate(species):
            if species_name in ['RedClover', 'PlantainForage']:
                densities[i] = str(float(densities[i]) * .25)
        pops[index] = ';'.join(densities)
        parameters['RotationPopulations']['Value'] = ', '.join(pops)
        changes.append(f'Reduce only the perennial underseed density to 25 percent in {year}')
    manager['CodeArray'] = code.splitlines()
    variables = ['[Clock].Today as Date', 'sum([NO3].kgha) + sum([NH4].kgha) as MineralN_kg_ha',
                 '[Swim3].SW as VolumetricSoilWater', '[Weather].Rain as Rain_mm']
    for plant in ['Wheat', 'Oats', 'Barley', 'RedClover', 'PlantainForage']:
        variables += [f'[{plant}].AboveGround.Wt as {plant}AboveGround_g_m2',
                      f'[{plant}].LAI as {plant}LAI']
        if plant in ['Wheat','Oats','Barley']:
            variables.append(f'[{plant}].Grain.Total.Wt as {plant}Grain_g_m2')
    variables += ['[RotationManager].Script.WheatStage as WheatStage',
                  '[RotationManager].Script.OatsStage as OatsStage',
                  '[RotationManager].Script.BarleyStage as BarleyStage',
                  '[RotationManager].Script.RedCloverStage as RedCloverStage',
                  '[RotationManager].Script.PlantainForageStage as PlantainForageStage']
    zone['Children'].append({'$type':'Models.Report, Models', 'Name':'MechanismTrace',
        'VariableNames':variables, 'EventNames':['[Clock].EndOfDay'],
        'Children':[], 'Enabled':True, 'ReadOnly':False})
    return doc, changes


def collect(folder, group, variant, source_rows):
    with sqlite3.connect((folder/'probe.db').resolve().as_uri()+'?mode=ro&immutable=1', uri=True) as con:
        con.row_factory = sqlite3.Row
        errors = [dict(r) for r in con.execute('SELECT * FROM _Messages WHERE MessageType=0')]
        if errors:
            raise RuntimeError(str(errors[:2]))
        field = group['field']
        harvests = [dict(r) for r in con.execute(f'SELECT * FROM HarvestReport_{field}')]
        daily = [dict(r) for r in con.execute('SELECT * FROM MechanismTrace')]
        warnings = [dict(r) for r in con.execute('SELECT * FROM _Messages WHERE MessageType=1')]
    scored = []
    for r in source_rows:
        if r['field'] != group['field'] or r.get('trained_through') != group['trained_through'] or r['year'] > group['end']:
            continue
        h = next(q for q in harvests if q['Year'] == r['year'])
        scored.append({'field':r['field'], 'year':r['year'], 'crop':r['crop'],
            'observed_t_ha':r['observed_t_ha'], 'archived_prediction_t_ha':r['simulated_dm_t_ha'],
            'prediction_t_ha':h['YieldDMTHa'], 'error_t_ha':h['YieldDMTHa']-r['observed_t_ha'],
            'replay_difference_t_ha':h['YieldDMTHa']-r['simulated_dm_t_ha'],
            'harvest_before_kg_ha':h.get('BeforeKgHa'), 'harvest_after_kg_ha':h.get('AfterKgHa')})
    if variant == 'baseline':
        assert scored and max(abs(r['replay_difference_t_ha']) for r in scored) < 1e-6, 'Historical forecast not reproduced'
    relevant = [r for r in daily if int(r['Date'][:4]) > group['trained_through']]
    peaks = []
    for year in range(group['trained_through']+1, group['end']+1):
        for plant in ['Wheat','Oats','Barley']:
            season = [r for r in relevant if r['Date'].startswith(str(year)) and f'{year}-03-01' <= r['Date'][:10] <= f'{year}-08-31']
            if season:
                maximum = max(season, key=lambda r:r[f'{plant}Grain_g_m2'])
                peaks.append({'year':year,'plant':plant,'peak_grain_t_ha':maximum[f'{plant}Grain_g_m2']*.01,'peak_date':maximum['Date'][:10]})
    return {'group':group, 'variant':variant, 'predictions':scored, 'grain_peaks':peaks,
            'warnings':warnings, 'daily':relevant, 'database':str(folder/'probe.db')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'runs/analysis/apsim_sensitivity_20261106')
    parser.add_argument('--archive', type=Path, default=ROOT.parent/'calibrovka/history_final')
    parser.add_argument('--run-probes', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    model_root = ROOT/'models/apsim/simulation/calibrated_history'
    source = read_json(model_root/'simulation.apsimx')
    definitions = read_json(model_root/'parameters.json')['definitions']
    source_rows = read_json(ROOT/'models/apsim/data/observations/new_calibr/yield_comparison.json')['pre_update_records']
    archive_state = read_json(args.archive/'calibration_state.json')
    assert archive_state['walkforward_yields'] == source_rows
    observations = [r for r in source_rows if valid(r)]
    runtime = read_json(ROOT/'models/apsim/configs/multiyear_rotation_native_2020_2023.json')['runtime']
    runtime['timeout_seconds'] = 600
    engine = Path(runtime['command'][0])
    assert fingerprint(engine) == read_json(model_root/'provenance.json')['engine']['sha256']
    source_hashes = {str(p.resolve()):fingerprint(p) for p in [model_root/'simulation.apsimx',model_root/'weather.met',model_root/'parameters.json',args.archive/'calibration_state.json']}
    results = []
    for group in GROUPS:
        parameter_file = args.archive/f"stages/{group['trained_through']}/parameters.json"
        values = read_json(parameter_file)
        source_hashes[str(parameter_file.resolve())] = fingerprint(parameter_file)
        for variant in group['variants']:
            folder = out/'probes_full_fields'/group['id']/variant
            folder.mkdir(parents=True, exist_ok=True)
            result_path = folder/'result.json'
            if result_path.exists():
                results.append(read_json(result_path))
                continue
            document, changes = build(source, definitions, values, group, variant)
            model_path = folder/'probe.apsimx'
            save(model_path,document)
            shutil.copyfile(model_root/'weather.met',folder/'weather.met')
            save(folder/'protocol.json', {'group':group,'variant':variant,'changes':changes,
                'parameter_sha256':fingerprint(parameter_file),'model_sha256':fingerprint(model_path),
                'source_area_ha':1,'note':'Controlled diagnostic perturbations, not a calibrated replacement model.'})
            if not args.run_probes:
                continue
            print(f"START {group['id']} {variant}",flush=True)
            start = time.monotonic()
            run_apsim({'runtime':runtime},model_path,folder/'probe.db')
            result = collect(folder,group,variant,observations)
            result['seconds'] = time.monotonic()-start
            result['changes'] = changes
            save(result_path,result)
            results.append(result)
            print(f"DONE {group['id']} {variant} {result['seconds']:.1f}s "+json.dumps(result['predictions'],ensure_ascii=False),flush=True)
            save(out/'probe_status.json',{'completed':len(results),'planned':sum(len(g['variants']) for g in GROUPS)})
    assert all(fingerprint(Path(p))==digest for p,digest in source_hashes.items())
    save(out/'mechanism_results.json',{'sources':source_hashes,'sources_unchanged':True,
        'engine_sha256':fingerprint(engine),'completed':len(results),'results':results})


if __name__ == '__main__':
    main()
