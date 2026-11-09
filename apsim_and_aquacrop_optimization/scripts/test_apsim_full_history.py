"""Evaluate removal of forced perennial spring reset across all fields and windows."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import date
import hashlib
import html
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
from analyze_apsim_errors import fingerprint, html_table, md_table, metrics, number, read_json, valid, walk
from check_apsim_model_mechanisms import apply_archived_parameters, save

SPRING_BLOCK = [
    '            if(Persistent(i) && Match("01-Mar"))',
    '                foreach(Plant p in plants.Values.Where(p=>Perennial(p.Name) && p.IsAlive))',
    '                    if(Pheno(p).Stage>3) Pheno(p).SetToStage(3);',
]


def disable_reset(document):
    count = 0
    for node in walk(document):
        if node.get('Name') != 'RotationManager':
            continue
        code = '\n'.join(node['CodeArray'])
        original = '\n'.join(SPRING_BLOCK)
        if code.count(original) != 1:
            raise ValueError('Expected exactly one original spring-reset block per field')
        node['CodeArray'] = code.replace(original,
            '            // Experimental variant: perennial phenology continues without a forced March reset.').splitlines()
        count += 1
    if count != 10:
        raise ValueError('Expected all ten rotation managers')
    return document


def phase_results(connection, phase_observations, rotations):
    results = []
    for phase in phase_observations:
        row = rotations[(phase['field'], phase['year'])]
        year = phase['year']
        start = f'{year}-03-01'
        if row['winter'] and phase['phase'].startswith('tillering'):
            start = f'{year-1}-07-01'
        if not row['winter'] and row['use']=='grain':
            sow = row['sow_observed'] or f'{year}-05-10'
            start = max(start,sow)
        table = 'PhenologyReport_'+phase['field']
        variable = phase['variable']
        allowed = {'WheatStage','WheatZadok','OatsStage','OatsZadok','BarleyStage','BarleyZadok',
                   'RedCloverStage','PlantainForageStage','MustardStage','RapeStage'}
        if variable not in allowed:
            raise ValueError('Unknown phase variable')
        finish = row['harvest_date_actual']+' 23:59:59'
        predicted = connection.execute(
            f'SELECT MIN(Date) FROM {table} WHERE Date>=? AND Date<? AND "{variable}">=?',
            (start,finish,phase['threshold'])).fetchone()[0]
        initial = connection.execute(
            f'SELECT "{variable}" FROM {table} WHERE Date>=? AND Date<? ORDER BY Date LIMIT 1',
            (start,finish)).fetchone()
        when = predicted[:10] if predicted else None
        error = (date.fromisoformat(when)-date.fromisoformat(phase['observed_date'])).days if when else None
        results.append(phase | {'simulated_date':when,'error_days':error,'missing':when is None,
            'threshold_already_met_at_window_start':bool(initial and initial[0]>=phase['threshold'])})
    return results


def run_job(job, source, definitions, observations, phases, config, model_root, output):
    folder = output/'simulations'/job['id']
    folder.mkdir(parents=True,exist_ok=True)
    result_path = folder/'result.json'
    document = deepcopy(source)
    if job.get('parameter_file'):
        apply_archived_parameters(document,definitions,read_json(Path(job['parameter_file'])))
    if job['variant']=='no_spring_reset':
        disable_reset(document)
    for node in list(walk(document)):
        if node.get('$type','').startswith('Models.Clock,'):
            node['End'] = f"{job['end']}-12-31T00:00:00"
        if node.get('$type','').startswith('Models.Core.Zone,'):
            node['Area'] = job['area_ha']
        if node.get('$type','').startswith('Models.Climate.Weather,'):
            node['FileName'] = 'weather.met'
        if node.get('$type','').startswith('Models.Storage.DataStore,'):
            node['CustomFileName'] = 'history.db'
        node['Children'] = [c for c in node.get('Children',[]) if
            not c.get('$type','').startswith('Models.Report,') or
            c['Name'].startswith(('HarvestReport_','PhenologyReport_'))]
    # Reuse completed work only if the prepared input and protocol still agree.
    input_digest = hashlib.sha256(json.dumps(document,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if result_path.exists():
        existing = read_json(result_path)
        if existing['input_digest'] != input_digest or existing['job'] != job:
            raise ValueError('Existing job has different inputs')
        return existing
    if (folder/'history.db').exists():
        raise FileExistsError(f'Incomplete prior attempt requires a fresh output directory: {folder}')
    save(folder/'simulation.apsimx',document)
    shutil.copyfile(model_root/'weather.met',folder/'weather.met')
    save(folder/'protocol.json',{'job':job,'input_digest':input_digest})
    print('START '+job['id'],flush=True)
    start = time.monotonic()
    run_apsim(config,folder/'simulation.apsimx',folder/'history.db')
    rotation_index = {(r['field'],r['year']):r for r in observations}
    source_rows = (observations if job['sample']=='final_fit' else job['target_rows'])
    with sqlite3.connect((folder/'history.db').resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as connection:
        connection.row_factory = sqlite3.Row
        errors = [dict(r) for r in connection.execute('SELECT * FROM _Messages WHERE MessageType=0')]
        if errors:
            raise RuntimeError(str(errors[:2]))
        harvested = {}
        for field in sorted({r['field'] for r in observations}):
            for row in connection.execute(f'SELECT * FROM HarvestReport_{field}'):
                key = (field,row['Year'])
                if key in harvested:
                    raise ValueError('Duplicate annual harvest')
                harvested[key] = dict(row)
        expected = {(r['field'],r['year']) for r in observations if r['year']<=job['end']}
        if set(harvested) != expected:
            raise ValueError('Missing or extra annual harvests')
        scored = []
        for row in source_rows:
            h = harvested[(row['field'],row['year'])]
            if h['Date'][:10] != row['harvest_date_actual'] or h['Crop'] != row['harvest_crops']:
                raise ValueError('Harvest calendar or product changed')
            prediction = h['YieldDMTHa']
            if not math.isfinite(prediction) or prediction<0:
                raise ValueError('Invalid simulated yield')
            scored.append(row | {'simulated_dm_t_ha':prediction,
                'error_t_ha':prediction-row['observed_t_ha'] if row['observed_t_ha'] is not None else None,
                'archived_prediction_t_ha':row['simulated_dm_t_ha']})
        target_phases = phases['records'] if job['sample']=='final_fit' else [p for p in phases['pre_update_records'] if
            p['trained_through']==job['trained_through'] and p['year']<=job['end']]
        phase_rows = phase_results(connection,target_phases,rotation_index)
        warnings = [dict(r) for r in connection.execute('SELECT * FROM _Messages WHERE MessageType=1')]
    if job['variant']=='baseline' and job['area_ha']==1:
        difference = max(abs(r['simulated_dm_t_ha']-r['archived_prediction_t_ha']) for r in scored)
        if difference>1e-8:
            raise ValueError(f'Archive control not reproduced, max difference {difference}')
    result = {'job':job,'input_digest':input_digest,'seconds':time.monotonic()-start,
        'annual_records':len(harvested),'yield_rows':scored,'phase_rows':phase_rows,
        'warnings':warnings,'database':str(folder/'history.db'),'engine_errors':0}
    save(result_path,result)
    print('DONE '+job['id']+' '+json.dumps(metrics(scored),ensure_ascii=False),flush=True)
    return result


def phase_score(rows):
    matched = [r for r in rows if r['error_days'] is not None]
    return {'n':len(rows),'matched':len(matched),'missing':len(rows)-len(matched),
        'mae_days':sum(abs(r['error_days']) for r in matched)/len(matched) if matched else None,
        'penalized_mae_days':sum(abs(r['error_days']) if r['error_days'] is not None else 90 for r in rows)/len(rows) if rows else None,
        'threshold_already_met_at_window_start':sum(r.get('threshold_already_met_at_window_start',False) for r in rows)}


def write_report(output, results, original, original_phases, source_hashes):
    original_forward = original['pre_update_records']
    changed_forward = [row for r in results if r['job']['sample']=='pre_update' for row in r['yield_rows']]
    changed_phases = [row for r in results if r['job']['sample']=='pre_update' for row in r['phase_rows']]
    if {(r['field'],r['year']) for r in changed_forward} != {(r['field'],r['year']) for r in original_forward}:
        raise ValueError('Changed rolling predictions do not cover the original sample')
    summary_rows = []
    scores = []
    datasets = [('Прогнозы до обновления, исходное правило',original_forward,original_phases['pre_update_records'],'pre_update_baseline',1),
        ('Прогнозы до обновления, без сброса фазы',changed_forward,changed_phases,'pre_update_no_spring_reset',1)]
    final_results = sorted([r for r in results if r['job']['sample']=='final_fit'],
                           key=lambda r:(-r['job']['area_ha'],r['job']['variant']!='baseline'))
    for result in final_results:
        job = result['job']
        if job['sample']=='final_fit':
            label = ('Исходное правило' if job['variant']=='baseline' else 'Без сброса фазы')+f", итоговые параметры, {job['area_ha']} га"
            datasets.append((label,result['yield_rows'],result['phase_rows'],job['id'],job['area_ha']))
    for label,rows,phase_rows,key,area in datasets:
        score = {'label':label,'key':key,'area_ha':area,**metrics(rows),'phenology':phase_score(phase_rows)}
        scores.append(score)
        summary_rows.append([label,score['n'],number(score['mae_t_ha']),number(score['rmse_t_ha']),
            number(score['bias_t_ha'],True),number(score['phenology']['mae_days']),score['phenology']['missing']])
    paired_baseline = []
    paired_changed = []
    for row in original_forward:
        if not valid(row):
            continue
        previous = [r['observed_t_ha'] for r in original['records'] if valid(r) and r['year']<=row['trained_through']
                    and (r['model_crops'],r['use'])==(row['model_crops'],row['use'])]
        if previous:
            mean = sum(previous)/len(previous)
            paired_baseline.append(row | {'past_mean_t_ha':mean})
            after = next(r for r in changed_forward if (r['field'],r['year'])==(row['field'],row['year']))
            paired_changed.append(after | {'past_mean_t_ha':mean})
    benchmark_scores = [
        {'label':'Исходный APSIM, парная выборка',**metrics(paired_baseline)},
        {'label':'Без сброса фазы, парная выборка',**metrics(paired_changed)},
        {'label':'Среднее прошлых наблюдений, та же выборка',**metrics(paired_baseline,'past_mean_t_ha')},
    ]
    phase_groups = []
    for label,rows in [('Исходное правило',original_phases['pre_update_records']),('Без сброса фазы',changed_phases)]:
        for plant in sorted({r['plant'] for r in rows}):
            phase_groups.append({'label':label,'plant':plant,**phase_score([r for r in rows if r['plant']==plant])})
    changed_index = {(r['field'],r['year']):r for r in changed_forward}
    comparisons = []
    for before in original_forward:
        if not valid(before):
            continue
        after = changed_index[(before['field'],before['year'])]
        change = abs(after['error_t_ha'])-abs(before['error_t_ha'])
        comparisons.append({'field':before['field'],'year':before['year'],'crop':before['crop'],
            'model_crops':before['model_crops'],'use':before['use'],'pasture_age':before['pasture_age'],
            'trained_through':before['trained_through'],'observed_t_ha':before['observed_t_ha'],
            'baseline_t_ha':before['simulated_dm_t_ha'],'changed_t_ha':after['simulated_dm_t_ha'],
            'baseline_absolute_error_t_ha':abs(before['error_t_ha']),
            'changed_absolute_error_t_ha':abs(after['error_t_ha']),'absolute_error_change_t_ha':change})
    by_group = []
    for grouping in ['field','model_crops','use','year','pasture_age']:
        for key in sorted({str(r[grouping]) for r in comparisons}):
            records = [r for r in comparisons if str(r[grouping])==key]
            before = sum(r['baseline_absolute_error_t_ha'] for r in records)/len(records)
            after = sum(r['changed_absolute_error_t_ha'] for r in records)/len(records)
            by_group.append({'grouping':grouping,'group':key,'n':len(records),
                'baseline_mae_t_ha':before,'changed_mae_t_ha':after,'mae_change_t_ha':after-before})
    improvement = 100*(scores[0]['mae_t_ha']-scores[1]['mae_t_ha'])/scores[0]['mae_t_ha']
    counts = {'improved':sum(r['absolute_error_change_t_ha'] < -1e-6 for r in comparisons),
        'worsened':sum(r['absolute_error_change_t_ha'] > 1e-6 for r in comparisons),
        'unchanged':sum(abs(r['absolute_error_change_t_ha']) <= 1e-6 for r in comparisons)}
    conclusions = [
        f"Проверены все десять полей, 200 годовых записей и 156 наблюдений итоговой модели. Последовательная проверка охватывает все 130 наблюдений до обновления параметров.",
        'В экспериментальном варианте отключен принудительный перевод многолетников на фазу 3 первого марта на всех полях во все годы. Остальные агрономические параметры сохранены.',
        f"MAE прогнозов по следующим годам изменилась с {number(scores[0]['mae_t_ha'])} до {number(scores[1]['mae_t_ha'])} т СВ/га. Относительное уменьшение составляет {improvement:.2f}%.",
        f"По абсолютной ошибке улучшились {counts['improved']} записей, ухудшились {counts['worsened']}, без существенного изменения остались {counts['unchanged']}.",
        'Архивные прогнозы и проверка окон используют исходную площадь 1 га на поле. Текущая площадь 0,1 га проверена отдельно на итоговом наборе параметров.',
        'Итоговые параметры уже подбирались с учетом всей истории. Их проверку следует читать отдельно от прогнозов до обновления.',
        'Параметры культур при этом эксперименте зафиксированы. Это проверка изменения менеджера с историческими настройками.',
        'Изменение выбрано после разбора этой истории. Проверка ретроспективная, с известными погодой и операциями. Для самостоятельной оценки переноса потребуется отдельный набор данных.',
    ]
    if scores[1]['phenology']['threshold_already_met_at_window_start']:
        conclusions.append('Для части фаз порог уже достигнут в начале окна поиска даты. После отключения сброса накопленная фаза может оставаться высокой. Такие даты требуют отдельной трактовки индикатора повторного отрастания.')
    conclusions.append('Штрафная ошибка фаз по одинаковым 282 записям изменилась с '+number(scores[0]['phenology']['penalized_mae_days'])+' до '+number(scores[1]['phenology']['penalized_mae_days'])+' дня. Отсутствующей фазе назначается штраф 90 дней. Для общей оценки изменения нужно учитывать урожай и фазовые показатели вместе.')
    if benchmark_scores[1]['rmse_t_ha'] >= benchmark_scores[2]['rmse_t_ha']:
        conclusions.append('На парной выборке простой прогноз среднего прошлых наблюдений остается точнее по RMSE. Экспериментальный вариант требует дальнейшей проверки для поддержки решений.')
    summary = {'change':'disable_forced_march_perennial_reset_all_fields_all_years',
        'scores':scores,'mae_reduction_percent':improvement,'comparison_counts':counts,
        'comparisons':comparisons,'by_group':by_group,'conclusions':conclusions,
        'paired_benchmark':benchmark_scores,'rolling_phases_by_plant':phase_groups,
        'source_hashes':source_hashes,'sources_unchanged':True,
        'total_engine_seconds':sum(r['seconds'] for r in results),
        'simulation_count':len(results),'modified_pre_update_rows':changed_forward,
        'modified_pre_update_phases':changed_phases,'experimental_model':str(output/'experimental_model/simulation.apsimx')}
    save(output/'comparison.json',summary)
    headers = ['Проверка','Число урожаев','MAE т СВ/га','RMSE т СВ/га','Смещение','MAE достигнутых фаз дни','Пропуски фаз']
    md = ['# Проверка изменения модели на всей истории',*conclusions,md_table(headers,summary_rows)]
    body = '<h1>Проверка изменения модели на всей истории</h1>'+''.join('<p>'+html.escape(t)+'</p>' for t in conclusions)
    body += html_table(headers,summary_rows)
    benchmark_rows = [[r['label'],r['n'],number(r['mae_t_ha']),number(r['rmse_t_ha'])] for r in benchmark_scores]
    benchmark_headers = ['Прогноз','Число','MAE т СВ/га','RMSE т СВ/га']
    body += '<h2>Сравнение с простым прогнозом</h2>'+html_table(benchmark_headers,benchmark_rows)
    md += ['## Сравнение с простым прогнозом',md_table(benchmark_headers,benchmark_rows)]
    phase_rows = [[r['label'],r['plant'],r['n'],number(r['mae_days']),r['missing'],r['threshold_already_met_at_window_start']] for r in phase_groups]
    phase_headers = ['Вариант','Культура модели','Число фаз','MAE дни','Пропуски','Порог достигнут к началу окна']
    body += '<h2>Фазы по культурам в последовательной проверке</h2>'+html_table(phase_headers,phase_rows)
    md += ['## Фазы по культурам в последовательной проверке',md_table(phase_headers,phase_rows)]
    phase_total_rows = [[r['label'],r['phenology']['n'],r['phenology']['matched'],
        number(r['phenology']['penalized_mae_days']),r['phenology']['missing']] for r in scores]
    phase_total_headers = ['Проверка','Число фаз','Достигнуты','Ошибка со штрафом дни','Пропуски']
    body += '<h2>Фазы с учетом отсутствующих дат</h2>'+html_table(phase_total_headers,phase_total_rows)
    md += ['## Фазы с учетом отсутствующих дат',md_table(phase_total_headers,phase_total_rows)]
    for grouping,title in [('field','По полям'),('model_crops','По культурам'),('use','По использованию продукции'),('year','По годам'),('pasture_age','По возрасту травостоя')]:
        rows = [[r['group'],r['n'],number(r['baseline_mae_t_ha']),number(r['changed_mae_t_ha']),number(r['mae_change_t_ha'],True)] for r in by_group if r['grouping']==grouping]
        cols = ['Группа','Число','MAE исходная','MAE после изменения','Изменение ошибки']
        body += '<h2>'+title+'</h2>'+html_table(cols,rows)
        md += ['## '+title,md_table(cols,rows)]
        if grouping=='pasture_age':
            note = 'Код возраста 0 включает однолетние культуры и травостой козлятника с неопределенным фактическим возрастом.'
            body += '<p>'+note+'</p>'
            md.append(note)
    for title,records in [('Наибольшие улучшения',sorted(comparisons,key=lambda r:r['absolute_error_change_t_ha'])[:15]),
                          ('Наибольшие ухудшения',sorted(comparisons,key=lambda r:r['absolute_error_change_t_ha'],reverse=True)[:15])]:
        rows = [[r['field'],r['year'],r['crop'],number(r['observed_t_ha']),number(r['baseline_t_ha']),number(r['changed_t_ha']),number(r['absolute_error_change_t_ha'],True)] for r in records]
        cols = ['Поле','Год','Культура','Наблюдение','Исходный расчет','Измененный расчет','Изменение абсолютной ошибки']
        body += '<h2>'+title+'</h2>'+html_table(cols,rows)
        md += ['## '+title,md_table(cols,rows)]
    body += '<h2>Файлы</h2><p><a href="comparison.json">Полные сравнения и происхождение данных</a></p><p><a href="REPORT.md">Текстовый отчет</a></p><p><a href="experimental_model/simulation.apsimx">Экспериментальная модель</a> и <a href="experimental_model/weather.met">погода</a></p>'
    style = 'body{font:16px/1.6 system-ui;background:#f3f6f8;color:#173044;margin:0}main{max-width:1250px;margin:25px auto;padding:28px;background:white}h1{font-size:28px}h2{margin-top:30px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px;border:1px solid #d1dce4;vertical-align:top;text-align:left}th{background:#e9f0f5}tr:nth-child(even){background:#f8fafb}.scroll{overflow:auto}a{color:#125e84}'
    (output/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Полная проверка APSIM</title><style>'+style+'</style><main>'+body+'</main></html>',encoding='utf-8')
    (output/'REPORT.md').write_text('\n\n'.join(md),encoding='utf-8')
    print(json.dumps({'scores':scores,'counts':counts,'output':str(output)},ensure_ascii=False),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'runs/analysis/apsim_full_history_test_20261106')
    parser.add_argument('--archive',type=Path,default=ROOT.parent/'calibrovka/history_final')
    parser.add_argument('--workers',type=int,choices=[1,2],default=2)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    model_root = ROOT/'models/apsim/simulation/calibrated_history'
    obs_root = ROOT/'models/apsim/data/observations/new_calibr'
    original = read_json(obs_root/'yield_comparison.json')
    phases = read_json(obs_root/'phenology_comparison.json')
    source = read_json(model_root/'simulation.apsimx')
    definitions = read_json(model_root/'parameters.json')['definitions']
    state = read_json(args.archive/'calibration_state.json')
    if state['walkforward_yields'] != original['pre_update_records']:
        raise ValueError('Archive and portable validation rows differ')
    runtime = read_json(ROOT/'models/apsim/configs/multiyear_rotation_native_2020_2023.json')['runtime']
    runtime['timeout_seconds'] = 900
    files = [model_root/'simulation.apsimx',model_root/'weather.met',model_root/'parameters.json',
             obs_root/'yield_comparison.json',obs_root/'phenology_comparison.json']
    hashes = {str(p):fingerprint(p) for p in files}
    engine = Path(runtime['command'][0])
    if fingerprint(engine) != read_json(model_root/'provenance.json')['engine']['sha256']:
        raise ValueError('APSIM executable differs from the archived calibration engine')
    hashes[str(engine)] = fingerprint(engine)
    experiment = output/'experimental_model'
    experiment.mkdir(exist_ok=True)
    save(experiment/'simulation.apsimx',disable_reset(deepcopy(source)))
    shutil.copyfile(model_root/'weather.met',experiment/'weather.met')
    save(experiment/'change.json',{'change':'Removed forced March 1 reset to phase 3 in all ten RotationManagers',
        'source_sha256':hashes[str(model_root/'simulation.apsimx')],
        'modified_sha256':fingerprint(experiment/'simulation.apsimx'),
        'area_note':'Export retains source 1 ha fields. The comparison also executes separate 0.1 ha copies.'})
    jobs = []
    for area in [1,.1]:
        for variant in ['baseline','no_spring_reset']:
            jobs.append({'id':f'final_{area}ha_{variant}','sample':'final_fit','end':2023,
                'area_ha':area,'variant':variant})
    for trained in sorted({r['trained_through'] for r in original['pre_update_records']}):
        targets = [r for r in original['pre_update_records'] if r['trained_through']==trained]
        parameter_file = args.archive/f'stages/{trained}/parameters.json'
        hashes[str(parameter_file)] = fingerprint(parameter_file)
        jobs.append({'id':f'rolling_after_{trained}_no_spring_reset','sample':'pre_update',
            'end':max(r['year'] for r in targets),'area_ha':1,'variant':'no_spring_reset',
            'trained_through':trained,'parameter_file':str(parameter_file.resolve()),'target_rows':targets})
    save(output/'protocol.json',{'jobs':jobs,'sources':hashes,'workers':args.workers,
        'primary_observation_source':'Урожайность','change':'disable spring reset, every field and every year'})
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_job,job,source,definitions,original['records'],phases,
            {'runtime':runtime},model_root,output) for job in jobs]
        for future in as_completed(futures):
            results.append(future.result())
            save(output/'status.json',{'completed':len(results),'total':len(jobs),'state':'running'})
    if any(fingerprint(Path(p)) != digest for p,digest in hashes.items()):
        raise ValueError('An input source changed during the test')
    write_report(output,results,original,phases,hashes)
    save(output/'status.json',{'completed':len(results),'total':len(jobs),'state':'complete'})


if __name__=='__main__':
    main()
