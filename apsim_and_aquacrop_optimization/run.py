"""Explicit Python entry point for the three supported optimization models."""
from pathlib import Path
import argparse
import sys

ROOT=Path(__file__).resolve().parent
if (ROOT/'.deps').is_dir():
    sys.path.insert(0,str(ROOT/'.deps'))


def main(argv=None):
    parser=argparse.ArgumentParser(
        description='Optimize APSIM, AquaCrop + APSIM, or standalone AquaCrop.',
        epilog='Pass --run to optimize, --prepare-only to prepare inputs, or --verify-model for one simulation. '
               'Also supported: --objectives, --algorithm, --population, --generations, --output.')
    parser.add_argument('model',choices=['apsim','coupled','aquacrop'],
                        help='apsim: APSIM only; coupled: AquaCrop harvest + APSIM soil; aquacrop: standalone AquaCrop')
    args,remaining=parser.parse_known_args(argv)
    if args.model=='aquacrop':
        from agro.aquacrop.standalone import main as execute
        return execute(remaining)
    from agro.planning.future_scenarios import main as execute
    config='future_rotation_2025_2029.json' if args.model=='apsim' else 'future_rotation_aquacrop_2025_2029.json'
    engine='apsim' if args.model=='apsim' else 'aquacrop'
    return execute(['--config',str(ROOT/'models/apsim/configs'/config),'--engine',engine,*remaining])


if __name__=='__main__':
    raise SystemExit(main())
