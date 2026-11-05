"""Standalone calculations must never obtain metrics or state from another model."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import sys

import pytest

from agro.aquacrop import standalone as module
from agro.contracts import FieldYearResult,SimulationResult

ROOT=Path(__file__).resolve().parents[2]


def scoring_fixture(tmp_path):
    config={'economics':{'price_per_kg_dm':{'Wheat':14.},'cost_per_ha_by_state':{'spring_wheat':23000.}},
            'water_balance_tolerance_mm':.01}
    ctx={'config':config,'fields':[{'id':'field1'}],'planning':{'start_year':2025,'years':1,
        'farm_crop_mix':{'categories':{'wheat':{'states':['spring_wheat'],'annual_min':1,'annual_max':1}}}}}
    folder=tmp_path/'outputs/field1';folder.mkdir(parents=True)
    module.save(folder/'summary.json',{'maximum_water_balance_error_mm':.001})
    with (folder/'annual.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['year','transpiration_mm','evaporation_mm','drainage_mm','runoff_mm'])
        writer.writerow([2024,9999,9999,9999,9999]);writer.writerow([2025,100,50,10,5])
    result=SimulationResult('aquacrop',(FieldYearResult('field1',2025,2.,{'Wheat':1000.}),))
    return ctx,result,{'field1':['spring_wheat']}


def test_native_units_objective_signs_and_bridge_exclusion(tmp_path):
    ctx,result,sequences=scoring_fixture(tmp_path)
    row=module.score_result(result,tmp_path,ctx,sequences,list(module.CRITERIA))
    assert row['yield_dm_kg']==2000 and row['net_profit_proxy']==-18000
    assert row['evapotranspiration_m3']==3000 and row['water_productivity_kg_m3']==pytest.approx(2/3)
    assert row['drainage_m3']==200 and row['runoff_m3']==100
    assert row['F']==[-2000,18000,3000,-2/3,200,100]
    assert row['feasible'] and 'humic' not in row and 'mineral_n_shortfall' not in row
    module.save(tmp_path/'outputs/field1/summary.json',{'maximum_water_balance_error_mm':1.})
    with pytest.raises(ValueError,match='water balance'):module.score_result(result,tmp_path,ctx,sequences,['yield'])


def test_native_model_has_no_runtime_source_project_and_builds_all_fields(tmp_path,monkeypatch):
    from agro.aquacrop import field_config,standalone_inputs
    ctx=module.context(ROOT/'models/aquacrop_standalone/optimization.json');seed=module.choices(ctx,[0]*10)
    original=deepcopy(ctx['planning']);model=module.materialize(ctx,seed,tmp_path/'model')
    assert not list(model.rglob('*.apsimx'))
    assert 'source_project' not in module.read(model/'project.json')
    monkeypatch.setattr(field_config,'ROOT',model)
    fields=field_config.registry();catalog=field_config.create_catalog()
    assert len(fields)==10
    for field in fields:
        plan=standalone_inputs.prepare_field(field,catalog)
        mapping=module.read(plan.parent/'source_mapping.json')
        data=module.read(model/field['model_file'])
        assert module.read(plan)['initial_water']==[layer['field_capacity'] for layer in data['soil']['layers']]
        assert len(mapping['source_rotation'])==6 and len(data['sequence'])==6
    assert ctx['planning']==original


@pytest.mark.parametrize('objectives',[['yield'],['yield','profit'],['yield','profit','water_use'],['yield','profit','water_use','drainage'],list(module.CRITERIA)])
def test_optimizer_routes_native_criteria_without_external_metrics(tmp_path,monkeypatch,objectives):
    ctx,result,sequences=scoring_fixture(tmp_path/'fixture')
    ctx['config'].update(command=[sys.executable],model_root=str(tmp_path/'inputs'),timeout_seconds=5)
    Path(ctx['config']['model_root']).mkdir()
    ctx['planning']['initial_states']={'field1':'spring_wheat'}
    ctx['pool']={'fields':{'field1':{'alternatives':[{'sequence_ids':['spring_wheat']}]}}}
    monkeypatch.setattr(module,'materialize',lambda *args:tmp_path/'model')
    monkeypatch.setattr(module,'prepare_inputs',lambda *args:None)
    monkeypatch.setattr(module,'report',lambda *args:None)
    seen=[]
    class Backend:
        def __init__(self,*args,**kwargs):pass
        def simulate(self,decisions,work):
            seen.append(decisions)
            # Reuse controlled native annual water data at the expected candidate path.
            import shutil
            work.mkdir(parents=True)
            shutil.copytree(tmp_path/'fixture/outputs',work/'outputs')
            return result
    monkeypatch.setattr(module,'AquaCropBackend',Backend)
    def search(options,evaluate,settings,n_objectives,n_constraints):
        assert n_objectives==len(objectives)
        assert settings['algorithm']==('nsga2' if len(objectives)<=3 else 'nsga3')
        assert len(evaluate([0])['F'])==n_objectives
        evaluate([0])
    monkeypatch.setattr(module,'search_integer',search)
    output=module.execute(ctx,tmp_path/'run',objectives,{'algorithm':'auto','population':10,'generations':2,'seed':42},'run')
    assert len(seen)==1 # Preflight and repeated optimizer requests share one native calculation.
    row=module.read(output/'selected_result.json')
    assert row['engine']=='aquacrop_standalone' and row['feasible']
    assert module.read(output/'optimization_summary.json')['pareto_candidates']==1


def test_unsupported_external_criterion_is_rejected_before_output(tmp_path):
    with pytest.raises(ValueError,match='standalone criteria'):
        module.execute({},tmp_path/'run',['humic'],{},'run')
    assert not (tmp_path/'run').exists()
