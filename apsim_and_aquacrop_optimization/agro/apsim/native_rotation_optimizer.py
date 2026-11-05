"""Multi-year management search over the calibrated RotationManager export."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time

from agro.adapters.apsim import run_apsim
from agro.adapters.rotation_management import apply_policies, read_applications, walk, manager, parameters
from agro.contracts import FieldYearResult, SimulationResult
from agro.apsim.multiyear_rotation_optimizer import variable_spec, decode_vector
from agro.optimization.nsga2 import search_integer, nondominated, checked

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def load_config(path):
    import yaml
    config = yaml.safe_load(path.read_text(encoding='utf-8-sig'))
    for name in ('model_root', 'output_dir'):
        config[name] = str((path.parent/config[name]).resolve())
    years = config['evaluation_years']
    if not years or years != list(range(min(years), max(years)+1)):
        raise ValueError('Evaluation years must be an ordered consecutive interval')
    if config.get('adapter') != 'native_history_rotation':
        raise ValueError('Expected native_history_rotation adapter')
    if set(config['management_policies']) - {'spring_wheat','oats_with_underseed','winter_rye'}:
        raise ValueError('Unsupported crop policy')
    if config.get('humic_constraint') != 'each_field_end_gte_start':
        raise ValueError('This workflow requires per-field period Humic conservation')
    runtime = config['runtime']
    if os.environ.get('APSIM_MODELS'):
        runtime['command'] = [os.environ['APSIM_MODELS']]
    for _,_,options in variable_spec(config):
        if not options:
            raise ValueError('Every search variable requires options')
    for setting in config['management_policies'].values():
        for key in ('population_options_m2','depth_options_mm','row_spacing_options_mm'):
            if any(not math.isfinite(float(v)) or float(v) <= 0 for v in setting[key]):
                raise ValueError(f'Invalid management values: {key}')
    return config


def prepare(source, config, policies):
    native = {name: {'population_m2':p['population'], 'depth_mm':p['depth'],
                    'row_spacing_mm':p['row_spacing'], 'cultivar':p['cultivar'],
                    'dose_kg_n_ha':p['fertilizer']['dose_kg_n_ha'],
                    'form':p['fertilizer']['apsim_type']} for name,p in (policies or {}).items()}
    document, changes = apply_policies(source, native, config['evaluation_years'])
    from agro.apsim.field_area import apply_field_area
    apply_field_area(document,config.get('field_area_ha'))
    for node in list(walk(document)):
        if node.get('$type') == 'Models.Climate.Weather, Models':
            node['FileName'] = 'weather.met'
        if node.get('$type') == 'Models.Storage.DataStore, Models':
            node['CustomFileName'] = 'candidate.db'
        # Keep native harvest events and remove expensive diagnostic time series.
        node['Children'] = [child for child in node.get('Children', [])
                            if child.get('$type') != 'Models.Report, Models'
                            or child['Name'].startswith('HarvestReport_')]
    for zone in [n for n in walk(document) if n.get('$type') == 'Models.Core.Zone, Models']:
        zone['Children'].append({'$type':'Models.Report, Models', 'Name':f'OptSoil_{zone["Name"]}',
            'VariableNames':['[Clock].Today as Date',
                'sum([NO3].kgha) + sum([NH4].kgha) as MineralN',
                'sum([Nutrient].Humic.C) as HumicCKgHa'],
            'EventNames':['[Clock].EndOfDay'], 'Children':[], 'Enabled':True, 'ReadOnly':False})
        if config.get('report_soil_water'):
            zone['Children'][-1]['VariableNames'].append('[Swim3].SW as SoilWater')
    return document, changes


def read_result(database, source, years):
    rows, stocks = [], {}
    zones = [n for n in walk(source) if n.get('$type') == 'Models.Core.Zone, Models']
    start_date, end_date = f'{min(years)-1}-12-31', f'{max(years)}-12-31'
    clock = next(n for n in walk(source) if n.get('$type') == 'Models.Clock, Models')
    first_year = int(clock['Start'][:4])
    expected_days = (dt.date.fromisoformat(clock['End'][:10])-dt.date.fromisoformat(clock['Start'][:10])).days+1
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        errors = list(connection.execute('SELECT Message FROM _Messages WHERE MessageType=0'))
        if errors:
            raise RuntimeError(str([dict(r) for r in errors[:3]]))
        for zone in zones:
            field, area = zone['Name'], float(zone['Area'])
            if not field.startswith('Field') or not field[5:].isdigit():
                raise ValueError('Unexpected field name')
            soil = {r['Date'][:10]:dict(r) for r in connection.execute(f'SELECT * FROM OptSoil_{field}')}
            if len(soil) != expected_days or start_date not in soil or end_date not in soil:
                raise ValueError(f'{field}: incomplete soil time series')
            stocks[field] = {'start_kg_ha':float(soil[start_date]['HumicCKgHa']),
                             'end_kg_ha':float(soil[end_date]['HumicCKgHa'])}
            harvests = [dict(r) for r in connection.execute(f'SELECT * FROM HarvestReport_{field}')]
            rp = parameters(manager(zone,'RotationManager'))
            dates = [v.strip() for v in rp['RotationHarvestDates'].split(',')]
            for year in years:
                annual = [r for r in harvests if int(r['Year']) == year]
                if len(annual) != 1:
                    raise ValueError(f'{field}/{year}: expected one annual harvest')
                row = annual[0]
                expected_date = dt.datetime.strptime(f'{year}-{dates[year-first_year]}','%Y-%d-%b').date().isoformat()
                if row['Date'][:10] != expected_date:
                    raise ValueError('Native harvest date changed')
                rows.append(FieldYearResult(field,year,area,{row['Crop']:float(row['YieldDMTHa'])*1000},
                            {'mineral_n_end_kg_ha':float(soil[f'{year}-12-31']['MineralN'])}))
    for stock in stocks.values():
        if any(not math.isfinite(v) or v < 0 for v in stock.values()):
            raise ValueError('Invalid Humic stocks')
    audit = {'humic':stocks, 'humic_units':'kg C/ha',
             'applications':read_applications(database,source,years)}
    return SimulationResult('apsim',tuple(rows),{'database':str(database)}), audit


def simulate(source, config, policies, work):
    document, changes = prepare(source,config,policies)
    work.mkdir(parents=True,exist_ok=False)
    path=work/'candidate.apsimx'
    save(path,document)
    shutil.copy2(Path(config['model_root'])/'weather.met',work/'weather.met')
    database=run_apsim(config,path)
    result,audit=read_result(database,document,config['evaluation_years'])
    audit['management_changes']=changes
    save(work/'simulation.json',asdict(result));save(work/'audit.json',audit)
    return result,audit


def score(result,audit,baseline,source,config):
    reference = {(r.field_id,r.year):r for r in baseline.fields}
    prices={v['apsim_type']:float(v['price_per_kg_n']) for v in config['fertilizer']['forms'].values()}
    costs,passes={},{}
    for event in audit['applications']:
        key=(event['field'],event['year'])
        costs[key]=costs.get(key,0.)+event['dose_kg_n_ha']*prices[event['form']]
        passes[key]=passes.get(key,0)+1
    economics=config['economics'];total=profit=shortfall=0.;details=[]
    for row in result.fields:
        key=row.field_id,row.year
        mass=sum(row.harvested_dm_kg_ha.values())
        crop=next(iter(row.harvested_dm_kg_ha))
        price=float(economics['native_harvest_price_per_kg_dm'].get(crop,0.))
        net=mass*price-costs.get(key,0.)-passes.get(key,0)*float(economics['application_cost_per_ha'])-float(economics['other_cost_per_ha_year'])
        ref=reference[key].metrics['mineral_n_end_kg_ha']
        deficit=max(0.,ref-row.metrics['mineral_n_end_kg_ha'])/max(abs(ref),1e-9)
        total+=mass*row.area_ha;profit+=net*row.area_ha;shortfall+=deficit
        details.append({'field':row.field_id,'year':row.year,'crop':crop,'area_ha':row.area_ha,
                        'yield_dm_kg_ha':mass,'net_profit_proxy_per_ha':net,
                        'mineral_n_end_kg_ha':row.metrics['mineral_n_end_kg_ha']})
    constraints=[(s['start_kg_ha']-s['end_kg_ha'])/max(abs(s['start_kg_ha']),1.)
                 for _,s in sorted(audit['humic'].items())]
    return {'F':[-total,-profit,shortfall],'G':constraints,'yield_dm_kg':total,
            'net_profit_proxy':profit,'mineral_n_shortfall':shortfall,
            'feasible':all(g<=0 for g in constraints),'fields':details,'humic':audit['humic']}


def run(config_path, output=None):
    config_path=Path(config_path).resolve();config=load_config(config_path)
    root=Path(config['model_root']);source=json.loads((root/'simulation.apsimx').read_text(encoding='utf-8-sig'))
    clock=next(n for n in walk(source) if n.get('$type')=='Models.Clock, Models')
    if min(config['evaluation_years']) <= int(clock['Start'][:4]) or max(config['evaluation_years']) > int(clock['End'][:4]):
        raise ValueError('Evaluation must leave a prior year for period-start soil stocks')
    output=Path(output) if output else Path(config['output_dir'])/dt.datetime.now().strftime('optimize_%Y%m%d_%H%M%S_%f')
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    save(output/'config_snapshot.json',config)
    save(output/'sources.json',{'source_config':str(config_path),'inputs':{
        name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('simulation.apsimx','weather.met')}})
    started=time.monotonic();cache={};history=[]
    fields=[n['Name'] for n in walk(source) if n.get('$type')=='Models.Core.Zone, Models']
    count=len(fields);specs=variable_spec(config)
    save(output/'variable_space.json',[{'policy':p,'variable':v,'options':o} for p,v,o in specs])

    def status(stage,**extra):
        save(output/'status.json',{'stage':stage,'pid':os.getpid(),'output':str(output),
             'evaluation_years':config['evaluation_years'],'evaluated_unique_candidates':len(cache),
             'successful_candidates':sum(v['status']=='ok' for v in cache.values()),
             'elapsed_seconds':time.monotonic()-started,**extra})

    print(f'OUTPUT: {output}',flush=True)
    try:
        status('baseline');print('Calculating native baseline and soil reference...',flush=True)
        baseline,baseline_audit=simulate(source,config,None,output/'baseline')
        save(output/'baseline_score.json',score(baseline,baseline_audit,baseline,source,config))

        def evaluate(vector):
            policies=decode_vector(config,vector)
            key=hashlib.sha256(json.dumps(policies,sort_keys=True).encode()).hexdigest()
            if key in cache:
                history.append({'candidate_id':cache[key]['candidate_id'],'cache_hit':True})
                return cache[key]
            identifier=f'candidate_{len(cache)+1:03d}'
            status('search',running_candidate=identifier)
            print(f'{identifier}: starting APSIM',flush=True)
            try:
                result,audit=simulate(source,config,policies,output/'candidates'/identifier)
                row=score(result,audit,baseline,source,config)
                checked(row,3,count)
                row.update(status='ok',candidate_id=identifier,policies=policies)
            except Exception as exc:
                row={'status':'failed','candidate_id':identifier,'policies':policies,'error':str(exc),
                     'F':[1e30]*3,'G':[1e30]*count}
            cache[key]=row;history.append({'candidate_id':identifier,'cache_hit':False})
            (output/'candidates'/identifier).mkdir(parents=True,exist_ok=True)
            save(output/'candidates'/identifier/'result.json',row)
            save(output/'evaluations.json',list(cache.values()))
            save(output/'evaluation_history.json',history)
            save(output/'pareto.json',nondominated(cache.values()))
            status('search',last_candidate=identifier,last_status=row['status'])
            print(f'{identifier}: {row["status"]}; F={row["F"]}; G={row["G"]}',flush=True)
            return row

        status('preflight')
        preflight=evaluate([0]*len(specs))
        if preflight['status']!='ok':
            raise RuntimeError('Candidate preflight failed: '+preflight['error'])
        search_integer([o for _,_,o in specs],evaluate,config['nsga2'],3,count)
        front=nondominated(cache.values());successful=[r for r in cache.values() if r['status']=='ok']
        summary={'status':'completed' if front else 'no_feasible_solution' if successful else 'failed',
                 'evaluation_years':config['evaluation_years'],'simulation_period':[clock['Start'],clock['End']],
                 'evaluated_unique_candidates':len(cache),'successful_candidates':len(successful),
                 'pareto_candidates':len(front),'objectives':['maximize dry harvest','maximize diagnostic profit',
                 'minimize mineral-N shortfall vs native baseline'],'constraint':'Humic end >= own period start on every field',
                 'limitations':config['limitations']}
        save(output/'optimization_summary.json',summary);status(summary['status'])
        print('FINISHED: '+json.dumps(summary),flush=True)
    except BaseException as exc:
        status('failed',error=str(exc));raise
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    return run(args.config,args.output)


if __name__=='__main__':
    raise SystemExit(main())
