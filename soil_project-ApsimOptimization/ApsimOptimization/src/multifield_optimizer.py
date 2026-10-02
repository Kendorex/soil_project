"""Configuration, APSIM execution and mixed-variable pymoo NSGA-II."""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from .historical_case import (compare_historical, load_observations,
                              validate_historical_mapping, write_comparison_csv)
from .multifield_model import fingerprint, inventory, prepare, write_json
from .multifield_results import aggregate_scenario_scores, finite, read_results, score

ROOT = Path(__file__).resolve().parents[1]


def load_config(path):
    import yaml
    path = Path(path).resolve()
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    for name in ('model', 'model_provenance', 'weather', 'weather_provenance',
                 'historical_readiness',
                 'rotation_parameter_reference', 'output_dir', 'historical_observations'):
        if config.get(name):
            p = Path(config[name])
            config[name] = str((path.parent / p).resolve() if not p.is_absolute() else p)
    raw_scenarios = config.get('environment_scenarios')
    for scenario in raw_scenarios if isinstance(raw_scenarios, list) else []:
        if not isinstance(scenario, dict):
            continue
        for name in ('model', 'model_provenance', 'weather',
                     'weather_provenance', 'rotation_parameter_reference'):
            if scenario.get(name):
                p = Path(scenario[name])
                scenario[name] = str((path.parent / p).resolve()
                                     if not p.is_absolute() else p)
    # A native Ubuntu/WSL installation can override the machine-specific runtime
    # without modifying a versioned scenario file.
    if os.environ.get('APSIM_MODELS'):
        config.setdefault('runtime', {})['command'] = [os.environ['APSIM_MODELS']]
    return config


def environment_scenarios(config):
    """Return explicit scenario specs, preserving legacy single-model configs."""
    scenarios = config.get('environment_scenarios')
    return list(scenarios) if scenarios is not None else [{'name': 'calibrated_baseline'}]


def environment_config(config, scenario):
    result = copy.deepcopy(config)
    result.pop('environment_scenarios', None)
    result.pop('scenario_aggregation', None)
    for key in ('model', 'weather'):
        if scenario.get(key):
            result[key] = scenario[key]
            # Never retain provenance for a different artifact.  A scenario may
            # explicitly supply its own provenance; otherwise verification is
            # disabled and the scenario still records the artifact hash.
            provenance_key = key + '_provenance'
            result[provenance_key] = scenario.get(provenance_key)
        elif key + '_provenance' in scenario:
            result[key + '_provenance'] = scenario.get(key + '_provenance')
    if scenario.get('rotation_parameter_reference'):
        result['rotation_parameter_reference'] = scenario['rotation_parameter_reference']
    return result


def require_compatible_environment(reference, candidate, name):
    """Fail closed when an environment variant also changes management structure."""
    if (candidate['simulation'] != reference['simulation'] or
            candidate['start'] != reference['start'] or candidate['end'] != reference['end'] or
            candidate['fields'].keys() != reference['fields'].keys()):
        raise ValueError(f'environment_scenarios.{name}: incompatible simulation, clock, or fields')
    for field in reference['fields']:
        left, right = reference['fields'][field], candidate['fields'][field]
        if (left['area_ha'] != right['area_ha'] or left['crops'] != right['crops'] or
                left['rotation'] != right['rotation'] or
                left['baseline_fertilizer'] != right['baseline_fertilizer']):
            raise ValueError(
                f'environment_scenarios.{name}: {field} changes management or crop structure')


