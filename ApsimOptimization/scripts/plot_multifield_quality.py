#!/usr/bin/env python3
"""Render reproducible plots from quality checkpoints and diagnostics."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_model import write_json
from src.multifield_optimizer import ROOT


QUALITY = ROOT/'outputs'/'multifield'/'quality_history'


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def newest(pattern, root):
    paths = [path for path in Path(root).glob(pattern) if path.is_dir()]
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    plot_dir = QUALITY/'plots'
    plot_dir.mkdir(parents=True, exist_ok=True)
    sources, outputs = [], []

    index_path = QUALITY/'index.csv'
    history = read_csv(index_path)
    if not history:
        raise SystemExit('No quality checkpoints found')
    sources.append(index_path)
    labels = [row['label'] for row in history]
    x = list(range(len(history)))
    fig, ax = plt.subplots(figsize=(max(8, 1.6*len(history)), 5.2))
    ax.plot(x, [number(row['overall_rmse_t_ha']) for row in history], 'o-',
            label='RMSE, all observations')
    ax.plot(x, [number(row['year_2023_rmse_t_ha']) for row in history], 's-',
            label='RMSE, 2023')
    ax.set_ylabel('Yield RMSE, t/ha')
    ax.set_xticks(x, labels, rotation=25, ha='right')
    ax.grid(axis='y', alpha=.3)
    coverage = ax.twinx()
    coverage.plot(x, [number(row['coverage_percent']) for row in history], 'k.--',
                  label='Coverage')
    coverage.set_ylabel('Comparable observations, %')
    coverage.set_ylim(0, 105)
    handles, legend_labels = ax.get_legend_handles_labels()
    handles2, labels2 = coverage.get_legend_handles_labels()
    ax.legend(handles+handles2, legend_labels+labels2, loc='best')
    ax.set_title('Historical model quality checkpoints')
    fig.tight_layout()
    quality_plot = plot_dir/'quality_progress.png'
    fig.savefig(quality_plot, dpi=180)
    plt.close(fig)
    outputs.append(quality_plot)

    latest_checkpoint = newest('*', QUALITY/'checkpoints')
    field_path = latest_checkpoint/'field_metrics.csv'
    fields = read_csv(field_path)
    sources.append(field_path)
    colors = {'standard': '#1f77b4', 'input_sensitive': '#d62728',
              'boundary_limited': '#9467bd', 'structural_proxy': '#ff7f0e'}
    fig, ax = plt.subplots(figsize=(7, 6.5))
    for tier in colors:
        group = [row for row in fields if row.get('evidence_tier') == tier and
                 number(row.get('simulated_yield_t_ha')) is not None]
        if group:
            ax.scatter([number(row['observed_yield_t_ha']) for row in group],
                       [number(row['simulated_yield_t_ha']) for row in group],
                       s=38, alpha=.85, color=colors[tier], label=tier)
    limit = max(max(number(row['observed_yield_t_ha']) or 0,
                    number(row.get('simulated_yield_t_ha')) or 0) for row in fields)*1.08
    ax.plot([0, limit], [0, limit], 'k--', linewidth=1, label='1:1')
    ax.set(xlim=(0, limit), ylim=(0, limit), xlabel='Observed yield, t/ha',
           ylabel='Simulated yield, t/ha', title='Latest historical yield comparison')
    ax.grid(alpha=.25)
    ax.legend()
    fig.tight_layout()
    fit_plot = plot_dir/'latest_observed_vs_simulated.png'
    fig.savefig(fit_plot, dpi=180)
    plt.close(fig)
    outputs.append(fit_plot)

    establishment = newest('establishment_water_diagnostic_*',
                           ROOT/'outputs'/'multifield')
    if establishment:
        summary_path = establishment/'summary.json'
        summary = json.loads(summary_path.read_text(encoding='utf-8-sig'))
        rows = summary.get('rows') or []
        if rows:
            sources.append(summary_path)
            scenarios = list(dict.fromkeys(row['scenario'] for row in rows))
            target_fields = summary['fields']
            width = .36
            fig, ax = plt.subplots(figsize=(max(8, 1.8*len(scenarios)), 5.5))
            for offset, field in enumerate(target_fields):
                selected = [next(row for row in rows if row['scenario'] == scenario and
                                 row['field'] == field) for scenario in scenarios]
                positions = [i+(offset-.5)*width for i in range(len(scenarios))]
                ax.bar(positions, [row['simulated_yield_t_ha'] for row in selected],
                       width=width, label=f'{field} simulated')
                observed = selected[0]['observed_yield_t_ha']
                ax.axhline(observed, linestyle='--', linewidth=1,
                           label=f'{field} observed ({observed:g})')
            ax.set_xticks(range(len(scenarios)), scenarios, rotation=20, ha='right')
            ax.set_ylabel('Yield, t/ha')
            ax.set_title('2019 establishment-water sensitivity (engineering only)')
            ax.grid(axis='y', alpha=.25)
            ax.legend(ncols=2)
            fig.tight_layout()
            water_plot = plot_dir/'establishment_water_sensitivity.png'
            fig.savefig(water_plot, dpi=180)
            plt.close(fig)
            outputs.append(water_plot)

    root_access = newest('soil_root_access_diagnostic_*',
                         ROOT/'outputs'/'multifield')
    if root_access:
        summary_path = root_access/'summary.json'
        summary = json.loads(summary_path.read_text(encoding='utf-8-sig'))
        scenarios = summary.get('scenarios') or []
        if scenarios:
            sources.append(summary_path)
            labels = [row['scenario'] for row in scenarios]
            x = list(range(len(labels)))
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(max(9, 1.8*len(labels)), 8),
                                           sharex=True)
            ax1.plot(x, [row['overall_rmse_t_ha'] for row in scenarios], 'o-',
                     label='All observations')
            ax1.plot(x, [row['spring_annual_rmse_t_ha'] for row in scenarios], 's-',
                     label='Oats + spring wheat')
            ax1.set_ylabel('Yield RMSE, t/ha')
            ax1.set_title('Soil/root-access sensitivity (engineering only)')
            ax1.grid(axis='y', alpha=.25)
            ax1.legend()
            ax2.plot(x, [row['field3_2019_yield_t_ha'] for row in scenarios], 'o-',
                     label='Field3 Oats')
            ax2.plot(x, [row['field7_2019_yield_t_ha'] for row in scenarios], 's-',
                     label='Field7 spring wheat')
            ax2.set_ylabel('2019 yield, t/ha')
            ax2.set_xticks(x, labels, rotation=22, ha='right')
            ax2.grid(axis='y', alpha=.25)
            ax2.legend()
            fig.tight_layout()
            root_plot = plot_dir/'soil_root_access_sensitivity.png'
            fig.savefig(root_plot, dpi=180)
            plt.close(fig)
            outputs.append(root_plot)

    oats_runs = ROOT/'outputs'/'multifield'/'OATS_YIELD_CALIBRATION'
    oats_run = newest('*', oats_runs)
    if oats_run and (oats_run/'summary.json').is_file():
        summary_path = oats_run/'summary.json'
        summary = json.loads(summary_path.read_text(encoding='utf-8-sig'))
        candidates = summary.get('candidates') or []
        if candidates:
            sources.append(summary_path)
            labels = [row['name'] for row in candidates]
            calibration = [row['metrics']['calibration']['rmse_t_ha'] for row in candidates]
            validation = [row['metrics']['validation']['rmse_t_ha'] for row in candidates]
            x = list(range(len(labels)))
            width = .36
            fig, ax = plt.subplots(figsize=(max(8, 2.1*len(labels)), 5.5))
            ax.bar([value-width/2 for value in x], calibration, width,
                   label='Calibration 2019-2022')
            ax.bar([value+width/2 for value in x], validation, width,
                   label='Validation 2023')
            ax.set_xticks(x, labels, rotation=18, ha='right')
            ax.set_ylabel('Oats yield RMSE, t/ha')
            ax.set_title('Oats yield-component candidates')
            ax.grid(axis='y', alpha=.25)
            ax.legend()
            fig.tight_layout()
            oats_plot = plot_dir/'oats_yield_candidate_rmse.png'
            fig.savefig(oats_plot, dpi=180)
            plt.close(fig)
            outputs.append(oats_plot)

    oats_n = newest('oats_n_diagnostic_*', ROOT/'outputs'/'multifield')
    if oats_n and (oats_n/'response.csv').is_file():
        response_path = oats_n/'response.csv'
        rows = read_csv(response_path)
        if rows:
            sources.append(response_path)
            fig, ax = plt.subplots(figsize=(7.5, 5.2))
            for field in sorted({row['field'] for row in rows}):
                selected = sorted((row for row in rows if row['field'] == field),
                                  key=lambda row: number(row['dose_apsim_no3n_kg_ha']))
                doses = [number(row['dose_apsim_no3n_kg_ha']) for row in selected]
                yields = [number(row['simulated_yield_t_ha']) for row in selected]
                ax.plot(doses, yields, 'o-', label=f'{field} simulated')
                observed = number(selected[0]['observed_yield_t_ha'])
                ax.axhline(observed, linestyle='--', linewidth=1,
                           label=f'{field} observed ({observed:g})')
            ax.set_xlabel('APSIM NO3N at sowing, kg/ha')
            ax.set_ylabel('Oats grain yield, t/ha')
            ax.set_title('Oats response at observed N30/N60 levels')
            ax.grid(alpha=.25)
            ax.legend(ncols=2)
            fig.tight_layout()
            n_plot = plot_dir/'oats_n_response_current.png'
            fig.savefig(n_plot, dpi=180)
            plt.close(fig)
            outputs.append(n_plot)

    canopy_runs = ROOT/'outputs'/'multifield'/'OATS_CANOPY_CALIBRATION'
    canopy_run = newest('*', canopy_runs)
    if canopy_run and (canopy_run/'summary.json').is_file():
        summary_path = canopy_run/'summary.json'
        summary = json.loads(summary_path.read_text(encoding='utf-8-sig'))
        candidates = summary.get('candidates') or []
        if candidates:
            sources.append(summary_path)
            labels = [row['name'] for row in candidates]
            calibration = [row['metrics']['calibration']['rmse_t_ha']
                           for row in candidates]
            validation = [row['metrics']['validation']['rmse_t_ha']
                          for row in candidates]
            x = list(range(len(labels)))
            width = .36
            fig, ax = plt.subplots(figsize=(max(8, 2.1*len(labels)), 5.5))
            ax.bar([value-width/2 for value in x], calibration, width,
                   label='Calibration 2019-2022')
            ax.bar([value+width/2 for value in x], validation, width,
                   label='Validation 2023')
            ax.set_xticks(x, labels, rotation=18, ha='right')
            ax.set_ylabel('Oats yield RMSE, t/ha')
            ax.set_title('Oats canopy and branching candidates')
            ax.grid(axis='y', alpha=.25)
            ax.legend()
            fig.tight_layout()
            canopy_plot = plot_dir/'oats_canopy_candidate_rmse.png'
            fig.savefig(canopy_plot, dpi=180)
            plt.close(fig)
            outputs.append(canopy_plot)

    write_json(plot_dir/'plots_manifest.json', {
        'latest_checkpoint': latest_checkpoint.name,
        'sources': [{'path': path.relative_to(ROOT).as_posix(),
                     'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                    for path in sources],
        'plots': [path.relative_to(ROOT).as_posix() for path in outputs],
    })
    print(f'QUALITY PLOTS: {plot_dir.resolve()}')


if __name__ == '__main__':
    main()
