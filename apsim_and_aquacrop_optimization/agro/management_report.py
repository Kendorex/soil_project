"""Russian artifacts for the bounded native/hybrid management study."""
from __future__ import annotations

import csv
import html
import json
import os
from pathlib import Path


def generate(work, output):
    os.environ.setdefault('MPLCONFIGDIR', str(work/'.matplotlib'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    output.mkdir(parents=True, exist_ok=True)
    summaries = json.loads((work/'summary.json').read_text(encoding='utf-8'))
    domain = json.loads((work/'domain.json').read_text(encoding='utf-8'))
    validation = json.loads((work/'heldout_validation.json').read_text(encoding='utf-8'))
    baseline = json.loads((work/'baseline_score.json').read_text(encoding='utf-8'))
    native_baseline_path = work/'baseline_aquacrop_original/result.json'
    if not native_baseline_path.exists():
        from agro.adapters.aquacrop import AquaCropBackend
        root = Path(__file__).resolve().parents[1]
        AquaCropBackend(root/'models/aquacrop',
            [str(root/'engines/aquacrop/field-target/debug/aquacrop-field-runner.exe')],
            [2020,2021,2022,2023]).simulate({}, native_baseline_path.parent)
    native_baseline = json.loads(native_baseline_path.read_text(encoding='utf-8'))
    aq_index = {(f'Field{int(row["field_id"].rsplit("_",1)[1])}',row['year']):row
                for row in native_baseline['fields']}
    aqua_baseline_yield = sum(sum(row['harvested_dm_kg_ha'].values())*row['area_ha']
                              for row in native_baseline['fields'])
    aqua_baseline_profit = baseline['net_profit_proxy']
    for row in baseline['fields']:
        if row['crop'] in ('Wheat','Oats'):
            aq = aq_index[(row['field'],row['year'])]
            aqua_baseline_profit += (sum(aq['harvested_dm_kg_ha'].values())-row['yield_dm_kg_ha'])*14.3*row['area_ha']
    base_values = {'apsim': (baseline['yield_dm_kg'],baseline['net_profit_proxy']),
                   'aquacrop_apsim_response': (aqua_baseline_yield,aqua_baseline_profit)}
    engines = [('apsim', 'APSIM', '#246a87'),
               ('aquacrop_apsim_response', 'AquaCrop + отклик APSIM', '#d77b37')]
    all_rows, tables, selected = [], [], {}
    def number(value):
        return f'{value:,.2f}'.replace(',', ' ').replace('.', ',')
    for key, title, color in engines:
        rows = json.loads((work/key/'evaluations.json').read_text(encoding='utf-8'))
        feasible = [row for row in rows if row['feasible']]
        choice = max(feasible, key=lambda row: row['yield_dm_kg']) if feasible else summaries[key]['best_yield_without_constraints']
        selected[key] = choice
        for row in rows:
            decision = row['decisions']
            all_rows.append({'engine': key, 'candidate_id': row['candidate_id'],
                'fertilizer_form': decision['scheme']['form'], 'dose_kg_n_ha': decision['scheme']['dose_kg_n_ha'],
                'population_m2': json.dumps(decision['population_regime']),
                'depth_mm': json.dumps(decision['depth_regime']), 'yield_dm_kg': row['yield_dm_kg'],
                'net_profit_proxy': row['net_profit_proxy'], 'mineral_n_shortfall': row['mineral_n_shortfall'],
                'applied_n_kg': row['applied_n_kg'], 'feasible': row['feasible'],
                'humic_failed_fields': ','.join(row['failed_humic_fields'])})
        status = ('Есть допустимые решения' if feasible else
                  'Допустимых решений нет: ограничение по Humic нарушено на части полей')
        choice_label = 'Лучший допустимый по урожаю' if feasible else 'Максимальный урожай без учёта ограничений — не допустимое решение'
        policy = choice['decisions']
        baseline_mass = base_values[key][0]
        best_profit = max(rows,key=lambda row: row['net_profit_proxy'])
        least = summaries[key]['least_violation']
        matrix = ''.join(f'<tr><td>{html.escape(name)}</td><td>{value:g}</td>'
                         f'<td>{policy["depth_regime"][name]:g}</td></tr>'
                         for name, value in policy['population_regime'].items())
        tables.append(f'<section><h2>{title}</h2><p><b>{status}</b></p>'
            f'<p>Рассчитано вариантов: {len(rows)}. Допустимых: {len(feasible)}. '
            f'Решений Парето: {summaries[key]["pareto_candidates"]}.</p>'
            f'<p>Исходный урожай до оптимизации: {number(baseline_mass/1000)} т. '
            f'Изменение в показанном варианте: {number((choice["yield_dm_kg"]-baseline_mass)/1000)} т '
            f'({number(100*(choice["yield_dm_kg"]/baseline_mass-1))}%).</p>'
            f'<h3>{choice_label}</h3><p>Вариант <code>{choice["candidate_id"]}</code>; '
            f'собрано <b>{number(choice["yield_dm_kg"]/1000)} т сухой массы</b> за четыре года со всех десяти полей.</p>'
            f'<p>Удобрение: <b>{html.escape(policy["scheme"]["form"])}</b>, '
            f'<b>{policy["scheme"]["dose_kg_n_ha"]:g} кг N/га</b> при посеве оптимизируемых зерновых. '
            f'Расчётный экономический результат: {number(choice["net_profit_proxy"])} условных рублей.</p>'
            '<table><thead><tr><th>Группа культур</th><th>Густота, растений/м²</th><th>Глубина, мм</th></tr></thead>'
            f'<tbody>{matrix}</tbody></table><p>Поля с нарушением Humic: '
            f'{html.escape(", ".join(choice["failed_humic_fields"]) or "нет")}.</p>'
            f'<p>Максимальная прибыль без учёта ограничений: <code>{best_profit["candidate_id"]}</code>, '
            f'{number(best_profit["net_profit_proxy"])} условных руб. '
            f'Наименьшее нарушение Humic: <code>{least["candidate_id"]}</code>; '
            f'не прошли поля: {html.escape(", ".join(least["failed_humic_fields"]) or "нет")}.</p>'
            f'<p><a href="{key}_summary.json">Подробная сводка JSON</a></p></section>')
        (output/f'{key}_summary.json').write_text(json.dumps(summaries[key],ensure_ascii=False,indent=2),encoding='utf-8')
    with (output/'evaluations.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)
    field_rows = [{'engine': engine, 'candidate_id': row['candidate_id'], **field}
                  for engine, row in selected.items() for field in row['fields']]
    with (output/'selected_fields.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(field_rows[0]));writer.writeheader();writer.writerows(field_rows)
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False}):
        fig, axes = plt.subplots(1, 2, figsize=(15, 6.5))
        fig.subplots_adjust(top=.75, bottom=.18, left=.07, right=.98, wspace=.28)
        fig.suptitle('Результаты поиска по удобрениям, густоте и глубине', x=.07, ha='left', y=.96, fontsize=20, weight='bold')
        fig.text(.07,.88,'Показаны максимумы урожая в исследованной сетке. Допустимость по Humic указана в отчёте.',fontsize=11)
        for offset, (key, label, color) in enumerate(engines):
            values=[selected[key]['yield_dm_kg']/1000,selected[key]['net_profit_proxy']/1000]
            for i,(axis,value) in enumerate(zip(axes,values)):
                before = base_values[key][i]/1000
                previous=axis.bar([offset-.19],[before],color=color,alpha=.35,width=.35,
                                  label='До оптимизации' if offset==0 else None)
                axis.bar_label(previous,labels=[number(before)],padding=5,fontsize=9)
                rect=axis.bar([offset+.19],[value],color=color,width=.35,
                              label='После поиска' if offset==0 else None)
                axis.bar_label(rect,labels=[number(value)],padding=5)
        for axis in axes:
            axis.set_xticks(range(2),['APSIM','AquaCrop +\nотклик APSIM']);axis.grid(axis='y',alpha=.18);axis.set_axisbelow(True)
            lo,hi=axis.get_ylim();axis.set_ylim(lo-(hi-lo)*.08,hi+(hi-lo)*.14)
        axes[0].set_ylabel('Собранная сухая масса за 2020–2023, т')
        axes[1].set_ylabel('Экономический результат, тыс. условных руб.')
        axes[0].legend(frameon=False,fontsize=9)
        fig.text(.07,.055,'AquaCrop использует внешний отклик удобрений/глубины и почвенные показатели из APSIM; независимой проверкой моделей это не является.',fontsize=10)
        fig.savefig(output/'objectives.png',dpi=170);plt.close(fig)
        fig,axis=plt.subplots(figsize=(14,6.5))
        fig.subplots_adjust(top=.78,bottom=.20,left=.075,right=.98)
        for offset,(key,label,color) in enumerate(engines):
            values=[sum(row['yield_dm_kg_ha'] for row in selected[key]['fields']
                        if row['field']==f'Field{field}')/1000 for field in range(1,11)]
            bars=axis.bar(np.arange(10)+(offset-.5)*.38,values,width=.35,color=color,label=label)
            axis.bar_label(bars,labels=[number(v) for v in values],padding=3,fontsize=8)
        axis.set_xticks(np.arange(10),[f'Поле {field}' for field in range(1,11)],rotation=25)
        axis.set_ylabel('Сумма урожая за четыре года, т сухой массы/га')
        axis.grid(axis='y',alpha=.18);axis.set_axisbelow(True);axis.legend(frameon=False,ncol=2)
        lo,hi=axis.get_ylim();axis.set_ylim(0,hi*1.18)
        fig.suptitle('Десять полей: варианты с максимальным урожаем',x=.075,ha='left',y=.96,fontsize=21,weight='bold')
        fig.text(.075,.88,'Годы 2020–2023. Это сравнение вариантов с разными найденными параметрами управления.',fontsize=11)
        fig.text(.075,.045,'Если ограничение Humic не пройдено, эти варианты служат только диагностикой и не являются допустимыми решениями.',fontsize=10)
        fig.savefig(output/'fields.png',dpi=170);plt.close(fig)
    page=('''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оптимизация APSIM и AquaCrop</title><style>
body{font:16px/1.6 system-ui,sans-serif;background:#f2f5f7;color:#172b3a;margin:0}
main{max-width:1200px;margin:35px auto;padding:0 20px}section{background:white;border-radius:14px;padding:25px;margin:25px 0}
img{width:100%;height:auto}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd}
a{color:#246a87}code{overflow-wrap:anywhere}.note{background:#fff0db}</style></head><body><main>
<h1>Оптимизация управления: APSIM и AquaCrop</h1><section class="note">
<p><b>AquaCrop здесь — гибридный расчёт с внешним откликом, полученным из APSIM.</b>
Ядро AquaCrop рассчитывает влияние густоты и исходный урожай; поправка на удобрения и глубину взята из отдельных экспериментов APSIM.
Минеральный азот и Humic поступают из APSIM. Обратная связь изменённой биомассы AquaCrop с почвенным балансом не рассчитывается;
это внешние индикаторы управления APSIM. Это не независимая проверка AquaCrop и не новая встроенная модель азотного баланса.</p>
<p>Источник: <code>rotation_fixed.apsimx</code>. Все 10 полей, годы оценки 2020–2023; 2019 используется для формирования состояния.
Севооборот, даты и состав смесей сохранены. Оптимизируются пшеница (включая прокси ржи) и овёс; рапс, горчица и многолетние травы сохраняют исходное управление.</p>
<p>Исследовано <b>36 связанных режимов</b>: N0 либо 90/210 кг N/га для четырёх форм удобрения; два режима густоты и два режима глубины,
взятые с границ существующей конфигурации. Все сочетания измерены в APSIM. NSGA-II использует исходные настройки популяции, поколений и seed.
Найденные результаты относятся к этой конечной сетке, а не ко всем независимым комбинациям исходной большой задачи.</p>
<p>Цели: максимум сухой массы, максимум диагностической прибыли, минимум дефицита остаточного N относительно исходного расчёта.
Жёсткое ограничение: запас модельного Humic в конце периода не ниже собственного начального запаса на каждом поле.
Цены — исходные диагностические прокси; стоимость урожая рапса, горчицы и трав не задана и принята нулевой, как неподтверждённая.</p></section>'''
          +''.join(tables)+f'<section><h2>Проверка на отложенных расчётах APSIM</h2>'
          f'<p>Четыре дополнительных режима, 160 пар «поле — год», не использованы в поиске. '
          f'Проверяется линейная интерполяция отклика между обучающими дозами: MAE = {number(validation["mae_kg_ha"])} кг/га; '
          f'RMSE = {number(validation["rmse_kg_ha"])} кг/га.</p>'
          '<p>Это проверка переноса расчётного отклика APSIM, а не сверка с полевыми наблюдениями. Финальный поиск использует только реально измеренные узлы сетки.</p></section>'
          '<section><h2>Графики</h2><img src="objectives.png" alt="Цели оптимизации"><img src="fields.png" alt="Урожай по полям"></section>'
          '<section><h2>Файлы</h2><p><a href="evaluations.csv">Все варианты CSV</a> · <a href="selected_fields.csv">Поля и годы выбранных вариантов</a> · '
          '<a href="domain.json">Параметры исследования</a> · <a href="heldout_validation.json">Отложенная проверка</a></p>'
          f'<p>Полные расчёты: <code>{html.escape(str(work))}</code>.</p></section></main></body></html>')
    (output/'index.html').write_text(page,encoding='utf-8')
    for name in ('domain.json','heldout_validation.json','baseline_score.json'):
        (output/name).write_bytes((work/name).read_bytes())
    (output/'baseline_comparison.json').write_text(json.dumps({
        'apsim':{'yield_dm_kg':base_values['apsim'][0],'net_profit_proxy':base_values['apsim'][1]},
        'aquacrop':{'yield_dm_kg':aqua_baseline_yield,'net_profit_proxy':aqua_baseline_profit},
        'note':'Same original management; diagnostic prices and actual APSIM application costs',
    },indent=2),encoding='utf-8')
    (output/'selected_policies.json').write_text(json.dumps({key:{
        'classification':'maximum_yield_diagnostic' if not row['feasible'] else 'maximum_feasible_yield',
        'candidate_id':row['candidate_id'],'decisions':row['decisions'],
        'feasible':row['feasible'],'failed_humic_fields':row['failed_humic_fields'],
    } for key,row in selected.items()},ensure_ascii=False,indent=2),encoding='utf-8')
    if (work/'runtime.json').exists():
        (output/'runtime.json').write_bytes((work/'runtime.json').read_bytes())
    parent_index=output.parent/'index.html'
    if parent_index.exists():
        contents=parent_index.read_text(encoding='utf-8')
        if 'href="optimization/index.html"' not in contents:
            contents=contents.replace('</h1>','</h1><p><a href="optimization/index.html"><b>Результаты оптимизации: удобрения, густота и глубина посева</b></a></p>',1)
            parent_index.write_text(contents,encoding='utf-8')
    print(f'REPORT: {output / "index.html"}',flush=True)
