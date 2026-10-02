"""Synthetic fixtures test mechanics only; none are approved field assumptions."""
import copy
import csv
import datetime as dt
import json
import sqlite3

import pytest

from src.multifield_model import (PERENNIAL_TRANSITION_OLD, fertilizer_code,
                                  fingerprint, historical_fertilizer_code,
                                  historical_replay_plan, inventory, load_model,
                                  prepare, read_json, rotation_transition_code)
from src.multifield_optimizer import (ROOT, configuration_errors,
                                      environment_config, environment_scenarios,
                                      load_config, optimize,
                                      write_candidate_summary_csv)
from src.multifield_results import aggregate_scenario_scores, read_results, score
from src.historical_case import compare_historical, write_comparison_csv
from src.historical_case import parse_technology_nutrients


@pytest.fixture
def actual_config():
    return load_config(ROOT/'configs/multifield_2023_n_diagnostic.yaml')


def synthetic_config():
    fs = {'fertilizer_options': ['a', 'b'], 'area_ha': 1,
          'objective_yield_components': ['Wheat'],
          'dose_kg_product_ha': {'min': 0, 'max': 10},
          'application_dates': ['2023-05-01'], 'application_depth_mm': 0,
          'application_cost_per_ha': 2, 'other_cost_per_ha': 10,
          'nutrients': [{'name': 'N', 'expression': 'sum([NO3].kgha)', 'unit': 'kg/ha',
                         'depth_description': 'whole profile', 'min': 10, 'max': 20, 'period': 'daily'}]}
    option = {'price_per_kg_product': 3, 'components': [{'apsim_type': 'NO3N',
              'mass_fraction': 1, 'apsim_amount_per_kg_product': 1}]}
    return {'scenario': {'kind': 'diagnostic'},
            'year': 2023, 'decision_period': {'start': '2022-09-01', 'end': '2023-12-31'},
            'planning_mode': 'historical_fixed_rotation',
            'rotation_transition_policy': 'preserve_source',
            'domain_values_confirmed': True, 'currency': 'TEST',
            'yield_aggregation': 'total_harvest_dry_matter_kg', 'dose_ratios': [],
            'nsga2': {'population': 8, 'generations': 3, 'seed': 42},
            'fertilizer_options': {'a': option, 'b': copy.deepcopy(option)},
            'fields': {'Field1': fs, 'Field2': dict(copy.deepcopy(fs), area_ha=2)},
            'crop_economics': {'Wheat': {'price_per_kg': 2, 'marketable_kg_per_kg_dm': 1}}}


def synthetic_meta():
    return {'simulation': 'Wheat', 'start': '2022-01-01', 'end': '2023-12-31',
            'fields': {f'Field{i}': {'area_ha': i, 'crops': ['Wheat'], 'rotation': {
                'RotationCrops': 'Wheat,Wheat', 'RotationCultivars': 'Zlata,Zlata'}} for i in (1, 2)}}


def test_current_model_has_complete_rotation_parameters(actual_config):
    before = fingerprint(actual_config['model'])
    data, sim, zones, restored = load_model(actual_config)
    assert len(zones) == 10
    assert restored == []
    original = read_json(actual_config['model'])
    for z in zones:
        manager = next(c for c in z['Children'] if c['Name']=='Sow using a variable rule')
        assert manager['Parameters']
    assert data == original
    assert fingerprint(actual_config['model']) == before


def test_actual_model_and_weather_provenance_are_verified(actual_config):
    meta = inventory(actual_config)
    assert meta['model_provenance']['artifact_sha256_verified']
    assert meta['weather_provenance']['artifact_sha256_verified']


def test_actual_diagnostic_config_is_complete_and_explicit(actual_config):
    errors = configuration_errors(actual_config, inventory(actual_config))
    assert errors == []
    assert actual_config['scenario']['kind'] == 'diagnostic'
    assert actual_config['scenario']['recommendation_status'] == 'prohibited'
    assert set(actual_config['fields']['Field2']['fertilizer_options']) == {
        'UreaN_old_price_proxy', 'NH4NO3N_old_price_proxy',
        'UAN_N_old_price_proxy', 'NO3N_old_price_proxy'}
    assert actual_config['fields']['Field2']['dose_kg_product_ha']['values'] == [
        0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0]


