"""Bounded APSIM experiments, explicit response transfer and common NSGA-II.

This first management study uses linked low/high population and depth regimes
from the existing optimizer's bounds, and three of its allowed nitrogen doses.
It exhaustively measures the resulting finite grid before searching it. This
provides exact APSIM results on that grid, without claiming a global optimum.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import time

from agro.adapters.rotation_management import (execute, read_applications, read_outputs,
                                              parameters, manager, walk)
from agro.adapters.aquacrop import AquaCropBackend
from agro.adapters.transferred_response import apply_response
from agro.contracts import FieldYearResult, SimulationResult
from agro.optimization.nsga2 import nondominated, search_integer

ROOT = Path(__file__).resolve().parents[1]
YEARS = [2020, 2021, 2022, 2023]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def simulation(value):
    return SimulationResult(value['engine'], tuple(FieldYearResult(**row) for row in value['fields']),
                            value.get('provenance', {}))


def load_experiment(path, policies, model_root, command):
    """Resume only a successful calculation with matching source and decisions."""
    result_path, audit_path = path/'simulation.json', path/'management.json'
    source_path = model_root/'configs/rotation_fixed.apsimx'
    source = json.loads(source_path.read_text(encoding='utf-8-sig'))
    fingerprint = hashlib.sha256(source_path.read_bytes()).hexdigest()
    if result_path.exists() and audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding='utf-8'))
        if audit['source_model_sha256'] != fingerprint or audit['policies'] != policies:
            raise ValueError(f'Cached experiment input mismatch: {path}')
        if 'applications' not in audit:
            audit['applications'] = read_applications(path/'candidate.db', source, YEARS)
            save(audit_path, audit)
        if audit.get('humic_units') != 'kg C/ha':
            recorded = json.loads(result_path.read_text(encoding='utf-8'))
            database = Path(recorded['provenance']['database'])
            _, audit['humic'] = read_outputs(database, source, YEARS)
            audit['humic_units'] = 'kg C/ha'
            save(audit_path, audit)
        return simulation(json.loads(result_path.read_text(encoding='utf-8'))), audit
    # Failed native artifacts are retained; a retry uses a separate child folder.
    work = path
    if path.exists():
        attempt = 1
        while (path/f'retry_{attempt:02}').exists():
            attempt += 1
        work = path/f'retry_{attempt:02}'
    result, audit = execute(model_root, policies, YEARS, command, work)
    save(result_path, asdict(result))
    save(audit_path, audit)
    return result, audit


def regimes(config):
    populations, depths = [], []
    for end in (0, -1):
        populations.append({name: float(spec['population_options_m2'][end])
                            for name, spec in config['management_policies'].items()})
        depths.append({name: float(spec['depth_options_mm'][end])
                       for name, spec in config['management_policies'].items()})
    return populations, depths


def policies(populations, depths, scheme):
    return {name: {'population_m2': population, 'depth_mm': depths[name],
                   'dose_kg_n_ha': scheme['dose_kg_n_ha'], 'form': scheme['form']}
            for name, population in populations.items()}


def fertilizer_schemes(config):
    allowed = config['fertilizer']['doses_kg_n_ha']
    doses = [0, 90, 210]
    if not set(doses) <= set(allowed):
        raise ValueError('Study nitrogen levels must belong to the existing optimization domain')
    result = [{'name': 'N0', 'dose_kg_n_ha': 0., 'form': 'NO3N'}]
    for dose in doses[1:]:
        for name, form in config['fertilizer']['forms'].items():
            result.append({'name': f'{name}_{dose}', 'dose_kg_n_ha': float(dose), 'form': form['apsim_type']})
    return result


def score(result, audit, baseline, config):
    base = {(row.field_id, row.year): row for row in baseline.fields}
    prices = {v['apsim_type']: float(v['price_per_kg_n']) for v in config['fertilizer']['forms'].values()}
    fertilizer_cost, total_n, passes = {}, 0., {}
    areas = {row.field_id: row.area_ha for row in result.fields}
    for event in audit['applications']:
        key = (event['field'], event['year'])
        fertilizer_cost[key] = fertilizer_cost.get(key, 0.) + event['dose_kg_n_ha']*prices[event['form']]
        total_n += event['dose_kg_n_ha']*areas[event['field']]
        passes[key] = passes.get(key, 0) + 1
    # Product identity is supplied by native APSIM, even for hybrid corrected totals.
    source = json.loads((ROOT/'models/aquacrop/configs/rotation_fixed.apsimx').read_text(encoding='utf-8-sig'))
    crops = {}
    for zone in [n for n in walk(source) if n.get('$type') == 'Models.Core.Zone, Models']:
        rp = parameters(manager(zone, 'RotationManager'))
        labels = rp['RotationHarvestCrops'].split(',')
        for year in YEARS:
            crops[(zone['Name'], year)] = labels[year-2019].strip()
    yield_kg = profit = shortfall = 0.
    details = []
    for row in result.fields:
        key = row.field_id, row.year
        mass = sum(row.harvested_dm_kg_ha.values())
        price = 14.3 if crops[key] in ('Wheat', 'Oats') else 0.
        # Identical diagnostic cereal prices in the existing configuration.
        if price and set(config['economics']['crop_price_per_kg_dm'][name] for name in
                         ('spring_wheat', 'winter_rye', 'oats_with_underseed')) != {price}:
            raise ValueError('Update product pricing for differing cereal prices')
        net = mass*price - fertilizer_cost.get(key, 0.) - passes.get(key, 0)*float(
            config['economics']['application_cost_per_ha']) - float(config['economics']['other_cost_per_ha_year'])
        reference_n = base[key].metrics['mineral_n_end_kg_ha']
        deficit = max(0., reference_n-row.metrics['mineral_n_end_kg_ha'])/max(abs(reference_n), 1e-9)
        shortfall += deficit
        yield_kg += mass*row.area_ha
        profit += net*row.area_ha
        details.append({'field': row.field_id, 'year': row.year, 'crop': crops[key], 'area_ha': row.area_ha,
                        'yield_dm_kg_ha': mass, 'net_profit_proxy_per_ha': net,
                        'mineral_n_end_kg_ha': row.metrics['mineral_n_end_kg_ha']})
    # Each field must conserve its own Humic stock. Other fields cannot compensate.
    constraints = [(stock['start_kg_ha']-stock['end_kg_ha'])/max(abs(stock['start_kg_ha']), 1.)
                   for name, stock in sorted(audit['humic'].items())]
    return {'F': [-yield_kg, -profit, shortfall], 'G': constraints, 'yield_dm_kg': yield_kg,
            'net_profit_proxy': profit, 'mineral_n_shortfall': shortfall, 'applied_n_kg': total_n,
            'feasible': all(value <= 0 for value in constraints),
            'failed_humic_fields': [name for (name, _), g in zip(sorted(audit['humic'].items()), constraints) if g > 0],
            'fields': details}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workdir', type=Path, default=ROOT/'runs/management_optimization_20261027')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args(argv)
    import yaml
    config = yaml.safe_load((ROOT/'models/apsim/configs/multiyear_rotation_nsga2_2020_2023.yaml').read_text(encoding='utf-8'))
    work = args.workdir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    model = ROOT/'models/aquacrop'
    command = [os.environ.get('APSIM_MODELS', 'C:/Program Files/APSIM2026.9.8115.0/bin/Models.exe')]
    populations, depths = regimes(config)
    schemes = fertilizer_schemes(config)
    domain = {'source': str(model/'configs/rotation_fixed.apsimx'),
              'source_sha256': hashlib.sha256((model/'configs/rotation_fixed.apsimx').read_bytes()).hexdigest(),
              'weather_sha256': hashlib.sha256((model/'configs/weather_rotation.met').read_bytes()).hexdigest(),
              'years': YEARS, 'population_regimes': populations, 'depth_regimes': depths,
              'fertilizer_schemes': schemes, 'nsga2': config['nsga2'],
              'scope': '36 linked management regimes; not the full independent policy space',
              'aquacrop_method': 'native population response plus external APSIM-derived fertilizer/depth correction'}
    if (work/'domain.json').exists() and json.loads((work/'domain.json').read_text(encoding='utf-8')) != domain:
        raise ValueError('Study inputs changed; use a fresh workdir')
    save(work/'domain.json', domain)
    started = time.monotonic()
    baseline, baseline_audit = load_experiment(work/'baseline_apsim_v2', {}, model, command)
    save(work/'baseline_score.json', score(baseline, baseline_audit, baseline, config))
    references, native_aqua = {}, {}
    for p, population in enumerate(populations):
        pop_only = {name: {'population_m2': value} for name, value in population.items()}
        print(f'Population reference {p+1}/2', flush=True)
        references[p] = load_experiment(work/f'reference_population_{p}', pop_only, model, command)[0]
        path = work/f'aquacrop_population_{p}'
        if (path/'common_result.json').exists():
            native_aqua[p] = simulation(json.loads((path/'common_result.json').read_text(encoding='utf-8')))
        else:
            backend = AquaCropBackend(model, [str(ROOT/'engines/aquacrop/field-target/debug/aquacrop-field-runner.exe')],
                                      YEARS, rotation_populations=pop_only)
            native = backend.simulate({}, path)
            native_aqua[p] = SimulationResult(native.engine, tuple(FieldYearResult(
                f'Field{int(row.field_id.rsplit("_", 1)[1])}', row.year, row.area_ha,
                row.harvested_dm_kg_ha, row.metrics) for row in native.fields), native.provenance)
            save(path/'common_result.json', asdict(native_aqua[p]))
    tasks = list(itertools.product(range(len(schemes)), range(2), range(2)))
    experiments = {}
    def calculate(key):
        s, p, d = key
        candidate_id = f'n{s:02}_p{p}_d{d}'
        policy = policies(populations[p], depths[d], schemes[s])
        result, audit = load_experiment(work/'experiments'/candidate_id, policy, model, command)
        return key, result, audit
    failures = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 2))) as pool:
        futures = {pool.submit(calculate, key): key for key in tasks}
        for future in as_completed(futures):
            key = futures[future]
            try:
                _, result, audit = future.result()
                experiments[key] = (result, audit)
                print(f'APSIM experiments: {len(experiments)}/{len(tasks)}; {key}; '
                      f'yield={sum(sum(r.harvested_dm_kg_ha.values())*r.area_ha for r in result.fields):.1f} kg', flush=True)
            except Exception as exc:
                failures[str(key)] = str(exc)
                print(f'FAILED experiment {key}: {exc}', flush=True)
            save(work/'status.json', {'stage': 'apsim_experiments', 'completed': len(experiments),
                 'total': len(tasks), 'failures': failures, 'elapsed_seconds': time.monotonic()-started})
    if failures:
        raise RuntimeError(f'{len(failures)} experiment(s) failed; see {work / "status.json"}. Resume after fixing.')
    # Intermediate N doses are reserved for a genuinely unseen interpolation check.
    validation = []
    for dose, p, d in ((60, 0, 0), (60, 1, 1), (150, 0, 1), (150, 1, 0)):
        scheme = {'dose_kg_n_ha': dose, 'form': 'NO3N'}
        actual, _ = load_experiment(work/'holdout'/f'n{dose}_p{p}_d{d}',
                                    policies(populations[p], depths[d], scheme), model, command)
        lower, upper = (0, 90) if dose < 90 else (90, 210)
        def at(n):
            s = next(i for i, item in enumerate(schemes) if item['dose_kg_n_ha'] == n and item['form'] == 'NO3N')
            return {(row.field_id, row.year): sum(row.harvested_dm_kg_ha.values())
                    for row in experiments[(s, p, d)][0].fields}
        lo, hi = at(lower), at(upper)
        fraction = (dose-lower)/(upper-lower)
        for row in actual.fields:
            key = row.field_id, row.year
            predicted = lo[key]*(1-fraction)+hi[key]*fraction
            observed = sum(row.harvested_dm_kg_ha.values())
            validation.append({'dose_kg_n_ha': dose, 'population_regime': p, 'depth_regime': d,
                'field': row.field_id, 'year': row.year, 'apsim_actual_kg_ha': observed,
                'interpolated_kg_ha': predicted, 'error_kg_ha': predicted-observed})
        print(f'Held-out check: N={dose}, population={p}, depth={d}', flush=True)
    save(work/'heldout_validation.json', {'rows': validation,
         'mae_kg_ha': sum(abs(r['error_kg_ha']) for r in validation)/len(validation),
         'rmse_kg_ha': math.sqrt(sum(r['error_kg_ha']**2 for r in validation)/len(validation)),
         'scope': 'Interpolation of APSIM responses only; not independent validation of AquaCrop',
         'used_in_final_optimization': False})
    summaries = {}
    for engine in ('apsim', 'aquacrop_apsim_response'):
        candidates = {}
        for key, (native, audit) in experiments.items():
            s, p, d = key
            identifier = f'n{s:02}_p{p}_d{d}'
            result = native if engine == 'apsim' else apply_response(native_aqua[p], references[p], native, identifier)
            row = score(result, audit, baseline, config)
            row.update(status='ok', candidate_id=identifier, engine=engine,
                       decisions={'scheme': schemes[s], 'population_regime': populations[p], 'depth_regime': depths[d]})
            candidates[key] = row
            save(work/engine/'candidates'/identifier/'simulation.json', asdict(result))
        visited = []
        def evaluate(vector):
            key = tuple(int(v) for v in vector)
            visited.append(candidates[key]['candidate_id'])
            return candidates[key]
        search_integer([schemes, populations, depths], evaluate, config['nsga2'], 3, 10)
        values = list(candidates.values())
        front = nondominated(values)
        least = min(values, key=lambda row: (sum(max(0., g) for g in row['G']), row['F'][0]))
        best_yield = max(values, key=lambda row: row['yield_dm_kg'])
        summary = {'engine': engine, 'evaluated_domain_size': len(values), 'nsga2_evaluations': len(visited),
                   'nsga2_unique_candidates': len(set(visited)), 'feasible_candidates': sum(row['feasible'] for row in values),
                   'pareto_candidates': len(front), 'status': 'completed' if front else 'no_feasible_solution',
                   'best_yield_without_constraints': best_yield, 'least_violation': least,
                   'objective_names': ['negative_harvested_dry_matter_kg', 'negative_diagnostic_profit', 'mineral_N_shortfall'],
                   'constraint': 'Humic conservation on each individual field, 31 Dec 2019 to 31 Dec 2023',
                   'soil_source': 'APSIM native outputs' if engine == 'apsim' else 'APSIM external soil block',
                   'scope': domain['scope']}
        save(work/engine/'evaluations.json', values)
        save(work/engine/'pareto.json', front)
        save(work/engine/'nsga2_history.json', visited)
        save(work/engine/'summary.json', summary)
        summaries[engine] = summary
    save(work/'summary.json', summaries)
    save(work/'status.json', {'stage': 'completed', 'completed': len(tasks), 'total': len(tasks),
                             'elapsed_seconds': time.monotonic()-started})
    print(f'STUDY COMPLETE: {work}', flush=True)
    from agro.management_report import generate
    generate(work, work/'report')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
