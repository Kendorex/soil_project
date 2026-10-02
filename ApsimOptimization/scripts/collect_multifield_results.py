"""Collect the newest compact verification outputs into one tar.gz archive."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT/'outputs'/'multifield'


def newest(pattern, root=OUTPUTS):
    paths = [path for path in root.rglob(pattern) if path.is_dir()]
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def newest_direct(pattern, root):
    paths = [path for path in Path(root).glob(pattern) if path.is_dir()]
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def selected_files(directory, kind):
    if directory is None:
        return []
    top = {
        'baseline': {'config_snapshot.json', 'model_inventory.json', 'preparation.json',
                     'baseline.json', 'historical_comparison.json',
                     'historical_comparison.csv', 'apsim.log'},
        'optimize': {'config_snapshot.json', 'model_inventory.json', 'pareto.json',
                     'evaluations.json', 'history.json', 'pareto_summary.csv',
                     'evaluations_summary.csv'},
        'response': {'summary.json', 'response.csv'},
        'oats': {'summary.json', 'results.csv', 'contrasts.csv'},
        'rye_timing': {'summary.json', 'results.csv', 'contrasts.csv'},
        'forage': {'summary.json', 'forage_harvests.csv'},
        'establishment': {'summary.json', 'results.csv', 'contrasts.csv'},
        'root_access': {'summary.json', 'scenario_metrics.csv', 'field_metrics.csv',
                        'patch_audit.csv'},
        'oats_yield_calibration': {'summary.json', 'candidate_metrics.csv',
                                   'oats_field_predictions.csv'},
        'oats_canopy_calibration': {'summary.json', 'candidate_metrics.csv',
                                    'oats_field_predictions.csv'},
        'quality': {'manifest.json', 'summary.json', 'field_metrics.csv',
                    'crop_trace.csv', 'management_mismatches.csv'},
        'validation': {'validation.log', 'environment.txt'},
    }[kind]
    files = [path for path in directory.iterdir() if path.is_file() and path.name in top]
    if kind in {'optimize', 'response', 'oats', 'rye_timing', 'forage', 'establishment',
                'root_access', 'oats_yield_calibration',
                'oats_canopy_calibration'}:
        for path in directory.rglob('*'):
            if path.is_file() and path.name in {
                    'result.json', 'apsim.log', 'preparation.json',
                    'candidate_provenance.json',
                    'pareto_objectives.png', 'pareto_doses.png',
                    'pareto_plots_manifest.json'}:
                files.append(path)
    return sorted(set(files))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT/f'multifield_check_{datetime.now():%Y%m%d_%H%M%S}.tar.gz')
    parser.add_argument('--extra', type=Path, help='validation log directory to include')
    args = parser.parse_args()

    chosen = [
        ('baseline', newest('baseline_*')),
        ('optimize', newest('optimize_*', OUTPUTS/'DIAGNOSTIC_2023_N')),
        ('response', newest('field_n_diagnostic_*')),
        ('oats', newest('oats_competition_diagnostic_*')),
        ('rye_timing', newest('rye_n_timing_diagnostic_*')),
        ('forage', newest('forage_transition_diagnostic_*')),
        ('establishment', newest('establishment_water_diagnostic_*')),
        ('root_access', newest('soil_root_access_diagnostic_*')),
        ('oats_yield_calibration',
         newest_direct('*', OUTPUTS/'OATS_YIELD_CALIBRATION')),
        ('oats_canopy_calibration',
         newest_direct('*', OUTPUTS/'OATS_CANOPY_CALIBRATION')),
        ('quality', newest('*', OUTPUTS/'quality_history'/'checkpoints')),
    ]
    if args.extra:
        chosen.append(('validation', args.extra.resolve()))
    files = []
    for kind, directory in chosen:
        files.extend(selected_files(directory, kind))
    for path in [
        OUTPUTS/'model_inventory.json', OUTPUTS/'configuration_status.json',
        OUTPUTS/'DIAGNOSTIC_2023_N'/'model_inventory.json',
        OUTPUTS/'DIAGNOSTIC_2023_N'/'configuration_status.json',
        ROOT/'configs'/'multifield.yaml',
        ROOT/'configs'/'multifield_2023_n_diagnostic.yaml',
        ROOT/'data'/'observations'/'multifield_2019_2023.json',
        ROOT/'simulation'/'calibrated'/'full_field2019_2023_calibrated_v2.provenance.json',
        OUTPUTS/'HISTORICAL_REPLAY'/'current_decision_readiness.json',
        OUTPUTS/'FIELD_SOIL_AUDIT'/'field_soil_audit.json',
        OUTPUTS/'FIELD_SOIL_AUDIT'/'field_soil_hashes.csv',
    ]:
        if path.is_file():
            files.append(path)
    for path in (OUTPUTS/'quality_history').glob('*.csv'):
        files.append(path)
    for path in (OUTPUTS/'quality_history'/'plots').glob('*'):
        if path.is_file() and path.suffix.lower() in {'.png', '.json'}:
            files.append(path)
    files = sorted(set(path.resolve() for path in files))
    if not files:
        raise SystemExit('No multifield outputs found')

    manifest = {
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'project_root_name': ROOT.name,
        'selected_runs': {kind: (directory.relative_to(ROOT).as_posix()
                                  if directory and directory.is_relative_to(ROOT) else
                                  str(directory) if directory else None)
                          for kind, directory in chosen},
        'files': [],
    }
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        manifest['files'].append({
            'path': relative,
            'size': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w:gz') as archive:
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        info = tarfile.TarInfo('multifield_check_manifest.json')
        info.size = len(payload)
        info.mtime = int(datetime.now().timestamp())
        archive.addfile(info, io.BytesIO(payload))
        for path in files:
            archive.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)
    print(f'RESULT ARCHIVE: {output}')
    print(json.dumps(manifest['selected_runs'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
