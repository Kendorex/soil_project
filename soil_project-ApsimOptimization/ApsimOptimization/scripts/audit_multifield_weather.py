#!/usr/bin/env python3
"""Audit the supplied multi-year .met and create a traceable units correction."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.met_weather import audit_met, write_units_corrected
from src.multifield_model import write_json
from src.multifield_optimizer import ROOT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=ROOT/'data'/'weather'/'weather_2019_2023_normalized.met')
    parser.add_argument('--output', type=Path,
                        default=ROOT/'data'/'weather'/'weather_2019_2023_units_corrected.met')
    args = parser.parse_args(argv)
    source, output = args.source.resolve(), args.output.resolve()
    before, after = write_units_corrected(source, output)
    provenance = {
        'created_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'source': str(source), 'source_sha256': sha(source),
        'output': str(output), 'output_sha256': sha(output),
        'changes': [
            {'column': 'sp', 'operation': 'multiply every daily value by 100',
             'reason': 'column unit is Pa while values were in the hPa numerical range'},
            {'metadata': 'tav', 'operation': 'recalculate from daily (maxt+mint)/2'},
            {'metadata': 'amp', 'operation': 'mean annual range of monthly mean temperature'},
        ],
        'daily_weather_changed_except_sp': False,
        'source_audit': before, 'output_audit': after,
    }
    write_json(output.with_suffix('.provenance.json'), provenance)
    print(f'WEATHER AUDIT: {before["issues"]}')
    print(f'Corrected tav={after["calculated_tav"]:.3f}; amp={after["calculated_amp"]:.3f}; '
          f'median sp={after["surface_pressure_median"]:.1f} Pa')
    print(f'CORRECTED WEATHER: {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
