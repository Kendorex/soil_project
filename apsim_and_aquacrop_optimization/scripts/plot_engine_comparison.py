"""Create Russian field comparisons from native APSIM and AquaCrop harvests."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR', str(ROOT / '.matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm
from matplotlib.patches import Patch
import numpy as np

YEARS = list(range(2019, 2024))
BLUE, ORANGE = '#246a87', '#d77b37'
CROPS = {'RedClover+PlantainForage': 'Клевер + подорожник', 'Oats': 'Овёс',
         'Wheat': 'Пшеница', 'Mustard': 'Горчица', 'Rape': 'Рапс',
         'Oats+RedClover': 'Овёс + клевер'}


def latest_run():
    candidates = []
    for path in (ROOT / 'runs/_launches').glob('*/launch.json'):
        meta = json.loads(path.read_text(encoding='utf-8'))
        if meta.get('profile') == 'aquacrop_all_fields' and meta.get('status') == 'completed':
            candidates.append((path.stat().st_mtime, Path(meta['output'])))
    if not candidates:
        raise FileNotFoundError('No completed aquacrop_all_fields run found')
    return max(candidates)[1]


def read_comparison(run):
    aqua_root = run / 'baseline' if (run / 'baseline/outputs').exists() else run
    rows, sources = [], []
    for path in (aqua_root / 'configs/rotation_fixed.apsimx', aqua_root / 'project.json'):
        sources.append({'path': str(path.relative_to(ROOT)),
                        'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    for number in range(1, 11):
        field_id = f'fixed_field_{number:02}'
        mapping_path = aqua_root / 'generated' / field_id / 'source_mapping.json'
        mapping = json.loads(mapping_path.read_text(encoding='utf-8'))
        assert mapping['field']['source_zone'] == f'Field{number}'
        aqua_path = aqua_root / 'outputs' / field_id / 'harvests.csv'
        # Calculation databases are outputs, never part of a model-data package.
        baseline = ROOT/'runs/management_optimization_20261027/baseline_apsim_v2/simulation.json'
        database = Path(json.loads(baseline.read_text(encoding='utf-8'))['provenance']['database'])
        with aqua_path.open(encoding='utf-8', newline='') as stream:
            aqua = list(csv.DictReader(stream))
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            apsim = [dict(row) for row in conn.execute('SELECT * FROM HarvestReport WHERE Zone=? ORDER BY Date',
                                                     (f'Field{number}',))]
            errors = conn.execute('SELECT COUNT(*) FROM _Messages WHERE MessageType=0').fetchone()[0]
            if errors:
                raise ValueError(f'APSIM source {field_id} contains {errors} errors')
        if len(aqua) != 5 or len(apsim) != 5:
            raise ValueError(f'{field_id}: expected five native harvest events per engine')
        native = {row['Date'][:10]: row for row in apsim}
        if len(native) != len(apsim) or {row['date'] for row in aqua} != set(native):
            raise ValueError(f'{field_id}: missing, duplicate or mismatched harvest dates')
        for event in aqua:
            match = native[event['date']]
            expected_kind = 'grain' if match['Use'] == 'grain' else 'forage'
            if event['kind'] != expected_kind or match['Zone'] != f'Field{number}':
                raise ValueError(f'{field_id}: inconsistent field or harvested product')
            ac, ap = float(event['dry_mass_t_ha']), float(match['YieldDMKgHa']) / 1000
            if not all(math.isfinite(value) and value >= 0 for value in (ac, ap)):
                raise ValueError('Invalid harvested dry matter')
            if not math.isclose(ap, float(match['YieldDMTHa']), rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError('APSIM harvest units do not agree')
            crop = CROPS.get(match['Crop'], match['Crop'])
            segment = next(s for s in mapping['segments'] if s['id'] == event['segment'])
            if 'Winter_rye' in segment['catalog_crop']:
                crop = 'Озимая рожь (прокси)'
            rows.append({'field': number, 'year': int(event['date'][:4]),
                         'date': event['date'], 'crop': crop, 'kind': event['kind'],
                         'area_ha': float(mapping['area_ha']), 'apsim_t_dm_ha': ap,
                         'aquacrop_t_dm_ha': ac, 'difference_t_dm_ha': ac-ap})
        for path in (aqua_path, database, mapping_path):
            sources.append({'path': str(path.relative_to(ROOT)),
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    rows.sort(key=lambda row: (row['field'], row['year']))
    if {(row['field'], row['year']) for row in rows} != {
            (field, year) for field in range(1, 11) for year in YEARS}:
        raise ValueError('Expected all 50 unique field/year pairs')
    return rows, sources


def number(value):
    return f'{value:.2f}'.replace('.', ',')


def bars(axis, rows, detail=False):
    x = np.arange(5)
    for offset, key, color in ((-.19, 'apsim_t_dm_ha', BLUE), (.19, 'aquacrop_t_dm_ha', ORANGE)):
        values = [row[key] for row in rows]
        rectangles = axis.bar(x+offset, values, width=.35, color=color, zorder=3)
        axis.bar_label(rectangles, labels=[number(value) for value in values],
                       padding=4, fontsize=11 if detail else 8)
    maximum = max(row[key] for row in rows for key in ('apsim_t_dm_ha', 'aquacrop_t_dm_ha'))
    axis.set_ylim(0, max(maximum, .1)*1.24)
    axis.set_xticks(x, YEARS)
    axis.set_ylabel('Сухая масса урожая, т/га')
    axis.grid(axis='y', alpha=.2, zorder=0)
    axis.set_axisbelow(True)
    axis.spines[['top', 'right']].set_visible(False)


def generate(rows, sources, run, output):
    (output / 'fields').mkdir(parents=True, exist_ok=True)
    legends = [Patch(color=BLUE, label='APSIM'), Patch(color=ORANGE, label='AquaCrop')]
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'figure.facecolor': '#ffffff', 'axes.labelcolor': '#334155',
                         'text.color': '#172b3a', 'xtick.color': '#334155', 'ytick.color': '#334155'}):
        for field in range(1, 11):
            selected = [row for row in rows if row['field'] == field]
            fig, axis = plt.subplots(figsize=(13, 6.5))
            fig.subplots_adjust(left=.09, right=.97, top=.79, bottom=.23)
            fig.suptitle(f'Поле {field}: AquaCrop и APSIM', x=.09, y=.97, ha='left', fontsize=23, weight='bold')
            fig.text(.09, .885, 'Собранная сухая масса • 2019–2023 • базовые сценарии', fontsize=12, color='#526575')
            bars(axis, selected, detail=True)
            axis.legend(handles=legends, frameon=False, ncol=2, loc='upper right')
            axis.set_xticks(range(5), [f"{row['year']}\n{row['crop']}" for row in selected], fontsize=10)
            fig.text(.09, .09, 'Кормовые культуры: сухая масса при укосе. Зерновые: сухая масса зерна.', fontsize=11)
            fig.text(.09, .045, 'AquaCrop — эффективные культуры, без дополнительной калибровки. Модели могут существенно расходиться.',
                     fontsize=10, color='#657789')
            fig.savefig(output / 'fields' / f'field_{field:02}.png', dpi=180)
            plt.close(fig)

        fig, axes = plt.subplots(5, 2, figsize=(18, 21))
        fig.subplots_adjust(top=.885, bottom=.06, left=.065, right=.98, hspace=.50, wspace=.20)
        fig.suptitle('AquaCrop и APSIM: сравнение десяти полей', x=.065, y=.975, ha='left', fontsize=25, weight='bold')
        fig.text(.065, .95, 'Собранная сухая масса, т/га • 2019–2023 • шкала по вертикали своя для каждого поля', fontsize=13)
        fig.legend(handles=legends, loc='upper left', bbox_to_anchor=(.058, .94), ncol=2, frameon=False, fontsize=13)
        for field, axis in enumerate(axes.flat, 1):
            bars(axis, [row for row in rows if row['field'] == field])
            axis.set_title(f'Поле {field}', loc='left', fontsize=16, weight='bold', pad=12)
        fig.text(.065, .025, 'APSIM: нативные события уборки той же фиксированной карты. AquaCrop: последний расчёт всех полей, без дополнительной калибровки.', fontsize=11)
        fig.savefig(output / 'all_fields.png', dpi=165)
        plt.close(fig)

        differences = np.array([[next(row['difference_t_dm_ha'] for row in rows
                                      if row['field'] == field and row['year'] == year)
                                 for year in YEARS] for field in range(1, 11)])
        limit = max(abs(differences.min()), abs(differences.max()), .01)
        fig, axis = plt.subplots(figsize=(10, 10.5))
        fig.subplots_adjust(left=.14, right=.87, top=.85, bottom=.12)
        fig.suptitle('Где расходятся результаты', x=.14, y=.96, ha='left', fontsize=23, weight='bold')
        fig.text(.14, .905, 'Разница AquaCrop − APSIM, т сухой массы/га', fontsize=13)
        image = axis.imshow(differences, cmap='RdBu_r', norm=TwoSlopeNorm(vcenter=0, vmin=-limit, vmax=limit), aspect='auto')
        axis.set_xticks(range(5), YEARS)
        axis.set_yticks(range(10), [f'Поле {field}' for field in range(1, 11)])
        axis.tick_params(length=0, pad=10)
        for i in range(10):
            for j in range(5):
                value = differences[i, j]
                axis.text(j, i, f'{value:+.2f}'.replace('.', ','), ha='center', va='center',
                          fontsize=12, color='white' if abs(value) > limit*.6 else '#172b3a')
        fig.colorbar(image, ax=axis, fraction=.035, pad=.04, label='AquaCrop − APSIM, т/га')
        fig.text(.14, .055, 'Положительное значение: AquaCrop выше APSIM. Отрицательное: ниже.\nРасхождение моделей не является ошибкой относительно наблюдений.', fontsize=11)
        fig.savefig(output / 'differences.png', dpi=180)
        plt.close(fig)

    with (output / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'sources.json').write_text(json.dumps({'aquacrop_run': str(run.relative_to(ROOT)),
        'rotation_source': 'models/aquacrop/configs/rotation_fixed.apsimx',
        'apsim_source': 'runs/management_optimization_20261027/baseline_apsim_v2/candidate.db: HarvestReport',
        'units': 't dry matter / ha', 'field_year_pairs': len(rows), 'files': sources},
        ensure_ascii=False, indent=2), encoding='utf-8')
    sections = []
    for field in range(1, 11):
        table = ''.join(f"<tr><td>{row['year']}</td><td>{html.escape(row['crop'])}</td>"
                        f"<td>{number(row['apsim_t_dm_ha'])}</td><td>{number(row['aquacrop_t_dm_ha'])}</td>"
                        f"<td>{number(row['difference_t_dm_ha'])}</td></tr>" for row in rows if row['field'] == field)
        sections.append(f'<section id="field{field}"><h2>Поле {field}</h2>'
                        f'<a href="fields/field_{field:02}.png"><img loading="lazy" src="fields/field_{field:02}.png" alt="Сравнение для поля {field}"></a>'
                        '<details><summary>Численные значения, т сухой массы/га</summary><table>'
                        '<thead><tr><th>Год</th><th>Культура APSIM</th><th>APSIM</th><th>AquaCrop</th><th>Разница</th></tr></thead>'
                        f'<tbody>{table}</tbody></table></details></section>')
    navigation = ' '.join(f'<a href="#field{field}">Поле {field}</a>' for field in range(1, 11))
    page = ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>AquaCrop и APSIM — 10 полей</title><style>'
            'body{font:16px/1.6 system-ui,sans-serif;color:#172b3a;background:#f2f5f7;margin:0}'
            'main{max-width:1250px;margin:35px auto;padding:0 22px}h1{font-size:34px;line-height:1.2}'
            'section,.intro{background:white;border-radius:14px;padding:24px;margin:25px 0}'
            'img{width:100%;height:auto}nav{display:flex;flex-wrap:wrap;gap:12px}a{color:#246a87}'
            'table{border-collapse:collapse;width:100%;margin-top:15px}th,td{text-align:left;padding:9px;border-bottom:1px solid #ddd}'
            'summary{cursor:pointer;font-weight:600}.note{color:#526575}html{scroll-behavior:smooth}</style></head><body><main>'
            '<h1>AquaCrop и APSIM: сравнение десяти полей</h1><div class="intro">'
            '<p>2019–2023 годы. Показатель: <b>собранная сухая масса, т/га</b>. Всего 50 пар «поле — год».</p>'
            '<p>Источник севооборота AquaCrop: <b>rotation_fixed.apsimx</b>. Отдельная таблица параметров севооборота не используется.</p>'
            '<p>Сравниваются последний базовый расчёт AquaCrop и нативные уборки APSIM для той же фиксированной карты севооборота. '
            'AquaCrop использует эффективные культуры и здесь не проходил дополнительную калибровку. Это сравнение моделей, а не проверка по наблюдениям.</p>'
            f'<p class="note">AquaCrop: {html.escape(run.name)}. APSIM: HarvestReport из моделей reference.</p>'
            '<p><a href="comparison.csv">Таблица CSV</a> · <a href="sources.json">Источники</a> · '
            '<a href="all_fields.png">Общий график PNG</a> · <a href="differences.png">Карта расхождений</a></p>'
            f'<nav>{navigation}</nav></div><section><h2>Все поля</h2><a href="all_fields.png"><img src="all_fields.png" alt="Все десять полей"></a></section>'
            '<section><h2>Разница AquaCrop − APSIM</h2><a href="differences.png"><img src="differences.png" alt="Разница результатов по полям и годам"></a></section>'
            + ''.join(sections) + '</main></body></html>')
    (output / 'index.html').write_text(page, encoding='utf-8')
    if (output/'optimization/index.html').exists():
        page=page.replace('</h1>','</h1><p><a href="optimization/index.html"><b>Результаты оптимизации: удобрения, густота и глубина посева</b></a></p>',1)
        (output/'index.html').write_text(page,encoding='utf-8')
    (output / 'README.md').write_text(
        '# Сравнение AquaCrop и APSIM\n\nОткройте `index.html` в браузере.\n\n'
        '- `all_fields.png` — все десять полей.\n- `fields/field_01.png` … `field_10.png` — отдельные поля.\n'
        '- `differences.png` — разница AquaCrop − APSIM.\n- `comparison.csv` — 50 пар значений.\n'
        '- `sources.json` — источники и контрольные суммы.\n\n'
        'Показатель: собранная сухая масса в т/га. Для кормовых культур — масса при укосе, '
        'для зерновых — масса зерна. Стоящая биомасса не используется.\n\n'
        f'AquaCrop: `{run.relative_to(ROOT)}`. APSIM: таблицы `HarvestReport` из '
        '`runs/management_optimization_20261027/baseline_apsim_v2/candidate.db`, а не оптимизированные варианты из `runs/apsim_search`. '
        'Эти reference-модели соответствуют фиксированному севообороту AquaCrop.\n\n'
        'AquaCrop показан без дополнительной калибровки. Его эффективные культуры не воспроизводят '
        'все процессы и конкуренцию видов APSIM. Нулевые значения APSIM за отдельные годы сохранены '
        'как результаты расчёта, а не заменены пропусками. Расхождения не являются оценкой точности без наблюдений.\n\n'
        'Повторная генерация из корня проекта: `python scripts/plot_engine_comparison.py`.\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aquacrop-run', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'runs/analysis/engine_comparison')
    args = parser.parse_args()
    run = (args.aquacrop_run or latest_run()).resolve()
    rows, sources = read_comparison(run)
    generate(rows, sources, run, args.output.resolve())
    print(f'Created 12 charts and HTML report: {args.output.resolve()}')
    print(f'Validated {len(rows)} field/year pairs against native harvest dates and units')


if __name__ == '__main__':
    main()
