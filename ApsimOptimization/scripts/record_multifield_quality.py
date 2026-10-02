#!/usr/bin/env python3
"""Record an immutable model-quality checkpoint and rebuild plot-ready histories."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.multifield_optimizer import ROOT
from src.multifield_quality import record_checkpoint


def newest_baseline():
    choices = [path for path in (ROOT/'outputs'/'multifield').rglob('baseline_*')
               if (path/'candidate.db').is_file()]
    return max(choices, key=lambda path: path.stat().st_mtime) if choices else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, default=newest_baseline())
    parser.add_argument('--observations', type=Path,
                        default=ROOT/'data'/'observations'/'multifield_2019_2023.json')
    parser.add_argument('--quality-root', type=Path,
                        default=ROOT/'outputs'/'multifield'/'quality_history')
    parser.add_argument('--label', required=True,
                        help='short stage name, e.g. baseline_before_oats_diagnostics')
    parser.add_argument('--notes', default='')
    args = parser.parse_args(argv)
    if args.baseline is None:
        parser.error('no baseline run found; pass --baseline')
    directory, summary = record_checkpoint(
        args.baseline, args.observations, args.quality_root, args.label, args.notes)
    print(f'QUALITY CHECKPOINT: {directory.resolve()}')
    print(f"All years: n={summary['overall']['n']}; "
          f"MAE={summary['overall']['mae_t_ha']:.3f}; "
          f"RMSE={summary['overall']['rmse_t_ha']:.3f}; "
          f"bias={summary['overall']['bias_t_ha']:.3f}")
    print(f"2023: n={summary['year_2023']['n']}; "
          f"MAE={summary['year_2023']['mae_t_ha']:.3f}; "
          f"RMSE={summary['year_2023']['rmse_t_ha']:.3f}; "
          f"bias={summary['year_2023']['bias_t_ha']:.3f}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
