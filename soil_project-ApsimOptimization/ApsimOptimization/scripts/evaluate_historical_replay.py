#!/usr/bin/env python3
"""Run a controlled historical N replay and archive its validation evidence."""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.historical_case import (compare_historical, load_observations,
                                 write_comparison_csv)
from src.historical_readiness import evaluate_historical_readiness
from src.multifield_model import fingerprint, inventory, prepare, write_json
from src.multifield_optimizer import ROOT, load_config, run_apsim
from src.multifield_quality import metrics, record_checkpoint
from src.multifield_results import read_results


QUALITY = ROOT/'outputs'/'multifield'/'quality_history'


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def run_case(config, directory, observations, replay_enabled):
    case = copy.deepcopy(config)
    case['historical_replay']['enabled'] = replay_enabled
    directory.mkdir(parents=True, exist_ok=False)
    meta = inventory(case)
    write_json(directory/'config_snapshot.json', case)
    write_json(directory/'model_inventory.json', meta)
    model = prepare(case, directory)
    database = run_apsim(case, model)
    result = read_results(database, meta, case['year'], case,
                          audit_applications=False)
    write_json(directory/'baseline.json', result)
    comparison = compare_historical(result, meta, observations, case['year'])
    write_json(directory/'historical_comparison.json', comparison)
    write_comparison_csv(directory/'historical_comparison.csv', comparison)
    return case, meta


def scoped_metrics(rows):
    return {
        'all_2019_2023': metrics(rows),
        'calibration_2019_2022': metrics(
            [row for row in rows if int(row['year']) <= 2022]),
        'validation_2023': metrics(
            [row for row in rows if int(row['year']) == 2023]),
    }


