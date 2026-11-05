"""Observation checks, crop-count diagnostics, and optional real weather/area probes."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import statistics
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.matplotlib'))
from agro.apsim.native_rotation_optimizer import simulate,load_config
from agro.planning.future_scenarios import context,choices,crop_mix
from agro.planning.crop_mix_repair import CropMixRepair


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def csv_save(path,rows):
    if not rows:return
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
def valid(r):return r.get('included_in_metrics') is True and r.get('observed_t_ha') is not None
def metrics(rows,prediction='simulated_dm_t_ha'):
    rs=[r for r in rows if valid(r) and r.get(prediction) is not None]
    if not rs:return {'n':0,'mae_t_ha':None,'rmse_t_ha':None,'bias_t_ha':None,'r2':None}
    errors=[r[prediction]-r['observed_t_ha'] for r in rs];mean=statistics.mean(r['observed_t_ha'] for r in rs)
    ss=sum((r['observed_t_ha']-mean)**2 for r in rs)
    return {'n':len(rs),'mae_t_ha':statistics.mean(abs(e) for e in errors),
            'rmse_t_ha':math.sqrt(statistics.mean(e*e for e in errors)),
            'bias_t_ha':statistics.mean(errors),'r2':1-sum(e*e for e in errors)/ss if ss else None}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-probes',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'runs/analysis/apsim_readiness_0p1ha')
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    modelroot=ROOT/'models/apsim/simulation/calibrated_history'
    obsroot=ROOT/'models/apsim/data/observations/new_calibr';obsroot.mkdir(parents=True,exist_ok=True)
    for name in ['yield_comparison.json','phenology_comparison.json']:
        target=obsroot/name
        if not target.exists():shutil.copy2(ROOT.parent/'new_calibr/result'/name,target)
    observations=read(obsroot/'yield_comparison.json');final=observations['records'];forward=observations['pre_update_records']
    assert all(r['predicted_before_update'] and r['trained_through']<r['year'] for r in forward)
    # The supplied rolling forecasts are frozen before each update. Historical
    # weather/operations and initial cultivar priors are still known/conditional.
    for r in forward:
        previous=[s['observed_t_ha'] for s in final if valid(s) and s['year']<=r['trained_through']
                  and (s['model_crops'],s['use'])==(r['model_crops'],r['use'])]
        r['past_crop_mean_t_ha']=statistics.mean(previous) if previous else None
    paired=[r for r in forward if valid(r) and r['past_crop_mean_t_ha'] is not None]
    scores={'final_fit':metrics(final),'chronological_pre_update':metrics(forward),
            'paired_pre_update':metrics(paired),'paired_past_crop_mean':metrics(paired,'past_crop_mean_t_ha')}
    yearly=[];bycrop=[]
    for label,rows in [('final_fit',final),('chronological_pre_update',forward)]:
        for year in sorted({r['year'] for r in rows}):yearly.append({'sample':label,'year':year,**metrics([r for r in rows if r['year']==year])})
        for crop,use in sorted({(r['model_crops'],r['use']) for r in rows}):
            bycrop.append({'sample':label,'crop':crop,'use':use,**metrics([r for r in rows if (r['model_crops'],r['use'])==(crop,use)])})
    csv_save(out/'quality_by_year.csv',yearly);csv_save(out/'quality_by_crop.csv',bycrop)
    columns=['field','year','model_crops','use','trained_through','predicted_before_update','observed_t_ha','simulated_dm_t_ha','past_crop_mean_t_ha','included_in_metrics']
    csv_save(out/'pre_update_observations.csv',[{k:r.get(k) for k in columns} for r in forward])
    csv_save(out/'fit_observations.csv',[{k:r.get(k) for k in columns} for r in final])
    ctx=context(ROOT/'models/apsim/configs/future_rotation_2025_2029.json')
    assert all(z['Area']==.1 for z in ctx['source']['Children'][0]['Children'] if z.get('$type')=='Models.Core.Zone, Models')
    old=ROOT/'runs/future_rotation/yield+profit+humic_20261103_163026_963118'
    old_rows=read(old/'evaluations.json') if (old/'evaluations.json').exists() else []
    violations=[];counts=Counter()
    if not old_rows and (out/'old_crop_violations.csv').exists():
        with (out/'old_crop_violations.csv').open(encoding='utf-8-sig',newline='') as stream:
            violations=list(csv.DictReader(stream))
        counts.update((r['category'],r['direction']) for r in violations)
    for r in old_rows:
        for row in r['crop_mix']['annual_counts']:
            for category,spec in r['crop_mix']['categories'].items():
                direction='above' if row[category]>spec['annual_max'] else 'below' if row[category]<spec['annual_min'] else None
                if direction:
                    counts[(category,direction)]+=1
                    violations.append({'candidate_id':r['candidate_id'],'year':row['year'],'category':category,
                        'count':row[category],'min':spec['annual_min'],'max':spec['annual_max'],'direction':direction})
    violation_summary=[{'category':c,'direction':d,'field_year_checks':n} for (c,d),n in counts.most_common()]
    csv_save(out/'old_crop_violations.csv',violations);csv_save(out/'old_crop_violations_summary.csv',violation_summary)
    import numpy as np
    repair=CropMixRepair(ctx);rng=np.random.default_rng(42);trials=[];unique=set()
    for i in range(200):
        vector=[int(rng.integers(len(ctx['pool']['fields'][f]['alternatives']))) for f in ctx['fields']]+[7,12]
        before=crop_mix(ctx,choices(ctx,vector[:10]))[1]
        fixed=repair(vector);after=crop_mix(ctx,choices(ctx,fixed[:10]))[1]
        assert after['within_historical_ranges'] and fixed[-2:].tolist()==[7,12]
        unique.add(tuple(fixed[:10]));trials.append({'trial':i+1,'before_feasible':before['within_historical_ranges'],
            'before_violation_units':before['range_violation_units'],'after_feasible':after['within_historical_ranges'],
            'after_violation_units':after['range_violation_units'],'changed_fields':sum(a!=b for a,b in zip(vector[:10],fixed[:10]))})
    csv_save(out/'map_repair_trials.csv',trials)
    assert len(unique)>20
    repair_check={**repair.summary(),'trials':len(trials),'before_feasible':sum(r['before_feasible'] for r in trials),
                  'after_feasible':sum(r['after_feasible'] for r in trials),'unique_repaired_maps':len(unique),
                  'note':'Structural test only; no yield/profit improvement is claimed.'}
    print('MAP REPAIR: '+json.dumps(repair_check),flush=True)
    config=load_config(ROOT/'models/apsim/configs/multiyear_rotation_native_2020_2023.json')
    source=read(modelroot/'simulation.apsimx');probe_results={}
    if args.run_probes:
        for label,scale,area in [('original_pressure',1,.1),('pressure_x100',100,.1),('original_area_control',1,1.)]:
            folder=out/'probes'/label;inputs=folder/'inputs';inputs.mkdir(parents=True,exist_ok=True)
            lines=(modelroot/'weather.met').read_text(encoding='utf-8-sig').splitlines();columns=None;updated=[]
            for line in lines:
                p=line.split()
                if p[:2]==['year','day']:columns=p
                if scale!=1 and columns and p and p[0].isdigit() and len(p)==len(columns):
                    p[columns.index('sp')]=f'{float(p[columns.index("sp")])*scale:.6f}';line=' '.join(p)
                updated.append(line)
            (inputs/'weather.met').write_text('\n'.join(updated)+'\n',encoding='utf-8')
            cfg=deepcopy(config);cfg['model_root']=str(inputs);cfg['field_area_ha']=area
            if not (folder/'simulation/simulation.json').exists():
                print('APSIM PROBE: '+label,flush=True)
                simulate(source,cfg,None,folder/'simulation')
            database=folder/'simulation/candidate.db';data={}
            with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as con:
                assert con.execute('SELECT count(*) FROM _Messages WHERE MessageType=0').fetchone()[0]==0
                for field in ctx['fields']:
                    for year,mass in con.execute(f'SELECT Year,YieldDMTHa FROM HarvestReport_{field}'):
                        data[(field,year)]=mass
            assert len(data)==200
            probe_results[label]=data
        raw=probe_results['original_pressure'];corrected=probe_results['pressure_x100']
        original_fit={(r['field'],r['year']):r['simulated_dm_t_ha'] for r in final}
        assert set(raw)==set(original_fit)
        replay_diff=max(abs(raw[k]-original_fit[k]) for k in raw)
        pressure_diff=max(abs(raw[k]-corrected[k]) for k in raw)
        original_area=probe_results['original_area_control']
        control_diff=max(abs(original_area[k]-original_fit[k]) for k in original_area)
        assert control_diff<1e-8,'Original-area control no longer reproduces the calibration export'
        probe_audit={'records':200,'fields':10,'field_area_ha':.1,'total_area_ha':1.,
                     'max_area_replay_difference_t_ha':replay_diff,'max_pressure_change_difference_t_ha':pressure_diff,
                     'max_original_area_control_difference_t_ha':control_diff,
                     'area_effect_note':'A 0.1 ha replay can slightly change per-hectare yields; original-area control checked separately. Do not just rescale archived totals.',
                     'source_production_t_dm':sum(original_fit.values()),'new_production_t_dm':sum(raw.values())*.1,
                     'engine_errors':0,'pressure_test':'Only sp was multiplied by 100; all other weather and crop inputs identical',
                     'scope':'Harvest comparison for the installed engine and this model, not proof for every APSIM configuration'}
        save(out/'probe_audit.json',probe_audit)
        for r in final:r['simulated_dm_t_ha']=raw[(r['field'],r['year'])]
        scores['new_0p1ha_replay']=metrics(final)
    elif (out/'probe_audit.json').exists():probe_audit=read(out/'probe_audit.json')
    else:probe_audit={'status':'not_run'}
    save(out/'quality.json',scores);save(out/'map_repair.json',repair_check)
    provenance={'source_model_sha256':hashlib.sha256((modelroot/'simulation.apsimx').read_bytes()).hexdigest(),
                'source_weather_sha256':hashlib.sha256((modelroot/'weather.met').read_bytes()).hexdigest(),
                'observations_sha256':hashlib.sha256((obsroot/'yield_comparison.json').read_bytes()).hexdigest(),
                'observation_source':'new_calibr/result; local copy in models/apsim/data/observations/new_calibr',
                'area_override_ha':.1,'economics':'unchanged diagnostic assumptions; no invented prices',
                'large_optimization_started':False,'nsga2_vs_nsga3_comparison':False}
    save(out/'audit.json',provenance)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    fig,axs=plt.subplots(1,2,figsize=(11,4.6))
    for ax,rows,title in zip(axs,[final,forward],['После подстройки','До очередной подстройки']):
        rows=[r for r in rows if valid(r)];x=[r['observed_t_ha'] for r in rows];y=[r['simulated_dm_t_ha'] for r in rows]
        ax.scatter(x,y,s=22,alpha=.65);limit=max(x+y);ax.plot([0,limit],[0,limit],color='#777',ls='--')
        ax.set(title=title,xlabel='Наблюдения, т сухой массы/га',ylabel='APSIM, т сухой массы/га')
    fig.tight_layout();fig.savefig(out/'01_observed.png',dpi=150);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4.5));rows=[r for r in bycrop if r['sample']=='chronological_pre_update' and r['n']]
    ax.barh([r['crop']+' / '+r['use']+f' (n={r["n"]})' for r in rows],[r['mae_t_ha'] for r in rows],color='#187f9c')
    ax.set(xlabel='MAE, т сухой массы/га',title='Ошибка до подстройки по группам продукции');fig.tight_layout();fig.savefig(out/'02_crop_errors.png',dpi=150);plt.close(fig)
    def table(rows):
        keys=list(rows[0]);return '<div class="scroll"><table><tr>'+''.join('<th>'+html.escape(k)+'</th>' for k in keys)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(r.get(k)))+'</td>' for k in keys)+'</tr>' for r in rows)+'</table></div>'
    qualityrows=[{'sample':k,**v} for k,v in scores.items()]
    paired_model=scores['paired_pre_update'];paired_mean=scores['paired_past_crop_mean']
    finding=(f'На одной и той же выборке из {paired_model["n"]} наблюдений прогнозы APSIM до подстройки имеют '
             f'RMSE {paired_model["rmse_t_ha"]:.3f} т/га, а среднее прежних наблюдений той же культуры/использования — '
             f'{paired_mean["rmse_t_ha"]:.3f} т/га. '
             'В этой проверке APSIM уступает простому ориентиру. Увеличение бюджета оптимизации не исправляет эту ошибку модели.')
    report=(
        '# Проверка APSIM и подготовка поиска\n\n'
        'Площадь каждого из десяти полей в рабочих конфигах — 0,1 га. Всего 1 га. Исходный калибровочный экспорт сохранён, площадь меняется в выполняемых копиях. '
        'Уменьшение площади само по себе не является улучшением модели или поиска. Контрольный расчёт показал небольшие изменения урожайности на гектар; '
        'поэтому старые итоги нельзя просто умножить на 0,1 и назвать новым запуском.\n\n'
        'Проверка по наблюдениям разделена на итоговую подгонку и сохранённые хронологические прогнозы до очередного включения лет в подбор. '
        'Для каждого такого прогноза проверено trained_through < year. Это условная проверка при известной погоде и операциях, а не новый полностью независимый полевой опыт. '
        'Важное уточнение прежнего отчёта: такие pre-update прогнозы в калибровочном пакете есть; теперь они разобраны отдельно.\n\n'
        'В paired сравнивается одна и та же выборка: APSIM до подстройки и среднее прежних наблюдений той же модельной культуры/использования. '
        'Показатели зерна и кормов дополнительно разделены. Сводки представлены в quality.json, quality_by_crop.csv, quality_by_year.csv.\n\n'
        +finding+'\n\n'+
        'Причина отклонения карт — несогласованное сочетание допустимых полевых последовательностей. В частности, избыток овса/трав и недостаток пшеницы/ржи. '
        'Исправление MILP выбирает ближайшую комбинацию из тех же разрешённых последовательностей до APSIM. Пределы состава и правила переходов не ослаблены; технологические гены сохраняются. '
        'Отклонения старого запуска и структурные испытания находятся в CSV. Улучшение урожая/прибыли этим тестом не доказано.\n\n'
        'Экономические цены и расходы не изменены. Прибыль остаётся условной, кормовая выручка не придумана.\n\n'
        'Большой поиск НЕ запущен. Команда: python -m agro.night_queue --config launchers/configs/apsim_extended.json. Три последовательных NSGA-II, seed 42/123/2026, '
        'популяция 20, 10 поколений, до 200 оценок на повтор плюс базовый расчёт. Из-за кеша уникальных симуляций может быть меньше. '
        'Подготовка без расчётов: python -m agro.night_queue --config launchers/configs/apsim_extended.json --prepare-only. Продолжение: python -m agro.night_queue --config launchers/configs/apsim_extended.json --resume <папка_batch>. '
        'Оценка длительности около суток ориентировочная. Старые замороженные очереди сохраняют прежнюю площадь и правила. '
        'NSGA-III и сравнение алгоритмов в этой очереди отсутствуют.\n')
    (out/'REPORT.md').write_text(report,encoding='utf-8')
    blocks=''.join('<p>'+html.escape(p)+'</p>' for p in report.split('\n\n')[1:])
    page='<!doctype html><html lang="ru"><meta charset="utf-8"><title>Проверка APSIM 0,1 га</title><style>body{font:16px/1.6 system-ui;background:#f2f5f8;color:#182c3c}main{max-width:1150px;margin:25px auto;padding:25px;background:white}th,td{border:1px solid #ccd7df;padding:8px;text-align:left}table{border-collapse:collapse}th{background:#eaf2f7}.scroll{overflow:auto}img{max-width:100%}</style><main><h1>APSIM: 0,1 га, наблюдения и допустимые карты</h1>'+blocks
    labels={'final_fit':'Итоговая подгонка, исходная площадь','chronological_pre_update':'Прогнозы до очередной подстройки',
            'paired_pre_update':'До подстройки, общая выборка','paired_past_crop_mean':'Среднее прошлых наблюдений, общая выборка',
            'new_0p1ha_replay':'Новый контрольный расчёт 0,1 га'}
    visible_quality=[{'Проверка':labels.get(k,k),'Наблюдений':v['n'],'MAE т/га':v['mae_t_ha'],
                      'RMSE т/га':v['rmse_t_ha'],'Смещение т/га':v['bias_t_ha'],'R²':v['r2']} for k,v in scores.items()]
    page+='<h2>Ошибки по наблюдениям</h2>'+table(visible_quality)+'<p>MAE — средняя абсолютная ошибка. RMSE сильнее учитывает большие ошибки. В обеих метриках меньше — лучше.</p><img src="01_observed.png"><img src="02_crop_errors.png">'
    page+='<h2>Нарушения старых карт</h2>'+table(violation_summary)+'<h2>Исправление карт</h2><pre>'+html.escape(json.dumps(repair_check,ensure_ascii=False,indent=2))+'</pre>'
    page+='<h2>Реальные контрольные симуляции</h2><pre>'+html.escape(json.dumps(probe_audit,ensure_ascii=False,indent=2))+'</pre>'
    page+='<h2>Файлы</h2><ul>'+''.join('<li><a href="'+p.name+'">'+p.name+'</a></li>' for p in sorted(out.iterdir()) if p.suffix in ['.csv','.json','.md'])+'</ul></main></html>'
    (out/'index.html').write_text(page,encoding='utf-8');
    print(json.dumps({'output':str(out),'quality':scores,'probes':probe_audit,'repair':repair_check},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