def configuration_errors(config, meta):
    """Collect all missing domain inputs before starting expensive simulations."""
    errors = []
    if config.get('schema_version', 1) != 1:
        errors.append('schema_version: unsupported schema')
    scenarios = environment_scenarios(config)
    if not scenarios:
        errors.append('environment_scenarios: at least one scenario required')
    scenario_names = []
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            errors.append(f'environment_scenarios.{index}: mapping required')
            continue
        name = scenario.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name):
            errors.append(f'environment_scenarios.{index}.name: safe unique name required')
        else:
            scenario_names.append(name)
        unknown = set(scenario) - {
            'name', 'model', 'model_provenance', 'weather',
            'weather_provenance', 'rotation_parameter_reference'
        }
        if unknown:
            errors.append(f'environment_scenarios.{index}: unsupported keys {sorted(unknown)}')
        for key in ('model', 'model_provenance', 'weather',
                    'weather_provenance', 'rotation_parameter_reference'):
            if scenario.get(key) and not Path(scenario[key]).is_file():
                errors.append(f'environment_scenarios.{index}.{key}: file not found')
    if len(scenario_names) != len(set(scenario_names)):
        errors.append('environment_scenarios: names must be unique')
    aggregation = config.get('scenario_aggregation')
    if len(scenarios) > 1 and not isinstance(aggregation, dict):
        errors.append('scenario_aggregation: required for multiple environment scenarios')
    if aggregation is not None:
        if not isinstance(aggregation, dict):
            errors.append('scenario_aggregation: mapping required')
        else:
            if set(aggregation) != {'yield', 'profit', 'soil'}:
                errors.append('scenario_aggregation: set yield, profit, and soil exactly')
            if aggregation.get('yield') not in {'mean', 'worst_case'}:
                errors.append('scenario_aggregation.yield: mean or worst_case required')
            if aggregation.get('profit') not in {'mean', 'worst_case'}:
                errors.append('scenario_aggregation.profit: mean or worst_case required')
            if aggregation.get('soil') != 'worst_case':
                errors.append('scenario_aggregation.soil: worst_case required')

    def number(value, label, low=None):
        try:
            v = finite(value, label)
            if low is not None and v < low:
                raise ValueError(f'{label}: must be >= {low}')
            return v
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
            return None

    def interval(lo, hi, label, positive_width=True):
        a, b = number(lo, label+'.min'), number(hi, label+'.max')
        if a is not None and b is not None and (a >= b if positive_width else a > b):
            errors.append(label+': invalid interval')

    def optional_number(value, label):
        if value is None:
            return None
        return number(value, label)

    year = config.get('year')
    if type(year) is not int or not int(meta['start'][:4]) <= year <= int(meta['end'][:4]):
        errors.append('year: select a year within the calibrated clock')
    period = config.get('decision_period') or {}
    period_start = period_end = None
    try:
        period_start = dt.date.fromisoformat(period.get('start', ''))
        period_end = dt.date.fromisoformat(period.get('end', ''))
        clock_start = dt.date.fromisoformat(meta['start'][:10])
        clock_end = dt.date.fromisoformat(meta['end'][:10])
        if period_start > period_end or period_start < clock_start or period_end > clock_end:
            raise ValueError()
        if type(year) is int and not (period_start.year <= year <= period_end.year):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        errors.append('decision_period: set valid inclusive start/end dates inside the model clock and spanning year')
    if config.get('planning_mode') != 'historical_fixed_rotation':
        errors.append('planning_mode: historical_fixed_rotation required for the historical case')
    scenario_kind = (config.get('scenario') or {}).get('kind')
    if scenario_kind != 'diagnostic':
        readiness_path = config.get('historical_readiness')
        if not readiness_path or not Path(readiness_path).is_file():
            errors.append('historical_readiness: a readable decision-readiness artifact is required')
        else:
            try:
                readiness = json.loads(Path(readiness_path).read_text(encoding='utf-8-sig'))
                selected = readiness.get('selected') or {}
                inputs = readiness.get('inputs') or {}
                if inputs.get('model_sha256') != fingerprint(config['model']):
                    errors.append('historical_readiness: model hash differs from validated model')
                if inputs.get('weather_sha256') != fingerprint(config['weather']):
                    errors.append('historical_readiness: weather hash differs from validated weather')
                if selected.get('recommendation_allowed') is not True:
                    errors.append('historical_readiness: historical validation does not allow recommendations')
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                errors.append(f'historical_readiness: invalid artifact: {exc}')
    if config.get('rotation_transition_policy') not in (
            'preserve_source', 'harvest_on_winter_sowing_proxy'):
        errors.append('rotation_transition_policy: select preserve_source or '
                      'harvest_on_winter_sowing_proxy')
    observations_path = config.get('historical_observations')
    if observations_path and not Path(observations_path).is_file():
        errors.append('historical_observations: configured file is not readable')
    elif observations_path and type(year) is int:
        try:
            validate_historical_mapping(meta, load_observations(observations_path), year)
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f'historical_observations: {exc}')
    if config.get('domain_values_confirmed') is not True:
        errors.append('domain_values_confirmed: must be true after reviewing domain inputs')
    if not config.get('currency'):
        errors.append('currency: required')
    if config.get('yield_aggregation') != 'total_harvest_dry_matter_kg':
        errors.append('yield_aggregation: explicitly select total_harvest_dry_matter_kg')
    if config.get('soil_constraint_mode', 'hard') not in ('hard', 'objective_only'):
        errors.append('soil_constraint_mode: select hard or objective_only')
    options = config.get('fertilizer_options') or {}
    if not options:
        errors.append('fertilizer_options: no approved products or mixtures')
    for name, option in options.items():
        number(option.get('price_per_kg_product'), f'{name}.price_per_kg_product', 0)
        components = option.get('components') or []
        if not components:
            errors.append(f'{name}: components required')
        fractions = []
        for c in components:
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', str(c.get('apsim_type', ''))):
                errors.append(f'{name}: invalid apsim_type')
            fractions.append(number(c.get('mass_fraction'), f'{name}.mass_fraction', 0))
            conversion = number(c.get('apsim_amount_per_kg_product'), f'{name}.apsim_amount_per_kg_product', 0)
            if conversion == 0:
                errors.append(f'{name}: conversion must be positive')
        if all(v is not None for v in fractions) and not math.isclose(sum(fractions), 1.0, abs_tol=1e-9):
            errors.append(f'{name}: component mass fractions must sum to 1')
    fields = config.get('fields') or {}
    if fields.keys() != meta['fields'].keys():
        errors.append('fields: configuration must match all model fields exactly')
    for field in meta['fields']:
        fs = fields.get(field) or {}
        number(meta['fields'][field]['area_ha'], f'{field}.model_area_ha', 0)
        if meta['fields'][field]['area_ha'] <= 0:
            errors.append(f'{field}: area must be positive')
        area = number(fs.get('area_ha'), f'{field}.area_ha', 0)
        if area is not None and area <= 0:
            errors.append(f'{field}.area_ha: must be positive')
        products = fs.get('objective_yield_components') or []
        rotation = meta['fields'][field]['rotation']['RotationCrops'].split(',')
        active = set()
        if type(year) is int:
            index = year - int(meta['start'][:4])
            if 0 <= index < len(rotation):
                active = set(rotation[index].strip().split('+'))
        if (not products or len(set(products)) != len(products) or
                set(products) - set(meta['fields'][field]['crops']) or
                (active and set(products) - active)):
            errors.append(f'{field}.objective_yield_components: select unique harvested products from the active rotation')
        for crop in products:
            ec = ((fs.get('product_economics') or {}).get(crop) or
                  (config.get('crop_economics') or {}).get(crop) or {})
            number(ec.get('price_per_kg'), f'{field}.{crop}.price_per_kg', 0)
            number(ec.get('marketable_kg_per_kg_dm'),
                   f'{field}.{crop}.marketable_kg_per_kg_dm', 0)
        allowed = fs.get('fertilizer_options') or []
        if not allowed or set(allowed) - options.keys() or len(set(allowed)) != len(allowed):
            errors.append(f'{field}.fertilizer_options: select unique configured options')
        bounds = fs.get('dose_kg_product_ha') or {}
        interval(bounds.get('min'), bounds.get('max'), field+'.dose_kg_product_ha', False)
        number(bounds.get('min'), field+'.dose_kg_product_ha.min', 0)
        for k in ('application_cost_per_ha', 'other_cost_per_ha', 'application_depth_mm'):
            number(fs.get(k), field+'.'+k, 0)
        dates = fs.get('application_dates') or []
        if not dates or len(set(dates)) != len(dates):
            errors.append(f'{field}.application_dates: explicit unique dates required')
        for date in dates:
            try:
                application_date = dt.date.fromisoformat(date)
                if period_start is None or period_end is None or not period_start <= application_date <= period_end:
                    raise ValueError('date outside decision period')
            except (ValueError, TypeError):
                errors.append(f'{field}: invalid application date {date}')
        nutrients = fs.get('nutrients') or []
        if not nutrients:
            errors.append(f'{field}.nutrients: approved soil intervals required')
        for n in nutrients:
            label = field+'.'+str(n.get('name'))
            lo = optional_number(n.get('min'), label+'.min')
            hi = optional_number(n.get('max'), label+'.max')
            if lo is None and hi is None:
                errors.append(label+': set min, max, or both')
            elif lo is not None and hi is not None and lo >= hi:
                errors.append(label+': invalid interval')
            scale = optional_number(n.get('normalization_scale'), label+'.normalization_scale')
            if scale is not None and scale <= 0:
                errors.append(label+'.normalization_scale: must be positive')
            if scale is None and ((lo is not None and hi is None and lo == 0) or
                                  (hi is not None and lo is None and hi == 0)):
                errors.append(label+': one-sided zero bound requires positive normalization_scale')
            for key in ('name', 'expression', 'unit', 'depth_description'):
                if not n.get(key):
                    errors.append(f'{field}.nutrients.{key}: required')
            if n.get('period') not in ('daily', 'date'):
                errors.append(f'{field}.nutrients.period: daily or date required')
            if n.get('period') == 'date':
                try:
                    if dt.date.fromisoformat(n.get('date', '')).year != year:
                        raise ValueError()
                except (ValueError, TypeError):
                    errors.append(f'{field}.nutrients.date: must be in selected year')
    if config.get('dose_ratios') is None:
        errors.append('dose_ratios: set [] explicitly for no between-field dose ratios')
    for r in config.get('dose_ratios') or []:
        interval(r.get('min'), r.get('max'), 'dose_ratio', False)
        number(r.get('min'), 'dose_ratio.min', 0)
        for key in ('numerator_field', 'denominator_field'):
            if r.get(key) not in fields:
                errors.append(f'dose_ratio.{key}: unknown field')
        lower = ((fields.get(r.get('denominator_field')) or {}).get('dose_kg_product_ha') or {}).get('min')
        if not isinstance(lower, (int, float)) or lower <= 0:
            errors.append('dose_ratio: denominator field lower dose must be > 0')
    reference = config.get('reference_decision')
    if reference is not None:
        if not isinstance(reference, dict) or reference.keys() != fields.keys():
            errors.append('reference_decision: must contain every configured field exactly once')
        else:
            for field, fs in fields.items():
                decision = reference.get(field) or {}
                if decision.get('option') not in (fs.get('fertilizer_options') or []):
                    errors.append(f'reference_decision.{field}.option: select an allowed option')
                dose = number(decision.get('dose_kg_product_ha'),
                              f'reference_decision.{field}.dose_kg_product_ha', 0)
                bounds = fs.get('dose_kg_product_ha') or {}
                if (dose is not None and isinstance(bounds.get('min'), (int, float)) and
                        isinstance(bounds.get('max'), (int, float)) and
                        not bounds['min'] <= dose <= bounds['max']):
                    errors.append(f'reference_decision.{field}.dose_kg_product_ha: outside field bounds')
    search = config.get('nsga2') or {}
    for key, minimum in [('population', 4), ('generations', 1), ('seed', 0)]:
        if type(search.get(key)) is not int or search[key] < minimum:
            errors.append(f'nsga2.{key}: integer >= {minimum} required')
    return list(dict.fromkeys(errors))


