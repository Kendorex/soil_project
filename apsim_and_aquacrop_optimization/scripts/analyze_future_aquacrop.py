"""Audit completed AquaCrop future-map searches and publish local analysis artifacts."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm,ListedColormap
import numpy as np
import pandas as pd


def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))


def near(a,b):
    if not math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-6):raise ValueError(f'Audit mismatch: {a} vs {b}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'runs/analysis/aquacrop_future')
    args=parser.parse_args()
    options=[p for p in (ROOT/'runs/future_rotation/aquacrop').iterdir() if (p/'optimization_summary.json').exists()]
    run=(args.run or max(options,key=lambda p:(p/'optimization_summary.json').stat().st_mtime)).resolve()
    status=read(run/'status.json');summary=read(run/'optimization_summary.json');inputs=read(run/'scenario_inputs.json')
    if status['stage'] not in ('completed','no_feasible_solution'):raise ValueError('Search is not finished')
    if inputs['engine']!='aquacrop':raise ValueError('Expected AquaCrop search')
    evaluations=read(run/'evaluations.json');successful=[r for r in evaluations if r['status']=='ok']
    baseline=read(run/'baseline_score.json');records={'baseline':baseline,**{r['candidate_id']:r for r in successful}}
    configuration=inputs['configuration']
    source_config=(read(Path(configuration['management_reference']))['native_config'] if configuration.get('management_reference')
                   else read(Path(configuration['optimization_run'])/'config_snapshot.json'))
    economics=inputs['configuration'].get('economics',source_config['economics'])
    prices={v['apsim_type']:v['price_per_kg_n'] for v in source_config['fertilizer']['forms'].values()}
    years=list(range(inputs['planning_config']['start_year'],inputs['planning_config']['start_year']+inputs['planning_config']['years']))
    candidate_rows=[];field_rows=[];humic_rows=[];files={}
    def tracked(path):
        value=read(path);files[str(path.relative_to(run))]=hashlib.sha256(path.read_bytes()).hexdigest();return value
    for name in ('evaluations.json','baseline_score.json','optimization_summary.json','status.json','scenario_inputs.json','pareto.json'):
        tracked(run/name)
    for identifier,row in records.items():
        folder=run/'baseline' if identifier=='baseline' else run/'candidates'/identifier
        audit=tracked(folder/'audit.json');native=tracked(folder/'aquacrop/result.json')
        actual=tracked(folder/'simulation.json')
        near(sum(sum(v['harvested_dm_kg_ha'].values())*v['area_ha'] for v in native['fields']),row['yield_dm_kg'])
        near(sum(sum(v['harvested_dm_kg_ha'].values())*v['area_ha'] for v in actual['fields']),row['yield_dm_kg'])
        if len(actual['fields'])!=50:raise ValueError('Expected ten fields and five evaluated years')
        applications={}
        for event in audit['applications']:
            if event['year'] not in years:raise ValueError('Application outside evaluated years')
            applications.setdefault((event['field'],event['year']),[]).append(event)
        for value in row['fields']:
            events=applications.get((value['field'],value['year']),[]);area=value['area_ha']
            revenue=value['yield_dm_kg_ha']*area*economics['native_harvest_price_per_kg_dm'].get(value['crop'],0.)
            fertilizer=sum(e['dose_kg_n_ha']*prices[e['form']]*area for e in events)
            operations=len(events)*economics['application_cost_per_ha']*area
            fixed=economics['other_cost_per_ha_year']*area
            profit=revenue-fertilizer-operations-fixed
            near(profit,value['net_profit_proxy_per_ha']*area)
            field_rows.append({'candidate':identifier,**value,'yield_t':value['yield_dm_kg_ha']*area/1000,
                'revenue':revenue,'fertilizer_cost':fertilizer,'application_cost':operations,'fixed_cost':fixed,
                'net_profit':profit,'applied_n_kg':sum(e['dose_kg_n_ha']*area for e in events)})
        stocks=[]
        for field,stock in row['humic'].items():
            change=stock['end_kg_ha']-stock['start_kg_ha'];g=-change/max(abs(stock['start_kg_ha']),1.)
            near(stock['start_kg_ha'],audit['humic'][field]['start_kg_ha'])
            near(stock['end_kg_ha'],audit['humic'][field]['end_kg_ha'])
            stocks.append(g)
            humic_rows.append({'candidate':identifier,'field':field,**stock,'change_kg_c_ha':change,
                              'change_percent':-100*g,'violated':g>0})
        expected=sorted(stocks)
        for a,b in zip(expected,sorted(row.get('humic_constraints_G',row['G']))):near(a,b)
        if identifier!='baseline':
            values={'yield':-row['yield_dm_kg'],'profit':-row['net_profit_proxy'],
                    'mineral_n':row['mineral_n_shortfall'],'humic':max(stocks),
                    'n_input':row['fertilizer_n_kg'],'n_cost':row['fertilizer_cost_proxy']}
            for name,f in zip(inputs['objectives'],row['F']):
                if name in values:near(f,values[name])
        candidate_rows.append({'candidate':identifier,'yield_t':row['yield_dm_kg']/1000,
            'net_profit':row['net_profit_proxy'],'mineral_n_shortfall':row['mineral_n_shortfall'],
            'humic_loss_fields':sum(v>0 for v in stocks),'worst_humic_change_percent':-100*max(stocks),
            'crop_mix_violation_units':row.get('crop_mix',{}).get('range_violation_units',0),
            'cv':sum(max(v,0) for v in row['G']),'feasible':all(v<=0 for v in row['G']),
            'yield_change_vs_baseline_percent':100*(row['yield_dm_kg']/baseline['yield_dm_kg']-1),
            'profit_change_vs_baseline':row['net_profit_proxy']-baseline['net_profit_proxy']})
    candidates=pd.DataFrame(candidate_rows);ledger=pd.DataFrame(field_rows);humic=pd.DataFrame(humic_rows)
    annual=ledger.groupby(['candidate','year'],sort=False)[['yield_t','revenue','fertilizer_cost','application_cost','fixed_cost','net_profit','applied_n_kg']].sum().reset_index()
    totals=annual.groupby('candidate').sum(numeric_only=True)
    for identifier,row in records.items():near(totals.loc[identifier,'net_profit'],row['net_profit_proxy'])
    for key in ('revenue','fertilizer_cost','application_cost','fixed_cost','applied_n_kg'):
        candidates[key]=candidates['candidate'].map(totals[key])
    trials=candidates[candidates.candidate!='baseline'].copy()
    chosen=summary['selected_candidate'];max_yield=trials.loc[trials.yield_t.idxmax(),'candidate'];max_profit=trials.loc[trials.net_profit.idxmax(),'candidate']
    nonseed=trials[trials.candidate!=chosen];nearest=nonseed.loc[nonseed.cv.idxmin(),'candidate'] if len(nonseed) else chosen
    selected=list(dict.fromkeys(['baseline',chosen,max_yield,max_profit,nearest]))
    labels={'baseline':'База',chosen:'Минимум нарушений',max_yield:'Максимум урожая',max_profit:'Максимум экономики',nearest:'Следующий по CV'}
    # Objective-only comparisons are diagnostic; they never override feasibility.
    diagnostic=[r for r in successful if not any(all(a<=b for a,b in zip(s['F'],r['F'])) and any(a<b for a,b in zip(s['F'],r['F'])) for s in successful)]
    official=read(run/'pareto.json')
    near(len(evaluations),summary['evaluated']);near(sum(trials.feasible),summary['feasible']);near(len(official),summary['pareto_candidates'])
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    tables={'candidates':candidates,'annual_results':annual,'field_year_results':ledger,'humic_constraints':humic,
        'selected_candidates':candidates[candidates.candidate.isin(selected)],
        'selected_annual':annual[annual.candidate.isin(selected)],'selected_humic':humic[humic.candidate.isin(selected)]}
    crop_rows=[]
    for identifier in selected:
        sequences=records[chosen]['crop_sequences'] if identifier=='baseline' else records[identifier]['crop_sequences']
        crop_rows.extend({'candidate':identifier,'field':field,**dict(zip(map(str,years),seq))} for field,seq in sequences.items())
    tables['selected_crop_maps']=pd.DataFrame(crop_rows)
    for name,table in tables.items():table.to_csv(out/(name+'.csv'),index=False,encoding='utf-8-sig')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    plots=[]
    def finish(fig,name):
        fig.tight_layout();fig.savefig(out/(name+'.png'),dpi=160,bbox_inches='tight');plt.close(fig);plots.append(name)
    fig,ax=plt.subplots(figsize=(10,6));points=ax.scatter(trials.yield_t,trials.net_profit/1000,c=trials.cv,cmap='viridis',s=65)
    fig.colorbar(points,ax=ax,label='Сумма нормированных нарушений (CV)')
    for identifier in selected:
        row=candidates.set_index('candidate').loc[identifier]
        ax.scatter(row.yield_t,row.net_profit/1000,s=95,facecolors='none',edgecolors='black')
        ax.annotate(identifier,(row.yield_t,row.net_profit/1000),xytext=(-6 if identifier==max_yield else 5,7),
                    ha='right' if identifier==max_yield else 'left',textcoords='offset points',fontsize=8)
    ax.axhline(0,color='#777',lw=1);ax.set(xlabel='Урожай за 2025–2029, т сухой массы',ylabel='Расчётный результат, тыс. усл. руб.',title=f'Урожай и экономика: допустимых вариантов {int(trials.feasible.sum())} из {len(trials)}');finish(fig,'01_objectives')
    fig,axes=plt.subplots(1,2,figsize=(12,4.5));x=np.arange(1,len(trials)+1)
    axes[0].plot(x,trials.cv,'o',alpha=.5,label='Вариант');axes[0].plot(x,np.minimum.accumulate(trials.cv),label='Лучший CV к этому моменту')
    axes[0].set(yscale='log',xlabel='Номер оценённого варианта',ylabel='CV, логарифмическая шкала',title='Начальный вариант остался ближайшим к допустимости');axes[0].legend()
    axes[1].scatter(trials.crop_mix_violation_units,trials.humic_loss_fields,s=55,alpha=.65)
    axes[1].set(xlabel='Нарушения структуры посевов, полей × лет',ylabel='Полей со снижением Humic',title='Причины недопустимости; нужен ноль по обеим осям');finish(fig,'02_constraints')
    fig,axes=plt.subplots(2,1,figsize=(11,8),sharex=True)
    for identifier in selected:
        data=annual[annual.candidate==identifier]
        for ax,key,factor in [(axes[0],'yield_t',1),(axes[1],'net_profit',.001)]:ax.plot(data.year,data[key]*factor,'o-',label=f'{identifier}: {labels[identifier]}')
    axes[0].set(ylabel='Урожай, т сухой массы',title='По годам: диагностические варианты, не допустимые рекомендации')
    axes[0].legend(fontsize=8,ncol=2);axes[1].axhline(0,color='#777',lw=1);axes[1].set(ylabel='Результат, тыс. усл. руб.',xlabel='Год',xticks=years);finish(fig,'03_annual')
    fields=sorted(baseline['humic'],key=lambda f:int(f[5:]))
    matrix=humic[humic.candidate!='baseline'].pivot(index='candidate',columns='field',values='change_percent').reindex(index=trials.candidate,columns=fields)
    bound=max(abs(matrix.min().min()),abs(matrix.max().max()),.001)
    fig,ax=plt.subplots(figsize=(10,12));heat=ax.imshow(matrix,cmap='RdYlGn',norm=TwoSlopeNorm(vmin=-bound,vcenter=0,vmax=bound),aspect='auto')
    ax.set(xticks=range(len(fields)),xticklabels=fields,yticks=range(len(matrix)),yticklabels=matrix.index,title='Изменение Humic, % к собственному запасу на 31.12.2024')
    fig.colorbar(heat,ax=ax,label='%; отрицательное значение нарушает ограничение');finish(fig,'04_humic_all')
    data=humic[humic.candidate==chosen].set_index('field').reindex(fields)
    fig,ax=plt.subplots(figsize=(10,4));ax.bar(fields,data.change_kg_c_ha,color=['#b44d47' if v<0 else '#4c8b78' for v in data.change_kg_c_ha])
    ax.axhline(0,color='#555',lw=1);ax.set(ylabel='Изменение, кг C/га',title=f'{chosen}: Humic по полям за 2025–2029');finish(fig,'05_selected_humic')
    values=candidates.set_index('candidate').loc[selected]
    fig,ax=plt.subplots(figsize=(11,5));positions=np.arange(len(values));w=.18
    for i,key in enumerate(['revenue','fertilizer_cost','fixed_cost','net_profit']):ax.bar(positions+(i-1.5)*w,values[key]/1000,w,label={'revenue':'Выручка','fertilizer_cost':'Удобрения','fixed_cost':'Прочие постоянные затраты','net_profit':'Финансовый результат'}[key])
    ax.axhline(0,color='#555',lw=1);ax.set(xticks=positions,xticklabels=values.index,ylabel='Тыс. усл. руб.',title='Экономика за пять лет; внесение удобрений также учтено в итоге');ax.legend(fontsize=8);finish(fig,'06_economics')
    states=['spring_wheat','winter_rye','oats_with_underseed','perennial_grass_year1','perennial_grass_year2','perennial_grass_year3']
    short=['Пш','Рж','Ов+','Т1','Т2','Т3'];colors=['#e7c35f','#c28c4e','#82bd8a','#a4c8d9','#689eb5','#3f748e']
    fig,axes=plt.subplots(1,3,figsize=(14,6))
    for ax,identifier in zip(axes,[chosen,max_yield,max_profit]):
        seq=records[identifier]['crop_sequences'];codes=np.array([[states.index(v) for v in seq[field]] for field in fields])
        ax.imshow(codes,cmap=ListedColormap(colors),vmin=-.5,vmax=5.5,aspect='auto')
        for i in range(10):
            for j in range(5):ax.text(j,i,short[codes[i,j]],ha='center',va='center')
        ax.set(xticks=range(5),xticklabels=years,yticks=range(10),yticklabels=fields,title=f'{identifier}\n{labels[identifier]} — недопустим')
    fig.suptitle('Карты: Пш — яровая пшеница; Рж — аналог ржи; Ов+ — овёс с подсевом; Т1–Т3 — возраст трав',fontsize=10);finish(fig,'07_crop_maps')
    loss=data[data.change_kg_c_ha<0]
    findings={'source_run':str(run),'status':status['stage'],'objectives':inputs['objectives'],
        'elapsed_minutes':status['elapsed_seconds']/60,'evaluated':len(evaluations),'successful':len(successful),'feasible':int(trials.feasible.sum()),
        'selected':chosen,'max_yield':max_yield,'max_profit':max_profit,
        'objective_only_nondominated_diagnostic':[r['candidate_id'] for r in diagnostic],
        'crop_mix_compliant':list(trials.loc[trials.crop_mix_violation_units==0,'candidate']),
        'humic_compliant_count':int((trials.humic_loss_fields==0).sum()),
        'selected_loss_fields':loss.reset_index().to_dict('records'),
        'audited_input_sha256':files,'model_limits':inputs['configuration']['assumptions']}
    (out/'analysis.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2),encoding='utf-8')
    table=candidates[candidates.candidate.isin(selected)][['candidate','yield_t','net_profit','applied_n_kg','humic_loss_fields','crop_mix_violation_units','cv']]
    headings={'candidate':'Вариант','year':'Год','yield_t':'Урожай, т СМ','net_profit':'Результат, усл. руб.',
              'applied_n_kg':'Внесено N, кг','humic_loss_fields':'Полей с потерей Humic',
              'crop_mix_violation_units':'Нарушения структуры, полей × лет','cv':'CV',
              'revenue':'Выручка','fertilizer_cost':'Удобрения, затраты','application_cost':'Внесение, затраты',
              'fixed_cost':'Прочие затраты','feasible':'Допустим','mineral_n_shortfall':'Дефицит N, индекс',
              'worst_humic_change_percent':'Худший Humic, %','yield_change_vs_baseline_percent':'Урожай к базе, %',
              'profit_change_vs_baseline':'Результат к базе, усл. руб.'}
    def html_table(frame):return frame.rename(columns=headings).to_html(index=False,border=0,float_format=lambda v:f'{v:,.3f}')
    losses='; '.join(f'{field}: {row.change_kg_c_ha:.2f} кг C/га ({row.change_percent:.3f}%)' for field,row in loss.iterrows())
    body=f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>Анализ оптимизации AquaCrop</title>
<style>body{{font:16px/1.6 system-ui;background:#f0f4f6;color:#223744;margin:30px auto;max-width:1350px;padding:0 20px}}section{{background:white;border-radius:12px;padding:22px;margin:20px 0}}img{{width:100%}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:9px;border-bottom:1px solid #ddd;text-align:right}}td:first-child,th:first-child{{text-align:left}}.scroll{{overflow:auto}}.warning{{border-left:6px solid #ba5a45}}code{{overflow-wrap:anywhere}}</style>
<h1>AquaCrop: анализ завершённой оптимизации</h1><p>Новая модель; пример 2025–2029; NSGA-II, цели: {html.escape(', '.join(inputs['objectives']))}. Исторические погодные аналоги.</p>
<section class="warning"><h2>Допустимое решение не найдено</h2><p>{len(evaluations)} вариантов, {len(successful)} успешных расчётов, <b>{int(trials.feasible.sum())} допустимых</b>. Время: {status['elapsed_seconds']/60:.1f} мин. Официальный набор Парето пуст.</p>
<p>Только {chosen} соблюдает структуру посевов. Он выбран из-за минимального нарушения ограничений и совпадает с начальной картой. У него снижается Humic: {losses}. У всех 40 вариантов потери Humic на полях 3 и 6.</p>
<p>Положительная расчётная прибыль есть у двух вариантов, но они нарушают и структуру посевов, и сохранность Humic. Максимумы урожая и экономики ниже приведены для диагностики, а не как допустимые рекомендации.</p></section>
<section><h2>Сравнение вариантов</h2><p>baseline — начальная карта при исходном управлении; {chosen} — та же карта со схемой управления candidate_028. Урожай — суммарные тонны сухой массы по десяти полям за пять лет. net_profit — условные рубли за весь горизонт; CV — сумма нормированных нарушений.</p><div class="scroll">{html_table(table)}</div>
<p>При переходе от базы к {chosen} урожай меняется на {100*(records[chosen]['yield_dm_kg']/baseline['yield_dm_kg']-1):+.3f}%, финансовый результат — на {records[chosen]['net_profit_proxy']-baseline['net_profit_proxy']:+,.0f} усл. руб. Источник этого сравнения: одна карта, но разные параметры управления; сравнение с другими картами включает изменения структуры посевов.</p>
<p>Затраты на удобрения: {totals.loc['baseline','fertilizer_cost']:,.0f} → {totals.loc[chosen,'fertilizer_cost']:,.0f} усл. руб.; внесение азота: {totals.loc['baseline','applied_n_kg']:,.0f} → {totals.loc[chosen,'applied_n_kg']:,.0f} кг. Прямого отклика урожая AquaCrop на дозу N в этой связке нет: доза учитывается в почвенном расчёте APSIM и затратах, но не преобразуется в стресс плодородия AquaCrop.</p></section>
<section><h2>Таблица результатов по годам</h2><div class="scroll">{html_table(tables['selected_annual'])}</div></section>'''
    titles=['Урожай и экономика','Ограничения и ход поиска','Результаты по годам','Humic по всем вариантам','Humic выбранного варианта','Состав экономики','Сравнение карт посевов']
    for name,title in zip(plots,titles):body+=f'<section><h2>{title}</h2><img src="{name}.png" alt="{title}"></section>'
    body+=f'<section><h2>Все кандидаты</h2><input id="filter" placeholder="Поиск по номеру кандидата" oninput="document.querySelectorAll(\'#all tbody tr\').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase()))"><div class="scroll" id="all">{html_table(trials)}</div></section>'
    body+='<section><h2>Таблицы CSV</h2>'+ ' · '.join(f'<a href="{name}.csv">{name}</a>' for name in tables)+'</section>'
    body+='''<section><h2>Как понимать результат</h2><p>Урожай и выручка основаны на нативных уборках AquaCrop. Humic, минеральный азот и фактические внесения удобрений взяты из APSIM на той же карте и погоде; обратной связи биомассы AquaCrop в почвенный баланс нет. Профили AquaCrop — некалиброванные эффективные приближения. Выручка задана только для Wheat/Oats; корма имеют нулевую цену. Финансовый результат условный, без заданных различий стоимости семян.</p><p>Поиск менял последовательности культур; сорт, густота, глубина и удобрения оставались фиксированы выбранной схемой. 40 проверенных вариантов не доказывают невозможность допустимого плана. При увеличении бюджета стоит повышать долю карт, удовлетворяющих структуре посевов, и отдельно проверять варианты управления органическими остатками и удобрениями для полей 3 и 6. Автоматически ослаблять Humic или подменять начальные запасы по этому результату оснований нет.</p><p>Проведена сверка нативного урожая, экономики и запасов Humic по сохранённым результатам. Хеши входных файлов и происхождение: <a href="analysis.json">analysis.json</a>. Исходные расчёты не изменялись.</p></section></html>'''
    (out/'index.html').write_text(body,encoding='utf-8')
    
    print(json.dumps({k:v for k,v in findings.items() if k not in ('audited_input_sha256','model_limits','selected_loss_fields')},ensure_ascii=False,indent=2))
    print('REPORT: '+str(out/'index.html'))


if __name__=='__main__':main()