def test_production_readiness_artifact_is_hash_bound(tmp_path):
    cfg = synthetic_config()
    cfg['scenario'] = {'kind': 'production'}
    cfg['model'] = str(tmp_path/'model.apsimx')
    cfg['weather'] = str(tmp_path/'weather.met')
    (tmp_path/'model.apsimx').write_text('{}')
    (tmp_path/'weather.met').write_text('weather')
    artifact = tmp_path/'readiness.json'
    artifact.write_text(json.dumps({
        'selected': {'recommendation_allowed': True},
        'inputs': {'model_sha256': fingerprint(cfg['model']),
                   'weather_sha256': fingerprint(cfg['weather'])},
    }))
    cfg['historical_readiness'] = str(artifact)
    assert configuration_errors(cfg, synthetic_meta()) == []
    (tmp_path/'model.apsimx').write_text('{"changed": true}')
    assert any('model hash differs' in error
               for error in configuration_errors(cfg, synthetic_meta()))


def test_preparation_keeps_full_clock_and_adds_crop_reports(actual_config, tmp_path):
    actual_config['year'] = 2023
    path = prepare(actual_config, tmp_path)
    from src.multifield_model import walk, kind
    nodes = [n for _, n in walk(read_json(path))]
    assert not any(n['Name']=='Synchroniser' for n in nodes)
    assert next(n for n in nodes if n['Name']=='Clock')['Start'].startswith('2019')
    reports = [n for n in nodes if kind(n)=='Models.Report' and n['Name'].startswith('OptHarvest')]
    assert len(reports) == 70
    assert all(n['EventNames'][0].endswith('.Harvesting') for n in reports)


def make_database(path):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE _Simulations (ID INTEGER, Name TEXT)')
        db.execute("INSERT INTO _Simulations VALUES (1, 'Wheat')")
        for field in ('Field1', 'Field2'):
            db.execute(f'CREATE TABLE OptSoil_{field} (SimulationID INTEGER, Date TEXT, MineralN_NO3_NH4 REAL, UreaN REAL, AppliedProduct REAL, ApplicationPasses REAL, Nutrient0 REAL)')
            for i in range(365):
                date = (dt.date(2023, 1, 1)+dt.timedelta(days=i)).isoformat()
                db.execute(f'INSERT INTO OptSoil_{field} VALUES (1, ?, 15, 3, 5, 1, 15)', (date,))
            db.execute(f'CREATE TABLE OptHarvest_{field}_Wheat (SimulationID INTEGER, Date TEXT, YieldDM REAL)')
            db.executemany(f'INSERT INTO OptHarvest_{field}_Wheat VALUES (?, ?, ?)',
                           [(1, '2023-07-01', 100), (1, '2023-09-01', 50), (1, '2022-08-01', 9000), (2, '2023-08-01', 8000)])


def test_harvest_sum_year_identity_area_profit(tmp_path):
    cfg = synthetic_config()
    path = tmp_path/'test.db'
    make_database(path)
    results = read_results(path, synthetic_meta(), 2023, cfg)
    choices = {f: {'option': 'a', 'dose_kg_product_ha': 5} for f in cfg['fields']}
    result = score(results, cfg, choices)
    assert result['yield_dm_kg'] == 450
    assert result['net_profit'] == (300-15-2-10)*3
    assert result['F'] == [-450, -819, 0]
    assert result['feasible']
    results['fields']['Field1']['nutrients'][0]['observed_min'] = 5
    assert score(results, cfg, choices)['soil_deviation'] == .5
    assert not score(results, cfg, choices)['feasible']