def run_apsim(config, model):
    runtime = config['runtime']
    command = runtime.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
        raise ValueError('runtime.command must be a nonempty argument list (no shell)')
    timeout = finite(runtime.get('timeout_seconds'), 'runtime.timeout_seconds')
    if timeout <= 0:
        raise ValueError('runtime.timeout_seconds must be positive')
    # cwd and basename work on Windows/Linux, with Models or dotnet Models.dll.
    command = command + [model.name, '--single-threaded']
    with (model.parent/'apsim.log').open('w', encoding='utf-8') as log:
        completed = subprocess.run(command, cwd=model.parent, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f'APSIM exit {completed.returncode}; see {model.parent / "apsim.log"}')
    db = model.with_suffix('.db')
    if not db.is_file():
        raise RuntimeError('APSIM produced no database')
    return db


def optimize(config, meta, outdir, evaluator=None):
    from pymoo.algorithms.moo.nsga2 import NSGA2, binary_tournament
    from pymoo.core.mixed import MixedVariableSampling, MixedVariableMating, MixedVariableDuplicateElimination
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.core.variable import Choice, Real
    from pymoo.operators.selection.tournament import TournamentSelection
    from pymoo.optimize import minimize
    import numpy as np

    errors = configuration_errors(config, meta)
    if errors:
        raise ValueError('\n'.join(errors))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    soil_constraints = (sum(
        int(n.get('min') is not None) + int(n.get('max') is not None)
        for f in config['fields'].values() for n in f['nutrients'])
        if config.get('soil_constraint_mode', 'hard') == 'hard' else 0)
    base_constraints = soil_constraints + 2*len(config['dose_ratios']) + 1
    scenario_specs = environment_scenarios(config)
    scenario_runtime = []
    if evaluator is None:
        for scenario in scenario_specs:
            scenario_cfg = environment_config(config, scenario)
            scenario_meta = inventory(scenario_cfg)
            require_compatible_environment(meta, scenario_meta, scenario['name'])
            scenario_runtime.append((scenario['name'], scenario_cfg, scenario_meta))
    n_constraints = base_constraints * (len(scenario_runtime) if evaluator is None else 1)
    variables = {}
    for field, fs in config['fields'].items():
        variables[field+'__type'] = Choice(options=fs['fertilizer_options'])
        lo, hi = fs['dose_kg_product_ha']['min'], fs['dose_kg_product_ha']['max']
        variables[field+'__dose'] = Real(bounds=(lo, hi)) if lo != hi else Choice(options=[lo])
    cache, history = {}, []

    def decode(x):
        return {field: {'option': str(x[field+'__type']), 'dose_kg_product_ha': float(x[field+'__dose'])}
                for field in config['fields']}

    def evaluate_decisions(decisions, source='nsga2'):
        key = hashlib.sha256(json.dumps(decisions, sort_keys=True).encode()).hexdigest()
        reused = key in cache
        if not reused:
            work = outdir / 'candidates' / key
            try:
                if evaluator:
                    summary = evaluator(decisions)
                else:
                    scenario_scores = []
                    for scenario_name, scenario_cfg, scenario_meta in scenario_runtime:
                        scenario_work = work/'environment_scenarios'/scenario_name
                        model = prepare(scenario_cfg, scenario_work, decisions)
                        db = run_apsim(scenario_cfg, model)
                        raw = read_results(db, scenario_meta, config['year'], config)
                        scenario_scores.append((scenario_name, score(raw, config, decisions)))
                    summary = aggregate_scenario_scores(
                        scenario_scores, config.get('scenario_aggregation'))
                if len(summary['F']) != 3 or len(summary['G']) != n_constraints:
                    raise ValueError('Invalid objective/constraint dimensions')
                if not all(math.isfinite(float(v)) for v in summary['F'] + summary['G']):
                    raise ValueError('Non-finite objective/constraint')
                summary['status'] = 'ok'
            except Exception as exc:
                summary = {'status': 'failed', 'error': str(exc), 'feasible': False,
                           'F': [1e30]*3, 'G': [1e30]*n_constraints}
            summary.update(candidate_id=key, decisions=decisions)
            cache[key] = summary
            write_json(work / 'result.json', summary)
            print(f"Candidate {len(cache)}: {summary['status']}; F={summary['F']}", flush=True)
        history.append({'evaluation': len(history)+1, 'candidate_id': key,
                        'cache_hit': reused, 'source': source})
        return cache[key]

    class Problem(ElementwiseProblem):
        def __init__(self):
            super().__init__(vars=variables, n_obj=3, n_ieq_constr=n_constraints)

        def _evaluate(self, x, out, *args, **kwargs):
            decisions = decode(x)
            row = evaluate_decisions(decisions)
            out['F'], out['G'] = row['F'], row['G']

    reference_row = None
    if config.get('reference_decision') is not None:
        reference_row = evaluate_decisions(config['reference_decision'], 'reference_decision')
    duplicate = MixedVariableDuplicateElimination()
    algorithm = NSGA2(pop_size=config['nsga2']['population'], sampling=MixedVariableSampling(),
                      mating=MixedVariableMating(selection=TournamentSelection(func_comp=binary_tournament),
                                                eliminate_duplicates=duplicate),
                      eliminate_duplicates=duplicate)
    minimize(Problem(), algorithm, ('n_gen', config['nsga2']['generations']),
             seed=config['nsga2']['seed'], verbose=True)
    # Retain every nondominated feasible evaluated candidate, including earlier generations.
    valid = [v for v in cache.values() if v['status'] == 'ok' and all(g <= 0 for g in v['G'])]
    pareto = []
    if valid:
        from pymoo.util.nds.non_dominated_sorting import NonDominatedSorting
        front = NonDominatedSorting().do(np.asarray([v['F'] for v in valid]), only_non_dominated_front=True)
        pareto = [valid[int(i)] for i in front]
    successful = [v for v in cache.values() if v['status'] == 'ok']
    least_violation = sorted(successful, key=lambda v: sum(max(0.0, g) for g in v['G']))[:3]
    payload = {'status': 'ok' if pareto else 'no_feasible_solutions', 'year': config['year'],
               'scenario': config.get('scenario'),
               'decision_period': config['decision_period'],
               'currency': config['currency'], 'objective_names': ['negative_yield_dm_kg', 'negative_net_profit', 'soil_interval_violation'],
               'environment_scenarios': [scenario['name'] for scenario in scenario_specs],
               'scenario_aggregation': config.get('scenario_aggregation') or {
                   'yield': 'mean', 'profit': 'mean', 'soil': 'worst_case'},
               'pareto': pareto,
               'reference_candidate_id': reference_row.get('candidate_id') if reference_row else None,
               'least_constraint_violation': [
                   {'candidate_id': v['candidate_id'],
                    'total_positive_constraint_violation': sum(max(0.0, g) for g in v['G']),
                    'F': v['F'], 'decisions': v['decisions']} for v in least_violation],
               'evaluated': len(cache), 'failed': sum(v['status']=='failed' for v in cache.values())}
    write_json(outdir/'pareto.json', payload)
    write_json(outdir/'evaluations.json', list(cache.values()))
    write_json(outdir/'history.json', history)
    write_candidate_summary_csv(outdir/'evaluations_summary.csv', list(cache.values()), config)
    write_candidate_summary_csv(outdir/'pareto_summary.csv', pareto, config)
    return payload


