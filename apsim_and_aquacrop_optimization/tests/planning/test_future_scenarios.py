"""Future maps require native simulation, explicit climate and preserved history."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys

import pytest
import yaml

from agro.adapters.rotation_management import manager, parameters
from agro.apsim.met_weather import read_met
from agro.planning.future_model import extend_model, source_parts, write_analogue_weather, fingerprint
from agro.planning.future_scenarios import management_presets, objective_value, crop_mix

ROOT=Path(__file__).resolve().parents[2]


def test_objective_only_retains_humic_losses_without_rejecting_a_crop_valid_map():
    from agro.planning.future_scenarios import evaluate_humic_policy
    row={'F':[-100.,5.,.2],'G':[.1,-.5],'yield_dm_kg':100.,'net_profit_proxy':-5.,
         'humic':{'Field1':{'start_kg_ha':100.,'end_kg_ha':90.},'Field2':{'start_kg_ha':100.,'end_kg_ha':150.}}}
    result=evaluate_humic_policy(row,[-1.,0.],['yield','profit','humic'],'objective_only')
    assert result['G']==[-1.,0.] and result['feasible']
    assert result['humic_constraints_G']==[.1,-.5] and result['F']==[-100.,5.,.1]
    result=evaluate_humic_policy({**row,'G':[.1,-.5]},[1.],['humic'],'objective_only')
    assert not result['feasible'] # Crop-count violations remain hard constraints.
MODEL=ROOT/'models/apsim/simulation/calibrated_history/simulation.apsimx'
PLANNING=ROOT/'models/apsim/configs/rotation_planning_2025_2029.yaml'


def planning_inputs():
    source=json.loads(MODEL.read_text(encoding='utf-8'))
    planning=yaml.safe_load(PLANNING.read_text(encoding='utf-8'))
    sequence=['spring_wheat','winter_rye','oats_with_underseed',
              'perennial_grass_year1','perennial_grass_year2']
    return source,planning,{f:list(sequence) for f in planning['initial_states']}


def test_extended_model_preserves_calibrated_rotation_and_adds_observed_bridge():
    source,planning,sequences=planning_inputs();original=deepcopy(source)
    extended,audit=extend_model(source,planning['initial_states'],sequences,2025)
    assert source==original
    clock,zones=source_parts(extended);_,old_zones=source_parts(source)
    assert clock['Start']=='2004-01-01T00:00:00'
    assert clock['End']=='2029-12-31T00:00:00'
    for field,zone in zones.items():
        old=parameters(manager(old_zones[field],'RotationManager'))
        new=parameters(manager(zone,'RotationManager'))
        for key in ('RotationCrops','RotationCultivars','RotationSowingDates','RotationHarvestDates',
                    'RotationPopulations','RotationSowingDepths','RotationRowSpacings','PastureAges'):
            assert [v.strip() for v in new[key].split(',')][:20]==[v.strip() for v in old[key].split(',')]
            assert len(new[key].split(','))==26
        assert new['RotationCrops'].split(',')[21].strip()=='Wheat'
    assert len(audit['assumed_calendar_profiles'])==60
    assert all(r['profile_source_year']<=2023 for r in audit['assumed_calendar_profiles'])


def test_unsupported_future_crop_is_rejected():
    source,planning,sequences=planning_inputs();sequences['Field1'][0]='potato'
    with pytest.raises(ValueError,match='No calibrated crop-state adapter'):
        extend_model(source,planning['initial_states'],sequences,2025)


def test_missing_intermediate_history_is_not_invented():
    source,planning,sequences=planning_inputs()
    with pytest.raises(ValueError,match='Missing observed bridge'):
        extend_model(source,planning['initial_states'],sequences,2027)


def test_climate_replay_matches_month_day_and_interpolates_missing_leap_day(tmp_path):
    path=tmp_path/'source.met';rows=[];current=dt.date(2019,1,1)
    while current.year<=2020:
        rows.append(f'{current.year} {current.timetuple().tm_yday} 10 15 5 {current.month*10+current.day}')
        current+=dt.timedelta(days=1)
    path.write_text('year day radn maxt mint rain\n() () (MJ/m2/d) (oC) (oC) (mm)\n'+'\n'.join(rows)+'\n')
    original=path.read_bytes();out=tmp_path/'weather.met'
    audit=write_analogue_weather(path,out,'2020-12-31',{2021:2019,2022:2020,2023:2019,2024:2019},2024)
    indexed={r['date']:r for r in read_met(out)['rows']}
    assert indexed[dt.date(2020,2,29)]['rain']==49
    assert indexed[dt.date(2022,3,1)]['rain']==31
    assert indexed[dt.date(2024,2,29)]['rain']==39.5
    assert audit['leap_days_interpolated']==['2024-02-29']
    assert path.read_bytes()==original
    assert len(indexed)==(dt.date(2025,1,1)-dt.date(2019,1,1)).days


def test_historical_results_supply_policies_not_future_objective_values(tmp_path):
    model=tmp_path/'model';model.mkdir()
    for name in ['simulation.apsimx','weather.met']:(model/name).write_text(name)
    run=tmp_path/'run';run.mkdir()
    (run/'optimization_summary.json').write_text(json.dumps({'status':'no_feasible_solution'}))
    (run/'sources.json').write_text(json.dumps({'inputs':{name:fingerprint(model/name) for name in ['simulation.apsimx','weather.met']}}))
    rows=[{'candidate_id':'A','status':'ok','yield_dm_kg':100,'net_profit_proxy':-10,
           'mineral_n_shortfall':.3,'G':[.1],'feasible':False,'policies':{'test':'policy A'}},
          {'candidate_id':'B','status':'ok','yield_dm_kg':90,'net_profit_proxy':-5,
           'mineral_n_shortfall':.1,'G':[.01],'feasible':False,'policies':{'test':'policy B'}}]
    (run/'evaluations.json').write_text(json.dumps(rows))
    presets=management_presets(run,model)
    assert presets['yield']['candidate_id']=='A'
    assert presets['profit']['candidate_id']=='B'
    assert not presets['yield']['historically_feasible']
    assert 'yield_dm_kg' not in presets['yield']
    (model/'simulation.apsimx').write_text('different calibration')
    with pytest.raises(ValueError,match='does not match'):management_presets(run,model)


def test_single_objectives_have_correct_direction_and_humic_uses_worst_field():
    row={'yield_dm_kg':100.,'net_profit_proxy':-5.,'mineral_n_shortfall':.2,
         'humic':{'Field1':{'start_kg_ha':100.,'end_kg_ha':90.},
                  'Field2':{'start_kg_ha':100.,'end_kg_ha':200.}}}
    assert objective_value(row,'yield')==-100
    assert objective_value(row,'profit')==5
    assert objective_value(row,'mineral_n')==.2
    assert objective_value(row,'humic')==.1


def test_portable_starting_reference_needs_no_archived_runs(tmp_path):
    from agro.planning.future_scenarios import load_management_reference
    model=tmp_path/'model';model.mkdir()
    for name in ['simulation.apsimx','weather.met']:(model/name).write_text(name)
    bundle={'schema_version':1,'kind':'management_starting_reference',
            'source_inputs_sha256':{n:fingerprint(model/n) for n in ['simulation.apsimx','weather.met']},
            'presets':{n:{'policies':{'example':n}} for n in ['yield','profit','mineral_n','humic']},
            'native_config':{'runtime':{'command':['Models']}}}
    reference=tmp_path/'reference.json';reference.write_text(json.dumps(bundle))
    presets,config=load_management_reference(reference,model)
    assert presets==bundle['presets'] and config==bundle['native_config']
    (model/'weather.met').write_text('different weather')
    with pytest.raises(ValueError,match='does not match'):load_management_reference(reference,model)


def test_crop_count_bounds_are_checked_independently_of_transition_validity():
    ctx={'fields':['Field1','Field2'],'planning':{'years':1,'start_year':2025,
         'farm_crop_mix':{'categories':{'wheat':{'states':['spring_wheat'],'annual_min':1,'annual_max':1},
                                        'oats':{'states':['oats_with_underseed'],'annual_min':1,'annual_max':1}}}}}
    selected={f:{'sequence_ids':['spring_wheat']} for f in ctx['fields']}
    constraints,summary=crop_mix(ctx,selected)
    assert any(g>0 for g in constraints) and not summary['within_historical_ranges']
    selected['Field2']['sequence_ids']=['oats_with_underseed']
    constraints,summary=crop_mix(ctx,selected)
    assert all(g<=0 for g in constraints) and summary['within_historical_ranges']


@pytest.mark.parametrize('run_search',[False,True])
@pytest.mark.parametrize('engine',['apsim','aquacrop'])
@pytest.mark.parametrize('objectives',[['yield'],['yield','profit'],['yield','profit','mineral_n'],['yield','profit','mineral_n','humic'],['yield','profit','mineral_n','humic','n_input','n_cost']])
def test_scenario_pipeline_prepares_without_simulation_and_scores_new_maps(tmp_path,monkeypatch,run_search,objectives,engine):
    from agro.planning import future_scenarios as module
    from agro.contracts import FieldYearResult,SimulationResult
    model=tmp_path/'model';model.mkdir();(model/'simulation.apsimx').write_text('{}')
    optimization=tmp_path/'optimization';optimization.mkdir();(optimization/'evaluations.json').write_text('[]')
    sentinel=tmp_path/'inputs.json';sentinel.write_text('{}')
    alternative=lambda state,rank:{'rank':rank,'sequence_ids':[state],'transition_codes':['Ц'],'mean_score':1.}
    ctx={'config':{'model_root':str(model),'optimization_run':str(optimization),
         'historical_weather_end':'2023-12-31','climate_analogue_years':{2024:2018,2025:2019},
         'nsga2':{'population':2,'generations':1,'seed':42}},
         'planning':{'start_year':2025,'years':1,'initial_states':{'Field1':'spring_wheat'},
         'farm_crop_mix':{'categories':{'cereal':{'states':['spring_wheat','winter_rye'],
                                                 'annual_min':0,'annual_max':1}}}},
         'fields':['Field1'],'pool':{'fields':{'Field1':{'alternatives':[
              alternative('spring_wheat',1),alternative('winter_rye',2)]}}},
         'presets':{'yield':{'candidate_id':'historical_028','historically_feasible':False,
                            'policies':{'marker':'fixed optimized management'}}},
         'native_config':{'evaluation_years':[2025],
              'fertilizer':{'forms':{'n':{'apsim_type':'NO3N','price_per_kg_n':1.}}},
              'economics':{'native_harvest_price_per_kg_dm':{'Wheat':1.},
                           'application_cost_per_ha':1.,'other_cost_per_ha_year':1.}},
         'excluded_states':[],'rules_path':sentinel,'planning_path':sentinel,'config_path':sentinel}
    calls=[]
    ctx['config'].update(engine=engine,aquacrop={'command':[sys.executable]})
    from agro.planning import aquacrop_future
    aqua_calls=[]
    monkeypatch.setattr(aquacrop_future,'prepare_model',lambda *args: {})
    monkeypatch.setattr(aquacrop_future,'prepare_inputs',lambda *args: None)
    ctx['config']['optimization']={'additional_objectives':{
        'n_input':{'label':'N input','metric':'fertilizer_n_kg','direction':'min'},
        'n_cost':{'label':'N cost','metric':'fertilizer_cost_proxy','direction':'min'}}}
    def build(context,selected,policies):
        return {'state':selected['Field1']['sequence_ids'][0],'managed':policies is not None},{}
    def weather(source,destination,*args):
        destination.write_text('climate analogue');return {'kind':'test climate analogue'}
    def simulator(document,config,policies,work):
        calls.append(document);work.mkdir(parents=True)
        (work/'candidate.apsimx').write_text(json.dumps(document))
        mass=1000. if not document['managed'] else 1500. if document['state']=='spring_wheat' else 2000.
        return SimulationResult('apsim',(FieldYearResult('Field1',2025,1.,{'Wheat':mass},
            {'mineral_n_end_kg_ha':10.}),)),{'applications':[],
            'humic':{'Field1':{'start_kg_ha':100.,'end_kg_ha':101.}}}
    monkeypatch.setattr(module,'build_model',build)
    monkeypatch.setattr(module,'write_analogue_weather',weather)
    monkeypatch.setattr(module,'native_prepare',lambda document,*args:(document,[]))
    monkeypatch.setattr(module,'simulate',simulator)
    def aqua_simulate(document,runtime,work,settings,bridge_year,apsim_simulate):
        aqua_calls.append(document)
        assert bridge_year==2024 and apsim_simulate is simulator
        result,audit=simulator(document,runtime,None,work)
        (work/'aquacrop_manifest.json').write_text('{}',encoding='utf-8')
        audit['metric_sources']={'harvest':'native AquaCrop','humic':'APSIM','mineral_n':'APSIM'}
        return result,audit
    monkeypatch.setattr(aquacrop_future,'simulate_map',aqua_simulate)
    monkeypatch.setattr(module,'artifacts',lambda *args:None)
    monkeypatch.setattr(module,'report',lambda *args:None)
    def search(options,evaluate,settings,n_objectives,n_constraints):
        assert n_objectives==len(objectives)
        assert settings['algorithm']==('nsga3' if len(objectives)>=4 else 'nsga2')
        for vector in ([0],[1]):
            row=evaluate(vector)
            assert len(row['F'])==n_objectives and len(row['G'])==n_constraints
        assert evaluate([0])['candidate_id']=='candidate_001' # cache
    monkeypatch.setattr(module,'search_integer',search)
    # A population of four suffices for NSGA-III's four axis directions.
    ctx['config']['nsga2']['population']=max(4,len(objectives))
    output=module.execute(ctx,objectives,tmp_path/'result',run_search=run_search)
    status=json.loads((output/'status.json').read_text())
    if not run_search:
        assert calls==[] and status['stage']=='prepared'
        assert not (output/'evaluations.json').exists()
    else:
        assert len(calls)==3 # native baseline and two distinct future maps
        result=json.loads((output/'selected_result.json').read_text())
        assert result['F']==[-2000.,-1999.,0.,-.01,0.,0.][:len(objectives)]
        assert result['crop_sequences']=={'Field1':['winter_rye']}
        assert status['stage']=='completed'
        assert result['engine']==engine
        if engine=='aquacrop':
            assert len(aqua_calls)==3 and result['metric_sources']['harvest']=='native AquaCrop'
            assert (output/'selected_aquacrop.json').exists()
        else:assert aqua_calls==[]
        assert (output/'evaluations.csv').exists()
        front=json.loads((output/'pareto.json').read_text())
        assert len(front)==1 and front[0]['candidate_id']==result['candidate_id']
        assert (output/'pareto.csv').exists()
        summary=json.loads((output/'optimization_summary.json').read_text())
        assert summary['objectives']==objectives
    inputs=json.loads((output/'scenario_inputs.json').read_text())
    assert inputs['objectives']==objectives and inputs['management_preset']=='yield'


def test_joint_objectives_retain_tradeoffs_and_display_choice_is_scale_independent():
    from agro.optimization.nsga2 import nondominated
    from agro.planning.future_scenarios import representative,objective_list
    rows=[{'candidate_id':str(i),'status':'ok','F':f,'G':[-1]} for i,f in
          enumerate([[-100.,-10.],[-80.,-40.],[-90.,-30.],[-70.,-5.]])]
    front=nondominated(rows)
    assert [r['candidate_id'] for r in front]==['0','1','2']
    assert representative(front)['candidate_id']=='2'
    scaled=deepcopy(front)
    for r in scaled:r['F'][0]*=1e6
    assert representative(scaled)['candidate_id']=='2'
    for names in ([],['yield','yield'],['unknown']):
        with pytest.raises(ValueError):objective_list(names)


@pytest.mark.parametrize('argv,expected',[
    ([],['profit','humic']),
    (['--objective','yield'],['yield']),
    (['--objectives','yield','profit','mineral_n','humic'],['yield','profit','mineral_n','humic']),
])
def test_cli_criteria_override_configuration_without_starting_search(tmp_path,monkeypatch,argv,expected):
    from agro.planning import future_scenarios as module
    ctx={'config':{'output_root':str(tmp_path),'nsga2':{'population':10,'generations':4,'seed':42},
                  'optimization':{'objectives':['profit','humic'],'algorithm':'auto'}}}
    monkeypatch.setattr(module,'context',lambda path:ctx)
    calls=[]
    monkeypatch.setattr(module,'execute',lambda *args:calls.append(args))
    assert module.main(argv)==0
    assert len(calls)==1 and calls[0][1]==expected
    assert calls[0][3:5]==(False,False)


def test_additional_metrics_use_future_applications_area_and_configured_direction():
    from agro.planning.future_scenarios import criteria_registry,supplementary_metrics,objective_list,objective_title
    config={'optimization':{'additional_objectives':{
        'n_input':{'label':'N input','metric':'fertilizer_n_kg','direction':'min'},
        'n_cost':{'label':'N cost','metric':'fertilizer_cost_proxy','direction':'min'},
        'c_gain':{'label':'Carbon gain','metric':'humic_total_change_kg','direction':'max'}}}}
    registry=criteria_registry(config)
    assert len(objective_list(list(registry),registry))==7
    assert objective_title(['c_gain'],registry)=='Carbon gain'
    row={'fields':[{'field':'Field1','area_ha':2.},{'field':'Field2','area_ha':3.}],
         'humic':{'Field1':{'start_kg_ha':100.,'end_kg_ha':105.},
                  'Field2':{'start_kg_ha':100.,'end_kg_ha':98.}}}
    audit={'applications':[{'field':'Field1','year':2025,'dose_kg_n_ha':30.,'form':'UreaN'},
                           {'field':'Field2','year':2024,'dose_kg_n_ha':999.,'form':'UreaN'}]}
    runtime={'evaluation_years':[2025],'fertilizer':{'forms':{'urea':{'apsim_type':'UreaN','price_per_kg_n':10.}}}}
    supplementary_metrics(row,audit,runtime)
    assert objective_value(row,'n_input',registry)==60.
    assert objective_value(row,'n_cost',registry)==600.
    assert objective_value(row,'c_gain',registry)==-4.
    config['optimization']['additional_objectives']['bad']={'label':'Bad','metric':'unknown','direction':'min'}
    with pytest.raises(ValueError,match='Unsupported metric'):criteria_registry(config)


@pytest.mark.parametrize('argv',[
    ['--objectives','yield','yield'],
    ['--objectives','yield','profit','mineral_n','humic','--algorithm','nsga2'],
    ['--objectives','yield','--algorithm','nsga3'],
    ['--objectives','yield','profit','mineral_n','humic','--population','3'],
])
def test_cli_rejects_invalid_search_before_creating_outputs(tmp_path,monkeypatch,argv):
    from agro.planning import future_scenarios as module
    ctx={'config':{'output_root':str(tmp_path),'nsga2':{'population':10,'generations':4,'seed':42}}}
    monkeypatch.setattr(module,'context',lambda path:ctx)
    def forbidden(*args):raise AssertionError('Invalid configuration created a scenario')
    monkeypatch.setattr(module,'execute',forbidden)
    with pytest.raises(SystemExit) as error:module.main(argv)
    assert error.value.code==2
    assert not list(tmp_path.iterdir())
