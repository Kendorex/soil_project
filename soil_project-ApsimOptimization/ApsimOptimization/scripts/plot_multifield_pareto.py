#!/usr/bin/env python3
"""Build reproducible plots for the newest multi-field Pareto run."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_optimizer import ROOT
from src.multifield_model import write_json


OPTIMIZE_ROOT = ROOT/'outputs'/'multifield'/'DIAGNOSTIC_2023_N'


def newest_optimize():
    choices = [path for path in OPTIMIZE_ROOT.glob('optimize_*')
               if (path/'evaluations.json').is_file() and (path/'pareto.json').is_file()]
    return max(choices, key=lambda path: path.stat().st_mtime) if choices else None


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def short_option(name):
    for suffix in ('_old_price_proxy', '_price_proxy'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--optimize', type=Path, default=newest_optimize(),
                        help='optimizer run directory; defaults to newest diagnostic run')
    args = parser.parse_args(argv)
    if args.optimize is None:
        parser.error('no optimize run found; pass --optimize')
    run = args.optimize.resolve()
    evaluations_path, pareto_path = run/'evaluations.json', run/'pareto.json'
    config_path = run/'config_snapshot.json'
    evaluations, pareto, config = map(read_json,
                                      (evaluations_path, pareto_path, config_path))
    front = pareto.get('pareto') or []
    successful = [row for row in evaluations if row.get('status') == 'ok']
    if not successful or not front:
        raise ValueError('successful evaluations and a nonempty Pareto front are required')

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    output_dir = run/'plots'
    output_dir.mkdir(parents=True, exist_ok=True)
    front_ids = {row['candidate_id'] for row in front}
    labels = {row['candidate_id']: f'P{index}' for index, row in enumerate(front, 1)}
    scenario_kind = (config.get('scenario') or {}).get('kind')
    title_suffix = f' ({scenario_kind})' if scenario_kind else ''

    fig, ax = plt.subplots(figsize=(9, 6.5))
    x = np.asarray([row['yield_dm_kg']/1000 for row in successful])
    y = np.asarray([row['net_profit'] for row in successful])
    soil = np.asarray([row['soil_deviation'] for row in successful])
    scatter = ax.scatter(x, y, c=soil, cmap='viridis', s=70, alpha=.85,
                         edgecolors='#333333', linewidths=.5)
    for row, px, py in zip(successful, x, y):
        if row['candidate_id'] in front_ids:
            ax.scatter([px], [py], s=160, facecolors='none', edgecolors='#d62728',
                       linewidths=1.8)
            ax.annotate(labels[row['candidate_id']], (px, py), xytext=(6, 6),
                        textcoords='offset points', fontsize=9, fontweight='bold')
    ax.axhline(0, color='#777777', linewidth=.8, linestyle='--')
    ax.set_xlabel('Total harvested dry matter, t')
    ax.set_ylabel(f'Net profit, {pareto.get("currency") or config.get("currency") or "currency"}')
    ax.set_title('Multi-field NSGA-II objectives'+title_suffix)
    ax.grid(alpha=.2)
    colorbar = fig.colorbar(scatter, ax=ax)
    colorbar.set_label('Soil interval violation')
    fig.tight_layout()
    objectives_plot = output_dir/'pareto_objectives.png'
    fig.savefig(objectives_plot, dpi=180)
    plt.close(fig)

    fields = list(config['fields'])
    doses = np.asarray([[row['decisions'][field]['dose_kg_product_ha']
                         for field in fields] for row in front], dtype=float)
    fig_height = max(3.2, 1.15*len(front)+1.8)
    fig, ax = plt.subplots(figsize=(max(11, .95*len(fields)), fig_height))
    image = ax.imshow(doses, cmap='YlGnBu', aspect='auto')
    for r_index, candidate in enumerate(front):
        for c_index, field in enumerate(fields):
            decision = candidate['decisions'][field]
            ax.text(c_index, r_index,
                    f'{decision["dose_kg_product_ha"]:.1f}\n{short_option(decision["option"])}',
                    ha='center', va='center', fontsize=7,
                    color='white' if doses[r_index, c_index] > doses.max()*.55 else 'black')
    ax.set_xticks(range(len(fields)), fields, rotation=45, ha='right')
    ax.set_yticks(range(len(front)), [labels[row['candidate_id']] for row in front])
    ax.set_xlabel('Field')
    ax.set_ylabel('Pareto solution')
    ax.set_title('Fertilizer decisions: dose and configured option'+title_suffix)
    colorbar = fig.colorbar(image, ax=ax)
    colorbar.set_label('Product dose per application, kg/ha')
    fig.tight_layout()
    doses_plot = output_dir/'pareto_doses.png'
    fig.savefig(doses_plot, dpi=180)
    plt.close(fig)

    sources = (evaluations_path, pareto_path, config_path)
    manifest_path = output_dir/'pareto_plots_manifest.json'
    write_json(manifest_path, {
        'optimize_run': run.relative_to(ROOT).as_posix(),
        'environment_scenarios': pareto.get('environment_scenarios'),
        'evaluated': len(evaluations),
        'successful': len(successful),
        'pareto_solutions': len(front),
        'sources': [{'path': path.relative_to(ROOT).as_posix(),
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in sources],
        'plots': [objectives_plot.relative_to(ROOT).as_posix(),
                  doses_plot.relative_to(ROOT).as_posix()],
    })
    print(f'PARETO PLOTS: {output_dir}')
    print(objectives_plot)
    print(doses_plot)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
