"""Create a read-only, auditable report of a completed native APSIM search."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
import numpy as np
import pandas as pd

POLICIES={'spring_wheat':'Яровая пшеница','oats_with_underseed':'Овёс, в том числе с подсевом',
          'winter_rye':'Аналог озимой ржи'}
COLORS=['#637782','#246a87','#d77b37','#7a9156','#956aa5']


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def close(a,b,label):
    if not math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-6):
        raise ValueError(f'Inconsistent {label}: {a} vs {b}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'runs/analysis/apsim_optimization')
    args=parser.parse_args()
    if args.run:
        run=args.run.resolve()
    else:
        completed=[p for p in (ROOT/'runs/apsim/native_multiyear').glob('optimize_*')
                   if (p/'optimization_summary.json').exists() and
                   read(p/'status.json')['stage'] in ('completed','no_feasible_solution')]
        if not completed: raise FileNotFoundError('No completed native optimization')
        run=max(completed,key=lambda p:p.name)
    summary=read(run/'optimization_summary.json');status=read(run/'status.json')
    if status['stage'] not in ('completed','no_feasible_solution'):
        raise ValueError('Search has not finished')
    config=read(run/'config_snapshot.json');baseline=read(run/'baseline_score.json')
    evaluations=read(run/'evaluations.json');official_front=read(run/'pareto.json')
    successful=[r for r in evaluations if r['status']=='ok']
    if not successful: raise ValueError('No successful candidates')
    close(len(evaluations),summary['evaluated_unique_candidates'],'evaluation count')
    years=config['evaluation_years']
    fields=sorted(baseline['humic'],key=lambda f:int(f.removeprefix('Field')))
    records={'baseline':baseline,**{r['candidate_id']:r for r in successful}}
    ledgers=[];humic_rows=[];policy_rows=[];application_rows=[];candidate_rows=[];audited_files=[]
    reference={(r['field'],r['year']):r for r in baseline['fields']}
    prices={r['apsim_type']:r['price_per_kg_n'] for r in config['fertilizer']['forms'].values()}

    for identifier,record in records.items():
        folder=run/'baseline' if identifier=='baseline' else run/'candidates'/identifier
        audit_path=folder/'audit.json';audit=read(audit_path);audited_files.append(audit_path)
        applications={}
        for event in audit['applications']:
            key=event['field'],event['year']
            item=applications.setdefault(key,{'cost':0.,'passes':0,'n':0.})
            item['cost']+=event['dose_kg_n_ha']*prices[event['form']]
            item['passes']+=1;item['n']+=event['dose_kg_n_ha']
            application_rows.append({'candidate_id':identifier,**event})
        checks=[]
        for field in sorted(record['humic']):
            stock=record['humic'][field]
            close(stock['start_kg_ha'],audit['humic'][field]['start_kg_ha'],'Humic start')
            close(stock['end_kg_ha'],audit['humic'][field]['end_kg_ha'],'Humic end')
            delta=stock['end_kg_ha']-stock['start_kg_ha']
            g=-delta/max(abs(stock['start_kg_ha']),1.)
            checks.append(g)
            humic_rows.append({'candidate_id':identifier,'field':field,**stock,'delta_kg_c_ha':delta,
                'delta_percent':100*delta/stock['start_kg_ha'],'g':g,'passed':g<=0})
        if len(checks)!=len(record['G']): raise ValueError('Wrong constraint count')
        for a,b in zip(checks,record['G']): close(a,b,'per-field constraint ordering')
        feasible=all(g<=0 for g in checks)
        if feasible != record['feasible']: raise ValueError('Wrong feasibility flag')
        subset=[]
        for row in record['fields']:
            key=row['field'],row['year'];base=reference[key]
            app=applications.get(key,{'cost':0.,'passes':0,'n':0.})
            area=row['area_ha'];mass=row['yield_dm_kg_ha']
            price=config['economics']['native_harvest_price_per_kg_dm'].get(row['crop'],0.)
            revenue=mass*price*area
            fertilizer=app['cost']*area
            passes=app['passes']*config['economics']['application_cost_per_ha']*area
            fixed=config['economics']['other_cost_per_ha_year']*area
            net=revenue-fertilizer-passes-fixed
            close(net,row['net_profit_proxy_per_ha']*area,'field economics')
            deficit=max(0.,base['mineral_n_end_kg_ha']-row['mineral_n_end_kg_ha'])/max(abs(base['mineral_n_end_kg_ha']),1e-9)
            subset.append({'candidate_id':identifier,**row,'production_t_dm':mass*area/1000,
                'revenue_proxy_rub':revenue,'fertilizer_cost_proxy_rub':fertilizer,
                'application_cost_proxy_rub':passes,'fixed_cost_proxy_rub':fixed,
                'net_profit_proxy_rub':net,'applied_n_kg':app['n']*area,
                'mineral_n_shortfall':deficit,'yield_change_kg_ha':mass-base['yield_dm_kg_ha'],
                'mineral_n_change_kg_ha':row['mineral_n_end_kg_ha']-base['mineral_n_end_kg_ha']})
        if len(subset)!=len(fields)*len(years) or len({(r['field'],r['year']) for r in subset})!=len(subset):
            raise ValueError('Incomplete field-year coverage')
        total=sum(r['production_t_dm'] for r in subset)
        net=sum(r['net_profit_proxy_rub'] for r in subset)
        n_shortfall=sum(r['mineral_n_shortfall'] for r in subset)
        close(total*1000,record['yield_dm_kg'],'total dry harvest')
        close(net,record['net_profit_proxy'],'total profit')
        close(n_shortfall,record['mineral_n_shortfall'],'N objective')
        for value,objective in zip([-total*1000,-net,n_shortfall],record['F']):close(value,objective,'objective sign')
        ledgers.extend(subset)
        candidate_rows.append({'candidate_id':identifier,'yield_t_dm':total,'net_profit_proxy_rub':net,
            'yield_change_percent':100*(total*1000/baseline['yield_dm_kg']-1),
            'profit_change_proxy_rub':net-baseline['net_profit_proxy'],
            'mineral_n_shortfall':n_shortfall,'humic_violation':sum(max(g,0.) for g in checks),
            'humic_loss_fields':sum(g>0 for g in checks),'worst_humic_change_percent':-100*max(checks),
            'feasible':feasible,'applied_n_kg':sum(r['applied_n_kg'] for r in subset),
            **{k:sum(r[k] for r in subset) for k in ['revenue_proxy_rub','fertilizer_cost_proxy_rub',
               'application_cost_proxy_rub','fixed_cost_proxy_rub']}})
        for policy,values in record.get('policies',{}).items():
            policy_rows.append({'candidate_id':identifier,'policy':policy,'crop_group':POLICIES[policy],
                'cultivar':values.get('cultivar') or 'historical',
                'population_m2':values['population'],'depth_mm':values['depth'],
                'row_spacing_mm':values['row_spacing'],'fertilizer_form':values['fertilizer']['apsim_type'],
                'dose_kg_n_ha':values['fertilizer']['dose_kg_n_ha']})

    candidates=pd.DataFrame(candidate_rows).set_index('candidate_id',drop=False)
    frame=candidates.drop(index='baseline');ledger=pd.DataFrame(ledgers);humic=pd.DataFrame(humic_rows)
    policies=pd.DataFrame(policy_rows);applications=pd.DataFrame(application_rows)
    feasible_ids=set(frame[frame['feasible']].index)
    f=np.array([records[i]['F'] for i in frame.index])
    objective_nd={identifier for j,identifier in enumerate(frame.index)
                  if not any(np.all(f[k]<=f[j]) and np.any(f[k]<f[j]) for k in range(len(f)) if k!=j)}
    expected_front={i for i in feasible_ids if not any(
        all(a<=b for a,b in zip(records[k]['F'],records[i]['F'])) and
        any(a<b for a,b in zip(records[k]['F'],records[i]['F'])) for k in feasible_ids if k!=i)}
    if {r['candidate_id'] for r in official_front}!=expected_front:raise ValueError('Incorrect saved Pareto front')
    candidates['objective_only_nondominated']=candidates['candidate_id'].isin(objective_nd)
    selected={'Максимум урожая':frame['yield_t_dm'].idxmax(),
              'Максимум расчётной прибыли':frame['net_profit_proxy_rub'].idxmax(),
              'Минимум дефицита N':frame['mineral_n_shortfall'].idxmin(),
              'Минимум нарушения Humic':frame['humic_violation'].idxmin()}
    selected_ids=list(dict.fromkeys(selected.values()));display_ids=['baseline']+selected_ids
    label={'baseline':'Базовое управление'}
    label.update({identifier:f'{identifier.removeprefix("candidate_")} · {role}' for role,identifier in selected.items()})
    short={i:('База' if i=='baseline' else i.removeprefix('candidate_')) for i in display_ids}
    colors=dict(zip(display_ids,COLORS))
    annual=ledger.groupby(['candidate_id','year'],as_index=False)[['production_t_dm','net_profit_proxy_rub','applied_n_kg']].sum()
    violations=humic[humic['candidate_id'].ne('baseline')].groupby('field').agg(
        failed_candidates=('passed',lambda s:int((~s).sum())),best_delta_percent=('delta_percent','max'),
        worst_delta_percent=('delta_percent','min')).reindex(fields)
    always_failing=list(violations[violations['failed_candidates'].eq(len(frame))].index)
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    plots=[]
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,
                         'axes.spines.right':False,'axes.titleweight':'bold','axes.grid':True,'grid.alpha':.18})

    def save(fig,name,title,caption):
        fig.savefig(output/name,dpi=170,bbox_inches='tight',facecolor='white');plt.close(fig)
        plots.append((name,title,caption))

    fig,ax=plt.subplots(figsize=(12,7),layout='constrained')
    points=ax.scatter(frame['yield_t_dm'],frame['net_profit_proxy_rub']/1000,
        c=frame['mineral_n_shortfall'],cmap='viridis_r',s=65,alpha=.85,edgecolors='white',linewidths=.6)
    fig.colorbar(points,ax=ax,label='Нормированный дефицит минерального N, меньше — лучше')
    ax.scatter(candidates.loc['baseline','yield_t_dm'],candidates.loc['baseline','net_profit_proxy_rub']/1000,
               marker='*',s=230,color=COLORS[0],label='База',edgecolor='white',zorder=5)
    for i in selected_ids:
        row=candidates.loc[i];ax.scatter(row['yield_t_dm'],row['net_profit_proxy_rub']/1000,
            s=180,facecolors='none',edgecolors=colors[i],linewidths=2,zorder=4)
        offset=(-35,-24) if i==selected['Минимум дефицита N'] else (12,14)
        ax.annotate(short[i],(row['yield_t_dm'],row['net_profit_proxy_rub']/1000),xytext=offset,
                    textcoords='offset points',fontsize=10,arrowprops={'arrowstyle':'-','color':colors[i],'lw':.8})
    ax.axhline(0,color='#777',lw=1,ls='--');ax.legend(loc='lower right')
    ax.set(xlabel='Собранная сухая масса за 2020–2023, т',ylabel='Расчётный результат, тыс. условных руб.',
           title='Компромисс урожая, экономики и азота')
    save(fig,'01_objectives.png','Три цели оптимизации',
         'Все 40 точек нарушают хотя бы одно ограничение Humic. Обведены отдельные диагностические лидеры; '
         'это не допустимые решения Парето. Прибыль во всех вариантах отрицательна.')

    ranked=frame.sort_values('yield_t_dm',ascending=False)
    fig,axes=plt.subplots(2,1,figsize=(17,9),sharex=True,layout='constrained')
    positions=np.arange(len(ranked));bar_colors=[colors.get(i,'#abc3cf') for i in ranked.index]
    axes[0].bar(positions,ranked['yield_t_dm'],color=bar_colors);axes[0].axhline(candidates.loc['baseline','yield_t_dm'],ls='--',color=COLORS[0],label='База')
    axes[0].set_ylabel('Сухая масса, т');axes[0].legend()
    axes[1].bar(positions,ranked['net_profit_proxy_rub']/1000,color=bar_colors)
    axes[1].axhline(candidates.loc['baseline','net_profit_proxy_rub']/1000,ls='--',color=COLORS[0])
    axes[1].set_ylabel('Результат, тыс. условных руб.');axes[1].set_xticks(positions,[i[-3:] for i in ranked.index],rotation=90)
    axes[1].set_xlabel('Номер кандидата; порядок по убыванию урожая')
    fig.suptitle('Все проверенные варианты: урожай и расчётная экономика',fontsize=17)
    save(fig,'02_candidate_ranking.png','Сравнение всех вариантов','Оба ряда показывают один и тот же порядок кандидатов. '
         'Все показанные кандидаты недопустимы по Humic; высокая урожайность не отменяет ограничений.')

    matrix=humic.pivot(index='candidate_id',columns='field',values='delta_percent').reindex(index=candidates.index,columns=fields)
    extent=max(abs(matrix.min().min()),abs(matrix.max().max()))
    fig,ax=plt.subplots(figsize=(12,18),layout='constrained');ax.grid(False)
    im=ax.imshow(matrix,aspect='auto',cmap='RdYlGn',norm=TwoSlopeNorm(vmin=-extent,vcenter=0,vmax=extent))
    ax.set_xticks(range(len(fields)),[f'Поле {f[5:]}' for f in fields],rotation=40,ha='right')
    ax.set_yticks(range(len(matrix)),['База' if i=='baseline' else i[-3:] for i in matrix.index],fontsize=9)
    for i in range(len(matrix)):
        for j in range(len(fields)):
            ax.text(j,i,f'{matrix.iloc[i,j]:+.2f}',ha='center',va='center',fontsize=7)
    ax.set_title('Изменение Humic на каждом поле: конец 2023 к концу 2019, %')
    fig.colorbar(im,ax=ax,label='Изменение собственного запаса Humic C, %',shrink=.6)
    save(fig,'03_humic_heatmap.png','Почему нет допустимых решений',
         'Красное — снижение Humic, зелёное — рост. Каждое поле должно иметь изменение ≥ 0. '
         'Даже небольшой отрицательный результат нарушает заданное строгое условие. '
         'На графике числа округлены; допустимость проверяется по полным значениям в таблицах.')

    fig,axes=plt.subplots(1,2,figsize=(14,6),layout='constrained')
    axes[0].barh([f'Поле {f[5:]}' for f in fields],violations['failed_candidates'],
                 color=['#c76453' if f in always_failing else '#246a87' for f in fields])
    axes[0].invert_yaxis();axes[0].set_xlabel('Кандидатов со снижением Humic из 40');axes[0].set_xlim(0,len(frame)+2)
    axes[0].set_title('Частота нарушения по полям')
    points=axes[1].scatter(frame['humic_loss_fields'],frame['humic_violation'],c=frame['yield_t_dm'],cmap='viridis',s=65)
    fig.colorbar(points,ax=axes[1],label='Сухая масса, т')
    axes[1].set(xlabel='Число полей со снижением Humic',ylabel='Сумма нормированных нарушений',title='Близость к допустимости')
    for i in selected_ids:
        row=candidates.loc[i]
        offsets={selected['Максимум урожая']:(15,-13),selected['Максимум расчётной прибыли']:(35,8),
                 selected['Минимум дефицита N']:(35,33),selected['Минимум нарушения Humic']:(20,0)}
        axes[1].annotate(short[i],(row['humic_loss_fields'],row['humic_violation']),
            xytext=offsets[i],textcoords='offset points',arrowprops={'arrowstyle':'-','color':'#637782','lw':.8})
    save(fig,'04_constraint_failures.png','Поля, ограничивающие поиск',
         'Поле считается нарушающим условие при любом снижении собственного Humic. '
         'Сумма нарушений — сумма положительных G, а не процент потери общего запаса углерода.')

    ordered=frame.copy();x=np.arange(1,len(ordered)+1)
    fig,axes=plt.subplots(3,1,figsize=(13,10),sharex=True,layout='constrained')
    for ax,column,title,unit,mode in [(axes[0],'yield_t_dm','Наибольший найденный урожай','т СВ','max'),
          (axes[1],'net_profit_proxy_rub','Наибольший расчётный результат','тыс. условных руб.','max'),
          (axes[2],'humic_violation','Наименьшее нарушение Humic','сумма положительных G','min')]:
        raw=ordered[column]/(1000 if column=='net_profit_proxy_rub' else 1)
        ax.scatter(x,raw,color='#abc3cf',s=20)
        ax.plot(x,raw.cummax() if mode=='max' else raw.cummin(),color='#246a87',lw=2)
        ax.set_title(title);ax.set_ylabel(unit)
    axes[-1].set_xlabel('Порядок расчёта уникальных кандидатов')
    save(fig,'05_search_progress.png','Ход поиска','Кривая — лучшее значение отдельного показателя среди уже '
         'проверенных вариантов. Разные кривые могут соответствовать разным решениям. Это не доказательство сходимости или глобального оптимума.')

    fig,axes=plt.subplots(2,1,figsize=(14,9),sharex=True,layout='constrained')
    x=np.arange(len(years));width=.8/len(display_ids)
    for n,i in enumerate(display_ids):
        part=annual[annual['candidate_id'].eq(i)].set_index('year').reindex(years)
        positions=x-.4+width*(n+.5)
        axes[0].bar(positions,part['production_t_dm'],width,color=colors[i],label=short[i])
        axes[1].bar(positions,part['net_profit_proxy_rub']/1000,width,color=colors[i])
    axes[0].set_ylabel('Сухая масса, т');axes[0].legend(title='Кандидат',ncols=len(display_ids))
    axes[1].set_ylabel('Результат, тыс. условных руб.');axes[1].set_xticks(x,years)
    fig.suptitle('База и диагностические лидеры по годам',fontsize=17)
    save(fig,'06_annual_comparison.png','Результаты по годам','Оценка относится к 2020–2023 годам на 10 модельных '
         'гектарах. Предшествующая история 2004–2019 формирует состояние модели; урожай за эти годы не входит в цели.')

    selected_frame=candidates.loc[display_ids]
    fig,axes=plt.subplots(2,1,figsize=(14,9),layout='constrained')
    x=np.arange(len(display_ids));bottom=np.zeros(len(display_ids))
    for key,name,color in [('fertilizer_cost_proxy_rub','Удобрения','#d77b37'),
            ('application_cost_proxy_rub','Внесение','#e5bd70'),('fixed_cost_proxy_rub','Прочие затраты','#aebcc3')]:
        values=selected_frame[key].to_numpy()/1000
        axes[0].bar(x+.18,values,.32,bottom=bottom,label=name,color=color);bottom+=values
    axes[0].bar(x-.18,selected_frame['revenue_proxy_rub']/1000,.32,color='#75a568',label='Выручка')
    axes[0].set_ylabel('Тыc. условных руб.');axes[0].legend(ncols=4,fontsize=9)
    axes[1].bar(x,selected_frame['net_profit_proxy_rub']/1000,color=[colors[i] for i in display_ids])
    axes[1].axhline(0,color='#555',lw=1);axes[1].set_ylabel('Итог, тыс. условных руб.')
    for ax in axes:ax.set_xticks(x,[short[i] for i in display_ids])
    axes[0].set_title('Выручка и структура затрат');axes[1].set_title('Расчётный результат: выручка минус затраты')
    save(fig,'07_economics.png','Почему экономика остаётся отрицательной',
         'Цены условные. Выручка задана только для убранного Wheat/Oats; кормовые и масличные не имеют цены. '
         'Затраты включают фактические внесения и 23 000 руб./га в год на всех полях. Это не оценка реальной рентабельности хозяйства.')

    by_field=ledger.groupby(['candidate_id','field'])['production_t_dm'].sum().unstack('field').reindex(columns=fields)
    delta=(by_field.loc[selected_ids]-by_field.loc['baseline']).T
    extent=max(abs(delta.min().min()),abs(delta.max().max()))
    fig,ax=plt.subplots(figsize=(12,8),layout='constrained');ax.grid(False)
    im=ax.imshow(delta,aspect='auto',cmap='RdBu',norm=TwoSlopeNorm(vmin=-extent,vcenter=0,vmax=extent))
    ax.set_yticks(range(len(fields)),[f'Поле {f[5:]}' for f in fields]);ax.set_xticks(range(len(selected_ids)),[short[i] for i in selected_ids])
    for i in range(len(fields)):
        for j in range(len(selected_ids)):
            value=delta.iloc[i,j];ax.text(j,i,f'{value:+.2f}',ha='center',va='center',color='white' if abs(value)>.6*extent else '#203441')
    ax.set_title('Изменение сухой массы относительно базы по полям');ax.set_xlabel('Номер кандидата')
    fig.colorbar(im,ax=ax,label='Изменение массы за четыре года, т СВ с поля')
    save(fig,'08_field_yield_changes.png','На каких полях изменился урожай',
         'Показана разность с базовым управлением за весь оцениваемый период. Каждое поле имеет площадь 1 га; '
         'рост урожая может сопровождаться нарушением Humic.')

    best_n=selected['Минимум дефицита N'];part=ledger[ledger['candidate_id'].eq(best_n)]
    n_matrix=part.pivot(index='field',columns='year',values='mineral_n_change_kg_ha').reindex(index=fields,columns=years)
    extent=max(abs(n_matrix.min().min()),abs(n_matrix.max().max()))
    fig,ax=plt.subplots(figsize=(10,8),layout='constrained');ax.grid(False)
    im=ax.imshow(n_matrix,aspect='auto',cmap='RdBu',norm=TwoSlopeNorm(vmin=-extent,vcenter=0,vmax=extent))
    ax.set_yticks(range(len(fields)),[f'Поле {f[5:]}' for f in fields]);ax.set_xticks(range(len(years)),years)
    for i in range(len(fields)):
        for j in range(len(years)):
            v=n_matrix.iloc[i,j];ax.text(j,i,f'{v:+.1f}',ha='center',va='center',color='white' if abs(v)>.6*extent else '#203441')
    ax.set_title(f'{best_n[-3:]}: минеральный азот на конец года относительно базы')
    fig.colorbar(im,ax=ax,label='Разность запасов минерального N, кг/га')
    save(fig,'09_mineral_n.png','Азот у варианта с наименьшим дефицитом',
         'Отрицательные ячейки дают вклад в третью цель. Положительные не компенсируют отрицательные. '
         'Базовый запас — модельный ориентир, а не подтверждённая агрономическая норма.')

    trajectory=[]
    for identifier in display_ids:
        folder=run/'baseline' if identifier=='baseline' else run/'candidates'/identifier
        with sqlite3.connect((folder/'candidate.db').as_uri()+'?mode=ro',uri=True) as connection:
            for field in fields:
                values=connection.execute(f"SELECT Date, HumicCKgHa FROM OptSoil_{field} WHERE Date LIKE '%-12-31%' ORDER BY Date").fetchall()
                origin=records[identifier]['humic'][field]['start_kg_ha']
                for date,value in values:
                    year=int(date[:4])
                    if min(years)-1 <= year <= max(years):
                        trajectory.append({'candidate_id':identifier,'field':field,'year':year,
                            'humic_c_kg_ha':value,'change_from_period_start_percent':100*(value/origin-1)})
    trajectories=pd.DataFrame(trajectory)
    if always_failing:
        fig,axes=plt.subplots(2,2,figsize=(14,9),sharex=True,layout='constrained')
        for ax,field in zip(axes.flat,always_failing[:4]):
            for identifier in display_ids:
                part=trajectories[trajectories['candidate_id'].eq(identifier)&trajectories['field'].eq(field)]
                ax.plot(part['year'],part['change_from_period_start_percent'],'-o',label=short[identifier],color=colors[identifier],markersize=4)
            ax.axhline(0,color='#555',ls='--',lw=1);ax.set_title(f'Поле {field[5:]}');ax.set_ylabel('Humic к концу 2019, %');ax.set_xticks([min(years)-1]+years)
        axes[0,0].legend(title='Кандидат',ncols=3,fontsize=9)
        fig.suptitle('Humic на полях, где все проверенные варианты нарушили условие',fontsize=16)
        save(fig,'10_humic_trajectories.png','Динамика Humic на ограничивающих полях',
             'Значения заново прочитаны из SQLite для базы и четырёх диагностических лидеров. '
             'Каждая траектория нормирована на собственный запас 31.12.2019. Условие оценивается по концу 2023 года.')

    expanded=candidates.loc[display_ids].copy()
    expanded['selection_reason']=[label[i].split(' · ',1)[-1] for i in display_ids]
    expanded['yield_change_t_dm']=expanded['yield_t_dm']-candidates.loc['baseline','yield_t_dm']
    expanded['total_cost_proxy_rub']=expanded[['fertilizer_cost_proxy_rub','application_cost_proxy_rub','fixed_cost_proxy_rub']].sum(axis=1)
    area_years=ledger[ledger['candidate_id'].eq('baseline')]['area_ha'].sum()
    expanded['annual_profit_per_ha_proxy_rub']=expanded['net_profit_proxy_rub']/area_years
    field_areas=ledger[ledger['candidate_id'].eq('baseline')].groupby('field')['area_ha'].first()
    for identifier in display_ids:
        stocks=humic[humic['candidate_id'].eq(identifier)].copy()
        stocks['area_ha']=stocks['field'].map(field_areas)
        expanded.loc[identifier,'humic_lost_kg_c']=float((-stocks['delta_kg_c_ha']).clip(lower=0).mul(stocks['area_ha']).sum())
        expanded.loc[identifier,'humic_gained_kg_c']=float(stocks['delta_kg_c_ha'].clip(lower=0).mul(stocks['area_ha']).sum())
        expanded.loc[identifier,'failed_humic_fields']=', '.join(f[5:] for f in fields if not bool(
            stocks.set_index('field').loc[f,'passed']))
    annual_selected=ledger[ledger['candidate_id'].isin(display_ids)].groupby(['year','candidate_id'],as_index=False)[
        ['production_t_dm','net_profit_proxy_rub','revenue_proxy_rub','fertilizer_cost_proxy_rub',
         'application_cost_proxy_rub','fixed_cost_proxy_rub','applied_n_kg','mineral_n_shortfall']].sum()
    annual_selected['total_cost_proxy_rub']=annual_selected[['fertilizer_cost_proxy_rub','application_cost_proxy_rub','fixed_cost_proxy_rub']].sum(axis=1)
    annual_base=annual_selected[annual_selected['candidate_id'].eq('baseline')].set_index('year')['production_t_dm']
    annual_selected['yield_change_t_dm']=annual_selected['production_t_dm']-annual_selected['year'].map(annual_base)
    annual_selected['selection_reason']=annual_selected['candidate_id'].map(expanded['selection_reason'])
    selected_policies=policies[policies['candidate_id'].isin(selected_ids)].copy()
    selected_policies['selection_reason']=selected_policies['candidate_id'].map(expanded['selection_reason'])
    selected_policies['cultivar']=selected_policies['cultivar'].replace({'historical':'Исторический по годам'})
    exports={'candidates.csv':candidates,'selected_candidates.csv':candidates.loc[display_ids],
        'management_policies.csv':policies,'field_year_results.csv':ledger,'humic_constraints.csv':humic,
        'annual_results.csv':annual,'fertilizer_applications.csv':applications,
        'constraint_failures_by_field.csv':violations.reset_index(),'humic_annual_selected.csv':trajectories,
        'selected_candidates_expanded.csv':expanded,'selected_annual_expanded.csv':annual_selected,
        'selected_management_policies.csv':selected_policies}
    for filename,data in exports.items():data.to_csv(output/filename,index=False,sep=';',encoding='utf-8-sig')
    improvements={role:{'candidate_id':identifier,**{key:(bool(value) if isinstance(value,(bool,np.bool_)) else
                int(value) if isinstance(value,np.integer) else float(value) if isinstance(value,np.floating) else value)
                for key,value in candidates.loc[identifier].to_dict().items()}} for role,identifier in selected.items()}
    result={'source_run':str(run),'status':summary['status'],'evaluated':len(evaluations),'successful':len(successful),
        'feasible':len(feasible_ids),'pareto':len(official_front),'evaluation_years':years,
        'duration_minutes':status['elapsed_seconds']/60,'always_failing_fields':always_failing,
        'positive_profit_candidates':int(frame['net_profit_proxy_rub'].gt(0).sum()),'selected':improvements,
        'objective_only_nondominated_candidates':sorted(objective_nd),'integrity_checks':'passed',
        'note':'Diagnostic leaders are not feasible recommendations. The objective-only front ignores Humic and is not the official Pareto result.'}
    write(output/'analysis_summary.json',result)
    sources=[run/n for n in ['evaluations.json','baseline_score.json','config_snapshot.json','optimization_summary.json',
                            'status.json','pareto.json','sources.json','evaluation_history.json']]+audited_files+[Path(__file__).resolve()]
    write(output/'sources.json',{'run':str(run),'files':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources]})
    notes=[
        f'Расчёт завершён за {status["elapsed_seconds"]/60:.1f} мин: {len(evaluations)} уникальных вариантов, '
        f'{len(successful)} успешных, {len(feasible_ids)} допустимых. Официальных решений Парето: {len(official_front)}.',
        'Весь период 2004–2023 рассчитывается внутри APSIM, но управление оптимизируется и цели суммируются за 2020–2023. '
        'Площадь — 10 модельных га, по 1 га на поле; базовый вариант рассчитан отдельно.',
        'Допустимых решений не найдено среди проверенных вариантов. Это не доказательство невозможности решения '
        'во всём пространстве параметров: поиск ограничен популяцией 10 и четырьмя поколениями.',
        f'На полях {", ".join(f[5:] for f in always_failing)} Humic снизился во всех {len(frame)} вариантах. '
        'Условие строгое: конец 2023 должен быть не ниже собственного конца 2019 на каждом поле. '
        'Положительные изменения на других полях не компенсируют снижение.',
        f'База: {baseline["yield_dm_kg"]/1000:.3f} т СВ, расчётный результат {baseline["net_profit_proxy"]:,.0f} условных руб. '
        f'Урожайный лидер {selected["Максимум урожая"]}: {frame.loc[selected["Максимум урожая"],"yield_t_dm"]:.3f} т '
        f'({frame.loc[selected["Максимум урожая"],"yield_change_percent"]:+.1f}% к базе), '
        f'но Humic снижается на {int(frame.loc[selected["Максимум урожая"],"humic_loss_fields"])} полях.',
        f'Наименьший расчётный убыток у {selected["Максимум расчётной прибыли"]}: '
        f'{frame.loc[selected["Максимум расчётной прибыли"],"net_profit_proxy_rub"]:,.0f} условных руб. '
        'Все 40 кандидатов убыточны в заданной системе цен и затрат. У кормов и масличных нет заданной выручки; '
        'эту условную экономику нельзя переносить на реальное хозяйство.',
        f'Ближе всего к выполнению Humic по сумме положительных G — {selected["Минимум нарушения Humic"]}; '
        f'снижение остаётся на {int(frame.loc[selected["Минимум нарушения Humic"],"humic_loss_fields"])} полях. '
        'Этот критерий близости к допустимости не превращает вариант в допустимое решение.',
        f'Минимальный дефицит азота среди кандидатов у {selected["Минимум дефицита N"]}: '
        f'{frame.loc[selected["Минимум дефицита N"],"mineral_n_shortfall"]:.4f}. '
        'Это сумма относительных дефицитов по полям и годам, не кг азота. База имеет ноль по определению.',
        'Проверены суммы урожая с учётом площади, экономические составляющие по фактическим внесениям, '
        'значения всех трёх целей, порядок десяти ограничений, исходные запасы Humic и сохранённый фронт Парето. '
        'Настройки модели и результаты расчёта не изменялись.',
        'Следующий обоснованный шаг — диагностировать баланс Humic на ограничивающих полях и проверить '
        'обоснованность строгого порога с учётом точности модели. Для расширения поиска можно отдельно исследовать '
        'число поколений и доступные меры управления; ослабление ограничения требует явного изменения постановки. '
        'Обнаруженные ранее ошибки калибровки и фенологии сохраняются.'
    ]
    make_html(output,run,candidates,policies,ledger,humic,selected,display_ids,label,plots,notes,exports)
    report='# Анализ многолетней оптимизации новой модели APSIM\n\n'+'\n\n'.join(notes)+'\n\n'
    report+='Открыть таблицы с сортировкой и выбором кандидата: [index.html](index.html).\n\n'
    for filename,title,caption in plots:report+=f'## {title}\n\n![{title}]({filename})\n\n{caption}\n\n'
    (output/'REPORT.md').write_text(report,encoding='utf-8')
    (output/'README.md').write_text('''# Просмотр результатов оптимизации APSIM

Откройте `index.html` или `index.html`. В отчёте есть сортировка таблицы кандидатов,
поиск по номеру, выбор варианта и подробные таблицы параметров, Humic и результатов
по полям и годам. Графики сохранены отдельно в PNG.

Результат этого запуска: 40 успешных кандидатов, 0 допустимых по Humic.
Диагностические лидеры не являются допустимыми рекомендациями.

## Таблицы CSV

Кодировка UTF-8 с BOM; разделитель `;`; десятичная точка. Для Excel используйте
импорт «Из текста/CSV» с этим разделителем. Русские заголовки и форматирование
доступны непосредственно в HTML-отчёте.

- `candidates.csv` — база и все кандидаты: цели, изменение к базе, ограничения, затраты.
- `selected_candidates.csv` — база и четыре диагностических лидера.
- `selected_candidates_expanded.csv` — расширенная сводка: прирост, выручка, все затраты, N и потери Humic.
- `selected_annual_expanded.csv` — расширенная сводка пяти вариантов отдельно за каждый год.
- `selected_management_policies.csv` — параметры всех трёх культур у четырёх диагностических лидеров.
- `management_policies.csv` — все 120 схем управления: сорт, густота, глубина, междурядье, N.
- `field_year_results.csv` — 1640 записей: поле, год, урожай, N и экономические составляющие.
- `humic_constraints.csv` — 410 проверок: собственный начальный и конечный запас Humic.
- `annual_results.csv` — масса, экономический результат и фактическое внесение N по годам.
- `fertilizer_applications.csv` — реальные события внесения из журнала APSIM.
- `constraint_failures_by_field.csv` — частота нарушения и диапазон изменений по полям.
- `humic_annual_selected.csv` — годовые запасы Humic из SQLite для базы и четырёх лидеров.

`baseline` — отдельный базовый расчёт; он не входит в 40 кандидатов NSGA-II.
`yield_t_dm` и `production_t_dm` — тонны сухого вещества, не зелёной массы.
`net_profit_proxy_rub` — условный экономический результат при ценах из конфига.
`mineral_n_shortfall` — безразмерная сумма относительных дефицитов.
`humic_violation` — сумма положительных G; это не процент потери общего углерода.
`objective_only_nondominated` игнорирует Humic и не является официальным фронтом Парето.

Точные источники и SHA-256 записаны в `sources.json`, выводы — в `REPORT.md`,
числовая сводка — в `analysis_summary.json`.

Повторить построение для последнего завершённого запуска из корня проекта:

```powershell
python scripts/analyze_native_optimization.py
```
''',encoding='utf-8')
    
    print(json.dumps(result,ensure_ascii=False,indent=2));print(output)


def make_html(output,run,candidates,policies,ledger,humic,selected,display_ids,label,plots,notes,exports):
    def number(value,digits=2):return f'{float(value):,.{digits}f}'.replace(',',' ').replace('.',',')
    columns=[('candidate_id','Кандидат'),('yield_t_dm','Урожай, т СВ'),('yield_change_percent','К базе, %'),
        ('net_profit_proxy_rub','Результат, усл. руб.'),('mineral_n_shortfall','Дефицит N'),
        ('humic_loss_fields','Полей с потерей Humic'),('humic_violation','Нарушение G+'),
        ('applied_n_kg','Внесено N, кг'),('feasible','Допустим')]
    def table(data,table_id,custom_columns=None):
        table_columns=custom_columns or columns
        text=f'<div class="scroll"><table id="{table_id}" class="sortable"><thead><tr>'
        text+=''.join(f'<th tabindex="0">{html.escape(spec[1])} ↕</th>' for spec in table_columns)+'</tr></thead><tbody>'
        for row in data.to_dict(orient='records'):
            identifier=row['candidate_id'];text+='<tr>'
            for spec in table_columns:
                key=spec[0]
                v=row[key]
                if key=='candidate_id':cell=f'<button class="candidate" data-candidate="{html.escape(identifier)}">{html.escape(identifier)}</button>'
                elif key=='feasible':cell='<span class="badge '+('ok' if v else 'bad')+'">'+('Да' if v else 'Нет')+'</span>'
                elif isinstance(v,str):cell=html.escape(v)
                else:cell=number(v,spec[2] if len(spec)>2 else 4 if key in ('humic_violation','mineral_n_shortfall') else 0 if key in ('humic_loss_fields','net_profit_proxy_rub') else 2)
                raw=identifier if key=='candidate_id' else str(int(v)) if key=='feasible' else str(v)
                text+=f'<td data-sort="{html.escape(raw)}">{cell}</td>'
            text+='</tr>'
        return text+'</tbody></table></div>'
    payload={'candidates':candidates.to_dict(orient='records'),'policies':policies.to_dict(orient='records'),
             'fields':ledger.to_dict(orient='records'),'humic':humic.to_dict(orient='records')}
    data=json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('</','<\\/')
    selected_rows=candidates.loc[display_ids].copy()
    styles='''<style>:root{--ink:#203441;--blue:#246a87}*{box-sizing:border-box}body{margin:0;background:#f2f5f7;color:var(--ink);font:16px/1.6 system-ui,sans-serif}
main{max-width:1480px;margin:32px auto;padding:0 24px}h1{font-size:36px;line-height:1.15}h2{font-size:25px;line-height:1.3}h3{font-size:19px}
section,.notice{background:#fff;border-radius:14px;padding:26px;margin:24px 0;border:1px solid #e0e7eb}.notice{border-left:6px solid #c76453;background:#fff7f3}
.sub{color:#5a6d78}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}.metric{background:white;padding:20px;border-radius:12px}.metric strong{font-size:30px;display:block}
.scroll{overflow:auto;max-height:650px}table{border-collapse:collapse;width:100%;font-size:14px;white-space:nowrap}th,td{text-align:right;padding:11px 13px;border-bottom:1px solid #e3e9ed}th:first-child,td:first-child{text-align:left}th{position:sticky;top:0;background:#eaf1f4;z-index:1;cursor:pointer}tr:hover{background:#f0f6f8}
.candidate{border:0;background:none;color:#176783;cursor:pointer;text-decoration:underline;font:inherit}.badge{display:inline-block;padding:2px 9px;border-radius:20px}.bad{color:#9b3c2e;background:#fdece7}.ok{color:#316b43;background:#e9f3ea}
img{max-width:100%;height:auto;display:block;margin:18px auto 0}a{color:#176783}nav a{display:inline-block;margin:4px 16px 4px 0}input,select{font:inherit;padding:9px 12px;border:1px solid #bccbd3;border-radius:7px;max-width:100%}li{margin:12px 0}.caption{color:#526873;font-size:15px}.download a{display:inline-block;margin:5px 14px 5px 0}
.detail-title{display:flex;justify-content:space-between;gap:16px;align-items:center;flex-wrap:wrap}@media print{body{background:white}section{break-inside:avoid}.scroll{max-height:none}nav,input,button{display:none}main{padding:0}img{max-height:850px;object-fit:contain}}</style>'''
    feasible=int(candidates.drop(index='baseline')['feasible'].sum())
    page='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>APSIM · Итоги оптимизации</title>'+styles+'<main>'
    page+='<h1>APSIM: итоги многолетней оптимизации</h1><p class="sub">Новая калибровка · цели за 2020–2023 · история 2004–2023 · 10 полей</p>'
    page+='<div class="metrics">'+''.join(f'<div class="metric">{title}<strong>{value}</strong></div>' for title,value in
        [('Рассчитано вариантов',len(candidates)-1),('Допустимых',feasible),('Решений Парето',0 if not feasible else 'см. источник'),('Графиков',len(plots))])+'</div>'
    page+='<div class="notice"><b>Допустимых решений не найдено.</b> Все проверенные варианты нарушают ограничение Humic. '
    page+='Показанные ниже лидеры служат для диагностики; они не являются допустимыми рекомендациями.</div>'
    page+='<nav><a href="#summary">Выводы</a><a href="#leaders">Лидеры</a><a href="#all">Все варианты</a><a href="#details">Параметры и поля</a><a href="#plots">Графики</a><a href="#data">Скачать таблицы</a></nav>'
    page+='<section id="summary"><h2>Что показал поиск</h2><ul>'+''.join(f'<li>{html.escape(n)}</li>' for n in notes)+'</ul></section>'
    expanded=exports['selected_candidates_expanded.csv']
    page+='<section id="leaders"><h2>Расширенные итоги за 2020–2023 гг.</h2><p>'+ '<br>'.join(f'<b>{html.escape(i)}</b>: {html.escape(label[i])}' for i in display_ids)+'</p>'
    page+='<h3>Урожай и экономический результат</h3><p class="caption">Итоги за четыре года со всех десяти полей. Денежные значения — условные рубли из конфигурации. Таблицы можно прокручивать по горизонтали и сортировать по заголовку.</p>'
    page+=table(expanded,'leaders-table',[
        ('candidate_id','Кандидат'),('yield_t_dm','Урожай, т СВ',2),('yield_change_t_dm','Прирост к базе, т СВ',2),
        ('yield_change_percent','Прирост к базе, %',1),('revenue_proxy_rub','Выручка, усл. руб.',0),
        ('total_cost_proxy_rub','Все затраты, усл. руб.',0),('net_profit_proxy_rub','Результат, усл. руб.',0),
        ('profit_change_proxy_rub','Улучшение к базе, усл. руб.',0),
        ('annual_profit_per_ha_proxy_rub','Средний результат, усл. руб./га·год',0)])
    page+='<h3>Затраты, азот и ограничения Humic</h3>'
    page+=table(expanded,'leaders-soil-table',[
        ('candidate_id','Кандидат'),('fertilizer_cost_proxy_rub','Удобрения, усл. руб.',0),
        ('application_cost_proxy_rub','Внесение, усл. руб.',0),('fixed_cost_proxy_rub','Прочие затраты, усл. руб.',0),
        ('applied_n_kg','Фактически внесено N, кг',0),('mineral_n_shortfall','Нормированный дефицит N',4),
        ('humic_loss_fields','Полей с потерей Humic',0),('failed_humic_fields','Номера этих полей'),
        ('worst_humic_change_percent','Худшее изменение Humic, %',3),
        ('humic_lost_kg_c','Потери на убывающих полях, кг C',2),
        ('humic_gained_kg_c','Прирост на растущих полях, кг C',2),('feasible','Допустим')])
    page+='<p class="caption">Дефицит N — сумма относительных дефицитов, не килограммы. Потери и прирост Humic посчитаны с учётом площадей относительно собственных запасов 31.12.2019; прирост на других полях не компенсирует нарушение.</p>'
    page+='<h3>Те же варианты отдельно по годам</h3>'
    page+=table(exports['selected_annual_expanded.csv'],'leaders-annual-table',[
        ('year','Год',0),('candidate_id','Кандидат'),('production_t_dm','Урожай, т СВ',2),
        ('yield_change_t_dm','Прирост к базе этого года, т СВ',2),('revenue_proxy_rub','Выручка, усл. руб.',0),
        ('total_cost_proxy_rub','Затраты, усл. руб.',0),('net_profit_proxy_rub','Результат, усл. руб.',0),
        ('applied_n_kg','Внесено N, кг',0),('mineral_n_shortfall','Нормированный дефицит N',4)])
    page+='<h3>Какие параметры выбраны</h3>'
    page+=table(exports['selected_management_policies.csv'],'leaders-management-table',[
        ('candidate_id','Кандидат'),('crop_group','Группа культур'),('cultivar','Сорт'),
        ('population_m2','Густота, растений/м²',0),('depth_mm','Глубина, мм',0),
        ('row_spacing_mm','Междурядье, мм',0),('fertilizer_form','Форма N'),('dose_kg_n_ha','Доза, кг N/га',0)])
    page+='<p class="caption">Для базы сохранены исторические параметры по полям и годам — единой схемы управления у неё нет. Дозы в таблице параметров заданы на гектар при внесении, а фактическое суммарное количество азота выше учитывает число внесений и площади.</p></section>'
    page+='<section id="all"><h2>Все 40 вариантов</h2><p>Нажмите заголовок для сортировки, код кандидата — для подробностей.</p><input id="filter" placeholder="Поиск по номеру кандидата…" aria-label="Поиск кандидата">'+table(candidates.drop(index='baseline'),'all-table')+'</section>'
    page+='<section id="details"><div class="detail-title"><h2>Подробности варианта</h2><select id="selection" aria-label="Выбор кандидата">'+''.join(f'<option value="{i}">{html.escape(i+" — "+label[i] if i in label else i)}</option>' for i in candidates.index)+'</select></div><div id="detail-metrics"></div><h3>Параметры управления</h3><div id="policy-table"></div><h3>Humic: 31.12.2019 → 31.12.2023</h3><div id="humic-table"></div><h3>Результаты по полям и годам</h3><div id="field-table"></div></section>'
    page+='<div id="plots">'+''.join(f'<section><h2>{html.escape(title)}</h2><p class="caption">{html.escape(caption)}</p><a href="{name}"><img loading="lazy" src="{name}" alt="{html.escape(title)}"></a></section>' for name,title,caption in plots)+'</div>'
    page+='<section id="data"><h2>Таблицы и источники</h2><p>CSV: UTF-8, разделитель — точка с запятой. В таблицах есть база и все кандидаты.</p><div class="download">'+''.join(f'<a download href="{name}">{name}</a>' for name in exports)+'</div><p><a href="analysis_summary.json">Сводка JSON</a> · <a href="sources.json">Источники и SHA-256</a> · <a href="REPORT.md">Текстовый отчёт</a></p><p>Источник: <code>'+html.escape(str(run))+'</code></p></section>'
    page+='<script type="application/json" id="data-json">'+data+'</script>'
    page+='''<script>
const data=JSON.parse(document.getElementById('data-json').textContent),sel=document.getElementById('selection');
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(x,d=2)=>Number(x).toLocaleString('ru-RU',{minimumFractionDigits:d,maximumFractionDigits:d});
function table(target,rows,cols){document.getElementById(target).innerHTML='<div class="scroll"><table><thead><tr>'+cols.map(c=>'<th>'+esc(c[1])+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+cols.map(c=>'<td>'+(c[2]===undefined?esc(r[c[0]]):num(r[c[0]],c[2]))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';}
function render(){const id=sel.value,row=data.candidates.find(r=>r.candidate_id===id);
document.getElementById('detail-metrics').innerHTML='<p><b>'+esc(id)+'</b> · '+num(row.yield_t_dm,3)+' т СВ · '+num(row.net_profit_proxy_rub,0)+' усл. руб. · Дефицит N '+num(row.mineral_n_shortfall,4)+' · Полей с потерей Humic: '+row.humic_loss_fields+' · <strong>'+(row.feasible?'Допустим':'Недопустим')+'</strong></p>';
const pp=data.policies.filter(r=>r.candidate_id===id).map(r=>({...r,cultivar:r.cultivar==='historical'?'Исторический по каждому году':r.cultivar}));
if(pp.length)table('policy-table',pp,[['crop_group','Культура'],['cultivar','Сорт'],['population_m2','Растений/м²',0],['depth_mm','Глубина, мм',0],['row_spacing_mm','Междурядье, мм',0],['fertilizer_form','Форма N'],['dose_kg_n_ha','Доза, кг N/га',0]]);
else document.getElementById('policy-table').innerHTML='<p>Исходные настройки новой калиброванной модели; общей политики оптимизатора нет. Все фактические внесения доступны в CSV.</p>';
const hh=data.humic.filter(r=>r.candidate_id===id).sort((a,b)=>Number(a.field.slice(5))-Number(b.field.slice(5))).map(r=>({...r,condition:r.passed?'Выполнено':'Нарушено'}));
table('humic-table',hh,[['field','Поле'],['start_kg_ha','Начало, кг C/га',2],['end_kg_ha','Конец, кг C/га',2],['delta_kg_c_ha','Изменение, кг C/га',2],['delta_percent','Изменение, %',3],['condition','Условие']]);
const ff=data.fields.filter(r=>r.candidate_id===id).sort((a,b)=>Number(a.field.slice(5))-Number(b.field.slice(5))||a.year-b.year);
table('field-table',ff,[['field','Поле'],['year','Год',0],['crop','Продукт модели'],['yield_dm_kg_ha','Урожай, кг СВ/га',1],['yield_change_kg_ha','К базе, кг СВ/га',1],['net_profit_proxy_rub','Результат, усл. руб.',0],['mineral_n_end_kg_ha','Мин. N, кг/га',2],['applied_n_kg','Внесено N, кг',0]]);}
sel.addEventListener('change',render);render();
document.addEventListener('click',e=>{const b=e.target.closest('[data-candidate]');if(b){sel.value=b.dataset.candidate;render();document.getElementById('details').scrollIntoView({behavior:'smooth'});}});
document.getElementById('filter').addEventListener('input',e=>document.querySelectorAll('#all-table tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(e.target.value.toLowerCase())));
document.querySelectorAll('table.sortable th').forEach(th=>{const sort=()=>{const t=th.closest('table'),index=[...th.parentElement.children].indexOf(th),asc=th.dataset.asc!=='true';th.dataset.asc=String(asc);
const rows=[...t.tBodies[0].rows];rows.sort((a,b)=>{const x=a.cells[index].dataset.sort,y=b.cells[index].dataset.sort;return (isNaN(Number(x))||isNaN(Number(y))?x.localeCompare(y):Number(x)-Number(y))*(asc?1:-1);});rows.forEach(r=>t.tBodies[0].appendChild(r));};th.addEventListener('click',sort);th.addEventListener('keydown',e=>{if(e.key==='Enter')sort();});});
</script></main></html>'''
    (output/'index.html').write_text(page,encoding='utf-8')


if __name__=='__main__':main()
