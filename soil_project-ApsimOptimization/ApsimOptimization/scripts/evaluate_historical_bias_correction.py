#!/usr/bin/env python3
"""Select on 2019-2022 and evaluate one empirical yield correction on 2023."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_bias import blocked_year_selection, error_metrics, predict
from src.multifield_model import write_json
from src.multifield_optimizer import ROOT


def newest_replay_checkpoint():
    paths = []
    for path in (ROOT/'outputs'/'multifield'/'quality_history'/'checkpoints').glob('*'):
        manifest = path/'manifest.json'
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding='utf-8-sig'))
            if data.get('historical_replay') is True:
                paths.append(path)
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def load_rows(path):
    rows = []
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for source in csv.DictReader(stream):
            if not source.get('simulated_yield_t_ha'):
                continue
            rows.append({
                'year': int(source['year']), 'field': source['field'],
                'crop_group': source['crop_group'],
                'observed': float(source['observed_yield_t_ha']),
                'simulated': float(source['simulated_yield_t_ha']),
                'evidence_tier': source['evidence_tier'],
            })
    return rows


def write_csv(path, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, default=newest_replay_checkpoint())
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    if args.checkpoint is None:
        parser.error('no historical replay checkpoint found')
    checkpoint = args.checkpoint.resolve()
    output = (args.output_dir.resolve() if args.output_dir else
              Path(json.loads((checkpoint/'manifest.json').read_text(
                  encoding='utf-8-sig'))['baseline_dir']).parent)
    output.mkdir(parents=True, exist_ok=True)
    rows = load_rows(checkpoint/'field_metrics.csv')
    calibration = [row for row in rows if row['year'] <= 2022]
    validation = [row for row in rows if row['year'] == 2023]
    model, cv = blocked_year_selection(calibration)
    predicted = [{**row, 'raw_prediction': row['simulated'],
                  'corrected_prediction': predict(model, row)} for row in validation]
    raw_rows = [{**row, 'prediction': row['raw_prediction']} for row in predicted]
    corrected_rows = [{**row, 'prediction': row['corrected_prediction']} for row in predicted]
    raw_metrics = error_metrics(raw_rows)
    corrected_metrics = error_metrics(corrected_rows)
    calibration_mean = sum(row['observed'] for row in calibration)/len(calibration)
    naive_rows = [{**row, 'prediction': calibration_mean} for row in validation]
    naive_metrics = error_metrics(naive_rows)
    has_ranking_skill = (corrected_metrics['pearson_r'] is not None and
                         corrected_metrics['pearson_r'] > 0)
    beats_both = (corrected_metrics['rmse_t_ha'] < raw_metrics['rmse_t_ha'] and
                  corrected_metrics['rmse_t_ha'] < naive_metrics['rmse_t_ha'])
    status = ('validated_predictive_improvement' if has_ranking_skill and beats_both else
              'rejected_no_field_ranking_skill_or_no_gain_over_training_mean')
    payload = {
        'status': status,
        'selection_rule': 'lowest blocked-year RMSE using 2019-2022 only',
        'selected_model': model,
        'calibration_years': [2019, 2020, 2021, 2022],
        'validation_year': 2023,
        'raw_2023': raw_metrics,
        'corrected_2023': corrected_metrics,
        'training_mean_2023': naive_metrics,
        'acceptance_checks': {
            'positive_2023_field_ranking_correlation': has_ranking_skill,
            'rmse_better_than_raw_and_training_mean': beats_both,
        },
        'corrected_minus_raw': {
            key: corrected_metrics[key]-raw_metrics[key]
            for key in ('mae_t_ha', 'rmse_t_ha', 'bias_t_ha')},
        'limitations': [
            'This is an empirical output correction, not a replacement for crop/soil process calibration.',
            'Only 27 calibration observations and one ten-field holdout year are available.',
            'Structural forage proxy and 2019 boundary/input-sensitive cases remain in the reported dataset.',
        ],
        'cv_candidates': cv,
        'validation_predictions': predicted,
    }
    write_json(output/'bias_correction_evaluation.json', payload)
    write_csv(output/'bias_correction_cv.csv', cv)
    write_csv(output/'bias_correction_predictions_2023.csv', predicted)

    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 5), sharex=True, sharey=True)
    limit = max(row['observed'] for row in predicted)+0.4
    for ax, key, title in ((axes[0], 'raw_prediction', 'Raw APSIM'),
                           (axes[1], 'corrected_prediction', 'Blocked-year correction')):
        for crop in sorted({row['crop_group'] for row in predicted}):
            group = [row for row in predicted if row['crop_group'] == crop]
            ax.scatter([row['observed'] for row in group], [row[key] for row in group],
                       label=crop, s=45)
        ax.plot([0, limit], [0, limit], 'k--', linewidth=1)
        ax.set(xlim=(0, limit), ylim=(0, limit), xlabel='Observed 2023, t/ha',
               ylabel='Predicted 2023, t/ha', title=title)
        ax.grid(alpha=.25)
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(output/'bias_correction_2023.png', dpi=180)
    plt.close(fig)
    print(f'BIAS CORRECTION: {payload["status"]}')
    print(f'Selected: {cv[0]["name"]}; blocked-year RMSE={cv[0]["rmse_t_ha"]:.3f}')
    print(f'2023 RMSE: raw={raw_metrics["rmse_t_ha"]:.3f}; '
          f'corrected={corrected_metrics["rmse_t_ha"]:.3f}; '
          f'training-mean={naive_metrics["rmse_t_ha"]:.3f}')
    print(f'OUTPUT: {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