def numeric(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def comparison_payload(control_checkpoint, replay_checkpoint):
    control_rows = read_csv(control_checkpoint/'field_metrics.csv')
    replay_rows = read_csv(replay_checkpoint/'field_metrics.csv')
    control = scoped_metrics(control_rows)
    replay = scoped_metrics(replay_rows)
    deltas = {}
    for scope in control:
        deltas[scope] = {
            key: replay[scope][key]-control[scope][key]
            for key in ('mae_t_ha', 'rmse_t_ha', 'bias_t_ha')
            if replay[scope][key] is not None and control[scope][key] is not None
        }
    by_control = {(row['year'], row['field']): row for row in control_rows}
    field_rows = []
    for row in replay_rows:
        key = (row['year'], row['field'])
        old = by_control[key]
        old_yield = numeric(old.get('simulated_yield_t_ha'))
        new_yield = numeric(row.get('simulated_yield_t_ha'))
        field_rows.append({
            'year': int(row['year']),
            'field': row['field'],
            'crop_group': row['crop_group'],
            'observed_yield_t_ha': numeric(row['observed_yield_t_ha']),
            'control_simulated_yield_t_ha': old_yield,
            'replay_simulated_yield_t_ha': new_yield,
            'yield_change_t_ha': (new_yield-old_yield
                                  if old_yield is not None and new_yield is not None else None),
            'control_absolute_error_t_ha': numeric(old.get('absolute_error_t_ha')),
            'replay_absolute_error_t_ha': numeric(row.get('absolute_error_t_ha')),
            'observed_n_label_kg_ha': numeric(row.get('observed_n_label_kg_ha')),
            'control_model_n_kg_ha': numeric(old.get('model_fertilizer_at_sowing_kg_ha')),
            'replay_model_n_kg_ha': numeric(row.get('model_fertilizer_at_sowing_kg_ha')),
            'replayed': row.get('model_fertilizer_amount_source') == 'historical_n_label_replay',
            'replay_flags': row.get('diagnostic_flags'),
        })
    validation_delta = deltas['validation_2023']['rmse_t_ha']
    calibration_delta = deltas['calibration_2019_2022']['rmse_t_ha']
    if validation_delta < 0 and calibration_delta <= 0:
        status = 'accuracy_improved_on_calibration_and_validation'
    elif validation_delta < 0:
        status = 'validation_improved_but_calibration_mixed'
    else:
        status = 'no_validation_accuracy_gain_mechanistic_calibration_required'
    selected_case = ('replay' if status.startswith('accuracy_improved') or
                     status == 'validation_improved_but_calibration_mixed' else 'control')
    readiness = {
        'control': evaluate_historical_readiness(control_rows),
        'replay': evaluate_historical_readiness(replay_rows),
    }
    return {
        'experiment': 'observed_n_label_historical_replay',
        'acceptance_status': status,
        'control': control,
        'replay': replay,
        'replay_minus_control': deltas,
        'selected_historical_case': selected_case,
        'decision_readiness': {
            'selected_case': selected_case,
            'selected': readiness[selected_case],
            'by_case': readiness,
        },
        'replay_scope': {
            'changed_quantity': 'fertilizer amount on an exact model sowing event',
            'amount_source': 'explicit N rate in the supplied technology label',
            'fertilizer_type': 'preserved from the APSIMX manager',
            'unchanged_unknowns': ['P and K response', 'fertilizer product',
                                   'independent application date', 'placement'],
        },
        'fields': field_rows,
    }


def write_field_comparison_csv(path, rows):
    fields = list(rows[0])
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_report(path, payload, control_checkpoint, replay_checkpoint):
    def value(value):
        return f'{value:.3f}' if value is not None else 'NA'

    def line(scope, title):
        old = payload['control'][scope]
        new = payload['replay'][scope]
        delta = payload['replay_minus_control'][scope]
        return (f'| {title} | {old["n"]} | {old["mae_t_ha"]:.3f} | '
                f'{new["mae_t_ha"]:.3f} | {delta["mae_t_ha"]:+.3f} | '
                f'{old["rmse_t_ha"]:.3f} | {new["rmse_t_ha"]:.3f} | '
                f'{delta["rmse_t_ha"]:+.3f} |')
    changed = [row for row in payload['fields'] if row['replayed']]
    changed.sort(key=lambda row: abs(row['yield_change_t_ha'] or 0), reverse=True)
    body = [
        '# Historical N replay evaluation', '',
        f'Acceptance status: `{payload["acceptance_status"]}`', '',
        'The control uses the original static field fertilizer managers. The replay changes only '
        'the N amount on sowing events that have an explicit N rate and sowing date in the supplied '
        'observation file. APSIM fertilizer type is preserved.', '',
        '| Scope | n | Control MAE | Replay MAE | Change | Control RMSE | Replay RMSE | Change |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
        line('calibration_2019_2022', 'Calibration 2019-2022'),
        line('validation_2023', 'Validation 2023'),
        line('all_2019_2023', 'All observations'), '',
        '## Decision-readiness gate', '',
        f'Selected historical case: `{payload["selected_historical_case"]}`', '',
        f'Status: `{payload["decision_readiness"]["selected"]["status"]}`', '',
        '| Check | Result |',
        '|---|---|',
    ]
    for name, check in payload['decision_readiness']['selected']['checks'].items():
        body.append(f'| {name} | {"PASS" if check["passed"] else "FAIL"} |')
    selected = payload['decision_readiness']['selected']
    body += [
        '',
        f'Validation RMSE: `{value(selected["validation_model_metrics"]["rmse_t_ha"])} t/ha`; '
        f'training-mean RMSE: `{value(selected["validation_training_mean_metrics"]["rmse_t_ha"])} t/ha`; '
        f'Spearman field rank: `{value(selected["validation_model_metrics"]["spearman_r"])}`.', '',
        selected['threshold_policy'], '',
        '## Largest field effects', '',
        '| Year/field | Crop | Observed N | Control N | Replay N | Yield change, t/ha |',
        '|---|---|---:|---:|---:|---:|',
    ]
    for row in changed[:12]:
        body.append(
            f'| {row["year"]}/{row["field"]} | {row["crop_group"]} | '
            f'{row["observed_n_label_kg_ha"]:g} | {row["control_model_n_kg_ha"]:g} | '
            f'{row["replay_model_n_kg_ha"]:g} | {row["yield_change_t_ha"]:+.3f} |')
    body += [
        '', 'P/K labels remain diagnostic flags because the APSIM model has no responsive P/K '
        'pools. The result cannot establish fertilizer dose bounds, nutrient target intervals, '
        'products, prices, or ratios.', '',
        f'Control checkpoint: `{control_checkpoint.relative_to(ROOT).as_posix()}`', '',
        f'Replay checkpoint: `{replay_checkpoint.relative_to(ROOT).as_posix()}`', '',
    ]
    Path(path).write_text('\n'.join(body), encoding='utf-8')


def create_archive(path, evaluation_dir, checkpoints):
    files = []
    allowed_case = {'config_snapshot.json', 'model_inventory.json', 'preparation.json',
                    'baseline.json', 'historical_comparison.json',
                    'historical_comparison.csv', 'apsim.log'}
    for case in ('control', 'replay'):
        files.extend(p for p in (evaluation_dir/case).iterdir()
                     if p.is_file() and p.name in allowed_case)
    files.extend(p for p in evaluation_dir.iterdir() if p.is_file())
    for checkpoint in checkpoints:
        files.extend(p for p in checkpoint.iterdir() if p.is_file())
    files.extend(p for p in QUALITY.glob('*.csv') if p.is_file())
    files.extend(p for p in (QUALITY/'plots').glob('*')
                 if p.is_file() and p.suffix.lower() in {'.png', '.json'})
    files += [ROOT/'configs'/'multifield_historical_replay.yaml',
              ROOT/'data'/'observations'/'multifield_2019_2023.json',
              ROOT/'data'/'weather'/'weather_2019_2023_units_corrected.provenance.json',
              ROOT/'simulation'/'calibrated'/'full_field2019_2023_calibrated_v2.provenance.json']
    files = sorted(set(p.resolve() for p in files if p.is_file()))
    manifest = {
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'evaluation_dir': evaluation_dir.relative_to(ROOT).as_posix(),
        'checkpoints': [p.relative_to(ROOT).as_posix() for p in checkpoints],
        'files': [{'path': p.relative_to(ROOT).as_posix(), 'size': p.stat().st_size,
                   'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
    }
    with tarfile.open(path, 'w:gz') as archive:
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        info = tarfile.TarInfo('historical_replay_manifest.json')
        info.size = len(data)
        info.mtime = int(dt.datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(data))
        for file in files:
            archive.add(file, arcname=file.relative_to(ROOT).as_posix(), recursive=False)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--config', type=Path,
                        default=ROOT/'configs'/'multifield_historical_replay.yaml')
    args = parser.parse_args(argv)
    config = load_config(args.config)
    observations = load_observations(config['historical_observations'])
    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    evaluation_dir = Path(config['output_dir'])/f'evaluation_{stamp}'
    evaluation_dir.mkdir(parents=True, exist_ok=False)
    print(f'Historical evaluation: {evaluation_dir}', flush=True)

    print('[1/4] Static-manager control', flush=True)
    run_case(config, evaluation_dir/'control', observations, False)
    control_checkpoint, _ = record_checkpoint(
        evaluation_dir/'control', config['historical_observations'], QUALITY,
        f'historical_static_control_{stamp}',
        'Control for the observed-N historical replay experiment')

    print('[2/4] Observed N-label replay', flush=True)
    run_case(config, evaluation_dir/'replay', observations, True)
    replay_checkpoint, _ = record_checkpoint(
        evaluation_dir/'replay', config['historical_observations'], QUALITY,
        f'historical_n_replay_{stamp}',
        'N amount replayed from supplied technology labels; model product and sowing event preserved')

    print('[3/4] Train/validation comparison and plots', flush=True)
    payload = comparison_payload(control_checkpoint, replay_checkpoint)
    payload['inputs'] = {
        'config': str(args.config.resolve()),
        'model_sha256': fingerprint(config['model']),
        'weather_sha256': fingerprint(config['weather']),
        'observations_sha256': fingerprint(config['historical_observations']),
    }
    readiness_artifact = {
        **payload['decision_readiness'],
        'inputs': payload['inputs'],
        'evaluation_dir': str(evaluation_dir.resolve()),
    }
    write_json(evaluation_dir/'historical_replay_evaluation.json', payload)
    write_json(evaluation_dir/'decision_readiness.json', readiness_artifact)
    write_json(Path(config['output_dir'])/'current_decision_readiness.json', readiness_artifact)
    write_field_comparison_csv(evaluation_dir/'historical_replay_field_comparison.csv',
                               payload['fields'])
    write_report(evaluation_dir/'historical_replay_evaluation.md', payload,
                 control_checkpoint, replay_checkpoint)
    subprocess.run([sys.executable, str(ROOT/'scripts'/'plot_multifield_quality.py')],
                   cwd=ROOT, check=True)
    subprocess.run([
        sys.executable, str(ROOT/'scripts'/'evaluate_historical_bias_correction.py'),
        '--checkpoint', str(replay_checkpoint), '--output-dir', str(evaluation_dir),
    ], cwd=ROOT, check=True)

    print('[4/4] Compact evidence archive', flush=True)
    archive = ROOT/f'historical_replay_{stamp}.tar.gz'
    create_archive(archive, evaluation_dir, [control_checkpoint, replay_checkpoint])
    validation = payload['replay_minus_control']['validation_2023']['rmse_t_ha']
    calibration = payload['replay_minus_control']['calibration_2019_2022']['rmse_t_ha']
    print(f'STATUS: {payload["acceptance_status"]}', flush=True)
    print(f'READINESS: {payload["decision_readiness"]["selected"]["status"]}', flush=True)
    print(f'RMSE change: calibration={calibration:+.3f} t/ha; validation={validation:+.3f} t/ha',
          flush=True)
    print(f'REPORT: {evaluation_dir / "historical_replay_evaluation.md"}', flush=True)
    print(f'SEND THIS ARCHIVE: {archive}', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
