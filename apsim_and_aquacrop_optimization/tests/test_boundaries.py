"""Cross-engine contracts and optimizer behavior on controlled inputs."""
import json
import sys

import pytest

from agro.adapters.apsim import normalize_results as apsim_results, run_apsim
from agro.adapters.aquacrop import normalize_results as aqua_results, AquaCropBackend
from agro.contracts import Capabilities, FieldYearResult, SimulationResult
from agro.evaluation.problem import SimulationEvaluator, harvested_dry_matter
from agro.optimization.nsga2 import checked, nondominated, search_integer


def aqua_fixture(root):
    generated = root / 'generated' / 'field'
    generated.mkdir(parents=True)
    output = root / 'outputs' / 'field'
    output.mkdir(parents=True)
    mapping = {'area_ha': 2, 'segments': [
        {'id': 'grain', 'catalog_crop': 'EffectiveWheat', 'source_yield_column': 'WheatYield',
         'harvest_dates': ['2023-08-01']},
        {'id': 'forage', 'catalog_crop': 'EffectiveMix', 'source_yield_column': 'MixYield',
         'harvest_dates': ['2023-06-01', '2023-09-01']}]}
    (generated / 'source_mapping.json').write_text(json.dumps(mapping), encoding='utf-8')
    harvests = output / 'harvests.csv'
    harvests.write_text('date,segment,kind,dry_mass_t_ha\n'
                        '2023-08-01,grain,grain,1.5\n'
                        '2023-06-01,forage,forage,2\n'
                        '2023-09-01,forage,forage,3\n', encoding='utf-8')
    return harvests


def test_engines_agree_on_units_area_and_multiple_cuts(tmp_path):
    aqua_fixture(tmp_path)
    aqua = aqua_results(tmp_path, ['field'], [2023])
    apsim = apsim_results({2023: {'fields': {'field': {
        'area_ha': 2, 'yield_dm_kg_ha': {'Wheat': 1500, 'Mix': 5000},
        'mineral_n_end_kg_ha': 12}}}})
    assert aqua.fields[0].harvested_dm_kg_ha == apsim.fields[0].harvested_dm_kg_ha
    assert harvested_dry_matter(aqua, {})['F'] == [-13000]
    assert 'mineral_n_end_kg_ha' not in aqua.fields[0].metrics


def test_missing_and_duplicate_harvests_fail(tmp_path):
    harvests = aqua_fixture(tmp_path)
    text = harvests.read_text()
    harvests.write_text(text.replace('2023-09-01,forage,forage,3\n', ''))
    with pytest.raises(ValueError, match='missing scheduled'):
        aqua_results(tmp_path, ['field'], [2023])
    harvests.write_text(text + '2023-09-01,forage,forage,3\n')
    with pytest.raises(ValueError, match='Duplicate'):
        aqua_results(tmp_path, ['field'], [2023])


def test_unsupported_nitrogen_is_rejected_before_simulation(tmp_path):
    backend = AquaCropBackend(tmp_path, ['engine'], [2023])
    with pytest.raises(ValueError, match='mineral_n_end'):
        SimulationEvaluator(backend, harvested_dry_matter, tmp_path / 'run', 1,
                            required_metrics=['mineral_n_end_kg_ha'])
    assert not (tmp_path / 'run').exists()


def test_evaluator_caches_equivalent_decisions_and_saves_common_result(tmp_path):
    class Backend:
        capabilities = Capabilities(frozenset({'harvested_dm_kg_ha'}), frozenset())
        calls = 0

        def simulate(self, decisions, workdir):
            self.calls += 1
            return SimulationResult('test', (FieldYearResult('field', 2023, 2, {'Wheat': 5}),))

    backend = Backend()
    evaluate = SimulationEvaluator(backend, harvested_dry_matter, tmp_path, 1)
    assert evaluate({'a': 1, 'b': 2}) == evaluate({'b': 2, 'a': 1})
    assert backend.calls == 1
    saved = next(tmp_path.glob('*/simulation.json'))
    assert json.loads(saved.read_text())['engine'] == 'test'


@pytest.mark.parametrize('objectives,constraints', [([1, 2], []), ([float('nan')], []), ([1], [0])])
def test_optimizer_rejects_invalid_evaluation_dimensions(objectives, constraints):
    with pytest.raises(ValueError):
        checked({'F': objectives, 'G': constraints}, 1, 0)


def test_integer_search_and_feasibility_are_engine_independent():
    history = []
    def evaluate(vector):
        row = {'F': [-float(vector[0])], 'G': [], 'status': 'ok'}
        history.append(row)
        return row
    search_integer([[0, 1, 2]], evaluate,
                   {'population': 3, 'generations': 2, 'seed': 42, 'verbose': False}, 1, 0)
    assert nondominated(history)[0]['F'] == [-2]
    assert not nondominated([{'F': [-3], 'G': [1], 'status': 'ok'}])


def test_apsim_process_failure_and_missing_database_fail_closed(tmp_path):
    model = tmp_path / 'candidate.apsimx'
    model.write_text('{}')
    config = {'runtime': {'command': [sys.executable, '-c', 'raise SystemExit(2)'],
                          'timeout_seconds': 5}}
    with pytest.raises(RuntimeError, match='APSIM exit 2'):
        run_apsim(config, model)
    config['runtime']['command'] = [sys.executable, '-c', 'pass']
    with pytest.raises(RuntimeError, match='no database'):
        run_apsim(config, model)
    model.with_suffix('.db').write_bytes(b'stale')
    with pytest.raises(FileExistsError):
        run_apsim(config, model)


def test_contract_rejects_invalid_mass_and_duplicate_fields():
    with pytest.raises(ValueError):
        FieldYearResult('field', 2023, 1, {'Wheat': float('nan')})
    row = FieldYearResult('field', 2023, 1, {'Wheat': 1})
    with pytest.raises(ValueError):
        SimulationResult('test', (row, row))
