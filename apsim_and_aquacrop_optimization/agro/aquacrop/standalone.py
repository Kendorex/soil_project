"""Standalone AquaCrop crop-map optimization; no APSIM runtime or results."""
from dataclasses import asdict
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from agro.adapters.aquacrop import AquaCropBackend
from agro.optimization.nsga2 import search_integer,integer_search_settings,nondominated,checked
from agro.planning.rotation_planner import RotationRules,plan_fields_stateful,select_farm_plan

ROOT=Path(__file__).resolve().parents[2]
CRITERIA={'yield':('yield_dm_kg',-1),'profit':('net_profit_proxy',-1),
          'water_use':('evapotranspiration_m3',1),'water_productivity':('water_productivity_kg_m3',-1),
          'drainage':('drainage_m3',1),'runoff':('runoff_m3',1)}


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def context(path):
    path=Path(path).resolve();config=read(path)
    if config.get('engine')!='aquacrop_standalone':raise ValueError('Expected standalone AquaCrop configuration')
    config['model_root']=str((path.parent/config['model_root']).resolve())
    config['output_root']=str((path.parent/config['output_root']).resolve())
    config['command']=[str((path.parent/config['command'][0]).resolve()),*config['command'][1:]]
    model=Path(config['model_root']);project=read(model/'project.json');planning=read(model/'planning.json')
    if planning['initial_state_year']!=planning['start_year']-1 or planning['years']<1:
        raise ValueError('Standalone planning needs preceding-year states and a positive horizon')
    economics=config['economics']
    if any(not math.isfinite(float(v)) or v<0 for values in economics.values() if isinstance(values,dict) for v in values.values()):
        raise ValueError('Standalone prices and costs must be finite and nonnegative')
    if project.get('schema_version')!=2 or project.get('engine')!='aquacrop_standalone':raise ValueError('Expected standalone input project')
    fields=[read(model/name) for name in project['fields']]
    names=[field['id'] for field in fields]
    if set(names)!=set(planning['initial_states']):raise ValueError('Planning field coverage differs')
    rules=RotationRules.from_file(model/'rotation_rules.json')
    pool=plan_fields_stateful(rules,planning['initial_states'],planning['state_model'],planning['years'],
        top_k=planning['candidate_pool'],minimum_score=planning['minimum_transition_score'])
    seed=select_farm_plan(pool['fields'],planning['farm_crop_mix'],planning['years'],beam_width=planning['farm_selection_beam_width'])
    for field in names:
        alternatives=pool['fields'][field]['alternatives'];rank=seed['selected_ranks'][field]
        pool['fields'][field]['alternatives']=[a for a in alternatives if a['rank']==rank]+[a for a in alternatives if a['rank']!=rank]
    return {'config':config,'planning':planning,'fields':fields,'pool':pool,'config_path':path}


def choices(ctx,vector):
    if len(vector)!=len(ctx['fields']):raise ValueError('One sequence index per field is required')
    return {field['id']:ctx['pool']['fields'][field['id']]['alternatives'][int(v)]['sequence_ids'] for field,v in zip(ctx['fields'],vector)}


def crop_constraints(ctx,sequences):
    p=ctx['planning'];categories=p['farm_crop_mix']['categories']
    mapping={s:name for name,spec in categories.items() for s in spec['states']}
    counts=[{name:0 for name in categories} for _ in range(p['years'])]
    for sequence in sequences.values():
        for i,state in enumerate(sequence):counts[i][mapping[state]]+=1
    constraints=[]
    for count in counts:
        for name,spec in categories.items():
            lo,hi=spec['annual_min'],spec['annual_max'];n=count[name]
            constraints.extend([(lo-n)/max(lo,1),(n-hi)/max(hi,1)])
    for name,spec in categories.items():
        lo=spec.get('horizon_min',0);hi=spec.get('horizon_max',len(sequences)*p['years']);n=sum(c[name] for c in counts)
        constraints.extend([(lo-n)/max(lo,1),(n-hi)/max(hi,1)])
    return constraints,counts


def materialize(ctx,sequences,destination):
    source=Path(ctx['config']['model_root']);destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(source/'project.json',destination/'project.json')
    for folder in ('fields','configs','DATA','management'):shutil.copytree(source/folder,destination/folder)
    for field in ctx['fields']:
        path=destination/field['model_file'];model=read(path)
        model['sequence']=[ctx['planning']['initial_states'][field['id']],*sequences[field['id']]]
        save(path,model)
    return destination


