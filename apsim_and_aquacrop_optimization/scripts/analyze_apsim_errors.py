"""Diagnose frozen APSIM rolling forecasts using archived observations and phases."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import hashlib
import html
import json
import math
from pathlib import Path
import statistics
import zipfile
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def valid(row):
    return row.get('included_in_metrics') is True and row.get('observed_t_ha') is not None


def metrics(rows, prediction='simulated_dm_t_ha'):
    pairs = [(r['observed_t_ha'], r[prediction]) for r in rows
             if valid(r) and r.get(prediction) is not None]
    if not pairs:
        return {'n': 0, 'mae_t_ha': None, 'rmse_t_ha': None, 'bias_t_ha': None}
    errors = [simulated - observed for observed, simulated in pairs]
    return {'n': len(pairs), 'mae_t_ha': statistics.mean(abs(e) for e in errors),
            'rmse_t_ha': math.sqrt(statistics.mean(e * e for e in errors)),
            'bias_t_ha': statistics.mean(errors)}


def walk(node):
    yield node
    for child in node.get('Children', []):
        yield from walk(child)


def read_workbook(path):
    """Read cached cell values and addresses without changing the original XLSX."""
    sheets = {}
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            strings = [''.join(n.itertext()) for n in root.findall('s:si', NS)]
        rels = {n.get('Id'): n.get('Target') for n in
                ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        root = ET.fromstring(archive.read('xl/workbook.xml'))
        for sheet in root.find('s:sheets', NS):
            target = rels[sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')]
            member = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            cells = {}
            for cell in ET.fromstring(archive.read(member)).findall('.//s:sheetData/s:row/s:c', NS):
                value = cell.findtext('s:v', default='', namespaces=NS)
                if cell.get('t') == 's' and value:
                    value = strings[int(value)]
                elif cell.get('t') == 'inlineStr':
                    value = ''.join(cell.find('s:is', NS).itertext())
                cells[cell.get('r')] = value
            sheets[sheet.get('name')] = cells
    return sheets


def groups(rows, keys, total_sse):
    buckets = defaultdict(list)
    for row in rows:
        buckets[tuple(row.get(k) for k in keys)].append(row)
    result = []
    for key, records in buckets.items():
        score = metrics(records)
        sse = sum(r['error_t_ha'] ** 2 for r in records)
        result.append(dict(zip(keys, key)) | score | {'squared_error_share_percent': 100 * sse / total_sse})
    return sorted(result, key=lambda r: r['squared_error_share_percent'], reverse=True)


def number(value, signed=False):
    if value is None:
        return 'нет данных'
    return f'{value:+.3f}' if signed else f'{value:.3f}'


def md_table(headers, rows):
    def cell(value):
        return str(value).replace('|', '/').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows])


def html_table(headers, rows):
    head = ''.join('<th>' + html.escape(str(v)) + '</th>' for v in headers)
    body = ''.join('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in row) + '</tr>' for row in rows)
    return '<div class="scroll"><table><thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div>'


CASE_NOTES = {
    ('Field6', 2013): (
        'Иргина существенно завышена при известных датах посева и уборки и N30. Ошибки начала и конца колошения составляют +4 и −2 дня.',
        'Вклад ошибок роста, распределения биомассы в зерно и накопленного почвенного азота после трех лет трав требует проверки.',
        'Повторить исходное окно с параметрами до 2012–2013 годов и сохранить биомассу, зерно, водный и азотный стресс. Не заменять исторические параметры итоговыми.'),
    ('Field9', 2019): (
        'Вико-овсяная смесь представлена Oats и RedClover. Сорт, даты и N30 условны. Наблюдений фаз нет. Большой промах сохраняется и после полной подгонки.',
        'Эффективный профиль смеси, момент зеленой уборки и основа пересчета массы могут вносить ошибку.',
        'Уточнить состав смеси и реальную уборку. Проверить компоненты собранной массы отдельно и долю СВ по измерениям.'),
    ('Field1', 2010): (
        'Козлятник представлен RedClover GalegaProxy. Посев и укос условны. В экспорте возраст равен 0, при этом модель ведет этот травостой с 2004 года.',
        'Профиль другого вида, весеннее восстановление и неизвестный реальный возраст могут объяснять устойчивое завышение.',
        'Разобрать GalegaProxy отдельно. Проверить число укосов и продукт измерения, динамику покрова, корней и весеннего восстановления.'),
    ('Field8', 2007): (
        'Для ячменя нет наблюденных фаз, дат посева и уборки. Уборка задана 31 августа. В итоговой подгонке расчетный урожай этой строки равен нулю.',
        'Поздняя фиксированная уборка и слабая идентификация профиля могут давать нестабильный выход зерна.',
        'Проверить зерно и зрелость перед уборкой в проверочном и итоговом вариантах. Сопоставить фактические сроки с 31 августа.'),
    ('Field1', 2011): (
        'Второй подряд крупный промах GalegaProxy на том же поле. Систематическое завышение остается после полной подгонки.',
        'Повторение ошибки усиливает гипотезу о проблеме профиля или учета многолетнего травостоя.',
        'Проверять совместно с 2010 годом и соседними годами этого поля. Сопоставить зимние остатки, укосы и состояние весной.'),
    ('Field8', 2013): (
        'Травы третьего года представлены RedClover и PlantainForage. Даты условны, наблюдений фаз нет. Итоговая подгонка уменьшает ошибку.',
        'Параметры по прежним годам плохо переносятся на третий год пользования. Механизм старения требует отдельной проверки.',
        'Разобрать переходы после подсева 2010 года и два предшествующих года трав. Проверить весенний сброс фазы и фактическое сохранение травостоя.'),
    ('Field4', 2022): (
        'Овес Яков сильно занижен. Расхождения четырех наблюденных фаз не превышают семи дней. Между листами Excel есть крупное расхождение урожая.',
        'Приоритет имеют согласование эталона, конкуренция с подсевом и проверка выхода зерна к уборке.',
        'Согласовать Урожайность и Фенофазы F31. Затем проверить выход зерна, стресс и вклад подсева. В модели подсев запланирован на день после посева овса.'),
    ('Field7', 2010): (
        'Травы третьего года, даты условны. Итоговая подгонка существенно уменьшает промах.',
        'Перенос параметров травостоя на следующие годы и весеннее восстановление требуют проверки.',
        'Проследить травостой после подсева 2007 года, сравнить запас и покрытие к началу сезона с первым и вторым годами.'),
    ('Field8', 2014): (
        'Иргина завышена при известных датах операций и N30. Колошение начинается на 12 и завершается на 15 дней раньше наблюдений.',
        'Здесь сдвиг репродуктивного периода может давать вклад вместе с ошибками роста и зернового распределения.',
        'Проверить настройку репродуктивного периода совместно с урожаем и почвенными стрессами. Сопоставить с Иргиной на поле 6 в 2013 году.'),
    ('Field6', 2012): (
        'Еще один травостой третьего года с завышенным урожаем до обновления. При итоговой подгонке ошибка намного меньше.',
        'Повторение на другом поле указывает на слабую переносимость настройки этой возрастной группы.',
        'Проверить единый механизм переходов травостоя на примерах полей 6, 7 и 8 и оценивать исправление на последующих годах.'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'runs/analysis/apsim_error_diagnosis_20261106')
    parser.add_argument('--workbook', type=Path, default=ROOT.parent / 'new_calibr/input/vniimz.xlsx')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    obs = ROOT / 'models/apsim/data/observations/new_calibr'
    model_root = ROOT / 'models/apsim/simulation/calibrated_history'
    sources = [obs / 'yield_comparison.json', obs / 'phenology_comparison.json',
               model_root / 'simulation.apsimx', model_root / 'provenance.json',
               model_root / 'metrics.json', model_root / 'assumptions.json']
    hashes_before = {str(path): fingerprint(path) for path in sources}
    yield_data = read_json(sources[0])
    phase_data = read_json(sources[1])
    provenance = read_json(sources[3])
    final = yield_data['records']
    forward = [dict(r) for r in yield_data['pre_update_records'] if valid(r)]
    assert len({(r['field'], r['year']) for r in final}) == len(final)
    assert len({(r['field'], r['year']) for r in forward}) == len(forward)
    for row in forward:
        assert row['predicted_before_update'] is True and row['trained_through'] < row['year']
        row['error_t_ha'] = row['simulated_dm_t_ha'] - row['observed_t_ha']
        assert math.isclose(row['error_t_ha'], next(r['error_t_ha'] for r in yield_data['pre_update_records']
                            if (r['field'], r['year']) == (row['field'], row['year'])), abs_tol=1e-9)
        history = [r['observed_t_ha'] for r in final if valid(r) and r['year'] <= row['trained_through']
                   and (r['model_crops'], r['use']) == (row['model_crops'], row['use'])]
        row['past_crop_mean_t_ha'] = statistics.mean(history) if history else None
    archive_metrics = read_json(model_root / 'metrics.json')['pre_update_validation']
    score = metrics(forward)
    assert score['n'] == archive_metrics['yield_n']
    assert math.isclose(score['mae_t_ha'], archive_metrics['yield_mae_t_ha'], abs_tol=1e-12)
    final_index = {(r['field'], r['year']): r for r in final}
    phase_index = defaultdict(list)
    for row in phase_data['pre_update_records']:
        assert row['trained_through'] < row['year']
        phase_index[(row['field'], row['year'], row['trained_through'])].append(row)
    total_sse = sum(r['error_t_ha'] ** 2 for r in forward)
    ordered = sorted(forward, key=lambda r: (-abs(r['error_t_ha']), r['year'], r['field_number']))
    workbook = {}
    book_audit = {'available': args.workbook.exists()}
    if args.workbook.exists():
        book_hash = fingerprint(args.workbook)
        book_audit.update(path=str(args.workbook.resolve()), sha256=book_hash,
                          matches_calibration_source=book_hash == provenance['inputs']['workbook']['sha256'])
        assert book_audit['matches_calibration_source'], 'Workbook differs from the archived calibration source'
        workbook = read_workbook(args.workbook)
        hashes_before[str(args.workbook)] = book_hash
    top = []
    for rank, row in enumerate(ordered[:10], 1):
        key = (row['field'], row['year'])
        phases = phase_index[(row['field'], row['year'], row['trained_through'])]
        final_row = final_index[key]
        notes = CASE_NOTES.get(key, ('См. исходные записи и операции.', 'Причина требует проверки.', 'Проверить трассу исходного окна.'))
        case = dict(row, rank=rank, phases=phases, confirmed_observation=notes[0],
                    hypothesis=notes[1], next_check=notes[2],
                    final_fit_prediction_t_ha=final_row['simulated_dm_t_ha'],
                    final_fit_error_t_ha=final_row['simulated_dm_t_ha'] - row['observed_t_ha'],
                    previous_three_years=[r for r in final if r['field'] == row['field'] and row['year']-3 <= r['year'] < row['year']])
        if workbook:
            cells = row['yield_source'].split('!', 1)[1].split(';')
            raw_values = [float(workbook['Урожайность'][cell]) / 10 for cell in cells]
            raw_mean = statistics.mean(raw_values)
            assert len(raw_values) == row['yield_n']
            assert math.isclose(raw_mean, row['source_yield_t_ha'], abs_tol=1e-10)
            pheno_cell = 'F' + str(row['pheno_row']) if row.get('pheno_row') else None
            case['workbook_verification'] = {'yield_cell_count': len(cells), 'mean_source_t_ha': raw_mean,
                'yield_mean_matches_export': True, 'pheno_yield_cell': pheno_cell,
                'pheno_yield_cell_value': workbook['Фенофазы'].get(pheno_cell) if pheno_cell else None}
        top.append(case)
    crop_groups = groups(forward, ['model_crops', 'use'], total_sse)
    field_groups = groups(forward, ['field'], total_sse)
    year_groups = groups(forward, ['year'], total_sse)
    stand_groups = []
    for age in [1, 2, 3]:
        records = [r for r in forward if r['use'] == 'hay' and r['pasture_age'] == age]
        stand_groups.append({'pasture_age': age, **metrics(records),
            'observed_mean_t_ha': statistics.mean(r['observed_t_ha'] for r in records),
            'simulated_mean_t_ha': statistics.mean(r['simulated_dm_t_ha'] for r in records)})
    sheet_disagreements = []
    for row in forward:
        other = row.get('pheno_average_t_ha')
        if row['use'] == 'grain' and other is not None:
            sheet_disagreements.append({'field': row['field'], 'year': row['year'],
                'point_mean_t_ha': row['source_yield_t_ha'], 'pheno_mean_t_ha': other,
                'difference_t_ha': row['source_yield_t_ha'] - other,
                'ratio': row['source_yield_t_ha'] / other if other else None,
                'source_cell': 'Фенофазы!F' + str(row['pheno_row']), 'yield_source': row['yield_source']})
    sheet_disagreements.sort(key=lambda r: abs(r['difference_t_ha']), reverse=True)
    model = read_json(model_root / 'simulation.apsimx')
    managers = [n for n in walk(model) if n.get('Name') == 'RotationManager']
    codes = {'\n'.join(n['CodeArray']) for n in managers}
    assert len(managers) == 10 and len(codes) == 1
    code = managers[0]['CodeArray']
    rules = [{'line': i+1, 'code': line.strip()} for i, line in enumerate(code)
             if any(token in line for token in ['Match("01-Mar")', 'SetToStage(3)', 'undersowDate=Clock.Today.AddDays(1)',
                 'RotationHarvestDates[i]', 'PastureAges[i]=="1"', 'WinterDiebackDate', 'RotationUses[i]=="hay"'])]
    output_hashes = provenance['output_files']
    source_checks = {path.name: fingerprint(path) == output_hashes[path.name]
                     for path in sources if path.name in output_hashes}
    assert all(source_checks.values())
    paired = [r for r in forward if r['past_crop_mean_t_ha'] is not None]
    findings = [
        'Десять крупнейших ошибок дают ' + number(100 * sum(r['error_t_ha']**2 for r in top)/total_sse) + '% суммы квадратов ошибок 130 проверочных наблюдений.',
        'На поле 1 завышены все восемь проверочных наблюдений. На поле 9 выделяется зеленая вико-овсяная смесь 2019 года.',
        'У трех крупнейших случаев с наблюденными фазами нет единого фенологического объяснения. Для пшеницы 2013 года колошение близко к наблюдениям, для пшеницы 2014 года оно раньше на 12–15 дней.',
        'Пять из десяти крупнейших промахов относятся к многолетним кормам, еще один — к зеленой смеси. Для семи из десяти случаев нет сопоставляемых фаз в экспорте.',
        'В группах трав возрастов 1, 2 и 3 лет завышение растет. Группы включают разные поля, годы и исторические настройки, поэтому это диагностическая связь.',
        'Для овса на поле 4 в 2022 году исходные листы дают 4,404 и 1,300 т/га. Разница не устраняется простым учетом 14% влажности зерна.',
        'В экспортированном менеджере подсев назначен на день после посева покровного зернового. Ранее в описании проекта его ошибочно связывали с днем после уборки. Весеннее восстановление многолетних включает принудительный перевод фазы на стадию 3 первого марта.',
    ]
    priorities = [
        ('1', 'Согласование эталона урожая', 'Сверить в первую очередь 2022 год на полях 4, 10, 2 и 8. Сохранить обе исходные величины до согласования смысла записей.'),
        ('2', 'Переходы многолетних трав', 'Проверить GalegaProxy поля 1 и травы третьего года на полях 6, 7 и 8. Сопоставить фактические укосы и возраст с правилами восстановления, зимнего удаления и завершения травостоя.'),
        ('3', 'Зерно и подсев', 'Для овса 2022 года проверить совместный рост с подсевом, зерно перед уборкой и стрессы. Для ячменя 2007 года разобрать смену от большого урожая до нуля при итоговой настройке.'),
        ('4', 'Переносимость Иргины', 'Сопоставить поля 6/2013 и 8/2014. Сохранить дневные биомассу, зерно, водный и азотный стресс и почвенные запасы до и после трав.'),
        ('5', 'Контроль результата исправлений', 'После выбора причины менять один механизм за раз и проверять на последующих годах. Считать общую ошибку, ошибки групп и тот же прогноз среднего по прошлым наблюдениям.'),
    ]
    audit = {'created_date': date.today().isoformat(), 'evaluation': 'frozen_chronological_pre_update',
        'area_scope': 'Archived predictions at the original calibration area. These are not new 0.1 ha reruns.',
        'method': 'Included finite yield observations, ranked by absolute simulated-minus-observed dry-matter yield error. Phases joined by field, year and trained_through.',
        'source_hashes': hashes_before, 'export_hash_checks': source_checks, 'workbook': book_audit,
        'metrics': score, 'paired_apsim': metrics(paired), 'paired_past_crop_mean': metrics(paired, 'past_crop_mean_t_ha'),
        'top10_squared_error_share_percent': 100 * sum(r['error_t_ha']**2 for r in top)/total_sse,
        'top10': top, 'by_field': field_groups, 'by_crop_use': crop_groups, 'by_year': year_groups,
        'by_pasture_age': stand_groups, 'grain_sheet_comparisons': sheet_disagreements,
        'rotation_manager_rules': rules, 'findings': findings,
        'priorities': [{'priority': a, 'topic': b, 'action': c} for a,b,c in priorities],
        'all_validation_records': forward,
        'limitations': [
            'The rolling evaluation conditions on historical weather and operations. Initial priors are not fully independent.',
            'The current export preserves final manager rules. Stage-specific daily biomass, stress and soil traces were not reconstructed in this audit.',
            'Missing phase observations and actual management dates remain missing. Pasture age 0 does not establish a first-year stand for GalegaProxy.',
            'Case hypotheses need controlled reruns or additional observations. No counterfactual simulation was run.',
        ]}
    headers = ['№','Поле','Год','Культура','Факт','APSIM до обновления','Ошибка','Обучено по']
    top_rows = [[r['rank'], r['field_number'], r['year'], r['crop'], number(r['observed_t_ha']),
                 number(r['simulated_dm_t_ha']), number(r['error_t_ha'], True), r['trained_through']] for r in top]
    score_rows = [['APSIM, все проверочные годы',score['n'],number(score['mae_t_ha']),number(score['rmse_t_ha'])],
        ['APSIM, парная выборка',len(paired),number(metrics(paired)['mae_t_ha']),number(metrics(paired)['rmse_t_ha'])],
        ['Среднее прошлых наблюдений, та же выборка',len(paired),number(metrics(paired,'past_crop_mean_t_ha')['mae_t_ha']),number(metrics(paired,'past_crop_mean_t_ha')['rmse_t_ha'])]]
    age_rows = [[r['pasture_age'],r['n'],number(r['observed_mean_t_ha']),number(r['simulated_mean_t_ha']),number(r['bias_t_ha'],True)] for r in stand_groups]
    md = ['# Разбор ошибок APSIM по следующим годам',
        'Проверены 130 сохраненных прогнозов за 2007–2023 годы. Урожай и ошибка выражены в т сухого вещества на гектар. Положительная ошибка означает завышение. Сортировка выполнена по абсолютной ошибке.',
        'Использованы прогнозы до обновления параметров с условием trained_through < year. Фазы сопоставлены по полю, году и тому же trained_through. Погода и операции в этой условной проверке известны. Итоговая подгонка показана отдельно в карточках.',
        'Это архив исходной калибровки. Проверочные прогнозы для площади 0,1 га заново не рассчитывались. Суточные трассы исторических окон здесь не восстановлены.',
        '## Что обнаружено', *findings,
        '## Общая точность',md_table(['Прогноз','Число','MAE т СВ/га','RMSE т СВ/га'],score_rows),
        '## Десять крупнейших ошибок',md_table(headers,top_rows),
        '## Возраст травостоя',md_table(['Год пользования','Число','Факт средний','APSIM средний','Смещение'],age_rows),
        'Для козлятника код pasture_age=0 означает отсутствие отдельной возрастной категории в адаптере. Его реальный возраст из этого кода определить нельзя.',
        '## Приоритеты проверки',md_table(['Порядок','Тема','Действие'],priorities)]
    body = '<h1>Разбор ошибок APSIM по следующим годам</h1>'
    body += ''.join('<p>'+html.escape(t)+'</p>' for t in md[1:4])
    body += '<h2>Что обнаружено</h2><ul>'+''.join('<li>'+html.escape(t)+'</li>' for t in findings)+'</ul>'
    body += '<h2>Общая точность</h2>'+html_table(['Прогноз','Число','MAE т СВ/га','RMSE т СВ/га'],score_rows)
    body += '<h2>Десять крупнейших ошибок</h2>'+html_table(headers,top_rows)
    body += '<h2>Возраст травостоя</h2>'+html_table(['Год пользования','Число','Факт средний','APSIM средний','Смещение'],age_rows)
    body += '<p>Разные возрастные группы включают разные годы и поля. Для козлятника код возраста 0 не определяет реальный возраст.</p>'
    for title, data, key in [('Ошибки по полям',field_groups,'field'),('Ошибки по годам',year_groups,'year')]:
        rows = [[r[key],r['n'],number(r['mae_t_ha']),number(r['bias_t_ha'],True),number(r['squared_error_share_percent'])] for r in data]
        cols = ['Группа','Число','MAE','Смещение','Доля суммы квадратов ошибок %']
        body += '<h2>'+title+'</h2>'+html_table(cols,rows)
        md += ['## '+title,md_table(cols,rows)]
    crop_rows = [[r['model_crops'],r['use'],r['n'],number(r['mae_t_ha']),number(r['bias_t_ha'],True),number(r['squared_error_share_percent'])] for r in crop_groups]
    crop_headers = ['Модельные культуры','Использование','Число','MAE','Смещение','Доля суммы квадратов ошибок %']
    body += '<h2>Ошибки по культурам и использованию</h2>'+html_table(crop_headers,crop_rows)
    md += ['## Ошибки по культурам и использованию',md_table(crop_headers,crop_rows)]
    sheet_rows = [[r['field'],r['year'],number(r['point_mean_t_ha']),number(r['pheno_mean_t_ha']),number(r['ratio']),r['source_cell']] for r in sheet_disagreements[:10]]
    sheet_headers = ['Поле','Год','Среднее точек т/га','Фенофазы т/га','Отношение','Ячейка']
    body += '<h2>Расхождения листов исходной книги</h2><p>Показаны исходные т/га до пересчета в СВ. В сравнении участвуют 54 записи зерновых. Сопоставимость продуктов и методик двух листов требует уточнения.</p>'+html_table(sheet_headers,sheet_rows)
    md += ['## Расхождения листов исходной книги','Значения в исходных т/га до пересчета в СВ. Всего 54 сравнения зерновых.',md_table(sheet_headers,sheet_rows)]
    phase_names = {'tillering_start':'Начало кущения','tillering_end':'Конец кущения','heading_start':'Начало колошения','heading_end':'Конец колошения'}
    for r in top:
        title = f"{r['rank']}. Поле {r['field_number']}, {r['year']}, {r['crop']}"
        text = [r['confirmed_observation'], 'Гипотеза. '+r['hypothesis'], 'Следующая проверка. '+r['next_check']]
        ops = [
            ['Сорт модели',r['cultivars']],['Возраст в адаптере',str(r['pasture_age'])],
            ['Посев в расписании',r['sow_date']],['Наблюденный посев',r['sow_observed'] or 'нет данных'],
            ['Уборка в расписании',r['harvest_date']],['Наблюденная уборка',r['harvest_observed'] or 'нет данных'],
            ['Уборка в экспортированном расчете',r['harvest_date_actual']],['Азот по расписанию кг N/га',r['nitrogen_kg_ha']],
            ['Густота по компонентам растений/м²',r['populations']],['Учет собранных культур',r['harvest_crops']],
            ['Коэффициент пересчета в СВ',r['dry_matter_fraction']],['Число точек урожая',r['yield_n']],
            ['Прогноз среднего по прошлому т СВ/га',number(r['past_crop_mean_t_ha'])],
            ['Итоговая подгонка т СВ/га',number(r['final_fit_prediction_t_ha'])],
            ['Ошибка итоговой подгонки т СВ/га',number(r['final_fit_error_t_ha'],True)],
            ['Предположения исходного пакета',r['assumptions'] or 'не перечислены'],
            ['Замена вида',r['proxy_note'] or 'не указана'],
        ]
        phase_rows = [[phase_names.get(q['phase'],q['phase']),q['observed_date'],q['simulated_date'] or 'не достигнута',q['error_days'] if q['error_days'] is not None else 'нет',q['source_cell']] for q in r['phases']]
        previous = [[q['year'],q['crop'],q['pasture_age'],q['sow_date'],q['harvest_date']] for q in r['previous_three_years']]
        detail = ''.join('<p>'+html.escape(t)+'</p>' for t in text)
        detail += html_table(['Показатель','Значение'],ops)
        if phase_rows:
            detail += html_table(['Фаза','Наблюдение','Расчет до обновления','Ошибка дней','Ячейка'],phase_rows)
        else:
            detail += '<p>Сопоставляемых наблюдений фаз для этого поля и года в экспорте нет.</p>'
        detail += '<h3>Три предшествующих года по расписанию</h3>'+html_table(['Год','Культура','Возраст','Посев','Уборка'],previous)
        detail += '<p>Источник урожая '+html.escape(r['yield_source'])+'. Источник культуры '+html.escape(r['rotation_cell'])+'.</p>'
        body += '<details open><summary>'+html.escape(title)+'</summary>'+detail+'</details>'
        md += ['## '+title,*text,md_table(['Показатель','Значение'],ops)]
        md += [md_table(['Фаза','Наблюдение','Расчет','Ошибка дней','Ячейка'],phase_rows)] if phase_rows else ['Сопоставляемых фаз в экспорте нет.']
        md += [md_table(['Год','Предшественник','Возраст','Посев','Уборка'],previous), 'Источник урожая '+r['yield_source']]
    body += '<h2>Приоритеты проверки</h2>'+html_table(['Порядок','Тема','Действие'],priorities)
    body += '<h2>Правила менеджера экспортированной модели</h2><p>Код RotationManager одинаков на десяти полях. Строки относятся к CodeArray менеджера в исходной модели.</p>'+html_table(['Строка','Код'],[[r['line'],r['code']] for r in rules])
    workbook_note = ('Исходная книга совпадает по SHA-256 с книгой калибровки. Все десять средних урожая перепроверены по исходным ячейкам.'
                     if book_audit['available'] else 'Исходная книга недоступна. Сравнение листов выполнено по значениям из калибровочного экспорта.')
    body += '<h2>Происхождение данных</h2><p>Хэши модели и наблюдений сверены с калибровочным provenance.json. '+workbook_note+'</p>'
    body += '<p>Подробные исходные строки, фазы, сводки и ограничения сохранены в <a href="diagnosis.json">diagnosis.json</a>. Текстовая версия — <a href="REPORT.md">REPORT.md</a>.</p>'
    assert all(fingerprint(Path(path)) == value for path,value in hashes_before.items())
    audit['sources_unchanged'] = True
    (out / 'diagnosis.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (out / 'REPORT.md').write_text('\n\n'.join(md),encoding='utf-8')
    style = 'body{margin:0;background:#f4f6f8;color:#172c3b;font:16px/1.55 system-ui,sans-serif}main{max-width:1180px;margin:24px auto;background:white;padding:28px}h1{font-size:29px}h2{margin-top:32px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #d3dde4;padding:9px;vertical-align:top;text-align:left}th{background:#e8eff4}tbody tr:nth-child(even){background:#f7f9fb}.scroll{overflow:auto;margin:14px 0}details{border:1px solid #d3dde4;padding:16px;margin:20px 0}summary{font-size:20px;font-weight:650;cursor:pointer}a{color:#125d89}@media print{main{margin:0;padding:0}details{break-inside:avoid}}'
    (out / 'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Разбор ошибок APSIM</title><style>'+style+'</style><main>'+body+'</main></html>',encoding='utf-8')
    print(json.dumps({'output':str(out),'metrics':score,'top10_sse_percent':audit['top10_squared_error_share_percent'],
                      'workbook':book_audit,'source_checks':source_checks},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
