"""Add explanatory charts to the existing night-analysis HTML without rerunning models."""
import os
from pathlib import Path
import re
import shutil
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'for_me/night_analysis'
OUT=ROOT/'runs/analysis/night_analysis'
OUT.mkdir(parents=True,exist_ok=True)
for source in SOURCE.glob('*.csv'):shutil.copy2(source,OUT/source.name)
comparison=pd.read_csv(SOURCE/'comparison.csv');annual=pd.read_csv(SOURCE/'annual.csv');quality=pd.read_csv(SOURCE/'quality.csv')
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'axes.spines.top':False,'axes.spines.right':False})
representatives=comparison.iloc[[0,2,4]]
labels=['APSIM','AquaCrop + APSIM','Чистый AquaCrop']
colors=['#446c87','#bf7f44','#598c76']
plots=[]
def save(fig,name,title,note):
    fig.tight_layout();fig.savefig(OUT/(name+'.png'),dpi=180,bbox_inches='tight');plt.close(fig)
    plots.append((name,title,note))

fig,ax=plt.subplots(figsize=(9,4.5))
values=representatives['Урожай т за 5 лет'].to_numpy();bars=ax.barh(labels,values,color=colors)
ax.bar_label(bars,fmt='%.2f',padding=8);ax.set(xlim=(0,250),xlabel='Тонны сухой массы по 10 полям за 2025–2029 годы',title='Урожай выбранного плана');ax.invert_yaxis()
save(fig,'01_selected_yield','Урожай выбранного плана','Пары NSGA-II и NSGA-III дали одинаковые выбранные планы, поэтому показаны три модели. APSIM и связка не прошли старый строгий запрет на потерю Humic. Общая сухая масса включает зерно и корма с разной ценой.')

fig,ax=plt.subplots(figsize=(10,5));x=np.arange(3);w=.24
for i,(column,title,color) in enumerate([('Выручка за 5 лет','Выручка','#64869d'),('Затраты за 5 лет','Затраты','#bf7f44'),('Прибыль за 5 лет','Прибыль','#598c76')]):
    vals=representatives[column].to_numpy()/50/1000
    b=ax.bar(x+(i-1)*w,vals,w,label=title,color=color);ax.bar_label(b,fmt='%.2f',padding=3,fontsize=10)
ax.axhline(0,color='#555',lw=1);ax.set(xticks=x,xticklabels=labels,ylabel='Тыс. условных рублей на гектар в год',title='Средние выручка затраты и прибыль');ax.legend(ncol=3)
save(fig,'02_economics_per_ha','Средняя экономика на гектар в год','Итоги поделены на 50 гектаро-лет: 10 га × 5 лет. Чистый AquaCrop использует другой состав затрат: 23 тыс. руб./га в год без отдельного счёта за удобрения APSIM. Положительная прибыль сама по себе не доказывает превосходства модели.')

fig,axes=plt.subplots(2,1,figsize=(10,7),sharex=True)
for (_,row),label,color in zip(representatives.iterrows(),labels,colors):
    data=annual[annual['Запуск']==row['Запуск']]
    axes[0].plot(data['Год'],data.yield_t,'o-',color=color,label=label)
    axes[1].plot(data['Год'],data.profit/10/1000,'o-',color=color,label=label)
axes[0].set(ylabel='Урожай по всем полям, т СМ',title='Результаты выбранной карты по годам');axes[0].legend(ncol=3,fontsize=10)
axes[1].axhline(0,color='#555',lw=1);axes[1].set(ylabel='Прибыль, тыс. усл. руб./га',xlabel='Сценарный год',xticks=range(2025,2030))
save(fig,'03_annual','Урожай и прибыль по годам','Это изменения в сценарии исторических погодных аналогов. Линии не являются доверительными интервалами или проверкой точности будущего прогноза. По каждому году финансовый результат поделён на площадь 10 га.')