def prepare_inputs(ctx,model,work):
    work.mkdir(parents=True,exist_ok=False)
    request={'model_root':str(model),'workspace':str(work),'command':ctx['config']['command'],
             'years':list(range(ctx['planning']['start_year'],ctx['planning']['start_year']+ctx['planning']['years'])),
             'field_ids':None,'decisions':{},'prepare_only':True}
    save(work/'request.json',request)
    with (work/'model.log').open('w',encoding='utf-8') as log:
        subprocess.run([sys.executable,'-m','agro.aquacrop.runner',str(work/'request.json')],cwd=ROOT,
                       stdout=log,stderr=subprocess.STDOUT,check=True,timeout=ctx['config']['timeout_seconds'])


def score_result(result,work,ctx,sequences,objectives):
    economics=ctx['config']['economics'];p=ctx['planning'];years=range(p['start_year'],p['start_year']+p['years'])
    mass=profit=water=drainage=runoff=0.;details=[]
    annual={}
    for field in ctx['fields']:
        folder=work/'outputs'/field['id'];summary=read(folder/'summary.json')
        if summary['maximum_water_balance_error_mm']>ctx['config']['water_balance_tolerance_mm']:
            raise ValueError('AquaCrop water balance exceeds configured tolerance')
        with (folder/'annual.csv').open(encoding='utf-8',newline='') as stream:
            annual.update({(field['id'],int(row['year'])):row for row in csv.DictReader(stream) if int(row['year']) in years})
    expected={(field['id'],year) for field in ctx['fields'] for year in years}
    if {(row.field_id,row.year) for row in result.fields}!=expected or set(annual)!=expected:
        raise ValueError('Incomplete standalone field-year results')
    for row in result.fields:
        stats=annual[row.field_id,row.year];area=row.area_ha
        harvested=sum(row.harvested_dm_kg_ha.values())*area
        revenue=sum(v*economics['price_per_kg_dm'].get(crop,0.) for crop,v in row.harvested_dm_kg_ha.items())*area
        state=sequences[row.field_id][row.year-p['start_year']]
        cost=economics['cost_per_ha_by_state'][state]*area
        et=(float(stats['transpiration_mm'])+float(stats['evaporation_mm']))*area*10
        d=float(stats['drainage_mm'])*area*10;r=float(stats['runoff_mm'])*area*10
        if not all(math.isfinite(v) for v in (harvested,revenue,cost,et,d,r)) or min(et,d,r)<-1e-6:
            raise ValueError('Invalid native AquaCrop annual metric')
        mass+=harvested;profit+=revenue-cost;water+=max(et,0.);drainage+=max(d,0.);runoff+=max(r,0.)
        details.append({'field':row.field_id,'year':row.year,'state':state,'area_ha':area,
            'yield_dm_kg':harvested,'revenue':revenue,'cost':cost,'net_profit_proxy':revenue-cost,
            'evapotranspiration_m3':et,'drainage_m3':d,'runoff_m3':r})
    if water<=0:raise ValueError('Water productivity requires positive evapotranspiration')
    constraints,counts=crop_constraints(ctx,sequences)
    record={'status':'ok','engine':'aquacrop_standalone','yield_dm_kg':mass,'net_profit_proxy':profit,
            'evapotranspiration_m3':water,'water_productivity_kg_m3':mass/water,'drainage_m3':drainage,'runoff_m3':runoff,
            'G':constraints,'feasible':all(v<=0 for v in constraints),'fields':details,'crop_counts':counts,
            'crop_sequences':sequences,'objective_names':objectives,'metric_source':'native AquaCrop; economics from explicit standalone prices/costs'}
    record['F']=[record[CRITERIA[name][0]]*CRITERIA[name][1] for name in objectives]
    checked(record,len(objectives),len(constraints));return record


def representative(rows):
    low=[min(r['F'][i] for r in rows) for i in range(len(rows[0]['F']))]
    high=[max(r['F'][i] for r in rows) for i in range(len(low))]
    return min(rows,key=lambda r:(sum(((v-low[i])/(high[i]-low[i]))**2 if high[i]>low[i] else 0 for i,v in enumerate(r['F'])),r['candidate_id']))


