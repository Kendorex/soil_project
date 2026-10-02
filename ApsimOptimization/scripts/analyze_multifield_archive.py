"""Verify and summarize an archive produced by run_multifield_ubuntu_check.sh."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import tarfile
from collections import defaultdict
from pathlib import Path, PurePosixPath


def close(a, b, tol=1e-9):
    return math.isclose(float(a), float(b), rel_tol=tol, abs_tol=tol)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--json-output', type=Path)
    parser.add_argument('--summary-only', action='store_true')
    args = parser.parse_args()

    checks = []

    def check(name, ok, detail=None):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    with tarfile.open(args.archive, 'r:gz') as bundle:
        members = {member.name: member for member in bundle.getmembers() if member.isfile()}
        safe = all(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
                   for name in members)
        check('safe archive member paths', safe)

        def read_bytes(name):
            member = members.get(name)
            if member is None:
                raise ValueError(f'archive member missing: {name}')
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError(f'cannot read archive member: {name}')
            return stream.read()

        def read_json(name):
            return json.loads(read_bytes(name).decode('utf-8-sig'))

        manifest = read_json('multifield_check_manifest.json')
        bad_hashes = []
        missing = []
        for row in manifest.get('files', []):
            if row['path'] not in members:
                missing.append(row['path'])
                continue
            digest = hashlib.sha256(read_bytes(row['path'])).hexdigest()
            if digest != row['sha256']:
                bad_hashes.append(row['path'])
        check('manifest files present', not missing, missing)
        check('manifest SHA-256 hashes', not bad_hashes, bad_hashes)

        selected = manifest['selected_runs']
        baseline_dir = selected['baseline']
        optimize_dir = selected['optimize']
        response_dir = selected['response']
        validation_dir = selected['validation']
        quality_dir = selected.get('quality')
        oats_dir = selected.get('oats')
        rye_timing_dir = selected.get('rye_timing')
        forage_dir = selected.get('forage')
        establishment_dir = selected.get('establishment')
        root_access_dir = selected.get('root_access')

        log = read_bytes(f'{validation_dir}/validation.log').decode('utf-8-sig')
        passed = re.search(r'(\d+) passed', log)
        check('pytest completed', passed is not None,
              int(passed.group(1)) if passed else None)

        production = read_json('outputs/multifield/configuration_status.json')
        diagnostic = read_json('outputs/multifield/DIAGNOSTIC_2023_N/configuration_status.json')
        check('production inputs remain explicitly incomplete',
              production.get('ready') is False and bool(production.get('errors')),
              len(production.get('errors') or []))
        check('diagnostic configuration valid',
              diagnostic == {'ready': True, 'errors': []}, diagnostic)

        baseline = read_json(f'{baseline_dir}/baseline.json')
        comparison = read_json(f'{baseline_dir}/historical_comparison.json')
        baseline_config = read_json(f'{baseline_dir}/config_snapshot.json')
        check('baseline year and ten fields',
              baseline.get('year') == 2023 and len(baseline.get('fields', {})) == 10)
        baseline_total = sum(
            sum(baseline['fields'][field]['yield_dm_kg_ha'][crop]
                for crop in baseline_config['fields'][field]['objective_yield_components'])
            * baseline['fields'][field]['area_ha']
            for field in baseline['fields'])
        check('baseline aggregate reconciles to comparison',
              close(baseline_total/1000, comparison['metrics']['simulated_total_t']),
              {'calculated_t': baseline_total/1000,
               'reported_t': comparison['metrics']['simulated_total_t']})

        pareto = read_json(f'{optimize_dir}/pareto.json')
        evaluations = read_json(f'{optimize_dir}/evaluations.json')
        history = read_json(f'{optimize_dir}/history.json')
        optimize_config = read_json(f'{optimize_dir}/config_snapshot.json')
        soil_mode = optimize_config.get('soil_constraint_mode', 'hard')
        check('recognized soil constraint mode', soil_mode in {'hard', 'objective_only'}, soil_mode)
        evaluation_csv = list(csv.DictReader(io.StringIO(
            read_bytes(f'{optimize_dir}/evaluations_summary.csv').decode('utf-8-sig'))))
        pareto_csv = list(csv.DictReader(io.StringIO(
            read_bytes(f'{optimize_dir}/pareto_summary.csv').decode('utf-8-sig'))))
        check('candidate CSV summaries reconcile',
              {row['candidate_id'] for row in evaluation_csv} ==
              {row['candidate_id'] for row in evaluations} and
              {row['candidate_id'] for row in pareto_csv} ==
              {row['candidate_id'] for row in pareto.get('pareto', [])},
              {'evaluations_csv': len(evaluation_csv), 'pareto_csv': len(pareto_csv)})

        configured_scenarios = optimize_config.get('environment_scenarios')
        if configured_scenarios is not None:
            scenario_names = [row['name'] for row in configured_scenarios]
            aggregation = optimize_config['scenario_aggregation']

            def aggregate(values, mode):
                return sum(values)/len(values) if mode == 'mean' else min(values)

            scenarios_reconcile = (
                pareto.get('environment_scenarios') == scenario_names and
                pareto.get('scenario_aggregation') == aggregation)
            for candidate in evaluations:
                scenarios = candidate.get('scenarios') or {}
                scenarios_reconcile &= list(scenarios) == scenario_names
                if not scenarios:
                    continue
                yields = [float(row['yield_dm_kg']) for row in scenarios.values()]
                profits = [float(row['net_profit']) for row in scenarios.values()]
                soil = [float(row['soil_deviation']) for row in scenarios.values()]
                constraints = [float(value) for row in scenarios.values()
                               for value in row['G']]
                scenarios_reconcile &= (
                    candidate.get('scenario_aggregation') == aggregation and
                    all(map(close, candidate['yield_dm_kg_range'],
                            [min(yields), max(yields)])) and
                    all(map(close, candidate['net_profit_range'],
                            [min(profits), max(profits)])) and
                    all(map(close, candidate['soil_deviation_range'],
                            [min(soil), max(soil)])) and
                    close(candidate['yield_dm_kg'], aggregate(yields, aggregation['yield'])) and
                    close(candidate['net_profit'], aggregate(profits, aggregation['profit'])) and
                    close(candidate['soil_deviation'], max(soil)) and
                    candidate['G'] == constraints)
            check('environment scenario outputs reconcile', scenarios_reconcile,
                  {'scenarios': scenario_names, 'aggregation': aggregation})

            csv_by_id = {row['candidate_id']: row for row in evaluation_csv}
            csv_ranges_reconcile = True
            for candidate in evaluations:
                row = csv_by_id.get(candidate['candidate_id']) or {}
                csv_ranges_reconcile &= row.get('environment_scenarios') == ';'.join(scenario_names)
                for metric in ('yield_dm_kg', 'net_profit', 'soil_deviation'):
                    try:
                        expected = candidate[metric+'_range']
                        csv_ranges_reconcile &= (
                            close(row[metric+'_min'], expected[0]) and
                            close(row[metric+'_max'], expected[1]))
                    except (KeyError, TypeError, ValueError):
                        csv_ranges_reconcile = False
            check('scenario ranges exported to candidate CSV', csv_ranges_reconcile,
                  {'rows': len(evaluation_csv), 'scenarios': scenario_names})
        pareto_plots_manifest_name = f'{optimize_dir}/plots/pareto_plots_manifest.json'
        if 'Pareto plots' in log or pareto_plots_manifest_name in members:
            pareto_plots_manifest = (read_json(pareto_plots_manifest_name)
                                     if pareto_plots_manifest_name in members else {})
            pareto_plot_paths = pareto_plots_manifest.get('plots') or []
            check('Pareto plots included and indexed',
                  len(pareto_plot_paths) == 2 and
                  all(path in members for path in pareto_plot_paths),
                  pareto_plot_paths)
        check('all optimization candidates successful',
              pareto.get('failed') == 0 and all(r.get('status') == 'ok' for r in evaluations),
              {'evaluated': len(evaluations), 'failed': pareto.get('failed')})
        reference_id = pareto.get('reference_candidate_id')
        reference = next((r for r in evaluations if r.get('candidate_id') == reference_id), None)
        check('reference candidate evaluated first',
              reference is not None and history and history[0].get('source') == 'reference_decision' and
              history[0].get('candidate_id') == reference_id)
        check('reference candidate is feasible',
              reference is not None and reference.get('feasible') is True and
              all(float(g) <= 0 for g in reference.get('G', [])) and
              close(reference.get('soil_deviation'), 0))
        check('nonempty feasible Pareto front',
              pareto.get('status') == 'ok' and bool(pareto.get('pareto')) and
              any(r.get('candidate_id') == reference_id for r in pareto.get('pareto', [])),
              len(pareto.get('pareto') or []))
        baseline_scenario = None
        if configured_scenarios is not None:
            baseline_scenario = next((row['name'] for row in configured_scenarios
                                      if not any(row.get(key) for key in
                                                 ('model', 'weather',
                                                  'rotation_parameter_reference'))), None)
        reference_baseline = ((reference.get('scenarios') or {}).get(baseline_scenario)
                              if reference and baseline_scenario else reference)
        reference_fields = reference_baseline.get('fields') if reference_baseline else None
        reference_matches = (reference is not None and reference_fields is not None and
                             close(reference_baseline['yield_dm_kg'], baseline_total))
        if reference_matches:
            for field, base in baseline['fields'].items():
                candidate = reference_fields[field]
                reference_matches &= close(candidate['mineral_n_end_kg_ha'], base['mineral_n_end_kg_ha'])
                reference_matches &= all(close(candidate['yield_dm_kg_ha'][crop], value)
                                         for crop, value in base['yield_dm_kg_ha'].items())
        check('reference reproduces baseline field outputs', reference_matches)

        response_text = read_bytes(f'{response_dir}/response.csv').decode('utf-8-sig')
        response = list(csv.DictReader(io.StringIO(response_text)))
        by_field = defaultdict(list)
        for row in response:
            row = dict(row)
            for key in ('dose_apsim_no3n_kg_ha', 'observed_yield_t_ha',
                        'simulated_yield_t_ha', 'mineral_n_end_kg_ha'):
                row[key] = float(row[key])
            by_field[row['field']].append(row)
        doses = sorted({row['dose_apsim_no3n_kg_ha']
                        for rows in by_field.values() for row in rows})
        complete = (len(by_field) == 10 and len(response) == len(doses)*10 and
                    all(sorted(r['dose_apsim_no3n_kg_ha'] for r in rows) == doses
                        for rows in by_field.values()))
        check('N response grid complete', complete,
              {'fields': len(by_field), 'doses': doses, 'rows': len(response)})
        monotonic = {}
        for field, rows in by_field.items():
            rows.sort(key=lambda r: r['dose_apsim_no3n_kg_ha'])
            monotonic[field] = all(a['simulated_yield_t_ha'] <= b['simulated_yield_t_ha'] + 1e-12
                                   for a, b in zip(rows, rows[1:]))
        check('yield response nondecreasing for every field', all(monotonic.values()), monotonic)

        quality = read_json(f'{quality_dir}/summary.json') if quality_dir else {}
        quality_manifest = read_json(f'{quality_dir}/manifest.json') if quality_dir else {}
        history_rows = list(csv.DictReader(io.StringIO(read_bytes(
            'outputs/multifield/quality_history/index.csv').decode('utf-8-sig'))))
        paired_rows = list(csv.DictReader(io.StringIO(read_bytes(
            'outputs/multifield/quality_history/paired_history.csv').decode('utf-8-sig'))))
        check('quality checkpoint covers all observations',
              quality.get('comparable') == quality.get('observations') == 37,
              {'comparable': quality.get('comparable'),
               'observations': quality.get('observations')})
        check('quality checkpoint is indexed',
              any(row.get('checkpoint_id') == quality_manifest.get('checkpoint_id')
                  for row in history_rows), quality_manifest.get('checkpoint_id'))
        paired_current = next((row for row in paired_rows
                               if row.get('checkpoint_id') ==
                               quality_manifest.get('checkpoint_id')), None)
        paired_complete = paired_current is not None
        if paired_complete:
            try:
                paired_complete = (
                    int(paired_current['n_common']) > 0 and
                    all(math.isfinite(float(paired_current[key])) for key in (
                        'reference_rmse_t_ha', 'current_rmse_t_ha',
                        'rmse_change_t_ha', 'reference_mae_t_ha',
                        'current_mae_t_ha', 'mae_change_t_ha')))
            except (KeyError, TypeError, ValueError):
                paired_complete = False
        check('paired quality history has comparable metrics', paired_complete,
              paired_current)

        oats = read_json(f'{oats_dir}/summary.json') if oats_dir else {}
        check('oats competition factorial complete',
              len(oats.get('rows') or []) == 8 and len(oats.get('contrasts') or []) == 4,
              {'rows': len(oats.get('rows') or []),
               'contrasts': len(oats.get('contrasts') or [])})
        rye_timing = read_json(f'{rye_timing_dir}/summary.json') if rye_timing_dir else {}
        check('rye timing scenarios complete',
              len(rye_timing.get('contrasts') or []) == 5,
              len(rye_timing.get('contrasts') or []))
        forage = read_json(f'{forage_dir}/summary.json') if forage_dir else {}
        forage_conflicts = forage.get('conflicts') or []
        check('forage transition conflicts recovered',
              len(forage_conflicts) == 2 and all(
                  row.get('red_clover_events') == 1 and
                  row.get('plantain_proxy_events') == 1
                  for row in forage_conflicts), forage_conflicts)

        establishment = (read_json(f'{establishment_dir}/summary.json')
                         if establishment_dir else {})
        establishment_rows = establishment.get('rows') or []
        pulse_scenarios = {row['scenario'] for row in establishment_rows}
        establishment_complete = (
            len(establishment_rows) == 2*len(pulse_scenarios) and
            pulse_scenarios and
            sum(float(row['rain_added_mm']) == 0.0 for row in establishment_rows) == 2 and
            {row['field'] for row in establishment_rows} == {'Field3', 'Field7'} and
            all(sum(row['field'] == field and row['scenario'] == scenario
                    for row in establishment_rows) == 1
                for field in ('Field3', 'Field7') for scenario in pulse_scenarios))
        check('2019 establishment rainfall grid complete', establishment_complete,
              {'rows': len(establishment_rows),
               'pulse_scenarios': sorted(pulse_scenarios)})
        check('2019 rainfall scenarios remain explicitly diagnostic',
              establishment.get('status') == 'engineering_diagnostic_only' and
              'not a weather correction' in establishment.get('warning', ''),
              establishment.get('status'))
        root_access = (read_json(f'{root_access_dir}/summary.json')
                       if root_access_dir else {})
        root_scenarios = {row['scenario']: row for row in root_access.get('scenarios') or []}
        required_root_scenarios = {
            'unmodified', 'unmodified_oats_only',
            'crop_ll_airdry_and_initial_dul',
            'crop_ll_airdry_initial_dul_oats_only',
        }
        check('soil/root-access factorial scenarios complete',
              root_access.get('status') == 'engineering_diagnostic_only' and
              required_root_scenarios <= root_scenarios.keys(),
              sorted(root_scenarios))
        root_mechanism_reproduced = False
        if required_root_scenarios <= root_scenarios.keys():
            base = root_scenarios['unmodified']
            combined = root_scenarios['crop_ll_airdry_and_initial_dul']
            combined_oats_only = root_scenarios['crop_ll_airdry_initial_dul_oats_only']
            root_mechanism_reproduced = (
                close(base['field3_2019_yield_t_ha'], 0) and
                float(combined['field7_2019_yield_t_ha']) > 0.5 and
                float(combined['field3_2019_yield_t_ha']) < 0.01 and
                float(combined_oats_only['field3_2019_yield_t_ha']) < 0.01)
        check('soil/root diagnostic separates wheat and oats response',
              root_mechanism_reproduced,
              {name: {'field3': row['field3_2019_yield_t_ha'],
                      'field7': row['field7_2019_yield_t_ha']}
               for name, row in root_scenarios.items()})
        plots_manifest_name = 'outputs/multifield/quality_history/plots/plots_manifest.json'
        plots_manifest = read_json(plots_manifest_name) if plots_manifest_name in members else {}
        plot_paths = plots_manifest.get('plots') or []
        check('quality plots included and indexed',
              len(plot_paths) >= 3 and all(path in members for path in plot_paths),
              plot_paths)

        inventory_paths = [
            'outputs/multifield/model_inventory.json',
            'outputs/multifield/DIAGNOSTIC_2023_N/model_inventory.json',
            f'{baseline_dir}/model_inventory.json',
            f'{optimize_dir}/model_inventory.json',
        ]
        fingerprints = {(read_json(path)['model_sha256'], read_json(path)['weather_sha256'])
                        for path in inventory_paths}
        check('model and weather fingerprints consistent', len(fingerprints) == 1,
              [{'model_sha256': model, 'weather_sha256': weather}
               for model, weather in fingerprints])

        result = {
            'status': 'passed' if all(row['ok'] for row in checks) else 'failed',
            'archive': str(args.archive.resolve()),
            'manifest_created_utc': manifest.get('created_utc'),
            'checks': checks,
            'baseline': comparison['metrics'],
            'optimization': {
                'evaluated': len(evaluations),
                'failed': pareto.get('failed'),
                'pareto_solutions': len(pareto.get('pareto') or []),
                'reference_yield_dm_kg': reference.get('yield_dm_kg') if reference else None,
                'reference_net_profit': reference.get('net_profit') if reference else None,
                'reference_soil_deviation': reference.get('soil_deviation') if reference else None,
                'random_candidate_ranges': {
                    'yield_dm_kg': [min(r['yield_dm_kg'] for r in evaluations if r != reference),
                                    max(r['yield_dm_kg'] for r in evaluations if r != reference)],
                    'net_profit': [min(r['net_profit'] for r in evaluations if r != reference),
                                   max(r['net_profit'] for r in evaluations if r != reference)],
                    'soil_deviation': [min(r['soil_deviation'] for r in evaluations if r != reference),
                                       max(r['soil_deviation'] for r in evaluations if r != reference)],
                },
            },
            'n_response': {
                field: [{
                    'dose': row['dose_apsim_no3n_kg_ha'],
                    'yield_t_ha': row['simulated_yield_t_ha'],
                    'mineral_n_end_kg_ha': row['mineral_n_end_kg_ha'],
                } for row in rows]
                for field, rows in sorted(by_field.items())
            },
            'quality': quality,
            'quality_paired_comparison': paired_current,
            'oats_competition': oats.get('contrasts'),
            'rye_n_timing': rye_timing.get('contrasts'),
            'forage_transition_conflicts': forage_conflicts,
            'establishment_water': establishment.get('contrasts'),
            'soil_root_access': root_access.get('scenarios'),
        }

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.summary_only:
        print(f'ARCHIVE ANALYSIS: {result["status"].upper()}')
        for row in result['checks']:
            print(f'[{"OK" if row["ok"] else "FAIL"}] {row["name"]}')
        print('Baseline: '
              f'{result["baseline"]["simulated_total_t"]:.6f} t simulated / '
              f'{result["baseline"]["observed_total_t"]:.6f} t observed')
        print('Optimization: '
              f'{result["optimization"]["evaluated"]} evaluated, '
              f'{result["optimization"]["failed"]} failed, '
              f'{result["optimization"]["pareto_solutions"]} Pareto')
    else:
        print(payload)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(payload+'\n', encoding='utf-8')
    raise SystemExit(0 if result['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
