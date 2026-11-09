"""Assess observation-source sensitivity and summarize controlled APSIM probes."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sqlite3

from analyze_apsim_errors import ROOT, fingerprint, html_table, md_table, metrics, number, read_json, valid


def evaluate(label, rows, target, note):
    changed = [dict(r, observed_t_ha=target(r)) for r in rows]
    return {'label':label, **metrics(changed), 'note':note,
            'record_ids':[f"{r['field']}/{r['year']}" for r in changed]}


def final_grain_diagnostics(database, records):
    if not database.exists():
        return {'available':False}
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        harvested = {}
        if 'HarvestReport_Field1' in tables:
            for field in sorted({r['field'] for r in records}):
                harvested.update({(field,r['Year']):r['YieldDMTHa'] for r in connection.execute(f'SELECT * FROM HarvestReport_{field}')})
        else:
            harvested = {(r['Zone'],r['Year']):r['YieldDMTHa'] for r in connection.execute('SELECT * FROM HarvestReport')}
        assert set(harvested) == {(r['field'],r['year']) for r in records}
        difference = max(abs(harvested[(r['field'],r['year'])]-r['simulated_dm_t_ha']) for r in records)
        assert difference < 1e-8, 'Archive daily diagnostics do not match the current final-fit export'
        result = []
        for r in records:
            if not valid(r) or r['use']!='grain' or r['simulated_dm_t_ha']!=0 or r['observed_t_ha']<=0:
                continue
            plant = r['harvest_crops']
            table = 'Report_'+r['field']
            columns = {c[1] for c in connection.execute(f'PRAGMA table_info({table})')}
            wanted = [plant+'AboveGround_g_m2',plant+'Stage',plant+'Zadok']
            assert all(column in columns for column in wanted)
            query = 'SELECT '+', '.join('MAX("'+c+'")' for c in wanted)+f' FROM {table} WHERE Date>=? AND Date<?'
            peaks = connection.execute(query,(f"{r['year']}-03-01",r['harvest_date_actual']+' 23:59:59')).fetchone()
            result.append({'field':r['field'],'year':r['year'],'crop':r['crop'],'plant':plant,
                'observed_t_ha':r['observed_t_ha'],'grain_yield_t_ha':r['simulated_dm_t_ha'],
                'aboveground_peak_t_ha':peaks[0]*.01,'stage_peak':peaks[1],'zadok_peak':peaks[2],
                'harvest_date':r['harvest_date_actual']})
    return {'available':True,'database':str(database.resolve()),'database_sha256':fingerprint(database),
            'matched_harvest_records':len(harvested),'max_final_export_difference_t_ha':difference,'zero_grain_cases':result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'runs/analysis/apsim_sensitivity_20261106')
    parser.add_argument('--archive', type=Path, default=ROOT.parent/'calibrovka/history_final')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    obsfile = ROOT/'models/apsim/data/observations/new_calibr/yield_comparison.json'
    source_hash = fingerprint(obsfile)
    observations = read_json(obsfile)
    final_diagnostics = final_grain_diagnostics(args.archive/'rotation_2004_2023_calibrated_results.db',observations['records'])
    rows = [r for r in observations['pre_update_records'] if valid(r)]
    paired = [r for r in rows if r.get('pheno_average_t_ha') is not None]
    grain = [r for r in paired if r['use']=='grain']
    hay = [r for r in paired if r['use']=='hay']
    base = lambda r:r['observed_t_ha']
    same_fraction = lambda r:r['pheno_average_t_ha'] * r['dry_matter_fraction']
    forage_is_dry = lambda r:r['pheno_average_t_ha'] * (r['dry_matter_fraction'] if r['use']=='grain' else 1)
    source_scores = [
        evaluate('Основной источник, все наблюдения',rows,base,'Лист Урожайность. Официальный эталон проекта.'),
        evaluate('Основной источник, парная выборка',paired,base,'Только записи с числом в обоих листах.'),
        evaluate('Фенофазы, те же коэффициенты СВ',paired,same_fraction,'Зерно × 0,86, корм × 0,20. Диагностическая трактовка.'),
        evaluate('Фенофазы, корм уже в СВ',paired,forage_is_dry,'Зерно × 0,86, корм × 1. Альтернативная трактовка продукта.'),
        evaluate('Зерно, основной источник',grain,base,'Одинаковые 54 записи зерновых.'),
        evaluate('Зерно, Фенофазы',grain,same_fraction,'Та же влажность зерна 14%.'),
        evaluate('Корм, основной источник',hay,base,'Одинаковые 33 записи кормов.'),
        evaluate('Корм, Фенофазы × 0,20',hay,same_fraction,'Предполагается исходная свежая масса.'),
        evaluate('Корм, Фенофазы × 1',hay,forage_is_dry,'Предполагается исходная сухая масса.'),
    ]
    discrepancy = {}
    comparisons = []
    for r in grain:
        primary = r['source_yield_t_ha']
        secondary = r['pheno_average_t_ha']
        denominator = max(abs(primary),abs(secondary))
        relative = abs(primary-secondary)/denominator if denominator else 0
        key = (r['field'],r['year'])
        discrepancy[key] = relative
        comparisons.append({'field':r['field'],'year':r['year'],'primary_source_t_ha':primary,
            'secondary_source_t_ha':secondary,'relative_difference':relative,
            'primary_dm_t_ha':r['observed_t_ha'],'secondary_dm_t_ha':same_fraction(r),
            'prediction_t_ha':r['simulated_dm_t_ha'], 'pheno_cell':'Фенофазы!F'+str(r['pheno_row'])})
    exclusions = []
    for cutoff in [.10,.25,.50]:
        excluded = {key for key,value in discrepancy.items() if value>cutoff}
        retained = [r for r in rows if (r['field'],r['year']) not in excluded]
        result = evaluate(f'Без зерновых с расхождением листов > {cutoff:.0%}',retained,base,
            'Порог исследовательский. Основан на различии исходных листов до учета ошибки модели.')
        result['cutoff'] = cutoff
        result['excluded_records'] = [r for r in comparisons if (r['field'],r['year']) in excluded]
        result['excluded_count'] = len(excluded)
        exclusions.append(result)
    dm_scores = []
    for fraction in [.15,.20,.25]:
        target = lambda r:r['source_yield_t_ha']*fraction if r['use'] in ['hay','green'] else r['observed_t_ha']
        dm_scores.append(evaluate(f'СВ кормов и зеленой массы {fraction:.0%}',rows,target,
            'Фиксированные прогнозы. Меняется только коэффициент перевода наблюдений.'))
    results_path = out/'mechanism_results.json'
    mechanism = read_json(results_path) if results_path.exists() else None
    probe_rows = []
    peaks = []
    names = {'baseline':'Исходное правило','underseed_30_days':'Подсев через 30 дней',
        'underseed_density_25pct':'Густота подсева 25%', 'no_spring_reset':'Без весеннего сброса фазы',
        'spring_reset_may':'Сброс фазы 1 мая','barley_final_phenology':'Итоговая фенология ячменя'}
    if mechanism:
        baselines = {r['group']['id']:r for r in mechanism['results'] if r['variant']=='baseline'}
        for result in mechanism['results']:
            baseline = baselines[result['group']['id']]
            baseline_yields = {r['year']:r['prediction_t_ha'] for r in baseline['predictions']}
            for row in result['predictions']:
                probe_rows.append({'group':result['group']['id'],'variant':result['variant'],
                    'label':names[result['variant']],**row,
                    'difference_from_control_t_ha':row['prediction_t_ha']-baseline_yields[row['year']]})
            for peak in result['grain_peaks']:
                if peak['peak_grain_t_ha']>0:
                    peaks.append({'group':result['group']['id'],'variant':result['variant'],**peak})
    conclusions = [
        'Основной эталон закреплен за листом Урожайность. На одинаковых 87 записях MAE меняется с '+number(source_scores[1]['mae_t_ha'])+' до '+number(source_scores[2]['mae_t_ha'])+' т СВ/га при использовании Фенофаз с теми же коэффициентами.',
        'Исключение пяти зерновых записей с расхождением источников больше 50% меняет общую MAE с '+number(source_scores[0]['mae_t_ha'])+' до '+number(exclusions[2]['mae_t_ha'])+' т СВ/га. Остальные ошибки требуют разбора модели.',
        'Трактовка кормового урожая в Фенофазах существенно влияет на оценку. Для 33 кормовых записей MAE составляет '+number(source_scores[7]['mae_t_ha'])+' при коэффициенте 0,20 и '+number(source_scores[8]['mae_t_ha'])+' при трактовке уже сухой массы. Выбор трактовки требует сведений о методике измерения.',
    ]
    if mechanism:
        probe_index = {(r['group'],r['variant'],r['year']):r for r in probe_rows}
        def prediction(group, variant, year):
            return number(probe_index[(group,variant,year)]['prediction_t_ha'])
        conclusions += [
            'Для овса 2022 года контрольный урожай '+prediction('oats_2022','baseline',2022)+' т СВ/га. Перенос подсева на 30-й день дает '+prediction('oats_2022','underseed_30_days',2022)+', снижение густоты подсева до четверти дает '+prediction('oats_2022','underseed_density_25pct',2022)+'. В исходном расчете пик зерна до уборки совпадает с собранным урожаем.',
            'Для трав третьего года на поле 6 отключение весеннего сброса в 2012 году меняет урожай с '+prediction('grass3_wheat_2012_2013','baseline',2012)+' до '+prediction('grass3_wheat_2012_2013','no_spring_reset',2012)+' т СВ/га при наблюдении 0,459. Пшеница следующего года меняется с '+prediction('grass3_wheat_2012_2013','baseline',2013)+' до '+prediction('grass3_wheat_2012_2013','no_spring_reset',2013)+' при наблюдении 1,268. Это подтверждает влияние правила через многолетнее состояние модели.',
            'Перенос весеннего сброса на первое мая дает небольшое изменение по сравнению с исходным правилом. У козлятника отключение сброса в 2010 году снижает урожай с '+prediction('galega_2010_2011','baseline',2010)+' до '+prediction('galega_2010_2011','no_spring_reset',2010)+' при наблюдении 1,535. Профиль козлятника и исходное состояние остаются отдельными направлениями проверки.',
        ]
        if ('barley_2007','barley_final_phenology',2007) in probe_index:
            conclusions.append('Замена только шести параметров фенологии ячменя на значения итоговой калибровки меняет зерно 2007 года с '+prediction('barley_2007','baseline',2007)+' до '+prediction('barley_2007','barley_final_phenology',2007)+' т СВ/га. В итоговой истории девять записей ячменя, включая подсев, имеют нулевое зерно при ненулевых наблюдениях. Приоритетная правка процедуры калибровки — контроль прохождения репродуктивных фаз и качества отдельно по культурам.')
    conclusions.append('Проверки механизмов локальные и используют архивные параметры и площадь 1 га на поле для точного воспроизведения исходных прогнозов. Диагностические изменения требуют проверки по остальным годам перед включением в рабочую модель.')
    source_policy = {
        'primary_yield_source':'Урожайность',
        'aggregation':'Arithmetic mean of available spatial points by field and year. Zero retained as observation.',
        'units':'Point values in c/ha divided by 10, then converted to dry matter.',
        'grain_dry_fraction':.86,'forage_and_green_dry_fraction':.20,
        'secondary_yield_source':'Фенофазы column F used for sensitivity analysis only.',
        'missing_secondary_policy':'Missing comparison stays missing. All source rows retained in primary evaluation.',
        'exclusion_policy':'Diagnostic subsets only. No records removed from the primary calibration target.',
    }
    summary = {'source_policy':source_policy,'observation_sha256':source_hash,
        'source_sensitivity':source_scores,'exclusion_sensitivity':exclusions,
        'dry_matter_sensitivity':dm_scores,'grain_source_comparisons':comparisons,
        'mechanism_probes_completed':mechanism['completed'] if mechanism else 0,
        'mechanism_yields':probe_rows,'grain_peaks':peaks,'final_grain_diagnostics':final_diagnostics,
        'conclusions':conclusions,'method_notes':[
            'All source-sensitivity metrics score the same archived pre-update predictions. No refit is performed.',
            'Compare source variants on matching record IDs. Exclusion subsets change the sample.',
            'Forage basis on the secondary sheet is ambiguous, so two explicitly labeled interpretations are scored.',
            'Experimental management perturbations are diagnostic scenarios. Their agronomic feasibility is not established.',
        ]}
    headers = ['Оценка','Число','MAE т СВ/га','RMSE т СВ/га','Смещение т СВ/га']
    def score_table(scores):
        return [[r['label'],r['n'],number(r['mae_t_ha']),number(r['rmse_t_ha']),number(r['bias_t_ha'],True)] for r in scores]
    source_table = score_table(source_scores)
    exclusion_table = score_table(exclusions)
    dm_table = score_table(dm_scores)
    intro = [
        'Основным источником урожая остается лист «Урожайность». Данные «Фенофаз» используются для проверки чувствительности оценки.',
        'Проверены 130 замороженных прогнозов за следующие годы. Во всех оценках использованы т сухого вещества на гектар. Положительное смещение означает завышение.',
        'Оба листа содержат числовой урожай для 87 проверочных записей, включая 54 зерновые и 33 кормовые. Для кормов отдельно рассмотрены две трактовки массы.',
        'В таблицах меняется эталон сравнения при фиксированных прогнозах. Результат показывает зависимость оценки от исходных данных. Контрольные симуляции механизмов приведены отдельным разделом.',
    ]
    body = '<h1>Чувствительность оценки APSIM и проверка механизмов</h1>'+''.join('<p>'+html.escape(t)+'</p>' for t in intro)
    md = ['# Чувствительность оценки APSIM и проверка механизмов',*intro]
    body += '<h2>Результаты простыми словами</h2><ul>'+''.join('<li>'+html.escape(t)+'</li>' for t in conclusions)+'</ul>'
    md += ['## Результаты простыми словами',*conclusions]
    for title, values, explanation in [
        ('Выбор источника урожая',source_table,'Сопоставляйте строки с одинаковым числом и составом наблюдений. Основная оценка остается по листу Урожайность.'),
        ('Выборки без расхождений между листами',exclusion_table,'Для зерна рассчитано |Урожайность − Фенофазы| / max(|Урожайность|, |Фенофазы|) до пересчета в СВ. Пороги 10%, 25% и 50% заданы для диагностики. Удаление записей не меняет основной набор данных.'),
        ('Доля сухого вещества',dm_table,'Показана чувствительность к 15%, 20% и 25% СВ кормов. Официальное допущение сохранено на уровне 20%. Параметры культур не перенастраивались.'),
    ]:
        body += '<h2>'+title+'</h2><p>'+explanation+'</p>'+html_table(headers,values)
        md += ['## '+title,explanation,md_table(headers,values)]
    discrepancies = sorted(comparisons,key=lambda r:r['relative_difference'],reverse=True)
    discrepancy_headers = ['Поле','Год','Основной лист т/га','Фенофазы т/га','Расхождение %','Ячейка']
    discrepancy_rows = [[r['field'],r['year'],number(r['primary_source_t_ha']),number(r['secondary_source_t_ha']),number(r['relative_difference']*100),r['pheno_cell']] for r in discrepancies[:15]]
    body += '<h2>Записи с наибольшими расхождениями</h2>'+html_table(discrepancy_headers,discrepancy_rows)
    md += ['## Записи с наибольшими расхождениями',md_table(discrepancy_headers,discrepancy_rows)]
    model_notes = [
        'В RotationManager поддержание травостоя проверяет живое растение, состав и совпадение сорта. Возраст 2 или 3 года сам по себе не задает отдельного снижения урожая. Возможное старение должно возникать из биологии профиля.',
        'Первого марта живые многолетники с фазой выше 3 принудительно переводятся на стадию 3. Это активное правило восстановления. Его влияние проверяется отключением и переносом на первое мая в одном выбранном году.',
        'В декабре надземная масса многолетних удаляется в остатки при продолжении травостоя. Завершение перед сменой культуры назначено на 20 августа. Эти даты заданы общими правилами.',
        'При подсеве зерновой высевается первым, многолетние — на следующий день. Для овса 2022 года исходная густота 250 растений/м² овса и по 150 растений/м² двух многолетних компонентов.',
        'Урожай зерна берется из Grain.Total.Wt непосредственно перед уборкой. Кормовой урожай вычисляется по разности надземной массы до и после Harvest. Кормовой укос использует удаление 90% надземной массы.',
        'Расписание содержит один учет уборки в год. Для записи без фактической даты зерновая уборка назначена на 31 августа, кормовой укос — на 15 июля.',
    ]
    body += '<h2>Что проверено в коде модели</h2><ul>'+''.join('<li>'+html.escape(t)+'</li>' for t in model_notes)+'</ul>'
    md += ['## Что проверено в коде модели',*model_notes]
    if final_diagnostics['available']:
        zero_headers = ['Поле','Год','Культура','Факт зерна','Расчет зерна','Максимум надземной массы','Максимум Zadok','Уборка']
        zero_rows = [[r['field'],r['year'],r['crop'],number(r['observed_t_ha']),number(r['grain_yield_t_ha']),number(r['aboveground_peak_t_ha']),number(r['zadok_peak']),r['harvest_date']] for r in final_diagnostics['zero_grain_cases']]
        explanation = 'Архивная база итоговой калибровки сверена по всем 200 урожаям с текущим экспортом. Массы в т СВ/га. Для ячменя нулевой урожай зерна сочетается с выращенной биомассой и незавершенным колошением к уборке.'
        body += '<h2>Нулевое зерно при наличии биомассы</h2><p>'+explanation+'</p>'+html_table(zero_headers,zero_rows)
        md += ['## Нулевое зерно при наличии биомассы',explanation,md_table(zero_headers,zero_rows)]
    if mechanism:
        control_rows = [[r['group']['id'],r['group']['trained_through'],
                        number(max(abs(p['replay_difference_t_ha']) for p in r['predictions'])),number(r['seconds'])]
                        for r in mechanism['results'] if r['variant']=='baseline']
        body += '<h2>Контроль воспроизводимости</h2><p>В расчетах сохранены десять полей по 1 га и исходная история. Использованы архивные параметры соответствующего этапа. Базовый расчет сверяется с прогнозами до обновления с допуском 0,000001 т СВ/га.</p>'
        body += html_table(['Группа','Параметры обучены по','Максимальная разница с архивом т СВ/га','Секунды'],control_rows)
        md += ['## Контроль воспроизводимости',md_table(['Группа','Обучено по','Разница с архивом','Секунды'],control_rows)]
        probe_headers = ['Группа','Год','Вариант','Факт','Расчет','Ошибка','Изменение к контролю']
        display = [[r['group'],r['year'],r['label'],number(r['observed_t_ha']),number(r['prediction_t_ha']),number(r['error_t_ha'],True),number(r['difference_from_control_t_ha'],True)] for r in probe_rows]
        body += '<h2>Контрольные изменения механизма</h2><p>Все урожаи в т СВ/га. Изменения менеджера применены в выбранном году. Последующие годы могут реагировать через сохраненное состояние почвы и растений. Проверка ячменя заменяет только шесть параметров фенологии на итоговые значения в полном окне.</p>'+html_table(probe_headers,display)
        md += ['## Контрольные изменения механизма',md_table(probe_headers,display)]
        peak_headers = ['Группа','Вариант','Год','Культура','Пик зерна т СВ/га','Дата пика']
        peak_rows = [[r['group'],names[r['variant']],r['year'],r['plant'],number(r['peak_grain_t_ha']),r['peak_date']] for r in peaks]
        body += '<h2>Максимальное зерно перед уборкой</h2><p>Максимум рассчитан по суточным значениям с 1 марта по 31 августа. Учет на конец дня может пропускать внутрисуточный максимум в день уборки.</p>'+html_table(peak_headers,peak_rows)
        md += ['## Максимальное зерно перед уборкой',md_table(peak_headers,peak_rows)]
    else:
        body += '<h2>Контрольные симуляции</h2><p>Результаты будут добавлены после завершения контрольной серии.</p>'
    body += '<h2>Файлы и методика</h2><p><a href="sensitivity.json">Все оценки и состав выборок</a></p><p><a href="REPORT.md">Текстовый отчет</a></p>'
    if mechanism:
        body += '<p><a href="mechanism_results.json">Контрольные расчеты и суточные трассы</a></p>'
    body += '<p>Основные наблюдения и исходная модель сохранены. Уменьшение ошибки при исключении записей относится к измененной выборке. Изменения механизма проверены на выбранных случаях с фиксированными архивными параметрами.</p>'
    assert fingerprint(obsfile)==source_hash
    summary['observations_unchanged'] = True
    (out/'sensitivity.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (out/'REPORT.md').write_text('\n\n'.join(md),encoding='utf-8')
    style = 'body{margin:0;background:#f3f6f8;color:#182e3d;font:16px/1.6 system-ui,sans-serif}main{max-width:1220px;margin:25px auto;padding:28px;background:white}h1{font-size:28px}h2{margin-top:32px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #d1dce4;padding:9px;text-align:left;vertical-align:top}th{background:#eaf0f4}tr:nth-child(even){background:#f7f9fa}.scroll{overflow:auto}a{color:#145e84}'
    (out/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Чувствительность APSIM</title><style>'+style+'</style><main>'+body+'</main></html>',encoding='utf-8')
    print(json.dumps({'sources':[{k:r[k] for k in ['label','n','mae_t_ha','rmse_t_ha']} for r in source_scores],
        'exclusions':[{k:r[k] for k in ['label','n','mae_t_ha','excluded_count']} for r in exclusions],
        'dry_matter':[{k:r[k] for k in ['label','mae_t_ha']} for r in dm_scores],
        'probes':summary['mechanism_probes_completed']},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
