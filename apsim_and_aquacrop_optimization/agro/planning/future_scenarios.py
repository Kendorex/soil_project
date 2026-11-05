"""Single/multiple-objective searches of future crop maps using native APSIM."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import datetime as dt
import html
import json
import math
import os
import shutil
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[2]
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.matplotlib'))

from agro.planning.future_model import SUPPORTED_STATES, extend_model, fingerprint, source_parts, write_analogue_weather
from agro.planning.rotation_planner import RotationRules, plan_fields_stateful, select_farm_plan
from agro.planning.rotation_visualization import plot_rotation_map, write_rotation_text_report
from agro.apsim.native_rotation_optimizer import simulate, score, prepare as native_prepare
from agro.optimization.nsga2 import checked, search_integer, nondominated, integer_search_settings

DEFAULT_CONFIG=ROOT/'models/apsim/configs/future_rotation_2025_2029.json'
OBJECTIVES={'yield':'Максимум сухой массы урожая','profit':'Максимум расчётного экономического результата',
            'mineral_n':'Минимум дефицита минерального азота','humic':'Максимум сохранности Humic на худшем поле'}


EXTRA_METRICS={'yield_dm_kg','net_profit_proxy','mineral_n_shortfall',
               'fertilizer_n_kg','fertilizer_cost_proxy','humic_total_change_kg'}


def criteria_registry(config):
    """Additional scalar criteria are declared explicitly; no expression evaluation."""
    registry=dict(OBJECTIVES)
    for name,spec in config.get('optimization',{}).get('additional_objectives',{}).items():
        if not name or not all(c.isascii() and (c.isalnum() or c=='_') for c in name) or name in registry:
            raise ValueError('Additional criterion needs a unique ASCII identifier: '+name)
        if not isinstance(spec,dict) or spec.get('metric') not in EXTRA_METRICS:
            raise ValueError(f'Unsupported metric for criterion {name}; available: {sorted(EXTRA_METRICS)}')
        if spec.get('direction') not in ('min','max') or not isinstance(spec.get('label'),str) or not spec['label'].strip():
            raise ValueError(f'Criterion {name} needs a label and direction min/max')
        registry[name]=dict(spec)
    return registry


def objective_list(value,registry=None):
    registry=OBJECTIVES if registry is None else registry
    names=[value] if isinstance(value,str) else list(value)
    if not names or len(set(names))!=len(names) or any(name not in registry for name in names):
        raise ValueError('Choose distinct objectives from: '+', '.join(registry))
    return names


def objective_title(value,registry=None):
    registry=OBJECTIVES if registry is None else registry
    return '; '.join(registry[name] if isinstance(registry[name],str) else registry[name]['label']
                     for name in objective_list(value,registry))


def representative(front):
    """Equal-weight distance to the ideal point after min-max normalization of this front."""
    low=[min(row['F'][i] for row in front) for i in range(len(front[0]['F']))]
    high=[max(row['F'][i] for row in front) for i in range(len(low))]
    def distance(row):
        return sum(((v-low[i])/(high[i]-low[i]))**2 if high[i]>low[i] else 0.
                   for i,v in enumerate(row['F']))
    return min(front,key=lambda row:(distance(row),row['candidate_id']))


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def objective_value(row,objective,registry=None):
    if objective=='yield':return -row['yield_dm_kg']
    if objective=='profit':return -row['net_profit_proxy']
    if objective=='mineral_n':return row['mineral_n_shortfall']
    if objective=='humic':return max((s['start_kg_ha']-s['end_kg_ha'])/max(abs(s['start_kg_ha']),1.) for s in row['humic'].values())
    if registry is not None and objective in registry and isinstance(registry[objective],dict):
        spec=registry[objective];value=float(row[spec['metric']])
        if not math.isfinite(value):raise ValueError('Non-finite criterion: '+objective)
        return value if spec['direction']=='min' else -value
    raise ValueError(f'Unknown objective: {objective}')


def evaluate_humic_policy(row,mix_g,objectives,policy,registry=None):
    row['all_objectives_F']=list(row['F'])
    row['humic_constraints_G']=list(row['G']) # Physical loss diagnostics retained even without a hard constraint.
    row['humic_policy']=policy
    row['G']=([] if policy=='objective_only' else row['G'])+mix_g
    row['F']=[objective_value(row,name,registry) for name in objectives]
    row['feasible']=all(g<=0 for g in row['G'])
    return row


def supplementary_metrics(row,audit,runtime):
    """Aggregate actual future-period applications and area-weighted carbon change."""
    areas={item['field']:item['area_ha'] for item in row['fields']}
    prices={v['apsim_type']:float(v['price_per_kg_n']) for v in runtime['fertilizer']['forms'].values()}
    applications=[event for event in audit['applications'] if event['year'] in runtime['evaluation_years']]
    row['fertilizer_n_kg']=sum(event['dose_kg_n_ha']*areas[event['field']] for event in applications)
    row['fertilizer_cost_proxy']=sum(event['dose_kg_n_ha']*prices[event['form']]*areas[event['field']] for event in applications)
    row['humic_total_change_kg']=sum((stock['end_kg_ha']-stock['start_kg_ha'])*areas[field]
                                    for field,stock in row['humic'].items())


def management_presets(optimization_run,model_root):
    summary=read(optimization_run/'optimization_summary.json')
    if summary['status'] not in ('completed','no_feasible_solution'):
        raise ValueError('Management source search has not completed')
    for name,expected in read(optimization_run/'sources.json')['inputs'].items():
        if fingerprint(model_root/name)!=expected:raise ValueError('Optimization source does not match the calibrated model/weather')
    rows=[r for r in read(optimization_run/'evaluations.json') if r['status']=='ok']
    if not rows:raise ValueError('No successful management records')
    selected={'yield':max(rows,key=lambda r:r['yield_dm_kg']),
              'profit':max(rows,key=lambda r:r['net_profit_proxy']),
              'mineral_n':min(rows,key=lambda r:r['mineral_n_shortfall']),
              'humic':min(rows,key=lambda r:sum(max(0.,g) for g in r['G']))}
    return {name:{'candidate_id':r['candidate_id'],'historically_feasible':r['feasible'],
                  'policies':deepcopy(r['policies']),
                  'use':'Fixed management starting policy, not a forecast or a feasible future recommendation'}
            for name,r in selected.items()}


def load_management_reference(path,model_root):
    """Load portable starting policies without keeping an old simulation run."""
    bundle=read(path)
    if bundle.get('schema_version')!=1 or bundle.get('kind')!='management_starting_reference':
        raise ValueError('Unsupported management reference')
    for name in ('simulation.apsimx','weather.met'):
        if fingerprint(model_root/name)!=bundle['source_inputs_sha256'][name]:
            raise ValueError('Management reference does not match calibrated model/weather')
    if set(bundle['presets'])!={'yield','profit','mineral_n','humic'}:
        raise ValueError('Management reference must include four starting policies')
    return deepcopy(bundle['presets']),deepcopy(bundle['native_config'])


def context(config_path):
    import yaml
    path=Path(config_path).resolve();config=read(path)
    if config.get('schema_version')!=1:raise ValueError('Unsupported future scenario schema')
    for key in ('planning_config','model_root','optimization_run','management_reference','output_root'):
        if key in config:config[key]=str((path.parent/config[key]).resolve())
    aqua=config.setdefault('aquacrop',{})
    aqua['model_root']=str((path.parent/aqua['model_root']).resolve()) if aqua.get('model_root') else str(ROOT/'models/aquacrop')
    command=aqua.get('command',[str(ROOT/'engines/aquacrop/field-target/debug/aquacrop-field-runner.exe')])
    aqua['command']=[str((path.parent/command[0]).resolve()),*command[1:]]
    aqua.setdefault('timeout_seconds',config['timeout_seconds'])
    planning_path=Path(config['planning_config'])
    planning=yaml.safe_load(planning_path.read_text(encoding='utf-8-sig'))
    if planning['initial_state_year']!=planning['start_year']-1:
        raise ValueError('Preceding observed states must match the planning horizon')
    if planning['years']<=0:raise ValueError('Planning horizon must be positive')
    unsupported_initial=set(planning['initial_states'].values())-SUPPORTED_STATES
    if unsupported_initial:raise ValueError(f'No calibrated bridge profiles for {sorted(unsupported_initial)}')
    if config['crop_mix_mode']!='hard_bounds' or config['humic_constraint'] not in ('each_field_end_gte_start','objective_only'):
        raise ValueError('Expected hard crop-count bounds and a supported Humic policy')
    root=Path(config['model_root']);source=read(root/'simulation.apsimx')
    _,zones=source_parts(source)
    if set(zones)!=set(planning['initial_states']):raise ValueError('Initial field coverage differs from calibration')
    state_model=deepcopy(planning['state_model'])
    excluded=[s for s in state_model['candidate_states'] if s not in SUPPORTED_STATES]
    state_model['candidate_states']=[s for s in state_model['candidate_states'] if s in SUPPORTED_STATES]
    if not state_model['candidate_states']:raise ValueError('No simulated crop states')
    rules_path=(planning_path.parent/planning['rotation_rules']).resolve()
    rules=RotationRules.from_file(rules_path)
    pool=plan_fields_stateful(rules,planning['initial_states'],state_model,planning['years'],
        top_k=planning.get('candidate_pool',20000),minimum_score=planning['minimum_transition_score'])
    selection=select_farm_plan(pool['fields'],planning['farm_crop_mix'],planning['years'],
                              beam_width=planning['farm_selection_beam_width'])
    fields=sorted(zones,key=lambda f:int(f.removeprefix('Field')))
    for field in fields:
        alternatives=pool['fields'][field]['alternatives'];rank=selection['selected_ranks'][field]
        pool['fields'][field]['alternatives']=([a for a in alternatives if a['rank']==rank]+
                                              [a for a in alternatives if a['rank']!=rank])
    if config.get('management_reference'):
        presets,native_config=load_management_reference(Path(config['management_reference']),root)
    else:
        presets=management_presets(Path(config['optimization_run']),root)
        native_config=read(Path(config['optimization_run'])/'config_snapshot.json')
    if 'field_area_ha' in config:
        from agro.apsim.field_area import apply_field_area
        apply_field_area(source,config['field_area_ha'])
        native_config['field_area_ha']=config['field_area_ha']
    if config.get('economics'):
        native_config['economics']=deepcopy(config['economics'])
    native_config['evaluation_years']=list(range(planning['start_year'],planning['start_year']+planning['years']))
    native_config['nsga2']=config['nsga2'];native_config['runtime']['timeout_seconds']=config['timeout_seconds']
    # Allow the same local APSIM executable override as the existing workflow.
    if os.environ.get('APSIM_MODELS'):native_config['runtime']['command']=[os.environ['APSIM_MODELS']]
    return {'config':config,'planning':planning,'pool':pool,'fields':fields,'presets':presets,
            'source':source,'native_config':native_config,'excluded_states':excluded,
            'config_path':path,'rules_path':rules_path,'planning_path':planning_path}


def choices(ctx,vector):
    if len(vector)!=len(ctx['fields']):raise ValueError('One sequence index is required per field')
    return {field:ctx['pool']['fields'][field]['alternatives'][int(index)] for field,index in zip(ctx['fields'],vector)}


def crop_mix(ctx,selected):
    planning=ctx['planning'];categories=planning['farm_crop_mix']['categories']
    mapping={state:category for category,spec in categories.items() for state in spec['states']}
    counts=[{category:0 for category in categories} for _ in range(planning['years'])]
    for alternative in selected.values():
        for i,state in enumerate(alternative['sequence_ids']):counts[i][mapping[state]]+=1
    constraints=[];violation=0
    for count in counts:
        for category,spec in categories.items():
            value=count[category];low=spec['annual_min'];high=spec['annual_max']
            constraints.extend([(low-value)/max(low,1),(value-high)/max(high,1)])
            violation+=max(low-value,0)+max(value-high,0)
    for category,spec in categories.items():
        low=spec.get('horizon_min',0);high=spec.get('horizon_max',len(ctx['fields'])*planning['years'])
        total=sum(c[category] for c in counts)
        constraints.extend([(low-total)/max(low,1),(total-high)/max(high,1)])
        violation+=max(low-total,0)+max(total-high,0)
    return constraints,{'categories':categories,'within_historical_ranges':violation==0,
        'range_violation_units':violation,'annual_counts':[{'year':planning['start_year']+i,**r} for i,r in enumerate(counts)]}


def plan_result(ctx,selected,state='prepared'):
    result=deepcopy(ctx['pool']);start=ctx['planning']['start_year']
    stages={'prepared':'future_candidate_map_not_apsim_optimized',
            'model_verified':'future_map_simulated_not_optimized',
            'completed':'future_objective_search_complete',
            'no_feasible_solution':'diagnostic_infeasible_future_map'}
    result.update(start_year=start,end_year=start+ctx['planning']['years']-1,status=stages[state])
    result['farm_selection']=crop_mix(ctx,selected)[1]
    for field,chosen in selected.items():
        result['fields'][field]['selected_alternative']=deepcopy(chosen)
        result['fields'][field]['alternatives']=[deepcopy(chosen)]
    return result


def build_model(ctx,selected,policies):
    return extend_model(ctx['source'],ctx['planning']['initial_states'],
        {f:a['sequence_ids'] for f,a in selected.items()},ctx['planning']['start_year'],policies,
        bridge_states=ctx['config'].get('bridge_states'))


def artifacts(folder,ctx,selected,state='prepared'):
    result=plan_result(ctx,selected,state)
    save(folder/'crop_plan.json',result)
    rows=[{'field':field,**{str(ctx['planning']['start_year']+i):s for i,s in enumerate(alt['sequence_ids'])}}
          for field,alt in selected.items()]
    with (folder/'crop_map.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    plot_rotation_map(folder/'crop_rotation_map.png',result)
    write_rotation_text_report(folder/'crop_rotation_report.txt',result)
    if state!='prepared':
        path=folder/'crop_rotation_report.txt';text=path.read_text(encoding='utf-8')
        label={'model_verified':'одна карта рассчитана APSIM; поиск ещё не выполнялся',
               'completed':'найден допустимый результат поиска APSIM; см. цели и набор Парето в отчёте',
               'no_feasible_solution':'допустимых решений не найдено; показан диагностический вариант'}[state]
        text=text.replace('Статус: карта совместимых переходов; APSIM не выполнялся.','Статус: '+label+'.')
        text=text.replace('Карта не является прогнозом урожая, прибыли или состояния почвы до многолетней проверки APSIM.',
            'Использован исторический погодный аналог; расчёт не является проверкой по будущим наблюдениям.')
        path.write_text(text,encoding='utf-8')


def evaluation_table(path,records,objective,registry=None):
    objectives=objective_list(objective,registry)
    columns=['candidate_id','status','objective','objective_value','yield_dm_kg','net_profit_proxy',
             'mineral_n_shortfall','worst_humic_change_percent','humic_loss_fields',
             'crop_mix_violation_units','sum_positive_constraints','feasible',
             'fertilizer_n_kg','fertilizer_cost_proxy','humic_total_change_kg','engine','harvest_source','soil_metrics_source']+['F_'+name for name in objectives]
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for row in records:
            stocks=row.get('humic')
            writer.writerow({'candidate_id':row['candidate_id'],'status':row['status'],
                'objective':'+'.join(objectives),'objective_value':row['F'][0] if len(objectives)==1 else json.dumps(row['F']),
                'yield_dm_kg':row.get('yield_dm_kg'),'net_profit_proxy':row.get('net_profit_proxy'),
                'mineral_n_shortfall':row.get('mineral_n_shortfall'),
                'worst_humic_change_percent':-100*max(row['humic_constraints_G']) if stocks else None,
                'humic_loss_fields':sum(g>0 for g in row['humic_constraints_G']) if stocks else None,
                'crop_mix_violation_units':row.get('crop_mix',{}).get('range_violation_units'),
                'sum_positive_constraints':sum(max(g,0) for g in row['G']),
                'feasible':row['feasible'],
                'engine':row.get('engine','apsim'),'harvest_source':row.get('metric_sources',{}).get('harvest','APSIM'),
                'soil_metrics_source':row.get('metric_sources',{}).get('humic','APSIM'),
                **{name:row.get(name) for name in ('fertilizer_n_kg','fertilizer_cost_proxy','humic_total_change_kg')},
                **{'F_'+name:row['F'][i] for i,name in enumerate(objectives)}})


def report(output,ctx,objective,state,record=None):
    inputs=read(output/'scenario_inputs.json')
    registry=inputs.get('criteria_registry',OBJECTIVES)
    preset=inputs['management_source']
    title=objective_title(objective,registry)
    engine=inputs.get('engine','apsim')
    title=('AquaCrop + APSIM soil: ' if engine=='aquacrop' else 'APSIM: ')+title
    note='Подготовлен начальный вариант. Оптимизация карты ещё не запускалась.' if state=='prepared' else (
        'Одна будущая модель проверена реальным запуском APSIM. Оптимизация карты ещё не запускалась.' if state=='model_verified' else
        'Оптимизация завершена; показано допустимое решение. Для нескольких целей это представитель набора Парето по нормированному расстоянию до идеальной точки.' if state=='completed' else
        'Допустимых решений не найдено; показан диагностический вариант с наименьшим нарушением.')
    if engine=='aquacrop' and state=='model_verified':
        note='Одна будущая карта рассчитана AquaCrop и APSIM. Оптимизационный поиск ещё не запускался.'
    start=ctx['planning']['start_year'];end=start+ctx['planning']['years']-1
    rows=''
    if record:
        rows=f'<p>Сухая масса: {record["yield_dm_kg"]/1000:.3f} т; расчётный результат: {record["net_profit_proxy"]:.0f} усл. руб.; дефицит N: {record["mineral_n_shortfall"]:.4f}.</p>'
    page=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font:17px/1.6 system-ui;max-width:1300px;margin:35px auto;padding:0 24px;color:#203441;background:#f2f5f7}}section{{background:white;padding:24px;margin:20px 0;border-radius:12px}}img{{width:100%}}code{{overflow-wrap:anywhere}}</style>
<h1>{html.escape(title)}</h1><section><b>{html.escape(note)}</b>{rows}<p>План {start}–{end}, исходные состояния {start-1} года. Погода после {html.escape(ctx['config']['historical_weather_end'])} — явно заданный исторический аналог, не наблюдения и не прогноз.</p>
<p>Алгоритм: {html.escape(inputs['search_settings']['algorithm'].upper())}. Выбранные критерии оцениваются одновременно, без свёртки в одну целевую функцию.</p>
<p>Начальная схема управления: {html.escape(preset['candidate_id'])}. В прошлом поиске она не прошла Humic; её пригодность на новой карте проверяется заново.</p>
<p>Меняются последовательности культур по полям. Сорт, густота, глубина, междурядье и удобрения фиксируются выбранной схемой управления. Для дат и профилей новых посевов используются явно отмеченные калиброванные аналоги.</p>
<p>Картофель и вико-овсяная смесь исключены из вариантов, поскольку для них не подготовлены соответствующие калиброванные компоненты.</p>
<p>Цены — условные; для кормов нет заданной выручки. Humic проверяется на каждом поле. Эти результаты относятся только к заданному модельному примеру.</p></section>
<section><a href="crop_map.csv">Карта CSV</a> · <a href="crop_plan.json">Состояния и переходы JSON</a> · <a href="scenario_inputs.json">Допущения и источники</a> · <a href="status.json">Статус</a></section>
<section><img src="crop_rotation_map.png" alt="Карта посевов"></section></html>'''
    extra=[(name,title) for name,title in [('evaluations.csv','Оценки кандидатов CSV'),
        ('selected_result.json','Оценка выбранного варианта'),('selected_future.apsimx','Модель выбранной карты'),
        ('optimization_summary.json','Итог поиска'),('pareto.csv','Набор Парето CSV'),
        ('pareto.json','Набор Парето JSON'),('verification.json','Проверка одной модели'),
        ('prepared_aquacrop_model/new_calibr_import.json','Происхождение новых входов AquaCrop'),
        ('prepared_aquacrop_inputs/prepared.json','Подготовленные планы AquaCrop'),
        ('selected_management.json','Выбранная технология и удобрения'),('decision_space.json','Переменные поиска')]
        if (output/name).exists()]
    if extra:
        links=' · '.join(f'<a href="{name}">{html.escape(title)}</a>' for name,title in extra)
        page=page.replace('</html>','<section>'+links+'</section></html>')
    if engine=='aquacrop':
        page=page.replace('</html>','<section><b>Источники показателей:</b> урожай — нативные уборки AquaCrop; выручка рассчитывается по этим уборкам и заданным ценам. Humic, минеральный азот и фактические внесения удобрений — APSIM для той же карты и погоды. Поправка урожая из APSIM не применяется. Обратной связи биомассы AquaCrop в углеродный и азотный баланс APSIM нет. AquaCrop использует эффективные профили культур; их калибровка не переносится автоматически из APSIM. При расчёте начальная влажность берётся из APSIM перед мостовым годом; подготовленные входы до этого содержат явно отмеченные временные значения. Состояние многолетних растений приближённое.</section></html>')
        if (output/'selected_aquacrop.json').exists():
            page=page.replace('</html>','<section><a href="selected_aquacrop.json">Входы и результаты выбранной модели AquaCrop</a></section></html>')
    if inputs['configuration'].get('humic_constraint')=='objective_only':
        page=page.replace('Humic проверяется на каждом поле.','Потери Humic показаны отдельно по каждому полю.')
        page=page.replace('</html>','<section><b>Humic является только целью оптимизации.</b> Допустимость определяется структурой посевов; снижение углерода не запрещено. Цель humic минимизирует относительную потерю на худшем поле. Допустимый план может терять Humic; проверьте абсолютные изменения перед выбором.</section></html>')
    if inputs.get('search_scope')=='joint':
        page=page.replace('Сорт, густота, глубина, междурядье и удобрения фиксируются выбранной схемой управления.',
                          'Сорт, густота, глубина, междурядье и схема удобрения также изменяются оптимизатором; исходная схема служит начальной точкой.')
        page=page.replace('</html>','<section><b>Совместный поиск карты и технологии.</b> Десять индексов последовательностей культур и десять параметров управления. Политика общая для культуры на всех полях и годах, а не отдельная доза для каждого поля. Найденная технология сохранена в selected_management.json.</section></html>')
    if (output/'pareto.json').exists():
        front=read(output/'pareto.json')
        headers='<th>Кандидат и модель</th>'+''.join('<th>'+html.escape(objective_title([n],registry))+'</th>' for n in objective_list(objective,registry))
        items=''.join('<tr><td><a href="candidates/'+html.escape(r['candidate_id'])+'/candidate.apsimx">'+html.escape(r['candidate_id'])+'</a></td>'+''.join(f'<td>{v:.6g}</td>' for v in r['F'])+'</tr>' for r in front)
        page=page.replace('</html>','<section><h2>Допустимые решения Парето</h2><p>Показаны значения F для минимизации: урожай и экономика со знаком минус, Humic — относительная потеря на худшем поле. Дополнительные критерии max также передаются со знаком минус; определения сохранены в scenario_inputs.json. Модели всех вариантов сохранены в candidates.</p><table><tr>'+headers+'</tr>'+items+'</table></section></html>')
    if inputs.get('field_area_ha') is not None:
        area=float(inputs['field_area_ha'])
        page=page.replace('</html>',f'<section><b>Площадь поля: {area:g} га.</b> Всего {len(ctx["fields"])*area:g} га. Площадь применяется в выполняемой APSIM-модели; показатели на гектар и общие итоги имеют разные единицы.</section></html>')
    if inputs.get('crop_map_repair')=='milp':
        page=page.replace('</html>','<section><b>Согласование состава посевов до симуляции.</b> MILP подбирает ближайшее сочетание разрешённых полевых последовательностей с исходными годовыми и общими ограничениями. Параметры технологии сохраняются. Улучшение урожая этим согласованием не гарантируется.</section></html>')
    (output/'index.html').write_text(page,encoding='utf-8')
    




def execute(ctx,objective,output,run_search=False,verify_model=False,algorithm=None,management_preset=None):
    registry=criteria_registry(ctx['config'])
    objectives=objective_list(objective,registry)
    objective_only=ctx['config'].get('humic_constraint')=='objective_only'
    if objective_only and 'humic' not in objectives:objectives.append('humic')
    objective='+'.join(objectives)
    config=ctx['config'];planning=ctx['planning']
    engine=config.get('engine','apsim')
    if engine not in ('apsim','aquacrop'):raise ValueError('Unknown future engine')
    if engine=='aquacrop' and (run_search or verify_model):
        command=config['aquacrop']['command']
        if not command or not (Path(command[0]).is_file() or shutil.which(command[0])):
            raise FileNotFoundError('AquaCrop executable is missing; build the field runner before starting the search')
    optimization=config.get('optimization',{})
    settings={**config['nsga2'],**optimization.get('settings',{})}
    settings['algorithm']=algorithm or optimization.get('algorithm','auto')
    settings=integer_search_settings(settings,len(objectives))
    if settings['algorithm']=='nsga2' and len(objectives)>3:
        raise ValueError('For more than three criteria choose --algorithm nsga3 or auto')
    preset_name=management_preset or optimization.get('management_preset') or next((name for name in objectives if name in ctx['presets']),'yield')
    if preset_name not in ctx['presets']:raise ValueError('Unknown management preset: '+preset_name)
    preset=ctx['presets'][preset_name]
    scope=optimization.get('search_scope','crop_map')
    if scope not in ('crop_map','joint'):raise ValueError('search_scope must be crop_map or joint')
    map_variables=len(ctx['fields'])
    search_options=[ctx['pool']['fields'][f]['alternatives'] for f in ctx['fields']]
    management_space=None
    if scope=='joint':
        from agro.planning.joint_management import policy_space,decode_policies
        management_space=policy_space(ctx['native_config'],preset['policies'])
        search_options+=management_space['options']
    repair_method=optimization.get('crop_map_repair','none')
    if repair_method not in ('none','milp'):raise ValueError('crop_map_repair must be none or milp')
    decision_repair=None
    if repair_method=='milp':
        from agro.planning.crop_mix_repair import CropMixRepair
        decision_repair=CropMixRepair(ctx)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    runtime=deepcopy(ctx['native_config']);runtime['model_root']=str(output)
    seed=choices(ctx,[0]*len(ctx['fields']))
    weather=write_analogue_weather(Path(config['model_root'])/'weather.met',output/'weather.met',
        config['historical_weather_end'],config['climate_analogue_years'],max(runtime['evaluation_years']))
    document,provenance=build_model(ctx,seed,preset['policies'])
    prepared,_=native_prepare(document,runtime,None)
    save(output/'prepared_future.apsimx',prepared)
    save(output/'decision_space.json',{'search_scope':scope,'crop_map_variables':map_variables,
        'management_variables':len(management_space['specs']) if management_space else 0,
        'total_variables':len(search_options),
        'management_options':[{'crop':crop,'parameter':key,'options':options} for (crop,key,_),options in
            zip(management_space['specs'],management_space['options'])] if management_space else []})
    if engine=='aquacrop':
        from agro.planning.aquacrop_future import prepare_model,prepare_inputs
        aqua_model=output/'prepared_aquacrop_model'
        prepare_model(document,output/'weather.met',config['aquacrop'],aqua_model,planning['start_year']-1)
        prepare_inputs(aqua_model,config['aquacrop'],output/'prepared_aquacrop_inputs',runtime['evaluation_years'])
    save(output/'sequence_options.json',{field:[{'index':i,**{key:alternative[key] for key in
         ('rank','sequence_ids','transition_codes','mean_score')}} for i,alternative in enumerate(
         ctx['pool']['fields'][field]['alternatives'])] for field in ctx['fields']})
    save(output/'scenario_inputs.json',{'objective':objective,'objectives':objectives,'objective_name':objective_title(objectives,registry),
        'engine':engine,'metric_sources':{'harvest':'native AquaCrop' if engine=='aquacrop' else 'APSIM','humic':'APSIM','mineral_n':'APSIM'},
        'criteria_registry':registry,
        'search_settings':settings,'management_preset':preset_name,
        'search_scope':scope,'management_preset_role':'Initial policy only' if scope=='joint' else 'Fixed policy',
        'crop_map_repair':repair_method,'field_area_ha':config.get('field_area_ha'),
        'management_source':preset,'weather':weather,'profile_assumptions':provenance,
        'excluded_unsupported_crop_states':ctx['excluded_states'],
        'planning_config':planning,'configuration':config,
        'source_model_sha256':fingerprint(Path(config['model_root'])/'simulation.apsimx'),
        'rotation_rules_sha256':fingerprint(ctx['rules_path']),
        'planning_config_sha256':fingerprint(ctx['planning_path']),
        'scenario_config_sha256':fingerprint(ctx['config_path']),
        'optimization_inputs_sha256':fingerprint(Path(config['management_reference']) if config.get('management_reference') else Path(config['optimization_run'])/'evaluations.json'),
        'note':'Future outcomes must be calculated by APSIM; historical candidate scores are never used as future crop-map scores.'})
    artifacts(output,ctx,seed)
    save(output/'status.json',{'stage':'prepared','objective':objective,'objectives':objectives,
        'engine':engine,'algorithm':settings['algorithm'],'optimization_executed':False})
    report(output,ctx,objectives,'prepared')
    
    print(f'PREPARED {objective}: {output}',flush=True)
    if not run_search and not verify_model:return output
    started=time.monotonic();cache={};mix_count=len(crop_mix(ctx,seed)[0]);n_constraints=(0 if objective_only else len(ctx['fields']))+mix_count

    def simulate_candidate(candidate,work):
        if engine=='aquacrop':
            from agro.planning.aquacrop_future import simulate_map
            return simulate_map(candidate,runtime,work,config['aquacrop'],planning['start_year']-1,simulate)
        return simulate(candidate,runtime,None,work)

    def status(stage,**extra):
        save(output/'status.json',{'stage':stage,'objective':objective,'objectives':objectives,
            'engine':engine,
            'algorithm':settings['algorithm'],'evaluated_unique_candidates':len(cache),
            'elapsed_seconds':time.monotonic()-started,'optimization_executed':run_search,**extra})
    try:
        if verify_model:
            status('verifying_model')
            result,audit=simulate_candidate(document,output/'verification')
            save(output/'verification.json',{'status':'passed','field_years':len(result.fields),
                'dry_harvest_kg':sum(sum(r.harvested_dm_kg_ha.values())*r.area_ha for r in result.fields),
                'humic':audit['humic'],'optimization_executed':False,
                'engine':engine,'metric_sources':audit.get('metric_sources',{'harvest':'APSIM','humic':'APSIM','mineral_n':'APSIM'}),
                'note':'One climate-analogue scenario was simulated, not a full optimization or a real-weather validation.'})
            status('model_verified');artifacts(output,ctx,seed,'model_verified')
            report(output,ctx,objectives,'model_verified')
            return output
        status('baseline')
        reference_document,reference_provenance=build_model(ctx,seed,None)
        baseline,baseline_audit=simulate_candidate(reference_document,output/'baseline')
        baseline_row=score(baseline,baseline_audit,baseline,reference_document,runtime)
        if objective_only:evaluate_humic_policy(baseline_row,crop_mix(ctx,seed)[0],objectives,'objective_only',registry)
        save(output/'baseline_score.json',baseline_row)

        def evaluate(vector):
            if len(vector)!=len(search_options):raise ValueError('Invalid joint decision vector length')
            if decision_repair is not None:vector=decision_repair(vector)
            selected=choices(ctx,vector[:map_variables])
            candidate_policies=decode_policies(ctx['native_config'],management_space,vector[map_variables:]) if management_space else preset['policies']
            key=json.dumps({'sequences':{f:a['sequence_ids'] for f,a in selected.items()},'policies':candidate_policies},sort_keys=True)
            if key in cache:return cache[key]
            identifier=f'candidate_{len(cache)+1:03d}';folder=output/'candidates'/identifier
            status('search',running_candidate=identifier)
            print(f'{identifier}: {engine} future map',flush=True)
            try:
                candidate,assumptions=build_model(ctx,selected,candidate_policies)
                result,audit=simulate_candidate(candidate,folder)
                row=score(result,audit,baseline,candidate,runtime)
                supplementary_metrics(row,audit,runtime)
                mix_g,mix=crop_mix(ctx,selected)
                evaluate_humic_policy(row,mix_g,objectives,'objective_only' if objective_only else 'each_field_end_gte_start',registry)
                row['objective_names']=objectives
                row['policies']=candidate_policies
                row['search_scope']=scope
                row['engine']=engine
                row['metric_sources']=audit.get('metric_sources',{'harvest':'APSIM','humic':'APSIM','mineral_n':'APSIM'})
                row['feasible']=all(g<=0 for g in row['G'])
                row.update(status='ok',candidate_id=identifier,crop_mix=mix,
                    crop_sequences={f:a['sequence_ids'] for f,a in selected.items()})
                checked(row,len(objectives),n_constraints)
                save(folder/'profile_assumptions.json',assumptions)
            except Exception as exc:
                folder.mkdir(parents=True,exist_ok=True)
                row={'status':'failed','candidate_id':identifier,'error':str(exc),
                     'engine':engine,'metric_sources':{'harvest':'native AquaCrop' if engine=='aquacrop' else 'APSIM','humic':'APSIM','mineral_n':'APSIM'},
                     'F':[1e30]*len(objectives),'G':[1e30]*n_constraints,'feasible':False,
                     'policies':candidate_policies,'search_scope':scope,
                     'crop_sequences':{f:a['sequence_ids'] for f,a in selected.items()}}
            cache[key]=row;save(folder/'result.json',row)
            if decision_repair is not None:save(output/'crop_map_repair.json',decision_repair.summary())
            save(output/'evaluations.json',list(cache.values()));save(output/'pareto.json',nondominated(cache.values()))
            evaluation_table(output/'evaluations.csv',cache.values(),objectives,registry)
            evaluation_table(output/'pareto.csv',nondominated(cache.values()),objectives,registry)
            print(f'{identifier}: {row["status"]}; F={row["F"]}; feasible={row["feasible"]}',flush=True)
            return row

        preflight=evaluate([0]*len(search_options))
        if preflight['status']!='ok':raise RuntimeError('Future management preflight failed: '+preflight['error'])
        repair_kwargs={'decision_repair':decision_repair} if decision_repair is not None else {}
        search_integer(search_options,evaluate,
                       settings,len(objectives),n_constraints,**repair_kwargs)
        if decision_repair is not None:save(output/'crop_map_repair.json',decision_repair.summary())
        successful=[r for r in cache.values() if r['status']=='ok'];front=nondominated(cache.values())
        if not successful:raise RuntimeError('No successful map evaluations')
        if front:
            chosen=representative(front)
        else:
            violation=min(sum(max(g,0) for g in row['G']) for row in successful)
            chosen=representative([row for row in successful if sum(max(g,0) for g in row['G'])==violation])
        best_selection={f:next(a for a in ctx['pool']['fields'][f]['alternatives'] if a['sequence_ids']==sequence)
                        for f,sequence in chosen['crop_sequences'].items()}
        stage='completed' if front else 'no_feasible_solution'
        artifacts(output,ctx,best_selection,stage);save(output/'selected_result.json',chosen)
        save(output/'selected_management.json',{'search_scope':scope,'policies':chosen['policies']})
        shutil.copyfile(output/'candidates'/chosen['candidate_id']/'candidate.apsimx',output/'selected_future.apsimx')
        if engine=='aquacrop':
            selected_folder=output/'candidates'/chosen['candidate_id']
            save(output/'selected_aquacrop.json',{'candidate_id':chosen['candidate_id'],
                'model':str(selected_folder/'aquacrop_model'),'prepared_and_executed_inputs':str(selected_folder/'aquacrop'),
                'manifest':read(selected_folder/'aquacrop_manifest.json')})
        save(output/'optimization_summary.json',{'status':stage,'objective':objective,'objectives':objectives,
            'engine':engine,
            'criteria_registry':registry,
            'search_settings':settings,'management_preset':preset_name,'management_source':preset,
            'search_scope':scope,'decision_variables':len(search_options),'selected_management':chosen['policies'],
            'pareto_candidates':len(front),
            'display_selection_rule':'Minimum equal-weight squared distance to the ideal point, min-max normalized within feasible Pareto front; if infeasible, within minimum-total-violation candidates. Ties: candidate_id.',
            'evaluated':len(cache),'successful':len(successful),'feasible':sum(r['feasible'] for r in successful),
            'selected_candidate':chosen['candidate_id'],'weather':weather,
            'note':'Crop sequences and management optimized jointly; climate analogue, not a forecast.' if scope=='joint' else 'Crop-map search at fixed management; climate analogue, not a forecast.'})
        status(stage);report(output,ctx,objectives,stage,chosen)
        
    except BaseException as exc:
        status('failed',error=str(exc));raise
    return output


def main(argv=None,default_objective='yield'):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    parser.add_argument('--engine',choices=['apsim','aquacrop'],help='AquaCrop harvests use APSIM soil indicators on the same new crop map')
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--objective',choices=OBJECTIVES,help='Legacy single-criterion option')
    selection.add_argument('--objectives',nargs='+',help='Built-in/configured criteria evaluated jointly, in the supplied order')
    parser.add_argument('--algorithm',choices=['auto','nsga2','nsga3'],help='auto: NSGA-II for 1-3 criteria, NSGA-III for 4 or more')
    parser.add_argument('--management-preset',choices=OBJECTIVES,help='Fixed management source; defaults to the first selected criterion')
    parser.add_argument('--search-scope',choices=['crop_map','joint'],help='Change only crop sequences, or sequences and management jointly')
    parser.add_argument('--population',type=int)
    parser.add_argument('--generations',type=int)
    parser.add_argument('--reference-partitions',type=int,help='Das-Dennis reference partitions for NSGA-III')
    parser.add_argument('--all',action='store_true',help='Prepare all four scenarios without running searches')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--run',action='store_true',help='Run APSIM baseline and the full selected-objective search')
    mode.add_argument('--verify-model',action='store_true',help='Run just one prepared future model')
    mode.add_argument('--prepare-only',action='store_true',help='Prepare files without starting APSIM (default)')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    if args.all and (args.run or args.verify_model or args.output or args.objective or args.objectives):
        parser.error('--all prepares scenarios only and uses default separate output folders')
    print('Preparing legal crop sequences and loading calibrated management presets...',flush=True)
    ctx=context(args.config)
    if args.engine:ctx['config']['engine']=args.engine
    try:registry=criteria_registry(ctx['config'])
    except ValueError as exc:parser.error(str(exc))
    optimization=ctx['config'].setdefault('optimization',{})
    if args.search_scope:optimization['search_scope']=args.search_scope
    overrides=optimization.setdefault('settings',{})
    for key in ('population','generations','reference_partitions'):
        if getattr(args,key) is not None:overrides[key]=getattr(args,key)
    groups=[[name] for name in OBJECTIVES] if args.all else [args.objectives or ([args.objective] if args.objective else optimization.get('objectives',[default_objective]))]
    if ctx['config'].get('humic_constraint')=='objective_only':
        groups=[[*objective_list(group,registry),*(['humic'] if 'humic' not in objective_list(group,registry) else [])] for group in groups]
    # Validate every requested group before creating outputs or launching APSIM.
    for group in groups:
        try:
            names=objective_list(group,registry)
            settings=integer_search_settings({**ctx['config']['nsga2'],**overrides,
                'algorithm':args.algorithm or optimization.get('algorithm','auto')},len(names))
            if settings['algorithm']=='nsga2' and len(names)>3:
                raise ValueError('For more than three criteria choose --algorithm nsga3 or auto')
        except ValueError as exc:parser.error(str(exc))
    for group in groups:
        root=Path(ctx['config']['output_root'])
        if ctx['config'].get('engine')=='aquacrop':root=root/'aquacrop'
        output=args.output or root/('+'.join(group)+'_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        execute(ctx,group,output,args.run,args.verify_model,args.algorithm,args.management_preset)
    return 0


if __name__=='__main__':raise SystemExit(main())
