#!/usr/bin/env python3
"""Rebuild plot-ready CSV tables for a completed oats calibration run."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.calibrate_oats_yield_components import write_experiment_tables


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    args = parser.parse_args(argv)
    summary = json.loads((args.run_dir/'summary.json').read_text(encoding='utf-8-sig'))
    write_experiment_tables(args.run_dir, summary['candidates'], summary['decision'])
    print(f'OATS TABLES: {args.run_dir.resolve()}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