def test_field_product_economics_distinguishes_same_apsim_crop(tmp_path):
    cfg = synthetic_config()
    cfg['fields']['Field2']['product_economics'] = {
        'Wheat': {'price_per_kg': 1, 'marketable_kg_per_kg_dm': 1}}
    path = tmp_path/'test.db'
    make_database(path)
    results = read_results(path, synthetic_meta(), 2023, cfg)
    choices = {f: {'option': 'a', 'dose_kg_product_ha': 5} for f in cfg['fields']}
    result = score(results, cfg, choices)
    assert result['net_profit'] == 273 + 2 * 123


@pytest.mark.parametrize('mutation', ['missing_day', 'missing_harvest', 'null', 'nan', 'duplicate_day'])
def test_reader_fails_closed(tmp_path, mutation):
    path = tmp_path/'test.db'
    make_database(path)
    with sqlite3.connect(path) as db:
        if mutation == 'missing_day':
            db.execute("DELETE FROM OptSoil_Field1 WHERE Date='2023-12-31'")
        elif mutation == 'missing_harvest':
            db.execute('DELETE FROM OptHarvest_Field1_Wheat')
        elif mutation == 'duplicate_day':
            db.execute('INSERT INTO OptSoil_Field1 SELECT * FROM OptSoil_Field1 LIMIT 1')
        else:
            db.execute('UPDATE OptHarvest_Field1_Wheat SET YieldDM=?', (None if mutation=='null' else 'NaN',))
    with pytest.raises(ValueError):
        read_results(path, synthetic_meta(), 2023, synthetic_config())


def test_message_type_and_inactive_crop(tmp_path):
    path = tmp_path/'test.db'
    make_database(path)
    meta = synthetic_meta()
    meta['fields']['Field1']['crops'].append('Oats')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE _Messages (MessageType INTEGER, Message TEXT)')
        db.execute("INSERT INTO _Messages VALUES (2, 'Information')")
    assert read_results(path, meta, 2023)['fields']['Field1']['yield_dm_kg_ha']['Oats'] == 0
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO _Messages VALUES (0, 'Failure')")
    with pytest.raises(ValueError, match='APSIM errors'):
        read_results(path, meta, 2023)


def test_constraints_and_application_audit(tmp_path):
    cfg = synthetic_config()
    assert configuration_errors(cfg, synthetic_meta()) == []
    cfg['fields']['Field1']['nutrients'][0]['max'] = 10
    assert configuration_errors(cfg, synthetic_meta())
    cfg = synthetic_config()
    path = tmp_path/'test.db'
    make_database(path)
    results = read_results(path, synthetic_meta(), 2023, cfg)
    with pytest.raises(ValueError, match='differs from decision'):
        score(results, cfg, {f: {'option':'a', 'dose_kg_product_ha': 6} for f in cfg['fields']})


def test_previous_autumn_application_is_inside_harvest_season():
    cfg = synthetic_config()
    cfg['fields']['Field1']['application_dates'] = ['2022-09-05', '2023-04-15']
    assert configuration_errors(cfg, synthetic_meta()) == []
    source = '''private void OnSowing(object sender, EventArgs e)\n        {\n        }\n    }'''
    code = fertilizer_code(source, cfg, 'Field1', {'option': 'a', 'dose_kg_product_ha': 5})
    assert 'new DateTime(2022, 9, 1)' in code
    assert 'new DateTime(2023, 12, 31)' in code
    assert 'Clock.Today.Date == new DateTime(2022, 9, 5)' in code
    assert 'Clock.Today.Year != 2023' not in code


def test_technology_parser_preserves_only_explicit_nutrient_rates():
    assert parse_technology_nutrients('традиц (N60P60K60)') == {
        'N': 60.0, 'P': 60.0, 'K': 60.0}
    assert parse_technology_nutrients('с удобрением (к70)') == {
        'N': None, 'P': None, 'K': 70.0}
    assert parse_technology_nutrients('без удобрений') == {
        'N': 0.0, 'P': 0.0, 'K': 0.0}


