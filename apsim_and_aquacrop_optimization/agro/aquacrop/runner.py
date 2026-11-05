"""Prepare and execute one isolated AquaCrop candidate for the common adapter."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys



from agro.adapters.aquacrop import normalize_results


def execute(request):
    source = Path(request['model_root']).resolve()
    workspace = Path(request['workspace']).resolve()
    # Preparation modules are loaded only inside this candidate process.
    from . import field_config
    field_config.ROOT = workspace
    for name in ('project.json',):
        shutil.copyfile(source / name, workspace / name)
    for name in ('configs', 'fields', 'DATA', 'management'):
        shutil.copytree(source / name, workspace / name)
    if request.get('rotation_populations'):
        from agro.adapters.rotation_management import apply_policies
        if any(set(policy) != {'population_m2'} for policy in request['rotation_populations'].values()):
            raise ValueError('Native AquaCrop rotation overrides support only population_m2')
        path = workspace / 'configs/rotation_fixed.apsimx'
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        document, changes = apply_policies(document, request['rotation_populations'], request['years'])
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
        (workspace / 'population_changes.json').write_text(json.dumps(changes, indent=2), encoding='utf-8')

    from .field_config import CROP_FIELDS, create_catalog, load_json, prepare_field, registry, replace_values, save_json
    from . import results
    results.ROOT = workspace
    registered = registry()
    field_ids = request['field_ids'] if request['field_ids'] is not None else [row['id'] for row in registered]
    known = {row['id'] for row in registered}
    if not field_ids or len(set(field_ids)) != len(field_ids) or set(field_ids) - known:
        raise ValueError('Expected unique registered AquaCrop fields')
    if set(request['decisions']) - set(field_ids):
        raise ValueError('Decisions contain fields outside this scenario')
    catalog = create_catalog()
    plans = []
    for field in registered:
        if field['id'] not in field_ids:
            continue
        if field.get('management_mode') == 'standalone':
            from .standalone_inputs import prepare_field as prepare_standalone
            plan_path = prepare_standalone(field, catalog)
        else:
            plan_path = prepare_field(field, catalog)
        policy = request['decisions'].get(field['id'], {})
        if set(policy) - {'crop_parameters'}:
            raise ValueError('Unsupported AquaCrop decision')
        mapping = load_json(workspace / 'generated' / field['id'] / 'source_mapping.json')
        if any(year < int(mapping['period_start'][:4]) or year > int(mapping['period_end'][:4])
               for year in request['years']):
            raise ValueError('Evaluation year outside the model simulation period')
        overrides = policy.get('crop_parameters', {})
        segment_ids = {row['id'] for row in mapping['segments']}
        if set(overrides) - segment_ids:
            raise ValueError('Unknown AquaCrop segment in crop_parameters')
        for segment_id, parameters in overrides.items():
            if set(parameters) - set(CROP_FIELDS):
                raise ValueError('Unknown AquaCrop crop parameter')
            # Version, type and cycle encodings are format metadata, not decisions.
            if set(parameters) & {'version', 'kind', 'planting_method', 'cycle_mode'}:
                raise ValueError('Crop format metadata cannot be optimized')
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) for value in parameters.values()):
                raise ValueError('Crop parameters must be finite numbers')
            path = workspace / 'generated' / field['id'] / 'DATA' / f'{segment_id}.CRO'
            path.write_text(replace_values(path.read_text(encoding='utf-8'),
                                          {CROP_FIELDS[key]: value for key, value in parameters.items()}),
                            encoding='utf-8')
        plans.append((field, plan_path))
    if request.get('prepare_only'):
        save_json(workspace / 'prepared.json', {'plans': [str(plan) for _, plan in plans]})
        return
    for field, plan in plans:
        subprocess.run([*request['command'], str(plan)], cwd=workspace, check=True)
        results.summarize(field)
    result = normalize_results(workspace, field_ids, request['years'])
    payload = asdict(result)
    payload['provenance']['command'] = request['command']
    payload['provenance']['input_sha256'] = {
        path.relative_to(workspace).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in ('configs', 'fields', 'management', 'generated') for path in sorted((workspace / folder).rglob('*'))
        if path.is_file() and path.suffix != '.jsonl'
    }
    save_json(workspace / 'result.json', payload)


if __name__ == '__main__':
    execute(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