def write_candidate_summary_csv(path, candidates, config):
    """Write one reviewable row per candidate without flattening the full JSON tree."""
    fixed = [
        'candidate_id', 'status', 'feasible', 'environment_scenarios',
        'yield_aggregation', 'profit_aggregation', 'soil_aggregation',
        'yield_dm_kg', 'yield_dm_kg_min', 'yield_dm_kg_max',
        'net_profit', 'net_profit_min', 'net_profit_max',
        'soil_deviation', 'soil_deviation_min', 'soil_deviation_max',
        'positive_constraint_violation',
    ]
    field_columns = []
    for field in config['fields']:
        field_columns.extend([
            f'{field}_option', f'{field}_dose_kg_product_ha',
            f'{field}_yield_dm_kg_ha', f'{field}_net_profit_per_ha',
            f'{field}_mineral_n_end_kg_ha',
            f'{field}_yield_dm_kg_ha_min', f'{field}_yield_dm_kg_ha_max',
            f'{field}_net_profit_per_ha_min', f'{field}_net_profit_per_ha_max',
            f'{field}_mineral_n_end_kg_ha_min', f'{field}_mineral_n_end_kg_ha_max'])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fixed + field_columns)
        writer.writeheader()
        for candidate in candidates:
            scenarios = candidate.get('scenarios') or {}
            aggregation = candidate.get('scenario_aggregation') or {}
            row = {key: candidate.get(key) for key in
                   ('candidate_id', 'status', 'feasible', 'yield_dm_kg',
                    'net_profit', 'soil_deviation')}
            row['environment_scenarios'] = ';'.join(scenarios)
            row['yield_aggregation'] = aggregation.get('yield')
            row['profit_aggregation'] = aggregation.get('profit')
            row['soil_aggregation'] = aggregation.get('soil')
            for metric in ('yield_dm_kg', 'net_profit', 'soil_deviation'):
                values = candidate.get(metric+'_range')
                if values is None and candidate.get(metric) is not None:
                    values = [candidate[metric], candidate[metric]]
                if values is not None:
                    row[metric+'_min'], row[metric+'_max'] = values
            row['positive_constraint_violation'] = sum(
                max(0.0, float(value)) for value in candidate.get('G', []))
            decisions = candidate.get('decisions') or {}
            results = candidate.get('fields') or {}
            for field, fs in config['fields'].items():
                decision = decisions.get(field) or {}
                result = results.get(field) or {}
                yields = result.get('yield_dm_kg_ha') or {}
                row[f'{field}_option'] = decision.get('option')
                row[f'{field}_dose_kg_product_ha'] = decision.get('dose_kg_product_ha')
                row[f'{field}_yield_dm_kg_ha'] = (sum(
                    float(yields.get(crop, 0.0))
                    for crop in fs.get('objective_yield_components') or [])
                    if result else None)
                row[f'{field}_net_profit_per_ha'] = result.get('net_profit_per_ha')
                row[f'{field}_mineral_n_end_kg_ha'] = result.get('mineral_n_end_kg_ha')
                scenario_fields = [
                    scenario.get('fields', {}).get(field)
                    for scenario in scenarios.values()
                    if scenario.get('fields', {}).get(field) is not None
                ]
                if not scenario_fields and result:
                    scenario_fields = [result]
                field_yields = [
                    sum(float(item.get('yield_dm_kg_ha', {}).get(crop, 0.0))
                        for crop in fs.get('objective_yield_components') or [])
                    for item in scenario_fields
                ]
                for metric, values in (
                        ('yield_dm_kg_ha', field_yields),
                        ('net_profit_per_ha', [float(item['net_profit_per_ha'])
                                               for item in scenario_fields]),
                        ('mineral_n_end_kg_ha', [float(item['mineral_n_end_kg_ha'])
                                                for item in scenario_fields])):
                    if values:
                        row[f'{field}_{metric}_min'] = min(values)
                        row[f'{field}_{metric}_max'] = max(values)
            writer.writerow(row)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Calibrated multi-field fertilizer NSGA-II')
    parser.add_argument('action', choices=['inspect', 'validate', 'prepare', 'baseline', 'historical', 'optimize'])
    parser.add_argument('--config', default=str(ROOT/'configs/multifield.yaml'))
    parser.add_argument('--year', type=int)
    parser.add_argument('--population', type=int, help='override NSGA-II population for this run')
    parser.add_argument('--generations', type=int, help='override NSGA-II generations for this run')
    parser.add_argument('--results', help='baseline.json to compare for the historical action')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.year is not None:
            config['year'] = args.year
        if args.population is not None:
            config.setdefault('nsga2', {})['population'] = args.population
        if args.generations is not None:
            config.setdefault('nsga2', {})['generations'] = args.generations
        meta = inventory(config)
        outdir = Path(config['output_dir'])
        if args.action in ('inspect', 'validate'):
            write_json(outdir/'model_inventory.json', meta)
            errors = configuration_errors(config, meta)
            write_json(outdir/'configuration_status.json', {'ready': not errors, 'errors': errors})
            print(json.dumps({'fields': len(meta['fields']), 'restored_managers': len(meta['restored_rotation_managers']),
                              'inventory': str(outdir/'model_inventory.json'), 'configuration_errors': errors}, ensure_ascii=False, indent=2))
            return 2 if errors and args.action == 'validate' else 0
        if args.action == 'optimize':
            errors = configuration_errors(config, meta)
            if errors:
                raise ValueError('\n'.join(errors))
        year = config.get('year')
        if type(year) is not int or not int(meta['start'][:4]) <= year <= int(meta['end'][:4]):
            raise ValueError('Select --year within the calibrated clock')
        if args.action == 'historical':
            if not args.results:
                raise ValueError('historical requires --results path/to/baseline.json')
            results_path = Path(args.results).resolve()
            results = json.loads(results_path.read_text(encoding='utf-8-sig'))
            comparison = compare_historical(
                results, meta, load_observations(config['historical_observations']), year)
            json_path = results_path.parent/'historical_comparison.json'
            csv_path = results_path.parent/'historical_comparison.csv'
            write_json(json_path, comparison)
            write_comparison_csv(csv_path, comparison)
            print(f'Historical comparison: {json_path}')
            print(json.dumps(comparison['metrics'], ensure_ascii=False, indent=2))
            return 0
        # New run directory prevents stale DB/cache reuse; record all inputs for reproducibility.
        outdir = outdir / (args.action+'_'+uuid.uuid4().hex[:12])
        write_json(outdir/'config_snapshot.json', config)
        write_json(outdir/'model_inventory.json', meta)
        print(f'Run directory: {outdir}', flush=True)
        if args.action == 'optimize':
            result = optimize(config, meta, outdir)
            print(f"{result['status']}: {len(result['pareto'])} Pareto solutions")
            return 0 if result['pareto'] else 3
        model = prepare(config, outdir)
        if args.action == 'baseline':
            db = run_apsim(config, model)
            results = read_results(db, meta, year, config, audit_applications=False)
            write_json(outdir/'baseline.json', results)
            if config.get('historical_observations'):
                comparison = compare_historical(
                    results, meta, load_observations(config['historical_observations']), year)
                write_json(outdir/'historical_comparison.json', comparison)
                write_comparison_csv(outdir/'historical_comparison.csv', comparison)
            print(f'Baseline complete: {outdir / "baseline.json"}')
        else:
            print(model)
        return 0
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