def test_historical_fertilizer_patch_is_date_specific_and_preserves_type():
    source = '''private void OnSowing(object sender, EventArgs e)\n        {\n            Fertiliser.Apply(amount: Amount, type: FertiliserType);\n        }'''
    code = historical_fertilizer_code(source, [
        {'sowing_date': '2021-05-18', 'amount_kg_n_ha': 60},
        {'sowing_date': '2023-05-03', 'amount_kg_n_ha': 30},
    ])
    assert 'double historicalReplayAmount = Amount;' in code
    assert 'new DateTime(2021, 5, 18)' in code
    assert 'historicalReplayAmount = 60;' in code
    assert 'new DateTime(2023, 5, 3)' in code
    assert 'type: FertiliserType' in code


def test_actual_historical_replay_plan_is_traceable(actual_config):
    actual_config['historical_observations'] = str(
        ROOT/'data'/'observations'/'multifield_2019_2023.json')
    actual_config['historical_replay'] = {
        'enabled': True,
        'nitrogen_source': 'technology_labels',
        'application_event': 'model_sowing_event_on_observed_date',
        'fertilizer_type': 'preserve_model',
    }
    _, sim, zones, _ = load_model(actual_config)
    plan = historical_replay_plan(actual_config, sim, zones)
    assert len(plan['entries']) == 22
    assert any(row['field'] == 'Field7' and row['harvest_year'] == 2023 and
               row['amount_kg_n_ha'] == 60 for row in plan['entries'])
    assert any(row['field'] == 'Field5' and row['harvest_year'] == 2019 and
               row['reason'] == 'sowing_date_outside_model_clock'
               for row in plan['skipped'])
    assert all(row['fertilizer_type'] in {'NO3N', 'NH4NO3N'} for row in plan['entries'])


def test_perennial_transition_policy_harvests_before_winter_sowing():
    code, changed = rotation_transition_code(
        PERENNIAL_TRANSITION_OLD, 'harvest_on_winter_sowing_proxy')
    assert changed
    assert 'if (IsPurePerennial(index))' in code
    assert code.index('HarvestGroup(index)') < code.index('EndPlantsNotIn(next)')
    preserved, changed = rotation_transition_code(
        PERENNIAL_TRANSITION_OLD, 'preserve_source')
    assert preserved == PERENNIAL_TRANSITION_OLD
    assert not changed


def test_unknown_transition_policy_is_rejected():
    cfg = synthetic_config()
    cfg['rotation_transition_policy'] = 'invented'
    assert any('rotation_transition_policy' in error
               for error in configuration_errors(cfg, synthetic_meta()))


def test_application_outside_decision_period_rejected():
    cfg = synthetic_config()
    cfg['fields']['Field1']['application_dates'] = ['2022-08-31']
    assert any('invalid application date' in e for e in configuration_errors(cfg, synthetic_meta()))


def test_historical_comparison_uses_explicit_product_components(tmp_path):
    path = tmp_path/'test.db'
    make_database(path)
    meta = synthetic_meta()
    results = read_results(path, meta, 2023)
    observations = {
        'decision_period': {'start': '2022-09-01', 'end': '2023-12-31'},
        'complete_field_coverage_by_year': {'2023': True},
        'years': {'2023': [
            {'field': field, 'crop_group': 'wheat', 'technology': 'observed',
             'observed_yield_t_ha': observed, 'model_crop': 'Wheat',
             'model_cultivar': 'Zlata', 'yield_components': ['Wheat']}
            for field, observed in [('Field1', .2), ('Field2', .1)]
        ]}}
    comparison = compare_historical(results, meta, observations, 2023)
    assert comparison['metrics']['fields_compared'] == 2
    assert comparison['metrics']['simulated_total_t'] == pytest.approx(.45)
    csv_path = tmp_path/'comparison.csv'
    write_comparison_csv(csv_path, comparison)
    assert 'simulated_yield_t_ha' in csv_path.read_text(encoding='utf-8-sig')


