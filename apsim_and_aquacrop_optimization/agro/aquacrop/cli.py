"""Run or prepare AquaCrop models without storing executable code in models."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys

from agro.adapters.aquacrop import AquaCropBackend
from . import field_config


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description='Prepare or execute a configuration-only AquaCrop model')
    parser.add_argument('--model-root', type=Path, default=root/'models/aquacrop')
    parser.add_argument('--field', action='append', help='Field identifier; repeat for several fields')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--list', action='store_true', help='List fields without modifying or running the model')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.field and args.all:
        parser.error('Use either --field or --all')
    model = args.model_root.resolve()
    field_config.ROOT = model
    fields = field_config.registry()
    if args.list:
        for field in fields:
            print(f"{field['id']}: {field['source_zone']} <- {field['source_project']}")
        return 0
    known = {field['id'] for field in fields}
    if args.field and (set(args.field)-known or len(args.field)!=len(set(args.field))):
        parser.error('Expected unique registered field identifiers')
    output = (args.output or root/'runs'/f'aquacrop_{dt.datetime.now():%Y%m%d_%H%M%S_%f}').resolve()
    if output.is_relative_to(root/'models'):
        parser.error('Output must be outside the model directories')
    command = [str(root/'engines/aquacrop/field-target/debug'/(
        'aquacrop-field-runner.exe' if sys.platform=='win32' else 'aquacrop-field-runner'))]
    if args.prepare_only:
        output.mkdir(parents=True,exist_ok=False)
        request={'model_root':str(model),'workspace':str(output),'command':command,
                 'years':list(range(2019,2024)),'field_ids':args.field,'decisions':{},'prepare_only':True}
        path=output/'request.json'
        path.write_text(json.dumps(request,indent=2),encoding='utf-8')
        subprocess.run([sys.executable,'-m','agro.aquacrop.runner',str(path)],cwd=root,check=True)
    else:
        backend=AquaCropBackend(model,command,range(2019,2024),args.field)
        result=backend.simulate({},output)
        (output/'simulation.json').write_text(json.dumps(asdict(result),indent=2,allow_nan=False),encoding='utf-8')
    print(output)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
