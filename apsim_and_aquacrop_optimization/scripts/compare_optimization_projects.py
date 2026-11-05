"""Read-only audit of the old release and current saved experiments; no simulators."""
from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / '.matplotlib'))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def walk(node):
    yield node
    for child in node.get('Children', []):
        yield from walk(child)


def csv_save(path, rows):
    if not rows:
        return
    columns = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def met(path):
    columns = None
    rows = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        parts = line.split()
        if parts[:2] == ['year', 'day']:
            columns = parts
        elif columns and len(parts) == len(columns) and parts[0].isdigit():
            row = dict(zip(columns, map(float, parts)))
            date = dt.date(int(row['year']), 1, 1) + dt.timedelta(days=int(row['day'])-1)
            rows[date.isoformat()] = row
    return rows


def close(actual, expected, label):
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-5):
        raise AssertionError(f'{label}: {actual} != {expected}')


def format_value(value):
    if value is None:
        return 'нет данных'
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, float):
        return f'{value:,.3f}'.replace(',', ' ').replace('.', ',')
    return str(value)


def build(old, out):
    out.mkdir(parents=True, exist_ok=True)
    source_files = []

    def source(path, purpose):
        path = path.resolve()
        row = {'project': 'старый' if path.is_relative_to(old) else 'новый',
               'file': str(path), 'purpose': purpose,
               'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
        if not any(r['file'] == str(path) for r in source_files):
            source_files.append(row)
        return path

    old_summary = read(source(old/'examples/reference_results/optimization_summary.json', 'Сводка одного запуска 16.10'))
    verification = read(source(old/'examples/reference_results/verification.json', 'Заявленная прежняя проверка; исходные БД не включены'))
    front = read(source(old/'examples/reference_results/validated_front.json', 'Пять решений объединённого фронта двух запусков'))
    native = ROOT/'runs/apsim/native_multiyear/optimize_20261029_123240_766585'
    new_summary = read(source(native/'optimization_summary.json', 'Новый исторический запуск'))
    new_config = read(source(native/'config_snapshot.json', 'Фактический конфиг нового исторического запуска'))
    baseline = read(source(native/'baseline_score.json', 'База нового исторического запуска'))
    evaluations = read(source(native/'evaluations.json', 'Все 40 новых исторических решений'))
    old_model_path = source(old/'simulation/calibrated/full_field2019_2023_calibrated_v3.apsimx', 'Старая модель до перестроения выбранной карты')
    new_model_path = source(ROOT/'models/apsim/simulation/calibrated_history/simulation.apsimx', 'Новая полная история')
    old_model, new_model = read(old_model_path), read(new_model_path)
    import yaml
    old_config = yaml.safe_load(source(old/'configs/multiyear_rotation_nsga2_2020_2023.yaml', 'Старый поиск: 3 цели, 10 переменных').read_text(encoding='utf-8-sig'))
    replay = yaml.safe_load(source(old/'configs/multiyear_rotation_replay_2019_2023.yaml', 'Старая карта, профили и расчётные площади').read_text(encoding='utf-8-sig'))
    old_area = sum(v['area_ha'] for v in replay['fields'].values())
    new_area = sum(n['Area'] for n in walk(new_model) if n.get('$type') == 'Models.Core.Zone, Models')
    assert old_area == 2 and new_area == 10
    years = len(old_summary['evaluation_years'])
    assert old_summary['evaluation_years'] == new_summary['evaluation_years'] == [2020, 2021, 2022, 2023]
    old_ha_years, new_ha_years = old_area*years, new_area*years
    new_ok = [r for r in evaluations if r['status'] == 'ok']
    old_oldrun = [r for r in front if r['source_run'] == 'optimize_20261016_165110_356871']
    assert len(front) == 5 and len(old_oldrun) == 4
    old_yield, old_profit = max(front, key=lambda r:r['yield_dm_kg']), max(front, key=lambda r:r['net_profit_proxy'])
    new_yield, new_profit = max(new_ok, key=lambda r:r['yield_dm_kg']), max(new_ok, key=lambda r:r['net_profit_proxy'])
    old_recent_yield = max(old_oldrun, key=lambda r:r['yield_dm_kg'])
    old_recent_profit = max(old_oldrun, key=lambda r:r['net_profit_proxy'])

    tables = {}
    historical = []
    def result_row(project, sample, role, row, area, n_years, feasible, source_id):
        return {'Проект':project,'Выборка':sample,'Вариант':role,'Источник и ID':source_id,
                'Площадь га':area,'Лет':n_years,'Урожай кг СВ всего':row['yield_dm_kg'],
                'Урожай т СВ на га в год':row['yield_dm_kg']/1000/area/n_years,
                'Прибыль условные руб всего':row['net_profit_proxy'],
                'Прибыль условные руб на га в год':row['net_profit_proxy']/area/n_years,
                'Допустим по Humic в том запуске':feasible}
    historical.append(result_row('Старый','Сводка 40 вариантов 16.10','База',old_summary['baseline'],old_area,years,None,'baseline'))
    historical.append(result_row('Новый','Все 40 вариантов 29.10','База',baseline,new_area,years,baseline['feasible'],'baseline'))
    for label, rows in [('4 доступных решения 16.10',old_oldrun),('5 решений из двух запусков',front)]:
        for role, r in [('Лидер урожая',max(rows,key=lambda r:r['yield_dm_kg'])),('Лидер прибыли',max(rows,key=lambda r:r['net_profit_proxy']))]:
            historical.append(result_row('Старый',label,role,r,old_area,years,all(g<=0 for g in r['G']),r['source_run']+'/'+r['candidate_id']))
    for role,r in [('Лидер урожая',new_yield),('Лидер прибыли',new_profit)]:
        historical.append(result_row('Новый','Все 40 вариантов 29.10',role,r,new_area,years,r['feasible'],r['candidate_id']))
    tables['historical_comparison'] = historical
    deltas = []
    for label,o,n in [('База урожай т/га/год',old_summary['baseline']['yield_dm_kg']/old_ha_years/1000,baseline['yield_dm_kg']/new_ha_years/1000),
                      ('Лидер урожая среди 4 доступных 16.10 и 40 новых',old_recent_yield['yield_dm_kg']/old_ha_years/1000,new_yield['yield_dm_kg']/new_ha_years/1000),
                      ('Лидер урожая объединённых 5 старых и 40 новых',old_yield['yield_dm_kg']/old_ha_years/1000,new_yield['yield_dm_kg']/new_ha_years/1000),
                      ('Лидер прибыли среди 4 доступных 16.10 и 40 новых',old_recent_profit['net_profit_proxy']/old_ha_years,new_profit['net_profit_proxy']/new_ha_years),
                      ('Лидер прибыли объединённых 5 старых и 40 новых',old_profit['net_profit_proxy']/old_ha_years,new_profit['net_profit_proxy']/new_ha_years)]:
        deltas.append({'Сопоставление':label,'Старый':o,'Новый':n,'Разница новый минус старый':n-o,'Изменение процентов':100*(n-o)/abs(o)})
    tables['normalized_differences'] = deltas

    candidates = []
    policies = []
    humic = []
    for project, rows, area in [('Старый',front,old_area),('Новый',new_ok,new_area)]:
        for r in rows:
            sid = r.get('source_run','optimize_20261029_123240_766585')+'/'+r['candidate_id']
            candidates.append(result_row(project,'5 сохранённых фронт' if project=='Старый' else '40 всех оценок','Кандидат',r,area,years,all(g<=0 for g in r['G']),sid))
            for crop,p in r['policies'].items():
                policies.append({'Проект':project,'ID':sid,'Культура':crop,'Сорт':p.get('cultivar'),'Густота шт на м2':p['population'],
                                 'Глубина мм':p['depth'],'Междурядье мм':p['row_spacing'],'Форма N':p['fertilizer']['apsim_type'],
                                 'Доза кг N на га':p['fertilizer']['dose_kg_n_ha']})
            stocks = r.get('humic')
            if stocks is None:
                stocks = {c['field']:{'start_kg_ha':c['start_kg_ha'],'end_kg_ha':c['end_kg_ha']}
                          for c in r['fertility_constraints']['humic_carbon']['checks']}
            for field,s in stocks.items():
                humic.append({'Проект':project,'ID':sid,'Поле':field,'Начало кг C на га':s['start_kg_ha'],
                              'Конец кг C на га':s['end_kg_ha'],'Изменение кг C на га':s['end_kg_ha']-s['start_kg_ha'],
                              'Изменение процентов':100*(s['end_kg_ha']-s['start_kg_ha'])/s['start_kg_ha']})
            close(r['F'][0],-r['yield_dm_kg'],sid+' F0')
            close(r['F'][1],-r['net_profit_proxy'],sid+' F1')
    tables['candidate_results'],tables['management_policies'],tables['humic_fields'] = candidates,policies,humic
    old_keys = {json.dumps(r['policies'],sort_keys=True) for r in front}
    new_keys = {json.dumps(r['policies'],sort_keys=True) for r in new_ok}
    common_policies = len(old_keys & new_keys)

    # Check all 40 native candidates from their raw SQLite harvests and actual N logs.
    audit_rows=[]
    prices={v['apsim_type']:v['price_per_kg_n'] for v in new_config['fertilizer']['forms'].values()}
    source_zones={n['Name']:n for n in walk(new_model) if n.get('$type')=='Models.Core.Zone, Models'}
    def params(zone,name):
        return {p['Key']:p['Value'] for n in zone.get('Children',[]) if n.get('Name')==name for p in n.get('Parameters',[])}
    field_details=[]
    for r in new_ok:
        work=native/'candidates'/r['candidate_id']
        dbpath=source(work/'candidate.db','Исходная база нового исторического кандидата')
        audit=read(work/'audit.json')
        model=read(work/'candidate.apsimx')
        zones={n['Name']:n for n in walk(model) if n.get('$type')=='Models.Core.Zone, Models'}
        raw_events=[]
        with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as con:
            errors=con.execute('SELECT count(*) FROM _Messages WHERE MessageType=0').fetchone()[0]
            warnings=con.execute('SELECT count(*) FROM _Messages WHERE MessageType=1').fetchone()[0]
            for date,component,message in con.execute("SELECT Date,ComponentName,Message FROM _Messages WHERE Message LIKE 'Applied %'"):
                m=re.fullmatch(r'Applied ([0-9.eE+-]+) kg/ha of (\S+) on .+',message)
                if not m: raise ValueError('Unrecognized fertilizer log')
                year=int(date[:4]);field=component.split('.')[0]
                cultivars=[v.strip() for v in params(zones[field],'RotationManager')['RotationCultivars'].split(',')]
                ix=year+1-2004
                if int(date[5:7])>=7 and ix<len(cultivars) and 'Winter_rye' in cultivars[ix]:year+=1
                if year in [2020,2021,2022,2023]:raw_events.append((field,year,float(m[1]),m[2]))
            cost=sum(d*prices[f]*zones[field]['Area'] for field,y,d,f in raw_events)
            application=sum(480*zones[field]['Area'] for field,y,d,f in raw_events)
            fixed=new_area*years*23000
            revenue=production=0.;zero=0
            for field,zone in zones.items():
                for year,crop,mass in con.execute(f'SELECT Year,Crop,YieldDMTHa FROM HarvestReport_{field} WHERE Year BETWEEN 2020 AND 2023'):
                    production+=mass*1000*zone['Area']
                    revenue+=mass*1000*zone['Area']*new_config['economics']['native_harvest_price_per_kg_dm'].get(crop,0)
                    zero+=int(mass==0)
                for date,key in [('2019-12-31','start_kg_ha'),('2023-12-31','end_kg_ha')]:
                    value=con.execute(f'SELECT HumicCKgHa FROM OptSoil_{field} WHERE substr(Date,1,10)=?',(date,)).fetchone()[0]
                    close(value,r['humic'][field][key],r['candidate_id']+' Humic '+field)
            close(production,r['yield_dm_kg'],r['candidate_id']+' raw harvest')
            close(revenue-cost-application-fixed,r['net_profit_proxy'],r['candidate_id']+' raw economics')
        assert errors==0
        audit_rows.append({'ID':r['candidate_id'],'Ошибок APSIM':errors,'Предупреждений APSIM':warnings,
                           'Полевых лет':len(r['fields']),'Нулевых урожаев':zero,'Урожай кг СВ':production,
                           'Выручка условные руб':revenue,'Удобрения условные руб':cost,'Внесение условные руб':application,
                           'Постоянные затраты условные руб':fixed,'Затраты всего условные руб':cost+application+fixed,
                           'Прибыль условные руб':revenue-cost-application-fixed,'Сверка БД':'пройдена'})
        for row in r['fields']:field_details.append({'ID':r['candidate_id'],**row})
    tables['new_raw_audit'],tables['new_field_years']=audit_rows,field_details

    old_weather=source(old/'data/weather/weather_2019_2023_units_corrected.met','Старая погода с исправленным давлением')
    new_weather=source(ROOT/'models/apsim/simulation/calibrated_history/weather.met','Новая погода, давление имеет отличающийся масштаб')
    om,nm=met(old_weather),met(new_weather)
    dates=sorted(set(om)&set(nm)); weather=[]
    for col in ['radn','maxt','mint','rain','wind_speed','sp']:
        diffs=[nm[d][col]-om[d][col] for d in dates]
        ratios=[om[d][col]/nm[d][col] for d in dates if nm[d][col]]
        weather.append({'Столбец':col,'Общих дней':len(dates),'Дней с различием':sum(abs(x)>1e-8 for x in diffs),
                        'Среднее старый':statistics.mean(om[d][col] for d in dates),
                        'Среднее новый':statistics.mean(nm[d][col] for d in dates),
                        'Средняя абсолютная разница':statistics.mean(abs(v) for v in diffs),
                        'Макс абсолютная разница':max(abs(v) for v in diffs),
                        'Медиана старый делить на новый':statistics.median(ratios) if ratios else None})
    tables['weather_comparison']=weather
    oldmap=read(source(old/'data/rotation/selected_rotation_2019_2023.json','Выбранная старая карта, не историческая карта новой модели'))
    crops=[]
    for field,sequence in oldmap['fields'].items():
        rp=params(source_zones[field],'RotationManager')
        for year in [2020,2021,2022,2023]:
            oldstate=sequence[year-2019]
            oldfamily='Травы' if oldstate.startswith('perennial') else oldstate
            ix=year-2004;crop=rp['RotationCrops'].split(',')[ix].strip();cult=rp['RotationCultivars'].split(',')[ix].strip()
            newfamily=('oats_with_underseed' if 'Oats' in crop else 'winter_rye' if 'Winter_rye' in cult else
                       'spring_wheat' if crop=='Wheat' else 'Травы' if 'RedClover' in crop else crop)
            crops.append({'Поле':field,'Год':year,'Старое состояние':oldstate,'Новая культура':crop,
                          'Новый сорт':cult,'Старая группа':oldfamily,'Новая группа':newfamily,'Совпадает группа':oldfamily==newfamily})
    tables['crop_map_comparison']=crops
    map_equal=sum(r['Совпадает группа'] for r in crops)
    ast_equal=[]
    def functions(path):
        return {n.name:ast.dump(n,include_attributes=False) for n in ast.parse(path.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}
    op=source(old/'src/multiyear_rotation_optimizer.py','Старые функции решений и целей')
    np=source(ROOT/'agro/apsim/multiyear_rotation_optimizer.py','Перенесённые функции решений')
    of,nf=functions(op),functions(np)
    for name in ['fertilizer_schemes','variable_spec','decode_vector']:
        ast_equal.append({'Функция':name,'AST идентичен':of[name]==nf[name]})
    tables['shared_functions']=ast_equal

    # Quality reports have different populations; no improvement percentage is computed.
    readiness=read(source(old/'results/final_20261002/decision_readiness.json','Старая отдельная отложенная проверка control 2023'))['selected']
    fit=read(source(ROOT/'for_me/apsim_new_calibr/metrics.json','Новая сводка соответствия наблюдениям 2004–2023'))['fit']
    tables['quality']=[{'Проект':'Старый','Выборка':'Отложенный 2023, control; отдельный исторический опыт',
                        'n':readiness['validation_model_metrics']['n'],'MAE т на га':readiness['validation_model_metrics']['mae_t_ha'],
                        'RMSE т на га':readiness['validation_model_metrics']['rmse_t_ha'],'R2':readiness['validation_model_metrics']['r2_1to1'],
                        'Независимая проверка':'2023 удержан отдельно в сохранённом отчёте'},
                       {'Проект':'Новый','Выборка':'2004–2023, разные культуры, включённые наблюдения после калибровки',
                        'n':fit['n'],'MAE т на га':fit['mae_t_ha'],'RMSE т на га':fit['rmse_t_ha'],'R2':fit['r2'],
                        'Независимая проверка':'Эта сводка не доказывает качество на отложенном годе'}]

    night=ROOT/'runs/night_optimization/batch_20261102_154755_922426'
    night_rows=[]; same_maps=[]
    for job in sorted((night/'outputs').iterdir()):
        work=job/'attempt_001'
        s=read(source(work/'optimization_summary.json','Фактические результаты ночного запуска до совместного поиска'))
        r=read(source(work/'selected_result.json','Выбранный вариант ночного запуска'))
        ev=read(work/'evaluations.json')
        ni=read(work/'scenario_inputs.json') if (work/'scenario_inputs.json').exists() else {}
        night_rows.append({'Запуск':job.name,'Алгоритм':s.get('search_settings',{}).get('algorithm'),
                           'Критерии':', '.join(s.get('objectives',[])),'Оценено':s.get('evaluated',len(ev)),
                           'Допустимых':s.get('feasible'),'Выбран':s.get('selected_candidate'),
                           'Урожай т СВ на га в год':r['yield_dm_kg']/50000,
                           'Прибыль условные руб на га в год':r['net_profit_proxy']/50,
                           'Правило Humic':'нет в чистом AquaCrop' if 'standalone' in job.name else 'строгое в этом сохранённом запуске',
                           'Область поиска':'только карта; технология фиксирована'})
        same_maps.append((job.name,[r.get('sequences',r.get('choices',r.get('crop_plan',r.get('vector')))) for r in ev]))
    tables['night_results']=night_rows
    future_rows=[];future_candidates=[];future_valid=[];future_selected_audit=[]
    future_paths=sorted((ROOT/'runs/future_rotation').glob('*/optimization_summary.json'))
    joint_summary=None
    for sp in future_paths:
        s=read(sp)
        if s.get('status') not in ('completed','no_feasible_solution'):continue
        work=sp.parent
        source(sp,'Завершённый будущий APSIM после ночной серии')
        ev=read(source(work/'evaluations.json','Все будущие кандидаты, включая совместный поиск'))
        r=read(source(work/'selected_result.json','Фактический выбранный будущий результат'))
        scope=s.get('search_scope','crop_map')
        policy=r.get('humic_policy','each_field_end_gte_start')
        future_rows.append({'Запуск':work.name,'Алгоритм':s['search_settings']['algorithm'],
                            'Область':scope,'Переменных':s.get('decision_variables',10),
                            'Критерии':', '.join(s['objectives']),'Оценено':s['evaluated'],
                            'Допустимых':s['feasible'],'Парето':s['pareto_candidates'],
                            'Выбран':s['selected_candidate'],'Humic':policy,
                            'Урожай выбранного т СВ на га в год':r['yield_dm_kg']/50000,
                            'Прибыль выбранного условные руб на га в год':r['net_profit_proxy']/50})
        for row in ev:
            if row['status']!='ok':continue
            assert row['feasible']==all(g<=0 for g in row['G'])
            record={'Запуск':work.name,'Область':scope,'ID':row['candidate_id'],
                    'Допустим по составу посевов':row['feasible'],
                    'Урожай т СВ на га в год':row['yield_dm_kg']/50000,
                    'Прибыль условные руб на га в год':row['net_profit_proxy']/50,
                    'Потерявших Humic полей':sum(v['end_kg_ha']<v['start_kg_ha'] for v in row['humic'].values()),
                    'Потери Humic худшего поля процентов':100*max((v['start_kg_ha']-v['end_kg_ha'])/v['start_kg_ha'] for v in row['humic'].values())}
            future_candidates.append(record)
            if row['feasible']:future_valid.append(record)
        assert sum(v['status']=='ok' and v['feasible'] for v in ev)==s['feasible']
        if scope=='joint':
            joint_summary=s
            joint_path=work
        selected=work/'candidates'/s['selected_candidate']
        source(selected/'candidate.db','БД выбранного будущего APSIM: независимая сверка')
        model=read(selected/'candidate.apsimx')
        zones={n['Name']:n for n in walk(model) if n.get('$type')=='Models.Core.Zone, Models'}
        audit=read(selected/'audit.json')
        raw_mass=revenue=0.
        with sqlite3.connect((selected/'candidate.db').as_uri()+'?mode=ro',uri=True) as con:
            errors=con.execute('SELECT count(*) FROM _Messages WHERE MessageType=0').fetchone()[0]
            for field,z in zones.items():
                for crop,mass in con.execute(f'SELECT Crop,YieldDMTHa FROM HarvestReport_{field} WHERE Year BETWEEN 2025 AND 2029'):
                    raw_mass+=mass*1000*z['Area']
                    revenue+=mass*1000*z['Area']*new_config['economics']['native_harvest_price_per_kg_dm'].get(crop,0)
                for date,key in [('2024-12-31','start_kg_ha'),('2029-12-31','end_kg_ha')]:
                    value=con.execute(f'SELECT HumicCKgHa FROM OptSoil_{field} WHERE substr(Date,1,10)=?',(date,)).fetchone()[0]
                    close(value,r['humic'][field][key],'future '+field+' '+key)
        events=[v for v in audit['applications'] if v['year'] in [2025,2026,2027,2028,2029]]
        costs=sum(e['dose_kg_n_ha']*prices[e['form']]*zones[e['field']]['Area']+480*zones[e['field']]['Area'] for e in events)+50*23000
        close(raw_mass,r['yield_dm_kg'],'future raw harvest')
        close(revenue-costs,r['net_profit_proxy'],'future selected profit')
        assert errors==0
        future_selected_audit.append({'Запуск':work.name,'ID':s['selected_candidate'],'Ошибок APSIM':errors,
                                     'Урожай кг СВ':raw_mass,'Выручка условные руб':revenue,
                                     'Затраты условные руб':costs,'Прибыль условные руб':revenue-costs,
                                     'Сверка БД':'пройдена; внесения взяты из audit.json'})
    assert joint_summary is not None
    tables['latest_future_runs']=future_rows
    tables['latest_future_candidates']=future_candidates
    tables['latest_future_feasible']=future_valid
    tables['latest_future_selected_audit']=future_selected_audit
    current=read(source(ROOT/'models/apsim/configs/future_rotation_2025_2029.json','Текущие будущие настройки; не подмена старых результатов'))
    source(ROOT/'agro/planning/future_scenarios.py','Выбор целей, правило Humic, совместный поиск и выбор представителя')
    source(ROOT/'agro/planning/joint_management.py','Декодирование совместной технологии')
    source(ROOT/'agro/apsim/native_rotation_optimizer.py','Реальный новый исторический расчёт целей и чтение БД')
    source(ROOT/'agro/adapters/rotation_management.py','Патч RotationManager и реестр фактических внесений')
    source(ROOT/'agro/optimization/nsga2.py','Общий настоящий NSGA-II/III')
    source(old/'scripts/run_multiyear_rotation_optimizer.py','Старый настоящий NSGA-II и этапы подготовки')
    source(old/'src/multifield_results.py','Старый читатель событий и переопределение расчётной площади')
    source(old/'src/rotation_planner.py','Старое построение карты без оценки урожая')
    source(ROOT/'agro/planning/aquacrop_future.py','Односторонний обмен APSIM → AquaCrop')
    source(ROOT/'agro/aquacrop/standalone.py','Самостоятельный AquaCrop и его цели')

    io=[
        {'Этап':'Старый основной NSGA-II','Вход':'YAML поиска + YAML replay; v3 .apsimx; выбранная JSON-карта 2019–2023; погода 2018–2023; площади по 0,2 га; цены и нормы N',
         'Решение алгоритма':'10 индексов: пшеница 5, овёс 3, рожь 2. Единая технология на культуру, календарь и карта фиксированы',
         'Расчёт':'patch_selected_rotation → patch_candidate_model → prepare/run_apsim → read_results/read_humic_period → summarize_period → NSGA2',
         'Выход':'F=[−кг сухой массы, −условная прибыль, N-дефицит]; G=[минеральное ограничение в мягком режиме=0, агрегат потерь Humic]; допустимое Парето; БД и JSON, если полный запуск сохранён'},
        {'Этап':'Новый исторический APSIM, запуск 29.10','Вход':'Новая simulation.apsimx и weather.met 2004–2023; JSON политики/экономики; 10 зон по 1 га; исходная история сохранена',
         'Решение алгоритма':'Те же 10 технологических переменных; историческая карта и даты фиксированы',
         'Расчёт':'prepare/apply_policies → run_apsim → read_result/read_applications → score → search_integer(NSGA-II)',
         'Выход':'Те же 3 смысла F; G по каждому из 10 полей; фактические внесения, 40 полевых лет/вариант, audit.json, БД, фронт или no_feasible_solution'},
        {'Этап':'Новый будущий APSIM, текущий конфиг','Вход':'История 2004–2023 + состояние полей 2024 + матрица переходов + аналоги погоды 2024–2029 + технологический домен и стартовый пресет',
         'Решение алгоритма':'20 переменных: 10 последовательностей на 2025–2029 + 10 параметров технологий; единая технология для каждой культуры',
         'Расчёт':'context/choices → decode_policies → extend_model → simulate/score → evaluate_humic_policy → search_integer(NSGA-II/III)',
         'Выход':'Выбранные F (1–3 или 4+), ограничения состава посевов; Humic только цель; карта CSV/PNG, selected_management.json, selected_future.apsimx, Парето и отчёт'},
        {'Этап':'Новый AquaCrop + APSIM','Вход':'Тот же план и история APSIM; собственные эффективные профили культур AquaCrop; начальная вода из APSIM на 31.12.2023',
         'Решение алгоритма':'В текущем конфиге 20 совместных переменных; часть технологических эффектов не представлена в AquaCrop',
         'Расчёт':'APSIM даёт воду на старте, Humic, N и журнал удобрений; AquaCrop отдельно считает урожай; затем общая оценка кандидата',
         'Выход':'Урожай AquaCrop, почвенные показатели APSIM, условная экономика. Это односторонняя передача, обратной связи биомассы AquaCrop в почву APSIM нет'},
        {'Этап':'Новый чистый AquaCrop','Вход':'Собственные JSON полей, слоёв почвы, начальной воды, погоды, культур и цен; состояние 2024 и правила переходов',
         'Решение алгоритма':'10 последовательностей культур. Удобрения N и Humic не моделируются этим сценарием',
         'Расчёт':'planned_windows → генерация нативных входов → AquaCrop → собственная оценка урожая, экономики и воды → NSGA-II/III',
         'Выход':'Урожай, условная прибыль, ET, дренаж, сток, продуктивность воды; карта, Парето, JSON/CSV и водный баланс'},
        {'Этап':'Старый будущий планировщик','Вход':'YAML 2025–2029, состояние 2024, JSON матрица переходов, правила травостоя, исторические количества полей',
         'Решение алгоритма':'Допустимые последовательности культур и выбор по правилам/структуре',
         'Расчёт':'plan_fields_stateful → select_farm_plan → visualization',
         'Выход':'Карта посева CSV/PNG и summary. Урожайность, прибыль и Humic для этой будущей карты не вычислялись'},
    ];tables['inputs_outputs']=io
    logic=[
        {'Аспект':'Оптимизатор как алгоритм','Старый':'Настоящий NSGA-II: SBX 0,9 eta=15, PM eta=20, целые индексы, seed=42','Новый':'NSGA-II + настоящий NSGA-III, выбираемые цели; базовые операторы II те же','Оценка':'Больше возможностей; сам NSGA-II не стал доказанно лучше'},
        {'Аспект':'Домен технологий','Старый':'10 переменных, N 0–210 шаг 30, 4 формы; 29 уникальных схем N','Новый':'Исторический домен перенесён; 3 функции идентичны по AST. Будущий совместный поиск добавляет 10 карт','Оценка':'Совместный план ближе к задаче пользователя'},
        {'Аспект':'История почвы и травостоя','Старый':'Перестроенная выбранная карта, технический старт осенью 2018; калиброванная инициализация 2019','Новый':'Полная последовательность 2004–2023, затем мост 2024 и будущие годы','Оценка':'Лучше сохраняется внутренняя непрерывность, но длинная история сама по себе не доказывает точность'},
        {'Аспект':'Урожай и события','Старый':'Строгий читатель событий по компонентам; даты и полнота дневных данных проверяются','Новый':'Сохранены нативные HarvestReport, проверяются год/дата и полные ряды','Оценка':'Оба подхода проверяемы; фактическая масса зависит от разных культур/менеджеров'},
        {'Аспект':'Экономика удобрений','Старый':'Стоимость из заданной схемы на полевой год; в основном поиске audit_applications=False','Новый':'Стоимость из фактических сообщений о внесении, озимая доза отнесена к году уборки','Оценка':'Новый расчёт лучше связан с выполненной симуляцией'},
        {'Аспект':'Площади','Старый':'Зоны исходной модели по 1 га, расчётные площади переопределены YAML на 0,2 га','Новый':'Расчёт берёт площадь зоны 1 га напрямую','Оценка':'Меньше скрытых масштабов; различие площади обязательно нормировать'},
        {'Аспект':'Humic','Старый':'Жёсткое end ≥ start на каждом поле; N отдельно мягкая цель','Новый':'Исторический и ночной сохранённые запуски тоже строгие; текущий будущий — Humic как цель','Оценка':'Смена предпочтения, а не исправление арифметики. Допустимость нового режима не означает сохранение C'},
        {'Аспект':'Погода: единицы давления','Старый':'sp исправлен из hPa в объявленные Pa','Новый':'Объявлены Pa, численно старый sp / новый sp ≈100 на общих датах','Оценка':'У старого вход подготовлен последовательнее. Использование sp движком и влияние нужно проверить отдельно'},
        {'Аспект':'Проверка точности','Старый':'Есть отложенный 2023 и сравнение с простым средним; проверка не пройдена','Новый':'Есть воспроизведение экспорта и сводка ошибок; показанная сводка не независимый holdout','Оценка':'Старый сильнее формализует допуск к решениям; превосходство нового по точности не доказано'},
        {'Аспект':'Карта и симуляция будущего','Старый':'Будущая карта по правилам, без расчёта будущих урожаев','Новый':'Карта передаётся в симуляцию и оптимизируется вместе с технологиями в текущем joint','Оценка':'Существенное функциональное улучшение'},
        {'Аспект':'Сходимость и ограничения','Старый':'40 оценок, три фиксированные цели, одна карта','Новый':'По умолчанию те же 40 оценок, но 20 переменных и строгие ограничения состава посевов','Оценка':'Новый домен сложнее; существующий бюджет не подтверждает сходимость'},
        {'Аспект':'Связь AquaCrop и APSIM','Старый':'Только APSIM','Новый':'Дополнительные движки, но N не даёт полноценного отклика урожая AquaCrop и нет обратного обмена C','Оценка':'Это дополнительная модельная гипотеза; её прибыль нельзя объявлять равноценной APSIM'},
        {'Аспект':'Воспроизводимость результата','Старый':'В поставке сводки 5 решений, проверочный манифест; 41 БД заявлена проверенной ранее, не включена','Новый':'Доступны БД, исходники кандидатов, конфиги и логи; есть очередь с замороженными настройками','Оценка':'Новые сохранённые результаты сейчас проверяются глубже'},
    ];tables['logic_review']=logic

    diagnostics={
        'date':'2026-11-05','old_root':str(old),'new_root':str(ROOT),'simulations_launched':False,
        'old_area_for_aggregation_ha':old_area,'old_model_zone_total_ha':sum(n['Area'] for n in walk(old_model) if n.get('$type')=='Models.Core.Zone, Models'),
        'new_area_ha':new_area,'common_management_policies_in_available_samples':common_policies,
        'crop_family_same_field_years':map_equal,'crop_family_compared_field_years':len(crops),
        'native_raw_candidates_verified':len(audit_rows),'native_raw_checks':'F mass, revenue/cost/profit, per-field Humic endpoints, APSIM errors',
        'old_original_databases_present':False,'old_all_40_candidate_records_present':False,
        'new_current_search_scope':current['optimization']['search_scope'],'new_current_humic_policy':current['humic_constraint'],
        'old_claimed_verified_databases':verification['raw_databases_verified'],
        'old_run_feasible':old_summary['feasible_candidates'],'old_run_pareto':old_summary['pareto_solutions'],
        'new_native_feasible':sum(r['feasible'] for r in new_ok),
        'raw_audit_warnings_total':sum(r['Предупреждений APSIM'] for r in audit_rows),
        'raw_audit_zero_harvests':sum(r['Нулевых урожаев'] for r in audit_rows),
        'latest_joint_run':str(joint_path),'latest_joint_evaluated':joint_summary['evaluated'],
        'latest_joint_feasible':joint_summary['feasible'],'latest_joint_pareto':joint_summary['pareto_candidates'],
        'additional_future_selected_databases_verified':len(future_selected_audit),
        'historical_differences':deltas,
    }
    # Document exact source locations for code findings without modifying either project.
    anchors=[(old/'src/multifield_results.py','area = config'),(old/'src/multiyear_rotation_optimizer.py','def summarize_period'),
             (old/'scripts/run_multiyear_rotation_optimizer.py','audit_applications=False'),
             (ROOT/'agro/apsim/native_rotation_optimizer.py','def score'),(ROOT/'agro/planning/future_scenarios.py','def evaluate_humic_policy'),
             (ROOT/'agro/planning/future_scenarios.py',"search_scope=config"),(ROOT/'agro/optimization/nsga2.py','def search_integer')]
    lines=[]
    for path,needle in anchors:
        for i,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(),1):
            if needle in line:lines.append({'Файл':str(path),'Строка':i,'Фрагмент':line.strip()});break
    tables['source_lines']=lines
    for name,rows in tables.items():csv_save(out/(name+'.csv'),rows)
    csv_save(out/'sources.csv',source_files)
    (out/'audit.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2),encoding='utf-8')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    colors=['#64748b','#187f9c']
    figures=[]
    def finish(fig,name,caption):
        fig.tight_layout();fig.savefig(out/name,dpi=150,bbox_inches='tight');plt.close(fig);figures.append((name,caption))
    fig,axs=plt.subplots(1,2,figsize=(12,4.8))
    axs[0].bar(['Старый','Новый'],[old_summary['baseline']['yield_dm_kg']/old_ha_years/1000,baseline['yield_dm_kg']/new_ha_years/1000],color=colors)
    axs[0].set(title='Базовый расчёт',ylabel='т сухой массы / га / год')
    axs[1].bar(['Старые 5 решений','Новые 40 вариантов'],[old_yield['yield_dm_kg']/old_ha_years/1000,new_yield['yield_dm_kg']/new_ha_years/1000],color=colors)
    axs[1].set(title='Наибольший доступный урожай',ylabel='т сухой массы / га / год')
    for ax in axs:
        for p in ax.patches:ax.annotate(f'{p.get_height():.3f}',(p.get_x()+p.get_width()/2,p.get_height()),ha='center',xytext=(0,5),textcoords='offset points')
        ax.margins(y=.16)
    finish(fig,'01_normalized_yield.png','2020–2023. Площади нормированы. Модели, карты и политики различны; это не оценка качества оптимизатора.')
    fig,ax=plt.subplots(figsize=(10,5))
    vals=[old_summary['baseline']['net_profit_proxy']/old_ha_years,baseline['net_profit_proxy']/new_ha_years,old_profit['net_profit_proxy']/old_ha_years,new_profit['net_profit_proxy']/new_ha_years]
    ax.bar(['Старая база','Новая база','Старый лидер\nиз 5 решений','Новый лидер\nиз 40 вариантов'],vals,color=[colors[0],colors[1],colors[0],colors[1]])
    ax.axhline(0,color='#334155',lw=.8);ax.set(ylabel='Условные рубли / га / год',title='Расчётная прибыль за 2020–2023')
    for i,v in enumerate(vals):ax.annotate(f'{v:,.0f}'.replace(',',' '),(i,v),ha='center',xytext=(0,5 if v>=0 else -16),textcoords='offset points')
    ax.margins(y=.22)
    finish(fig,'02_normalized_profit.png','Старый лидер допустим по старому Humic, новый лидер нарушает прежнее ограничение. Цены диагностические, не подтверждённая прибыль хозяйства.')
    fig,ax=plt.subplots(figsize=(10,5.4))
    ax.scatter([r['yield_dm_kg']/new_ha_years/1000 for r in new_ok],[r['net_profit_proxy']/new_ha_years for r in new_ok],label='Новые 40: все недопустимы по Humic',color=colors[1],s=42,alpha=.8)
    ax.scatter([r['yield_dm_kg']/old_ha_years/1000 for r in front],[r['net_profit_proxy']/old_ha_years for r in front],label='Старые 5: сохранённый допустимый фронт',color=colors[0],marker='D',s=65)
    ax.set(xlabel='т сухой массы / га / год',ylabel='Условные рубли / га / год',title='Доступные варианты: разные задачи и разные выборки');ax.legend(fontsize=9)
    finish(fig,'03_candidate_cloud.png','Старые точки — отобранный фронт двух запусков, новые — все 40 вариантов одного запуска. Средние этих выборок сравнивать некорректно.')
    fig,ax=plt.subplots(figsize=(10,5))
    for project, sid,label,color,offset in [('Старый',old_yield['source_run']+'/'+old_yield['candidate_id'],'Старый лидер урожая',colors[0],-.18),('Новый','optimize_20261029_123240_766585/'+new_yield['candidate_id'],'Новый лидер урожая',colors[1],.18)]:
        records=sorted([r for r in humic if r['Проект']==project and r['ID']==sid],key=lambda r:int(r['Поле'][5:]))
        ax.bar([i+offset for i in range(10)],[r['Изменение процентов'] for r in records],width=.36,label=label,color=color)
    ax.set_xticks(range(10),[str(i) for i in range(1,11)]);ax.axhline(0,color='#334155',lw=.8)
    ax.set(xlabel='Поле',ylabel='Изменение Humic за 2020–2023, %',title='Запас модельного углерода: конец относительно своего начала');ax.legend()
    finish(fig,'04_humic.png','Начальные состояния и карты различаются. Humic — модельный пул углерода, не лабораторный процент гумуса.')
    fig,axs=plt.subplots(1,2,figsize=(12,4.8))
    labels=['APSIM','AquaCrop + APSIM','AquaCrop отдельно'];nr=[night_rows[i] for i in [0,2,4]]
    axs[0].bar(labels,[r['Урожай т СВ на га в год'] for r in nr],color=['#187f9c','#b97a28','#687c56'])
    axs[0].set(ylabel='т сухой массы / га / год',title='Выбранный урожай 2025–2029')
    axs[1].bar(labels,[r['Прибыль условные руб на га в год'] for r in nr],color=['#187f9c','#b97a28','#687c56'])
    axs[1].axhline(0,color='#334155',lw=.8);axs[1].set(ylabel='Условные рубли / га / год',title='Выбранная расчётная прибыль')
    for ax in axs:ax.tick_params(axis='x',labelsize=9)
    finish(fig,'05_night_models.png','Ночная серия 02.11: выбран candidate_001, фиксированная технология. У первых двух моделей нет допустимого решения. Различается состав затрат.')
    fig,axs=plt.subplots(1,2,figsize=(12,4.8))
    jr=[r for r in future_candidates if r['Область']=='joint']
    for feasible,color,label in [(False,'#a4acb5','Нарушают состав посевов'),(True,'#187f9c','Допустимы по составу посевов')]:
        rr=[r for r in jr if r['Допустим по составу посевов']==feasible]
        axs[0].scatter([r['Урожай т СВ на га в год'] for r in rr],[r['Прибыль условные руб на га в год'] for r in rr],c=color,label=label,alpha=.8,s=40)
    axs[0].set(xlabel='т сухой массы / га / год',ylabel='Условные рубли / га / год',title='Совместный поиск 03.11: 40 вариантов');axs[0].legend(fontsize=8)
    valid=[r for r in jr if r['Допустим по составу посевов']]
    axs[1].bar([r['ID'].replace('candidate_','') for r in valid],[r['Потери Humic худшего поля процентов'] for r in valid],color='#b97a28')
    axs[1].set(xlabel='Кандидат',ylabel='Потери Humic худшего поля, %',title='Допустимые варианты всё равно теряют C')
    finish(fig,'06_latest_joint.png','Совместный поиск: 3/40 допустимых, 2 Парето. Отмена запрета потерь Humic не гарантирует сохранение углерода; 37 карт не прошли ограничения состава посевов.')

    stats={'Среднее по 40 новым историческим вариантам':{
        'Урожай т СВ/га/год':statistics.mean(r['yield_dm_kg']/new_ha_years/1000 for r in new_ok),
        'Прибыль условные руб/га/год':statistics.mean(r['net_profit_proxy']/new_ha_years for r in new_ok),
        'Затраты условные руб/га/год':statistics.mean(r['Затраты всего условные руб']/new_ha_years for r in audit_rows)},
        'Старое среднее по всем 40':None}
    tables['sample_statistics']=[{'Показатель':k,'Значение':v,'Выборка':'40 новых исторических вариантов; все нарушают старый Humic'} for k,v in next(iter(stats.values())).items()]
    csv_save(out/'sample_statistics.csv',tables['sample_statistics'])
    # The report is a self-contained local page; no external services or CDN.
    sections=[];md=[]
    def prose(title,text):
        sections.append('<section><h2>'+html.escape(title)+'</h2>'+''.join('<p>'+html.escape(p)+'</p>' for p in text.split('\n'))+'</section>')
        md.append('## '+title+'\n\n'+text+'\n')
    def table(title,key,caption=''):
        rows=tables[key];columns=list(rows[0])
        content='<section><h2>'+html.escape(title)+'</h2>'
        if caption:content+='<p>'+html.escape(caption)+'</p>'
        content+='<a download href="'+key+'.csv">Скачать CSV</a><div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(k)+'</th>' for k in columns)+'</tr></thead><tbody>'
        content+=''.join('<tr>'+''.join('<td>'+html.escape(format_value(r.get(k)))+'</td>' for k in columns)+'</tr>' for r in rows)+'</tbody></table></div></section>'
        sections.append(content)
        md.append('## '+title+'\n\n'+caption+'\n\n| '+' | '.join(columns)+' |\n| '+' | '.join(['---']*len(columns))+' |\n'+''.join('| '+' | '.join(format_value(r.get(k)).replace('|','/') for k in columns)+' |\n' for r in rows)+'\n')
    def chart(index):
        name,caption=figures[index];sections.append('<figure><img src="'+name+'" alt="'+html.escape(caption)+'"><figcaption>'+html.escape(caption)+'</figcaption></figure>');md.append('!['+caption+']('+name+')\n')

    prose('Самое простое объяснение',
          'Старый проект отвечает: «Как настроить выращивание на одной заранее выбранной карте 2020–2023?» Новый добавляет вопрос: «Какую будущую карту выбрать и как выращивать культуры на ней?»\n'
          'Новый проект функционально шире и лучше хранит проверяемые расчёты. Однако доказательств, что он точнее предсказывает реальные урожаи или что его оптимизатор находит лучшие решения при одинаковых условиях, сейчас нет.\n'
          'Между сохранёнными историческими опытами одновременно изменились модель, история почвы, карта культур и расчётная площадь. Поэтому различие результатов нельзя приписать только NSGA или новой архитектуре.')
    prose('Что именно проверено 5 ноября 2026',
          f'Старый проект: {old}. Новый: {ROOT}. Все новые материалы находятся только в этой отдельной папке. Симуляторы и оптимизации при сравнении не запускались.\n'
          'В старом пакете есть сводка одного запуска (40 оценок, 35 допустимых, 6 Парето) и 5 сохранённых решений общего фронта из двух запусков. Это разные множества. Полного массива 40 старых вариантов и исходных SQLite здесь нет. verification.json сообщает о прежней проверке 41 БД, но повторить её без этих БД невозможно.\n'
          'Для нового исторического запуска прочитаны все 40 исходных БД: пересчитаны урожай, выручка, затраты по сообщениям о фактических внесениях и прибыль; сверены Humic на обе даты каждого поля. Все проверки сошлись. Новые исходные БД не редактировались.')
    prose('Свежий результат, который уже есть в проекте',
          'После ночной серии 02.11 появились завершённые опыты APSIM 03.11. Один — NSGA-III, четыре цели и только карта. Другой — NSGA-II, три цели и совместный поиск карты и технологий: 20 переменных. Поэтому ночная панель уже не описывает всю последнюю работу.\n'
          f'Совместный запуск {joint_path.name}: {joint_summary["evaluated"]} оценок, {joint_summary["feasible"]} допустимых, {joint_summary["pareto_candidates"]} Парето. Выбран candidate_001: 153,948 т сухой массы и +3 475,90 условных руб за пять лет на 10 га, то есть 3,079 т/га/год и +69,52 руб/га/год. Это тот же исход, который показывала старая ночная серия, но теперь он допустим по обновлённому правилу.\n'
          'Остальные 37 из 40 joint-вариантов нарушили структуру посевов. Два других допустимых варианта: candidate_015 — немного меньше урожая и прибыли, но меньше худшая потеря Humic; candidate_035 уступает по всем трём целям. Поэтому расширение поиска ещё не привело к улучшению выбранного результата. Оно дало второй вариант компромисса.\n'
          'Выбранные варианты обоих новых опытов 03.11 дополнительно сверены с исходными БД по урожаю и Humic; прибыль проверена с журналом внесений audit.json. Их результаты нельзя напрямую сравнивать со старым 2020–2023: годы, погода и постановка разные.')
    table('Последние завершённые будущие опыты 03.11','latest_future_runs')
    table('Допустимые будущие варианты 03.11','latest_future_feasible',
          'Допустимость здесь относится к структуре посевов. Числа потерь Humic остаются положительными даже у допустимых решений.')
    chart(5)
    table('Входы, решения и выходы', 'inputs_outputs')
    prose('Как честно сопоставить числа',
          'Для старого расчёта YAML задаёт по 0,2 га на поле, всего 2 га. Исходные зоны старой модели имеют по 1 га, но читатель явно заменяет площадь для агрегирования значением из конфига. Для нового расчёта используется фактическая площадь зон: 10 га. Разница масштабов — ровно 5 раз.\n'
          'Сравниваемый период исторических целей одинаковый: 2020–2023. Старые итоги делятся на 8 га·лет, новые — на 40 га·лет. Средний урожай ниже — это общая сухая масса всех учитываемых продуктов на гектар всей площади в год, а не урожайность отдельного зернового поля.\n'
          f'После приведения к группам культур старая выбранная карта совпадает с новой исторической только в {map_equal} из 40 полевых лет ({map_equal/40:.1%}). В {40-map_equal} полевых годах культуры различаются. Детализация возраста трав и сорта этим совпадением не подтверждается. Общих технологических политик среди доступных 5 старых и 40 новых решений: {common_policies}.')
    table('Исторические итоги 2020–2023', 'historical_comparison',
          '«Лидер» означает максимум только среди указанной доступной выборки. Новый лидер недопустим по строгому Humic в сохранённом опыте; старый допустимый фронт нельзя считать равным по составу новым 40 вариантам.')
    table('Насколько различаются нормированные результаты', 'normalized_differences',
          'Проценты считаются как (новый − старый) / |старый| × 100. Для прибыли надёжнее читать абсолютную разницу: смена знака не равна ухудшению точности прогноза.')
    chart(0);chart(1);chart(2)
    prose('Что означают эти различия',
          f'Новая база даёт на гектар в год на {abs(deltas[0]["Изменение процентов"]):.1f}% меньше сухой массы старой базы. Наибольший урожай среди 40 новых ниже на {abs(deltas[2]["Изменение процентов"]):.1f}% наибольшего среди пяти сохранённых старых решений. Это большая модельная разница, даже после устранения масштаба площади.\n'
          f'Старый сохранённый лидер прибыли: {old_profit["net_profit_proxy"]/old_ha_years:,.0f} условных руб/га/год. Новый лидер: {new_profit["net_profit_proxy"]/new_ha_years:,.0f}. В новых 40 исторических вариантах положительной прибыли нет. Состав культур, продажа только части продуктов и расходы меняются, поэтому разница не доказывает преимущество старого оптимизатора.\n'
          'Старый показатель N-дефицита и новый имеют близкий математический смысл: сумма нормированных недоборов конечного минерального N относительно собственной базы. Их базы различны. Сравнивать, например, старое значение около 2 с новым около 0,2 как «в 10 раз лучше почва» нельзя.\n'
          'Сравнение лучших доступных решений показывает диапазон исходов этих опытов. Контрольного опыта «одни и те же входы и политики, два программных контура» пока нет.')
    table('Средние новых исторических вариантов', 'sample_statistics',
          'Это средние по 40 вариантам поиска, не ожидаемые показатели хозяйства. Старое среднее по всем 40 недоступно. Среднее по пяти старым точкам Парето намеренно не подставлено вместо него.')
    chart(3)
    prose('Почему раньше было 35 допустимых, а теперь 0',
          'В старой сводке 35 из 40 вариантов сохраняли Humic на каждом поле. В новом историческом опыте 0 из 40 проходили то же по смыслу правило. Потери не компенсируются ростом на другом поле. Изменились начальные запасы, длительность предыстории, растительные остатки, календарь и карта. Поэтому допустимость одной модели не переносится на другую.\n'
          'Текущий будущий конфиг уже использует выбранное вами правило: Humic — цель, без запрета потерь. Это меняет определение допустимости. Такой вариант может быть допустимым по карте и всё равно терять углерод. Старые исторические и ночные результаты от этой правки не пересчитываются.')
    prose('Ошибки расчёта и качество поиска — разные вещи',
          f'Во всех 40 проверенных новых исторических БД — 0 ошибок APSIM. При этом суммарно записано {sum(r["Предупреждений APSIM"] for r in audit_rows)} предупреждений ({audit_rows[0]["Предупреждений APSIM"]} на вариант). Нулевых урожаев в 1 600 исторических полевых годах этой выборки — {sum(r["Нулевых урожаев"] for r in audit_rows)}. Это отдельная выборка от ночных будущих опытов, где нулевые урожаи встречаются.\n'
          'Технически успешный расчёт может нарушать ограничения или плохо соответствовать наблюдениям. В старом проекте сообщается о 0 сбоях на 40 вариантах, но исходные БД не включены, поэтому количество старых предупреждений здесь независимо не проверено.')
    table('Сравнение погоды на общих датах', 'weather_comparison',
          'Сопоставлены ежедневные записи 2019–2023 по дате. sp в обоих заголовках объявлен Pa. Масштаб нужно проверить до следующей научной интерпретации; файлы погоды в этом анализе не менялись.')
    prose('Проблема давления в новой погоде',
          'Старая погода содержит явную пометку исправления hPa → Pa. Новая сохраняет числа порядка 1000 при заголовке Pa; на общих датах старое давление примерно в 100 раз выше. Это несогласованность единиц входного файла, а не доказанная причина разницы урожаев.\n'
          'Данный аудит не установил, использует ли установленная версия APSIM дополнительный столбец sp в активной конфигурации. Без такой проверки нельзя утверждать, что исправление sp изменит урожай. Для повторного исследования нужно согласовать погоду, версию движка и повторить один контрольный вариант.')
    table('Логическая корректность по отдельным аспектам', 'logic_review')
    table('Сопоставление сохранённых ошибок относительно наблюдений', 'quality',
          'Выборки и протоколы различны. Старые числа относятся к control на отложенном 2023, не к пяти оптимизированным политикам. Новые относятся к 156 наблюдениям разных лет и культур. По этой таблице нельзя считать процент улучшения точности.')
    prose('Насколько новый проект действительно лучше',
          'Да: совместный выбор будущих культур и технологий, сохранение полной новой истории, расчёт затрат по фактическим внесениям, выбор целей, NSGA-III, отдельные движки и возобновляемая очередь. Это конкретные улучшения постановки и воспроизводимости.\n'
          'Не доказано: более точный прогноз, лучший оптимум, физическая достоверность всех культурных прокси и будущая прибыль. Базовые функции построения технологического домена и декодирования перенесены из старого проекта без изменения AST. Настоящий NSGA-II был уже в старом проекте.\n'
          'Старый проект сильнее в формализованной проверке на отложенном годе и в подготовке единиц давления. Его сохранённая проверка качества при этом не пройдена: модель уступает простому среднему и неверно ранжирует поля. Новый проект также не имеет подтверждения пригодности к производственным решениям: отрицательный R² и калибровочная сводка не дают такого подтверждения.\n'
          'Число «новый логически лучше на X%» научно не определено. Оно смешало бы возможности, качество входов, ошибки модели и качество поиска. Здесь эти свойства оценены отдельно.')
    table('Что реально посчитала ночная серия', 'night_results',
          'Это сохранённая серия 02.11 на 2025–2029. Это не новые joint-результаты и не повтор старой оптимизации 2020–2023. У NSGA-II и III выбран одинаковый кандидат внутри каждой модели.')
    chart(4)
    prose('Почему ночная серия не доказывает преимущество NSGA-II или III',
          'В сохранённой ночной серии технология фиксирована, поиск менял только карты. У APSIM и связки допустимых вариантов нет; у чистого AquaCrop — один. В отчёте показан candidate_001, поэтому это диагностический выбор при плохой допустимости, а не подтверждённый лучший производственный план.\n'
          'Кроме того, NSGA-II и III использовали разные наборы целей и одинаковый малый бюджет. Нельзя по одинаковому выбранному результату заключить, что реализации алгоритмов одинаковы или что одна лучше другой.\n'
          'В текущем будущем конфиге уже joint и Humic как цель. Завершённый отдельный joint-опыт 03.11 приведён выше; он не заменяет замороженную ночную серию. Сопоставимой пары joint NSGA-II и joint NSGA-III при одинаковых целях здесь нет. Возобновление старой замороженной очереди сохраняет старые условия.')
    prose('Что сделать для доказательного сравнения',
          '1. Выровнять входы: одна модель, один исполняемый APSIM, одна погода с согласованными единицами, одинаковая карта, стартовые состояния, площади, годы и цены.\n'
          '2. Подать одинаковую базу и несколько одинаковых политик в оба контура. Сверить урожай/поле/год, N, Humic на двух датах, фактические внесения и расходы. Сначала проверить эту эквивалентность, затем сравнивать поиск.\n'
          '3. NSGA-II и III сравнивать на одинаковых целях и числе уникальных симуляций, с несколькими seed. Считать допустимый фронт, покрытие целей и устойчивость; одинаковый seed сам по себе не обеспечивает честное сравнение разных постановок.\n'
          '4. Для joint обеспечить достаточное число допустимых карт: согласовать правила структуры посевов и генерацию/исправление карт. Бюджет 10×4 — демонстрационный для 20 переменных. Увеличение бюджета без решения проблемы допустимости будет дорогим.\n'
          '5. Для точности выбрать общую независимую выборку наблюдений: те же поля, годы, культуры и сухая масса. Раздельно оценить зерно и корм. Прокси-культуры, нулевой ячмень, отсутствие цены кормов и экономические предположения оставить видимыми.\n'
          '6. Связку AquaCrop + APSIM оценивать отдельно: сейчас её урожай и почвенный углерод получены разными движками без обратной связи. Чистый AquaCrop не выдаёт Humic и не заменяет азотный баланс APSIM.')
    table('Функции, перенесённые без изменения алгоритма', 'shared_functions')
    table('Где в коде находятся ключевые правила', 'source_lines')
    prose('Подробные данные и происхождение',
          'Все исходные пути, размеры и SHA-256 находятся в sources.csv. Детализация: candidate_results.csv (45 записей: 5 старых и 40 новых), management_policies.csv, humic_fields.csv, crop_map_comparison.csv, new_raw_audit.csv и new_field_years.csv. audit.json содержит результаты проверок.\n'
          'Эти CSV — выгрузки анализа. Исходные проекты, модели, конфиги и завершённые результаты не изменялись. Для просмотра отчёта запустите index.html или откройте index.html.')
    links=''.join('<li><a href="'+k+'.csv">'+html.escape(k)+'.csv</a></li>' for k in tables)
    sections.append('<section><h2>Все таблицы</h2><ul>'+links+'<li><a href="sources.csv">sources.csv — источники и SHA-256</a></li><li><a href="mini_report.txt">Короткий отчёт простыми словами</a></li><li><a href="REPORT.md">REPORT.md — текстовый отчёт</a></li><li><a href="audit.json">audit.json — проверки</a></li></ul></section>')
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Сравнение старого и нового оптимизаторов</title><style>
body{font:16px/1.6 system-ui,Segoe UI,sans-serif;margin:0;background:#f2f5f8;color:#172b3a}main{max-width:1380px;margin:auto;padding:30px}header,section,figure{background:white;border:1px solid #dce4ec;border-radius:12px;padding:24px;margin:20px 0}h1{font-size:32px;line-height:1.2}h2{font-size:23px;line-height:1.3}a{color:#126b88}p{max-width:1120px}.scroll{overflow:auto;margin-top:14px}table{border-collapse:collapse;font-size:14px;width:100%}th,td{border:1px solid #dce4ec;padding:10px;min-width:115px;text-align:left;vertical-align:top}th{background:#eaf2f7;position:sticky;top:0}tr:nth-child(even){background:#f8fafc}img{width:100%;max-width:1150px;height:auto}figcaption{color:#526777;font-size:14px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:15px}.card{padding:15px;background:#edf4f8;border-radius:8px}.card b{display:block;font-size:25px}@media print{main{padding:0}section,figure{break-inside:avoid}.scroll{overflow:visible}th,td{min-width:0;font-size:9px;padding:4px}}
</style><main><header><h1>Старый и новый проекты оптимизации</h1><p>Сравнение кода, входов и сохранённых результатов · 5 ноября 2026 · без новых симуляций</p><div class="cards">'''
    page+=f'<div class="card">Разница площади<b>×5</b>Нормирована в сравнении</div><div class="card">Новая база, урожай/га/год<b>{deltas[0]["Изменение процентов"]:.1f}%</b>Другие модель и карта</div><div class="card">Допустимых исторических<b>35/40 → 0/40</b>По старому строгому Humic</div><div class="card">Проверено новых исходных БД<b>40</b>Все сверки сошлись</div>'
    page+='</div></header>'+''.join(sections)+'</main></html>'
    (out/'index.html').write_text(page,encoding='utf-8')
    (out/'REPORT.md').write_text('# Сравнение старого и нового оптимизаторов\n\nДата: 05.11.2026.\n\n'+'\n'.join(md),encoding='utf-8')
    mini=(
        'СРАВНЕНИЕ ДВУХ ПРОЕКТОВ. 05.11.2026\n\n'
        '1. Старый проект настраивал технологию на выбранной карте 2020–2023. '
        'Новый также подбирает будущую карту и технологию вместе, поддерживает '
        'NSGA-II/III и отдельные сценарии APSIM, AquaCrop + APSIM и чистого AquaCrop.\n\n'
        '2. Общие тонны сравнивать нельзя: старая расчётная площадь 2 га, новая 10 га. '
        'После приведения к гектару в год старая база даёт 2,062 т сухой массы, '
        'новая 1,219 т (−40,8%). Лучший среди пяти сохранённых старых вариантов '
        'даёт 3,705 т/га/год, лучший среди 40 новых исторических — 2,649 т (−28,5%).\n\n'
        '3. Старая лучшая доступная прибыль +10 527 условных руб/га/год, новая '
        'историческая лучшая −3 386. Это разные опыты, а не доказательство того, '
        'что старый алгоритм лучше: карты совпадают по группе культур только '
        'в 9 из 40 полевых лет, различаются модели и начальные состояния.\n\n'
        '4. Старый запуск: 35 допустимых из 40 по строгому Humic. Новый исторический: '
        '0 из 40. Новый совместный будущий запуск 03.11: 3 из 40 и 2 Парето, '
        'но Humic уже не запрещает потери. Эти показатели допустимости имеют разные условия.\n\n'
        '5. В последнем совместном запуске выбран исходный candidate_001: '
        '153,948 т сухой массы и +3 475,90 условных руб за 2025–2029 на 10 га '
        '(3,079 т/га/год и +69,52 руб/га/год). Он совпадает с выбранным результатом '
        'старой ночной серии. Joint реально работал, но выбранный результат пока не улучшил.\n\n'
        '6. Новый проект лучше по возможностям и проверяемости, но не доказанно точнее. '
        '40 новых исторических БД проверены: цифры сошлись, ошибок APSIM нет. '
        'У старого исходные БД не включены. Старый имеет отдельную проверку '
        'на отложенном 2023 году, но она не пройдена. Новая калибровочная сводка '
        'не заменяет такую проверку.\n\n'
        '7. Найдена несогласованность новой погоды: sp объявлен в Pa, но значения '
        'в 100 раз меньше исправленных старых. Влияние на движок не установлено. '
        'Остальные ежедневные погодные ряды 2019–2023 совпадают полностью.\n\n'
        '8. Для честного вывода нужно посчитать одинаковые политики на одинаковой '
        'модели, карте, погоде, площадях и ценах в обоих контурах. Затем сравнить '
        'алгоритмы на одинаковых целях и бюджете с несколькими seed.\n\n'
        'Подробности, входы/выходы, функции, таблицы, 6 графиков и источники: index.html. '
        'Исходные проекты не изменены. Новых симуляций при анализе не запускали.\n'
    )
    (out/'mini_report.txt').write_text(mini,encoding='utf-8-sig')
    
    # A final hash pass proves the audit did not alter any registered input.
    for r in source_files:
        assert hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()==r['sha256'],r['file']
    assert len(tables['candidate_results'])==45 and len(tables['humic_fields'])==450
    assert len(tables['new_field_years'])==1600
    print(json.dumps({'output':str(out),'diagnostics':diagnostics,'weather':weather,'sample_statistics':stats},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-root',type=Path,default=ROOT.parent/'ApsimOptimization')
    parser.add_argument('--out',type=Path,default=ROOT/'runs/analysis/project_comparison')
    args=parser.parse_args()
    build(args.old_root.resolve(),args.out.resolve())