def test_lower_only_soil_constraint(tmp_path):
    cfg = synthetic_config()
    for field in cfg['fields'].values():
        field['nutrients'][0]['max'] = None
    assert configuration_errors(cfg, synthetic_meta()) == []
    path = tmp_path/'test.db'
    make_database(path)
    results = read_results(path, synthetic_meta(), 2023, cfg)
    choices = {f: {'option': 'a', 'dose_kg_product_ha': 5} for f in cfg['fields']}
    result = score(results, cfg, choices)
    assert result['soil_deviation'] == 0
    assert len(result['G']) == 3  # one lower bound per field plus padding
    results['fields']['Field1']['nutrients'][0]['observed_min'] = 5
    assert score(results, cfg, choices)['soil_deviation'] == .5


def test_soil_objective_only_keeps_tradeoff_candidate_feasible(tmp_path):
    cfg = synthetic_config()
    cfg['soil_constraint_mode'] = 'objective_only'
    path = tmp_path/'test.db'
    make_database(path)
    results = read_results(path, synthetic_meta(), 2023, cfg)
    results['fields']['Field1']['nutrients'][0]['observed_min'] = 5
    choices = {f: {'option': 'a', 'dose_kg_product_ha': 5} for f in cfg['fields']}
    result = score(results, cfg, choices)
    assert result['soil_deviation'] == .5
    assert result['G'] == [0.0]
    assert result['feasible']


def test_mixed_nsga2_and_failure_exclusion(tmp_path):
    cfg, meta = synthetic_config(), synthetic_meta()
    def evaluate(d):
        x = sum(v['dose_kg_product_ha'] for v in d.values())
        if d['Field1']['option'] == 'b':
            raise RuntimeError('synthetic APSIM failure')
        return {'F': [-x, x, 0.0], 'G': [-1]*5, 'feasible': True}
    result = optimize(cfg, meta, tmp_path, evaluate)
    assert result['status']=='ok' and result['failed'] > 0
    assert all(v['status']=='ok' for v in result['pareto'])
    assert all(v['decisions']['Field1']['option']=='a' for v in result['pareto'])
    assert (tmp_path/'evaluations_summary.csv').is_file()
    assert (tmp_path/'pareto_summary.csv').is_file()
    result2 = optimize(cfg, meta, tmp_path/'repeat', evaluate)
    assert [v['F'] for v in result['pareto']] == [v['F'] for v in result2['pareto']]


def test_nsga2_uses_only_configured_discrete_fertilizer_doses(tmp_path):
    cfg, meta = synthetic_config(), synthetic_meta()
    allowed = {0.0, 5.0, 10.0}
    for field in cfg['fields'].values():
        field['dose_kg_product_ha']['values'] = sorted(allowed)
    seen = []

    def evaluate(decisions):
        seen.extend(value['dose_kg_product_ha'] for value in decisions.values())
        total = sum(value['dose_kg_product_ha'] for value in decisions.values())
        return {'F': [-total, total, 0.0], 'G': [-1] * 5, 'feasible': True}

    assert configuration_errors(cfg, meta) == []
    optimize(cfg, meta, tmp_path, evaluate)
    assert seen and set(seen) <= allowed


def test_no_feasible_front(tmp_path):
    result = optimize(synthetic_config(), synthetic_meta(), tmp_path,
                      lambda d: {'F': [1,2,3], 'G': [1]*5, 'feasible': False})
    assert result['status'] == 'no_feasible_solutions'
    assert result['pareto'] == []
    assert result['least_constraint_violation'][0]['total_positive_constraint_violation'] == 5


def test_reference_decision_is_always_evaluated(tmp_path):
    cfg = synthetic_config()
    cfg['reference_decision'] = {
        field: {'option': 'a', 'dose_kg_product_ha': 5.0} for field in cfg['fields']}

    def evaluate(decisions):
        feasible = decisions == cfg['reference_decision']
        return {'F': [-1, -2, 0], 'G': ([-1] * 5 if feasible else [1] * 5),
                'feasible': feasible}

    result = optimize(cfg, synthetic_meta(), tmp_path, evaluate)
    assert result['status'] == 'ok'
    assert result['reference_candidate_id']
    assert any(row['candidate_id'] == result['reference_candidate_id'] for row in result['pareto'])
    assert all(row['decisions'] == cfg['reference_decision'] for row in result['pareto'])