def export(folder,records,objectives):
    columns=['candidate_id','status','feasible',*list(dict.fromkeys(metric for metric,_ in CRITERIA.values())),
             'cv',*['F_'+name for name in objectives]]
    with (folder/'evaluations.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for r in records:
            writer.writerow({**{key:r.get(key) for key in columns if not key.startswith('F_')},
                'cv':sum(max(v,0) for v in r['G']),**{'F_'+n:r['F'][i] for i,n in enumerate(objectives)}})


def report(folder,ctx,sequences,stage,records=()):
    p=ctx['planning'];years=list(range(p['start_year'],p['start_year']+p['years']))
    rows=''.join('<tr><td>'+html.escape(field)+'</td>'+''.join('<td>'+html.escape(s)+'</td>' for s in seq)+'</tr>' for field,seq in sequences.items())
    table='<table><tr><th>Поле</th>'+''.join(f'<th>{y}</th>' for y in years)+'</tr>'+rows+'</table>'
    if records:
        table+='<h2>Рассчитанные варианты</h2><table><tr><th>Вариант</th><th>Статус</th><th>Урожай, т СМ</th><th>Результат, усл. руб.</th><th>Эвапотранспирация, м³</th><th>Допустим</th></tr>'
        for r in records:
            values=[f'{r["yield_dm_kg"]/1000:.3f}',f'{r["net_profit_proxy"]:.0f}',f'{r["evapotranspiration_m3"]:.1f}'] if r['status']=='ok' else ['—']*3
            table+=f'<tr><td>{r["candidate_id"]}</td><td>{r["status"]}</td>'+''.join(f'<td>{v}</td>' for v in values)+f'<td>{r["feasible"]}</td></tr>'
        table+='</table>'
    labels={'prepared':'Входы подготовлены; расчёт не запускался','verified':'Один вариант рассчитан; поиск не запускался',
            'completed':'Поиск завершён; показан представитель допустимого набора Парето',
            'no_feasible_solution':'Допустимый план не найден; показан диагностический вариант','failed':'Ошибка — см. status.json'}
    links=' · '.join(f'<a href="{n}">{n}</a>' for n in ['status.json','scenario_inputs.json','crop_map.csv','evaluations.csv','pareto.json','selected_result.json','verification.json'] if (folder/n).exists())
    (folder/'index.html').write_text(f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Самостоятельный AquaCrop</title>
<style>body{{font:16px/1.6 system-ui;max-width:1300px;margin:30px auto;padding:0 20px;background:#f3f6f7;color:#234}}section{{background:white;padding:24px;margin:20px 0;border-radius:12px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}.scroll{{overflow:auto}}</style>
<h1>Самостоятельный AquaCrop: {years[0]}–{years[-1]}</h1><section><b>{labels[stage]}</b><p>Расчёт выполняет только AquaCrop. Почва, погода, профили и последовательности культур заданы собственными JSON и файлами AquaCrop. Исходная влажность — полевая влагоёмкость; 2024 год — разогрев. Начальные запасы многолетних растений приближённые.</p>
<p>Профили культур некалиброванные, погодные годы — исторические аналоги. Экономика условная: цены зерна заданы, цена кормов по умолчанию нулевая; годовые затраты заданы по культурам. Минеральный азот и Humic не рассчитываются и не входят в ограничения.</p><p>water_use — суммарная эвапотранспирация (испарение + транспирация), не объём полива; water_productivity — кг сухой массы / м³ эвапотранспирации. Сценарий богарный.</p>{links}</section><section class="scroll">{table}</section></html>''',encoding='utf-8')
    with (folder/'crop_map.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['field',*years]);writer.writerows([field,*seq] for field,seq in sequences.items())


def execute(ctx,folder,objectives,settings,mode):
    if not objectives or len(set(objectives))!=len(objectives) or any(n not in CRITERIA for n in objectives):
        raise ValueError('Choose distinct standalone criteria: '+', '.join(CRITERIA))
    settings=integer_search_settings(settings,len(objectives))
    if settings['algorithm']=='nsga2' and len(objectives)>3:raise ValueError('Use NSGA-III or auto for 4+ criteria')
    config=ctx['config'];folder=Path(folder).resolve()
    if mode!='prepare' and not Path(config['command'][0]).is_file():raise FileNotFoundError('AquaCrop field runner is missing')
    folder.mkdir(parents=True,exist_ok=False);started=time.monotonic();cache={};seed=choices(ctx,[0]*len(ctx['fields']))
    source=Path(config['model_root'])
    save(folder/'scenario_inputs.json',{'engine':'aquacrop_standalone','objectives':objectives,'search_settings':settings,
        'configuration':config,'decision_variables':'One legal five-year crop-sequence index per field',
        'initial_water':'Explicit field-capacity initialization; one bridge year spin-up',
        'input_sha256':{p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()},
        'note':'Only AquaCrop is executed; no external soil indicators or external management presets.'})
    def status(stage,**extra):save(folder/'status.json',{'stage':stage,'engine':'aquacrop_standalone','objectives':objectives,
        'algorithm':settings['algorithm'],'optimization_executed':mode=='run','evaluated_unique_candidates':len(cache),'elapsed_seconds':time.monotonic()-started,**extra})
    def simulate(sequences,work):
        model=materialize(ctx,sequences,work/'model')
        backend=AquaCropBackend(model,config['command'],range(ctx['planning']['start_year'],ctx['planning']['start_year']+ctx['planning']['years']),timeout_seconds=config['timeout_seconds'])
        result=backend.simulate({},work/'simulation');save(work/'simulation.json',asdict(result))
        return score_result(result,work/'simulation',ctx,sequences,objectives)
    try:
        status('preparing');model=materialize(ctx,seed,folder/'prepared_model');prepare_inputs(ctx,model,folder/'prepared_inputs')
        status('prepared');report(folder,ctx,seed,'prepared')
        if mode=='prepare':return folder
        if mode=='verify':
            status('verifying');row=simulate(seed,folder/'verification');save(folder/'verification.json',row)
            status('verified');report(folder,ctx,seed,'verified');return folder
        n_constraints=len(crop_constraints(ctx,seed)[0])
        def evaluate(vector):
            sequences=choices(ctx,vector);key=json.dumps(sequences,sort_keys=True)
            if key in cache:return cache[key]
            identifier=f'candidate_{len(cache)+1:03d}';work=folder/'candidates'/identifier
            status('search',running_candidate=identifier);print(identifier+': standalone AquaCrop',flush=True)
            try:row=simulate(sequences,work)
            except Exception as exc:row={'status':'failed','error':str(exc),'F':[1e30]*len(objectives),'G':[1e30]*n_constraints,'feasible':False,'crop_sequences':sequences}
            row['candidate_id']=identifier;cache[key]=row;save(work/'result.json',row)
            save(folder/'evaluations.json',list(cache.values()));save(folder/'pareto.json',nondominated(cache.values()));export(folder,cache.values(),objectives)
            print(f'{identifier}: {row["status"]}; F={row["F"]}; feasible={row["feasible"]}',flush=True);return row
        first=evaluate([0]*len(ctx['fields']))
        if first['status']!='ok':raise RuntimeError('Standalone preflight failed: '+first['error'])
        search_integer([ctx['pool']['fields'][f['id']]['alternatives'] for f in ctx['fields']],evaluate,settings,len(objectives),n_constraints)
        valid=[r for r in cache.values() if r['status']=='ok'];front=nondominated(cache.values())
        if front:chosen=representative(front)
        else:
            cv=min(sum(max(g,0) for g in r['G']) for r in valid)
            chosen=representative([r for r in valid if sum(max(g,0) for g in r['G'])==cv])
        stage='completed' if front else 'no_feasible_solution';save(folder/'selected_result.json',chosen)
        save(folder/'optimization_summary.json',{'status':stage,'evaluated':len(cache),'successful':len(valid),
            'feasible':sum(r['feasible'] for r in valid),'pareto_candidates':len(front),'selected_candidate':chosen['candidate_id'],
            'objectives':objectives,'search_settings':settings,'engine':'aquacrop_standalone',
            'display_rule':'Minimum equal-weight normalized squared distance to ideal point within feasible Pareto set, or minimum-CV set if none feasible.'})
        status(stage);report(folder,ctx,chosen['crop_sequences'],stage,list(cache.values()));return folder
    except BaseException as exc:status('failed',error=str(exc));report(folder,ctx,seed,'failed',list(cache.values()));raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'models/aquacrop_standalone/optimization.json')
    parser.add_argument('--objectives',nargs='+',choices=CRITERIA)
    parser.add_argument('--algorithm',choices=['auto','nsga2','nsga3'])
    parser.add_argument('--population',type=int);parser.add_argument('--generations',type=int)
    parser.add_argument('--reference-partitions',type=int);parser.add_argument('--output',type=Path)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--run',action='store_true');mode.add_argument('--verify-model',action='store_true');mode.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args(argv);ctx=context(args.config);settings=dict(ctx['config']['search'])
    for key in ('algorithm','population','generations','reference_partitions'):
        if getattr(args,key) is not None:settings[key]=getattr(args,key)
    objectives=args.objectives or ctx['config']['objectives']
    folder=args.output or Path(ctx['config']['output_root'])/('+'.join(objectives)+'_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    print(execute(ctx,folder,objectives,settings,'run' if args.run else 'verify' if args.verify_model else 'prepare'),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
