from copy import deepcopy
import json
from pathlib import Path

import pytest
from agro.planning.joint_management import policy_space,decode_policies


def domain():
    return {'management_policies':{'spring_wheat':{'cultivar_options':['Zlata','Irgina'],
        'population_options_m2':[250,500],'depth_options_mm':[20], 'row_spacing_options_mm':[150]}},
        'fertilizer':{'doses_kg_n_ha':[0,30],'forms':{'urea':{'apsim_type':'UreaN','price_per_kg_n':1.}}}}


def test_anchor_and_bounds():
    config=domain()
    preset={'spring_wheat':{'cultivar':'Irgina','population':500.,'depth':20.,'row_spacing':150.,
            'fertilizer':{'name':'urea_30','apsim_type':'UreaN','dose_kg_n_ha':30.,'price_per_kg_n':1.}}}
    original=deepcopy(config);space=policy_space(config,preset)
    assert decode_policies(config,space,[0]*3)==preset
    changed=decode_policies(config,space,[1]*3)['spring_wheat']
    assert changed['cultivar']=='Zlata' and changed['population']==250 and changed['fertilizer']['dose_kg_n_ha']==0
    assert config==original
    with pytest.raises(ValueError):decode_policies(config,space,[0])
    with pytest.raises(ValueError):decode_policies(config,space,[10,0,0])


def test_same_map_different_management_runs_distinct_simulations(tmp_path,monkeypatch):
    from agro.planning import future_scenarios as m
    from agro.contracts import SimulationResult,FieldYearResult
    config=domain();space=policy_space(config,{'spring_wheat':{'cultivar':'Irgina','population':500.,'depth':20.,'row_spacing':150.,'fertilizer':{'name':'urea_30','apsim_type':'UreaN','dose_kg_n_ha':30.,'price_per_kg_n':1.}}})
    preset=decode_policies(config,space,[0,0,0]);model=tmp_path/'source';model.mkdir();(model/'simulation.apsimx').write_text('{}')
    (model/'evaluations.json').write_text('[]');sentinel=model/'simulation.apsimx'
    alternatives=[{'rank':i+1,'sequence_ids':[s],'transition_codes':['x'],'mean_score':1.} for i,s in enumerate(['spring_wheat','winter_rye'])]
    ctx={'config':{'model_root':str(model),'optimization_run':str(model),'historical_weather_end':'2023-12-31',
        'climate_analogue_years':{},'optimization':{'search_scope':'joint'},'nsga2':{'population':4,'generations':2,'seed':42}},
        'planning':{'initial_states':{'Field1':'spring_wheat'},'start_year':2025,'years':1,
            'farm_crop_mix':{'categories':{'grain':{'states':['spring_wheat','winter_rye'],'annual_min':0,'annual_max':1}}}},
        'fields':['Field1'],'pool':{'fields':{'Field1':{'alternatives':alternatives}}},
        'presets':{'yield':{'candidate_id':'anchor','policies':preset}},'native_config':{**config,'evaluation_years':[2025],
        'economics':{'native_harvest_price_per_kg_dm':{'Wheat':1.},'other_cost_per_ha_year':1.,'application_cost_per_ha':1.}},
        'excluded_states':[],'config_path':sentinel,'planning_path':sentinel,'rules_path':sentinel}
    calls=[]
    def build(ctx,selected,policies):return {'map':selected['Field1']['sequence_ids'][0],'policies':policies},{}
    def weather(source,destination,*args):destination.write_text('weather');return {}
    def simulate(document,runtime,unused,work):
        calls.append(document);work.mkdir(parents=True);m.save(work/'candidate.apsimx',document)
        dose=document['policies']['spring_wheat']['fertilizer']['dose_kg_n_ha'] if document['policies'] else 0
        mass=1000+dose*10+(2000 if document['map']=='winter_rye' else 0)
        return SimulationResult('apsim',(FieldYearResult('Field1',2025,1.,{'Wheat':mass},{'mineral_n_end_kg_ha':10.}),)),{
            'applications':[],'humic':{'Field1':{'start_kg_ha':100.,'end_kg_ha':101.}}}
    monkeypatch.setattr(m,'build_model',build);monkeypatch.setattr(m,'simulate',simulate)
    monkeypatch.setattr(m,'write_analogue_weather',weather);monkeypatch.setattr(m,'native_prepare',lambda doc,*args:(doc,[]))
    for name in ('artifacts','report'):monkeypatch.setattr(m,name,lambda *args:None)
    def search(options,evaluate,*args):
        assert len(options)==4 # One map index and three management indices.
        first=evaluate([0,0,0,0]);second=evaluate([0,0,0,1]);third=evaluate([1,0,0,0])
        assert first['candidate_id']!=second['candidate_id'] and first['F']!=second['F']
        assert evaluate([0,0,0,1])['candidate_id']==second['candidate_id']
    monkeypatch.setattr(m,'search_integer',search)
    output=m.execute(ctx,['yield'],tmp_path/'output',run_search=True)
    assert len(calls)==4 # Reference, seed, changed fertilizer on same map, changed map.
    chosen=m.read(output/'selected_result.json')
    assert chosen['crop_sequences']['Field1']==['winter_rye']
    assert chosen['policies']==preset and m.read(output/'decision_space.json')['total_variables']==4