def test_environment_scenario_validation_is_fail_closed():
    cfg = synthetic_config()
    cfg['environment_scenarios'] = [
        {'name': 'wet'}, {'name': 'dry'}]
    errors = configuration_errors(cfg, synthetic_meta())
    assert any('scenario_aggregation' in error for error in errors)
    cfg['scenario_aggregation'] = {
        'yield': 'mean', 'profit': 'worst_case', 'soil': 'worst_case'}
    assert configuration_errors(cfg, synthetic_meta()) == []
    assert [row['name'] for row in environment_scenarios(cfg)] == ['wet', 'dry']


def test_environment_model_override_does_not_keep_stale_provenance():
    cfg = synthetic_config()
    cfg.update(model='base.apsimx', model_provenance='base.provenance.json',
               weather='weather.met', weather_provenance='weather.provenance.json')
    without = environment_config(cfg, {'name': 'variant', 'model': 'variant.apsimx'})
    assert without['model'] == 'variant.apsimx'
    assert without['model_provenance'] is None
    with_provenance = environment_config(cfg, {
        'name': 'variant', 'model': 'variant.apsimx',
        'model_provenance': 'variant.provenance.json',
    })
    assert with_provenance['model_provenance'] == 'variant.provenance.json'


def test_environment_scores_support_mean_and_worst_case():
    first = {'yield_dm_kg': 100, 'net_profit': 20, 'soil_deviation': .1,
             'F': [-100, -20, .1], 'G': [-1, 0], 'feasible': True,
             'fields': {'Field1': {}}}
    second = {'yield_dm_kg': 60, 'net_profit': -10, 'soil_deviation': .4,
              'F': [-60, 10, .4], 'G': [-.5, 0], 'feasible': True,
              'fields': {'Field1': {}}}
    result = aggregate_scenario_scores(
        [('wet', first), ('dry', second)],
        {'yield': 'mean', 'profit': 'worst_case', 'soil': 'worst_case'})
    assert result['F'] == [-80, 10, .4]
    assert result['yield_dm_kg_range'] == [60, 100]
    assert result['net_profit_range'] == [-10, 20]
    assert result['G'] == [-1, 0, -.5, 0]
    assert 'fields' not in result


def test_environment_ranges_are_written_to_candidate_csv(tmp_path):
    cfg = synthetic_config()
    field_result = lambda y, p, n: {
        'yield_dm_kg_ha': {'Wheat': y},
        'net_profit_per_ha': p,
        'mineral_n_end_kg_ha': n,
    }
    first = {'yield_dm_kg': 100, 'net_profit': 20, 'soil_deviation': .1,
             'F': [-100, -20, .1], 'G': [-1, 0], 'feasible': True,
             'fields': {'Field1': field_result(100, 20, 15),
                        'Field2': field_result(80, 10, 14)}}
    second = {'yield_dm_kg': 60, 'net_profit': -10, 'soil_deviation': .4,
              'F': [-60, 10, .4], 'G': [-.5, 0], 'feasible': True,
              'fields': {'Field1': field_result(50, -10, 9),
                         'Field2': field_result(70, 5, 12)}}
    candidate = aggregate_scenario_scores(
        [('wet', first), ('dry', second)],
        {'yield': 'mean', 'profit': 'worst_case', 'soil': 'worst_case'})
    candidate.update(candidate_id='abc', status='ok', decisions={
        field: {'option': 'a', 'dose_kg_product_ha': 5} for field in cfg['fields']})
    path = tmp_path/'summary.csv'
    write_candidate_summary_csv(path, [candidate], cfg)
    with path.open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row['environment_scenarios'] == 'wet;dry'
    assert row['yield_aggregation'] == 'mean'
    assert float(row['yield_dm_kg_min']) == 60
    assert float(row['yield_dm_kg_max']) == 100
    assert float(row['Field1_yield_dm_kg_ha_min']) == 50
    assert float(row['Field1_yield_dm_kg_ha_max']) == 100
    assert float(row['Field1_mineral_n_end_kg_ha_min']) == 9
    assert float(row['Field1_mineral_n_end_kg_ha_max']) == 15
