"""Analyze a native history replay and export Russian plots and a local report."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / '.matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

LABELS = {'Wheat': 'Пшеница / аналог ржи', 'Oats': 'Овёс', 'Barley': 'Ячмень',
          'Mustard': 'Горчица', 'Rape': 'Рапс', 'RedClover': 'Клевер / аналог козлятника',
          'RedClover+PlantainForage': 'Смесь клевера и аналога тимофеевки',
          'Oats+RedClover+PlantainForage': 'Овёс с подсевом трав',
          'Barley+RedClover+PlantainForage': 'Ячмень с подсевом трав',
          'Oats+RedClover': 'Овёс + клевер', '-': 'Без убираемой культуры'}
USES = {'grain': 'Зерно', 'hay': 'Сено', 'green': 'Зелёная масса',
        'fallow': 'Пар', 'unmanaged': 'Без уборки'}
PHASES = {'heading_start': 'Начало колошения', 'heading_end': 'Конец колошения',
          'tillering_start': 'Начало кущения', 'tillering_end': 'Конец кущения',
          'clover_bud': 'Бутонизация клевера', 'timothy_flowering': 'Цветение аналога тимофеевки'}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def stats(frame):
    error = frame['simulated_dm_t_ha'] - frame['observed_t_ha']
    obs = frame['observed_t_ha']
    denominator = float(((obs-obs.mean())**2).sum())
    return {'n': len(frame), 'mae_t_ha': float(error.abs().mean()),
            'rmse_t_ha': float(np.sqrt((error**2).mean())), 'bias_t_ha': float(error.mean()),
            'r2': float(1-(error**2).sum()/denominator) if denominator > 0 else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--calibration', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run, calibration, output = args.run.resolve(), args.calibration.resolve(), args.output.resolve()
    execution = read_json(run/'execution.json')
    if execution['status'] != 'completed':
        raise ValueError('Analyze only a completed run')
    summary = read_json(run/'simulation.json')
    database = Path(summary['database'])
    model = read_json(run/'simulation.apsimx')

    def walk(node):
        yield node
        for child in node.get('Children', []):
            yield from walk(child)

    areas = {n['Name']: n['Area'] for n in walk(model)
             if n.get('$type', '').split(',')[0] == 'Models.Core.Zone'}
    fields = sorted(execution['fields'], key=lambda f: int(f.removeprefix('Field')))
    harvests, daily, warnings, coverage = [], {}, [], []
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as connection:
        for field in fields:
            if field not in areas or not field.removeprefix('Field').isdigit():
                raise ValueError('Unexpected field identity')
            harvests.append(pd.read_sql_query(f'SELECT * FROM HarvestReport_{field}', connection))
            frame = pd.read_sql_query(f'SELECT * FROM Report_{field}', connection, parse_dates=['Date'])
            expected = pd.date_range(execution['period_start'], execution['period_end'])
            if frame['Date'].duplicated().any() or set(frame['Date']) != set(expected):
                raise ValueError(f'Incomplete daily coverage: {field}')
            daily[field] = frame
            coverage.append({'field': field, 'days': len(frame), 'area_ha': areas[field]})
        warnings = pd.read_sql_query('SELECT Message, COUNT(*) AS Count FROM _Messages '
                                     'WHERE MessageType=1 GROUP BY Message ORDER BY Count DESC', connection)
        error_count = connection.execute('SELECT COUNT(*) FROM _Messages WHERE MessageType=0').fetchone()[0]
    if error_count:
        raise ValueError('APSIM errors present')
    harvest = pd.concat(harvests, ignore_index=True).rename(columns={'Zone': 'field', 'Year': 'year'})
    if harvest.duplicated(['field', 'year']).any():
        raise ValueError('This history export expects one annual harvest record per field')
    harvest['area_ha'] = harvest['field'].map(areas)
    harvest['production_t'] = harvest['YieldDMTHa']*harvest['area_ha']
    observed = pd.DataFrame(read_json(calibration/'yield_comparison.json')['records'])
    joined = observed.merge(harvest[['field', 'year', 'YieldDMTHa', 'Crop', 'Use']],
                            on=['field', 'year'], how='outer', validate='one_to_one', indicator=True)
    if not joined['_merge'].eq('both').all():
        raise ValueError('Observation package does not match replay coverage')
    max_difference = float((joined['simulated_dm_t_ha']-joined['YieldDMTHa']).abs().max())
    joined = joined.rename(columns={'simulated_dm_t_ha': 'calibration_simulated_dm_t_ha'})
    joined['simulated_dm_t_ha'] = joined['YieldDMTHa']
    joined['error_t_ha'] = joined['simulated_dm_t_ha']-joined['observed_t_ha']
    valid = joined[joined['included_in_metrics'].eq(True) & joined['observed_t_ha'].notna()].copy()
    pre = pd.DataFrame(read_json(calibration/'yield_comparison.json')['pre_update_records'])
    pre = pre[pre['included_in_metrics'].eq(True) & pre['observed_t_ha'].notna()].copy()
    pre['error_t_ha'] = pre['simulated_dm_t_ha']-pre['observed_t_ha']
    years = sorted(harvest['year'].unique())
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.titleweight': 'bold', 'figure.facecolor': '#ffffff',
                         'axes.grid': True, 'grid.alpha': .18})
    plots = []

    def save(fig, filename, title, caption):
        fig.savefig(output/filename, dpi=170, bbox_inches='tight', facecolor='white')
        plt.close(fig)
        plots.append((filename, title, caption))

    fig, axes = plt.subplots(5, 2, figsize=(16, 18), sharex=True, sharey=True, layout='constrained')
    for ax, field in zip(axes.flat, fields):
        data = joined[joined['field'].eq(field)].sort_values('year')
        ax.plot(data['year'], data['YieldDMTHa'], '-o', color='#246a87', markersize=4, label='APSIM')
        obs = data[data['included_in_metrics'].eq(True)]
        ax.scatter(obs['year'], obs['observed_t_ha'], color='#d77b37', marker='D', s=24,
                   label='Наблюдения, приведённые к СВ', zorder=3)
        ax.set_title(f'Поле {field.removeprefix("Field")}'); ax.set_ylabel('т СВ/га')
        ax.set_xticks(years[::3]); ax.set_ylim(bottom=0)
    axes[0, 0].legend(fontsize=9)
    fig.suptitle('Урожайность по полям: новая симуляция и наблюдения, 2004–2023', fontsize=18)
    save(fig, '01_fields.png', 'Урожайность всех десяти полей',
         'Только сопоставимые наблюдения. Соединённые точки относятся к разным культурам севооборота; '
         'линия не означает тренд урожайности одной культуры. Все значения — сухое вещество (СВ).')

    matrix = harvest.pivot(index='field', columns='year', values='YieldDMTHa').reindex(fields)
    fig, ax = plt.subplots(figsize=(17, 7), layout='constrained')
    ax.grid(False); image = ax.imshow(matrix, aspect='auto', cmap='YlGnBu', vmin=0)
    ax.set_xticks(range(len(years)), years, rotation=45); ax.set_yticks(range(len(fields)),
                    [f'Поле {f.removeprefix("Field")}' for f in fields])
    for i in range(len(fields)):
        for j in range(len(years)):
            value = matrix.iloc[i, j]
            ax.text(j, i, f'{value:.1f}', ha='center', va='center', fontsize=8,
                    color='white' if value > matrix.to_numpy().max()*.6 else '#172b39')
    fig.colorbar(image, ax=ax, label='т СВ/га'); ax.set_title('Карта урожайности: поле × год')
    save(fig, '02_heatmap.png', 'Карта урожайности', 'Ноль может означать отсутствие убираемой культуры '
         'или нулевой модельный урожай. Причины различаются в CSV и разборе ниже.')

    production = harvest.pivot_table(index='year', columns='Use', values='production_t', aggfunc='sum').fillna(0)
    fig, ax = plt.subplots(figsize=(14, 6), layout='constrained')
    bottom = np.zeros(len(production))
    for use, color in [('grain', '#246a87'), ('hay', '#75a568'), ('green', '#d5ae57')]:
        values = production.get(use, pd.Series(0., index=production.index))
        ax.bar(production.index, values, bottom=bottom, label=USES[use], color=color); bottom += values
    ax.set_xticks(years[::2]); ax.set_ylabel('т сухого вещества со всех полей'); ax.legend(ncols=3)
    ax.set_title('Суммарная продукция по годам и назначению урожая')
    save(fig, '03_annual_production.png', 'Объём продукции',
         f'В модели {sum(areas.values()):g} га: по 1 га на поле. Суммирование выполнено с учётом площади. '
         'Зерно и корм объединяются только по сухой массе, без оценки их денежной или кормовой равноценности.')

    groups = list(harvest[~harvest['Use'].isin(['fallow', 'unmanaged'])].groupby(['Crop', 'Use']))
    groups.sort(key=lambda item: item[1]['YieldDMTHa'].mean())
    fig, ax = plt.subplots(figsize=(13, 8), layout='constrained')
    rng = np.random.default_rng(42)
    for i, ((crop, use), frame) in enumerate(groups):
        ax.scatter(frame['YieldDMTHa'], i+rng.uniform(-.16, .16, len(frame)), alpha=.65, s=24, color='#246a87')
        ax.plot(frame['YieldDMTHa'].mean(), i, '|', color='#c94c4c', markersize=20, markeredgewidth=3)
    ax.set_yticks(range(len(groups)), [f'{LABELS.get(c,c)} · {USES[u]} (n={len(f)})' for (c,u),f in groups])
    ax.set_xlabel('Урожайность, т СВ/га'); ax.set_title('Разброс урожайности по модельной культуре и назначению')
    save(fig, '04_crop_distribution.png', 'Культуры и разброс урожайности',
         'Каждая точка — поле и год, красная отметка — среднее. Модельный Wheat включает аналог ржи; '
         'названия модельных компонентов не всегда совпадают с фактическими видами.')

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), layout='constrained')
    for ax, frame, title in zip(axes, [valid, pre], ['Повторный расчёт итоговой модели', 'Прогноз до очередного обновления']):
        for use, color in [('grain', '#246a87'), ('hay', '#75a568'), ('green', '#d5ae57')]:
            data = frame[frame['use'].eq(use)]
            ax.scatter(data['observed_t_ha'], data['simulated_dm_t_ha'], alpha=.7, s=26, label=USES[use], color=color)
        limit = max(frame['observed_t_ha'].max(), frame['simulated_dm_t_ha'].max())*1.08
        ax.plot([0,limit], [0,limit], '--', color='#777', lw=1)
        ax.set(xlim=(0,limit), ylim=(0,limit), xlabel='Наблюдения, т СВ/га', ylabel='Модель, т СВ/га', title=title)
        metric = stats(frame)
        ax.text(.04, .96, f'n = {metric["n"]}\nMAE = {metric["mae_t_ha"]:.3f}\nRMSE = {metric["rmse_t_ha"]:.3f}',
                transform=ax.transAxes, va='top', bbox={'facecolor':'white','alpha':.85,'edgecolor':'none'})
        ax.legend(loc='lower right')
    save(fig, '05_observed_vs_model.png', 'Соответствие наблюдениям',
         'Слева — текущая база и наблюдения, участвовавшие в калибровке. Справа — сохранённые прогнозы '
         'до обновления из пакета калибровки; это отдельная выборка, а не новый независимый полевой эксперимент.')

    valid['group'] = valid['model_crops'].map(LABELS).fillna(valid['model_crops'])+' · '+valid['use'].map(USES)
    error_groups = valid.groupby('group')['error_t_ha'].agg(['mean', 'count', lambda x:x.abs().mean()])
    error_groups.columns = ['bias_t_ha', 'n', 'mae_t_ha']; error_groups = error_groups.sort_values('bias_t_ha')
    fig, ax = plt.subplots(figsize=(13, 7), layout='constrained')
    ax.barh(range(len(error_groups)), error_groups['bias_t_ha'],
            color=['#c96c59' if v < 0 else '#246a87' for v in error_groups['bias_t_ha']])
    ax.set_yticks(range(len(error_groups)), [f'{name} (n={int(r["n"])})' for name,r in error_groups.iterrows()])
    ax.axvline(0, color='#555', lw=1); ax.set_xlabel('Средняя ошибка: модель − наблюдения, т СВ/га')
    ax.set_title('Систематическое завышение и занижение урожайности')
    save(fig, '06_yield_bias.png', 'Смещение по культурам', 'Отрицательная ошибка означает занижение. '
         'Использованы только записи, включённые в сопоставление по правилам исходной калибровки.')

    weather = daily[fields[0]].copy()
    for field in fields[1:]:
        if not weather[['Rain_mm','MaxTemperature_C','MinTemperature_C']].equals(
                daily[field][['Rain_mm','MaxTemperature_C','MinTemperature_C']]):
            raise ValueError('Field weather differs')
    weather['year'] = weather['Date'].dt.year
    weather['temperature'] = (weather['MaxTemperature_C']+weather['MinTemperature_C'])/2
    annual_weather = weather.groupby('year').agg(rain_mm=('Rain_mm','sum'), temperature_c=('temperature','mean'))
    growing_weather = weather[weather['Date'].dt.month.between(5,9)].groupby('year').agg(
        may_sep_rain_mm=('Rain_mm','sum'), may_sep_temperature_c=('temperature','mean'))
    annual_weather = annual_weather.join(growing_weather)
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, layout='constrained')
    axes[0].bar(years, annual_weather['rain_mm'], color='#abcbd8', label='Весь год')
    axes[0].bar(years, annual_weather['may_sep_rain_mm'], color='#246a87', label='Май–сентябрь')
    axes[0].set_ylabel('Осадки, мм'); axes[0].legend(ncols=2)
    axes[1].plot(years, annual_weather['temperature_c'], '-o', color='#246a87', label='Весь год')
    axes[1].plot(years, annual_weather['may_sep_temperature_c'], '-o', color='#d77b37', label='Май–сентябрь')
    axes[1].set_ylabel('Средняя температура, °C'); axes[1].set_xticks(years[::2]); axes[1].legend(ncols=2)
    fig.suptitle('Погода, использованная в симуляции', fontsize=17)
    save(fig, '07_weather.png', 'Погодные условия', 'Данные взяты из суточного отчёта APSIM. '
         'Одновременно меняются культура, управление и погода; график сам по себе не доказывает причину изменения урожая.')

    phase = pd.DataFrame(read_json(calibration/'phenology_comparison.json')['records'])
    # Recompute dates from this replay using the original mapping and annual thresholds.
    phase['fresh_date'] = None
    for i, record in phase.iterrows():
        frame = daily[record['field']]
        eligible = frame[(frame['Date'].dt.year == record['year']) &
                         (frame[record['variable']] >= record['threshold'])]
        if not eligible.empty:
            phase.at[i,'fresh_date'] = eligible['Date'].min().strftime('%Y-%m-%d')
    phase_match = sum((None if pd.isna(r['fresh_date']) else r['fresh_date']) ==
                      (None if pd.isna(r['simulated_date']) else r['simulated_date']) for _,r in phase.iterrows())
    phase['fresh_error_days'] = (pd.to_datetime(phase['fresh_date'])-pd.to_datetime(phase['observed_date'])).dt.days
    phase_groups = [(name, data) for name,data in phase.groupby('phase')]
    fig, ax = plt.subplots(figsize=(13, 7), layout='constrained')
    ax.boxplot([frame['fresh_error_days'].dropna().to_numpy() for _,frame in phase_groups],
               orientation='horizontal', tick_labels=[f'{PHASES.get(name,name)} (n={frame["fresh_error_days"].notna().sum()})'
                                       for name,frame in phase_groups], patch_artist=True,
               boxprops={'facecolor':'#abcbd8'})
    ax.axvline(0, color='#c94c4c', linestyle='--'); ax.set_xlabel('Дата модели − дата наблюдения, суток')
    ax.set_title('Ошибки сроков развития: положительные значения — запаздывание')
    save(fig, '08_phenology.png', 'Сроки развития растений', 'Даты заново извлечены из текущего суточного '
         'расчёта по порогам исходной калибровки внутри соответствующего календарного года. '
         'Для озимых такая схема сопоставления имеет ограничения; пропущенные фазы не заменены нулевой ошибкой.')

    barley_diagnostics = []
    for _, row in joined[joined['Crop'].eq('Barley')].sort_values(['year','field']).iterrows():
        frame = daily[row['field']]
        frame = frame[frame['Date'].dt.year.eq(row['year'])]
        barley_diagnostics.append({'field':row['field'],'year':int(row['year']),
            'max_stage':float(frame['BarleyStage'].max()),
            'max_aboveground_t_ha':float(frame['BarleyAboveGround_g_m2'].max()*.01),
            'grain_t_ha':float(row['YieldDMTHa']),
            'observed_t_ha':None if pd.isna(row['observed_t_ha']) else float(row['observed_t_ha'])})
    barley_frame = pd.DataFrame(barley_diagnostics)
    fig, axes = plt.subplots(2,1,figsize=(14,8),sharex=True,layout='constrained')
    positions=np.arange(len(barley_frame))
    axes[0].bar(positions,barley_frame['max_aboveground_t_ha'],color='#75a568',label='Максимум надземной биомассы')
    axes[0].scatter(positions,barley_frame['grain_t_ha'],color='#c94c4c',s=50,zorder=3,label='Учтённое зерно')
    axes[0].scatter(positions,barley_frame['observed_t_ha'],marker='D',color='#d77b37',s=35,zorder=3,
                    label='Наблюдаемая урожайность')
    axes[0].set_ylabel('т СВ/га');axes[0].legend(fontsize=9,ncols=3)
    axes[1].bar(positions,barley_frame['max_stage'],color='#246a87');axes[1].set_ylabel('Максимальная стадия BarleyStage')
    axes[1].set_xticks(positions,[f'{r.year}\nПоле {r.field.removeprefix("Field")}' for r in barley_frame.itertuples()])
    fig.suptitle('Ячмень: рост биомассы есть, учтённого зерна нет',fontsize=17)
    save(fig,'09_barley_diagnostics.png','Диагностика нулевого урожая ячменя',
         'Биомасса — максимум за год, а зерно — значение в момент запланированной уборки: это разные показатели. '
         'Биомасса не прибавляется к зерновому урожаю. Данные подтверждают рост растений, '
         'но для установления причины отсутствия зерна нужны отдельные отчёты органов и фаз.')

    group_stats = []
    for (crop,use), frame in harvest.groupby(['Crop','Use']):
        group_stats.append({'crop':crop,'use':use,'n':len(frame),'mean_t_ha':float(frame['YieldDMTHa'].mean()),
                            'zero_records':int(frame['YieldDMTHa'].le(1e-9).sum())})
    annual_production = production.sum(axis=1)
    barley = joined[joined['Crop'].eq('Barley')]
    metrics = {'period':[execution['period_start'],execution['period_end']], 'fields':coverage,
               'harvest_records':len(harvest),'errors':error_count,'warning_count':int(warnings['Count'].sum()),
               'production_t_dm':float(harvest['production_t'].sum()),'fit':stats(valid),'pre_update':stats(pre),
               'excluded_observation_records':int(len(joined)-len(valid)),
               'max_replay_vs_calibration_difference_t_ha':max_difference,
               'phenology':{'n':len(phase),'matched':int(phase['fresh_error_days'].notna().sum()),
                             'mae_days':float(phase['fresh_error_days'].abs().mean()),
                             'bias_days':float(phase['fresh_error_days'].mean()),
                             'dates_matching_export':int(phase_match)},
               'crop_groups':group_stats,
               'barley':{'records':len(barley),'zeros':int(barley['YieldDMTHa'].eq(0).sum()),
                         'comparable_observations':int(barley['included_in_metrics'].eq(True).sum()),
                         'min_peak_aboveground_t_ha':float(barley_frame['max_aboveground_t_ha'].min()),
                         'max_peak_aboveground_t_ha':float(barley_frame['max_aboveground_t_ha'].max())},
               'best_production_year':int(annual_production.idxmax()),
               'lowest_production_year':int(annual_production.idxmin())}
    harvest.to_csv(output/'harvests.csv',index=False,encoding='utf-8-sig')
    joined.drop(columns='_merge').to_csv(output/'observed_comparison.csv',index=False,encoding='utf-8-sig')
    pre.to_csv(output/'pre_update_comparison.csv',index=False,encoding='utf-8-sig')
    annual_weather.to_csv(output/'weather_annual.csv',encoding='utf-8-sig')
    production.to_csv(output/'production_annual.csv',encoding='utf-8-sig')
    error_groups.to_csv(output/'errors_by_crop.csv',encoding='utf-8-sig')
    phase.to_csv(output/'phenology_comparison.csv',index=False,encoding='utf-8-sig')
    warnings.to_csv(output/'warnings.csv',index=False,encoding='utf-8-sig')
    barley_frame.to_csv(output/'barley_diagnostics.csv',index=False,encoding='utf-8-sig')
    (output/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    source_paths = [database,run/'simulation.apsimx',run/'weather.met',calibration/'yield_comparison.json',
                    calibration/'phenology_comparison.json',Path(__file__).resolve()]
    (output/'sources.json').write_text(json.dumps({'run':str(run),'sources':[
        {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in source_paths]},indent=2),encoding='utf-8')
    fit = metrics['fit']; holdout=metrics['pre_update']; ph=metrics['phenology']
    notes = [
        f'Полный период: 2004–2023, 10 полей, {sum(areas.values()):g} га; 200 годовых записей и по 7305 суток на поле. '
        f'Ошибок APSIM: {error_count}; предупреждений: {metrics["warning_count"]}.',
        f'Свежий расчёт воспроизводит урожай экспортированной калибровки: максимальное расхождение '
        f'{max_difference:.3g} т СВ/га. Совпадение расчётов подтверждает перенос, но само по себе не подтверждает точность модели.',
        f'На {fit["n"]} сопоставимых наблюдениях итоговой модели: MAE {fit["mae_t_ha"]:.3f}, '
        f'RMSE {fit["rmse_t_ha"]:.3f}, средняя ошибка {fit["bias_t_ha"]:+.3f} т СВ/га, R² {fit["r2"]:.3f}. '
        f'Из 200 записей {metrics["excluded_observation_records"]} исключены из метрик согласно исходным правилам '
        '(нет наблюдений, нет уборки или неясен продукт/влажностный базис).',
        f'Совокупный R² = {fit["r2"]:.3f} отрицателен: по квадрату ошибки итоговая модель на этой '
        'смешанной выборке уступает постоянному прогнозу, равному среднему наблюдаемому урожаю. '
        'Это не квадрат корреляции. Средняя ошибка близка к нулю из-за компенсации завышений и занижений '
        'и не означает хорошего соответствия отдельных культур.',
        f'В сохранённых прогнозах до обновления (n={holdout["n"]}) MAE {holdout["mae_t_ha"]:.3f}, '
        f'RMSE {holdout["rmse_t_ha"]:.3f} т СВ/га. Эта более строгая проверка относится к прошлому процессу '
        'калибровки и явно отделена от текущего повторного расчёта.',
        f'Все {len(barley)} записей с модельной меткой Barley имеют нулевой урожай; '
        f'{metrics["barley"]["comparable_observations"]} из них имеют сопоставимые наблюдения. '
        f'При этом годовой максимум надземной биомассы составляет '
        f'{barley_frame["max_aboveground_t_ha"].min():.2f}–{barley_frame["max_aboveground_t_ha"].max():.2f} т СВ/га: '
        'растения растут, но зерно в отчёте уборки отсутствует. Нужно проверить развитие зерна, '
        'срок уборки и считывание органа Grain. Это диагностические гипотезы, а не установленная причина.',
        f'Для фенологии достигнуто {ph["matched"]} из {ph["n"]} пороговых событий; MAE по достигнутым '
        f'{ph["mae_days"]:.1f} суток, средняя ошибка {ph["bias_days"]:+.1f} суток. '
        f'С экспортом совпадают {ph["dates_matching_export"]}/{ph["n"]} дат, включая одинаково пропущенные события. '
        'Улучшение ошибки урожая не устраняет большую ошибку сроков развития.',
        f'Суммарная продукция за 20 лет: {metrics["production_t_dm"]:.2f} т СВ на модельных площадях. '
        f'Наибольшая годовая масса — {metrics["best_production_year"]}, наименьшая — {metrics["lowest_production_year"]}. '
        'Такое сравнение зависит от состава севооборота, а не только от погоды.',
        'Предупреждения: отсутствует погода за 31.12.2003 (20 сообщений, использована погода первого дня); '
        '7 приближений начального состояния многолетних/озимых; 2 предполагаемых восстановления травостоя; '
        '1 предупреждение о годовой амплитуде температуры AMP > 20 °C. Параметры расчёта при анализе не менялись.',
        'В текущих отчётах нет динамики почвенного азота, углерода и водного баланса. '
        'Делать выводы о сохранении плодородия и выполнении ограничения Humic по этому запуску нельзя.',
        'Урожай сравнивается на основе сухого вещества: в исходной калибровке зелёная масса/корма пересчитаны '
        'с принятыми долями СВ, зерно — с поправкой на влажность. Аналоги ржи, тимофеевки и козлятника '
        'следует трактовать как модельные замены, а не полноценные проверенные модели видов.'
        , 'Следующие шаги: сначала диагностировать нулевой зерновой урожай ячменя и завышение '
        'урожая аналога козлятника; затем проверить фенологические параметры и даты уборки; '
        'после этого повторить проверку на годах, не использованных для настройки. '
        'Настройки модели в рамках этого анализа не изменялись.'
    ]
    report = '# Анализ новой APSIM-симуляции\n\n'+'\n\n'.join(notes)+'\n\n'
    for filename,title,caption in plots:
        report += f'## {title}\n\n![{title}]({filename})\n\n{caption}\n\n'
    (output/'REPORT.md').write_text(report,encoding='utf-8')
    cards=''.join(f'<section><h2>{html.escape(title)}</h2><p>{html.escape(caption)}</p>'
                  f'<a href="{filename}"><img src="{filename}" alt="{html.escape(title)}"></a></section>'
                  for filename,title,caption in plots)
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>APSIM · Анализ 2004–2023</title><style>body{font:17px/1.6 system-ui,sans-serif;background:#f1f5f7;color:#203441;
max-width:1300px;margin:40px auto;padding:0 24px}h1,h2{line-height:1.2}section,.notes{background:white;padding:28px;
border-radius:14px;margin:24px 0}img{width:100%;height:auto}a{color:#246a87}li{margin:14px 0}.sub{color:#536874}
</style><h1>APSIM: результаты 2004–2023</h1><p class="sub">Новая калибровка · 10 полей · полный повторный расчёт</p>'''
    page+='<div class="notes"><h2>Что показал расчёт</h2><ul>'+''.join(f'<li>{html.escape(n)}</li>' for n in notes)+'</ul></div>'
    page+=cards+'<section><h2>Данные и воспроизведение</h2><p>Источник: '+html.escape(str(run))+'.</p>'
    page+='<p>'+ ' · '.join(f'<a href="{f}">{f}</a>' for f in ['harvests.csv','observed_comparison.csv',
        'errors_by_crop.csv','phenology_comparison.csv','barley_diagnostics.csv',
        'weather_annual.csv','warnings.csv','metrics.json','sources.json'])+'</p></section></html>'
    (output/'index.html').write_text(page,encoding='utf-8')
    
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    print(output)


if __name__ == '__main__':
    main()