short=['APSIM II','APSIM III','Связка II','Связка III','Чистый II','Чистый III']
fig,axes=plt.subplots(1,2,figsize=(12,4.7));times=comparison['Время мин'].to_numpy()
b=axes[0].barh(short,times,color=np.repeat(colors,2));axes[0].bar_label(b,fmt='%.1f',padding=4,fontsize=10);axes[0].invert_yaxis();axes[0].set(xlim=(0,110),xlabel='Минуты',title='Время полного поиска')
feasible=comparison['Допустимых'].to_numpy();successful=comparison['Успешно'].to_numpy()
axes[1].barh(short,successful-feasible,left=feasible,color='#d8a391',label='Нарушают ограничения');axes[1].barh(short,feasible,color='#598c76',label='Допустимы')
axes[1].invert_yaxis();axes[1].set(xlabel='Число из 40 кандидатов',title='Допустимость планов',xlim=(0,43));axes[1].legend(fontsize=9,loc='lower right')
for i,n in enumerate(feasible):axes[1].text(40.5,i,str(n),va='center',fontsize=10)
save(fig,'04_search_outcomes','Скорость и допустимость','У APSIM и связки было дополнительное ограничение сохранности Humic. У чистого AquaCrop его нет. Поэтому эти доли нельзя читать как рейтинг качества моделей. Цели NSGA-II и NSGA-III также различались.')

fig,ax=plt.subplots(figsize=(10,4.5));zero=quality['Нулевые уборки из 2000 поле лет'].to_numpy()
b=ax.bar(short,zero/2000*100,color=np.repeat(colors,2));ax.bar_label(b,labels=[f'{n}/2000' for n in zero],padding=5,fontsize=10)
ax.set(ylabel='Доля записей об уборках, %',ylim=(0,17),title='Нулевой урожай требует отдельной проверки')
save(fig,'05_zero_harvests','Нулевые уборки','Для каждого поиска проверено 40 вариантов × 10 полей × 5 лет = 2000 записей. Нулевая уборка не автоматически означает сбой программы. Причину нужно выяснять по культуре, календарю и состоянию растения. Это не MAE и не процент ошибки прогноза.')

fig,ax=plt.subplots(figsize=(10,4.5));water=quality.iloc[2:]['Макс невязка воды мм'].astype(float)
ax.bar(short[2:],water,color=['#bf7f44','#bf7f44','#598c76','#598c76']);ax.axhline(.01,color='#b44f43',ls='--',label='Допуск 0,01 мм')
ax.set(yscale='log',ylabel='Максимальная невязка, мм; логарифмическая шкала',title='Проверка водного баланса AquaCrop');ax.legend()
save(fig,'06_water_balance','Водный баланс','Проверено 1600 водных отчётов AquaCrop. Максимальные невязки порядка 10⁻¹³ мм существенно ниже допуска. Это подтверждает численную согласованность баланса, но не точность урожайности относительно наблюдений.')

block='<section id="night-charts"><h2>Графики ночного расчёта</h2><p><b>Эти результаты получены при прежнем строгом ограничении Humic.</b> Новое правило «Humic только как цель» к ним не применялось. По нему нужны новые запуски.</p>'
for name,title,note in plots:block+=f'<h3>{title}</h3><img src="{name}.png" alt="{title}" style="width:100%;max-width:1100px;display:block"><p>{note}</p>'
block+='</section>'
path=OUT/'index.html';page=(SOURCE/'index.html').read_text(encoding='utf-8')
page=re.sub(r'<section id="night-charts">.*?</section>','',page,flags=re.S)
position=page.find('<section>',page.find('</section>')+10)
page=page[:position]+block+page[position:] if position>=0 else page.replace('</html>',block+'</html>')
path.write_text(page,encoding='utf-8')
import json
(OUT/'charts.json').write_text(json.dumps(plots,ensure_ascii=False,indent=2),encoding='utf-8')
print('Added',len(plots),'charts:',path)
