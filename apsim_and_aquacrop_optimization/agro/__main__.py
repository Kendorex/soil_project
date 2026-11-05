"""Command-line entry point for either engine through the same optimization path."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import datetime as dt
import json
import os
from pathlib import Path

from agro.adapters.apsim import ApsimBackend
from agro.adapters.aquacrop import AquaCropBackend
from agro.evaluation.problem import SimulationEvaluator, harvested_dry_matter
from agro.optimization.nsga2 import search_integer, nondominated
from agro.configuration import resolved_config


def create_backend(config, base):
    def resolve(value):
        path = Path(value)
        return (base / path).resolve() if not path.is_absolute() else path

    if config['engine'] == 'aquacrop':
        command = list(config['command'])
        command[0] = str(resolve(command[0]))
        return AquaCropBackend(resolve(config['model_root']), command, config['years'],
                               config.get('field_ids'), config.get('timeout_seconds', 600))
    if config['engine'] == 'apsim':
        from agro.apsim.multifield_optimizer import load_config
        model = load_config(resolve(config['model_config']))
        if config.get('command'):
            model['runtime']['command'] = config['command']
        elif os.environ.get('APSIM_MODELS'):
            model['runtime']['command'] = [os.environ['APSIM_MODELS']]
        return ApsimBackend(model, config['years'], config.get('source_reports', 'preserve'))
    raise ValueError(f"Unknown engine: {config['engine']}")


def decode(variables, vector, defaults):
    decisions = deepcopy(defaults)
    for spec, index in zip(variables, vector):
        node = decisions
        for part in spec['path'][:-1]:
            node = node.setdefault(part, {})
        node[spec['path'][-1]] = spec['values'][int(index)]
    return decisions


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run APSIM or AquaCrop through common simulation contracts')
    parser.add_argument('--config', type=Path, required=True, help='JSON model and search specification')
    parser.add_argument('--output', type=Path, help='New run directory (must not exist)')
    parser.add_argument('--simulate-only', action='store_true', help='Calculate the default decision once')
    args = parser.parse_args(argv)
    path = args.config.resolve()
    config = resolved_config(path)
    if config.get('native_full_simulation'):
        if config['engine'] != 'apsim' or not args.simulate_only:
            raise ValueError('Native APSIM exports require --simulate-only')
        if config.get('decisions') or config.get('variables'):
            raise ValueError('Native full simulation preserves exported management')
        from agro.apsim.native_simulation import run
        output = (args.output or Path('runs') / dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')).resolve()
        if os.environ.get('APSIM_MODELS'):
            config['command'] = [os.environ['APSIM_MODELS']]
        print(run(config, output, path))
        return 0
    backend = create_backend(config, path.parent)
    defaults = config.get('decisions', {})
    if 'decisions' not in config and config['engine'] == 'apsim' and not args.simulate_only:
        defaults = deepcopy(backend.config.get('reference_decision') or {})
        if not defaults:
            raise ValueError('APSIM search requires explicit decisions or a model reference_decision')
    variables = config.get('variables', [])
    paths = []
    for variable in variables:
        parts = variable['path']
        if len(parts) < 2 or not all(isinstance(part, str) and part for part in parts):
            raise ValueError('Variable path must identify a field and decision parameter')
        if not variable['values']:
            raise ValueError('Every variable must have at least one value')
        paths.append(tuple(parts))
    if len(paths) != len(set(paths)) or any(
            a != b and b[:len(a)] == a for a in paths for b in paths):
        raise ValueError('Variable paths must be unique and must not overlap')
    decision_names = {parts[1] for parts in paths}
    decision_names.update(key for policy in defaults.values() for key in policy)
    backend.capabilities.require(config.get('required_metrics', ['harvested_dm_kg_ha']), decision_names)
    output = (args.output or Path('runs') / dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'config.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    (output / 'execution.json').write_text(json.dumps({
        'source_config': str(path), 'simulate_only': args.simulate_only,
    }, indent=2), encoding='utf-8')
    if args.simulate_only:
        result = backend.simulate(defaults, output / 'baseline')
        (output / 'simulation.json').write_text(json.dumps(asdict(result), indent=2, allow_nan=False), encoding='utf-8')
    else:
        if not variables:
            raise ValueError('Optimization requires variables; use --simulate-only for a single calculation')
        evaluator = SimulationEvaluator(backend, harvested_dry_matter, output / 'candidates', 1,
                                        required_metrics=['harvested_dm_kg_ha'],
                                        required_decisions=decision_names)
        def evaluate(vector):
            return evaluator(decode(variables, vector, defaults))
        search_integer([spec['values'] for spec in variables], evaluate, config['nsga2'], 1, 0)
        evaluations = list(evaluator.cache.values())
        (output / 'evaluations.json').write_text(json.dumps(evaluations, indent=2), encoding='utf-8')
        (output / 'pareto.json').write_text(json.dumps(nondominated(evaluations), indent=2), encoding='utf-8')
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
