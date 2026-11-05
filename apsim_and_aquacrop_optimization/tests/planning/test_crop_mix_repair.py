from copy import deepcopy
import json
from pathlib import Path
import numpy as np
import pytest

from agro.planning.crop_mix_repair import CropMixRepair
from agro.planning.future_scenarios import crop_mix


def context():
    return {'fields':['Field1','Field2'],'planning':{'years':1,'start_year':2025,
        'farm_crop_mix':{'categories':{
            'wheat':{'states':['w'],'annual_min':1,'annual_max':1},
            'oats':{'states':['o'],'annual_min':1,'annual_max':1}}}},
        'pool':{'fields':{f:{'alternatives':[{'sequence_ids':['w']},{'sequence_ids':['o']}]} for f in ['Field1','Field2']}}}


def test_repair_preserves_feasible_maps_and_all_management_genes():
    ctx=context();before=deepcopy(ctx);repair=CropMixRepair(ctx)
    assert repair([0,1,7,12]).tolist()==[0,1,7,12]
    result=repair([0,0,7,12]);assert result[:2].tolist() in [[0,1],[1,0]]
    assert result[2:].tolist()==[7,12] and ctx==before
    assert np.array_equal(repair([0,0,99,4])[:2],result[:2])
    assert repair.solves==1


def test_infeasible_crop_rules_fail_without_running_simulations():
    ctx=context();ctx['planning']['farm_crop_mix']['categories']['wheat']['annual_min']=3
    with pytest.raises(ValueError,match='No crop-count-compliant'):CropMixRepair(ctx)([0,0])


def test_real_legal_sequences_are_repaired_without_relaxing_crop_bounds():
    import yaml
    root=Path(__file__).resolve().parents[2]
    from agro.planning.rotation_planner import RotationRules,plan_fields_stateful
    from agro.planning.future_model import SUPPORTED_STATES
    planning_path=root/'models/apsim/configs/rotation_planning_2025_2029.yaml'
    planning=yaml.safe_load(planning_path.read_text(encoding='utf-8'))
    state_model=deepcopy(planning['state_model'])
    state_model['candidate_states']=[s for s in state_model['candidate_states'] if s in SUPPORTED_STATES]
    rules=RotationRules.from_file((planning_path.parent/planning['rotation_rules']).resolve())
    pool=plan_fields_stateful(rules,planning['initial_states'],state_model,planning['years'],top_k=20000,minimum_score=.5)
    seq={f:v['alternatives'] for f,v in pool['fields'].items()}
    ctx={'fields':list(seq),'planning':planning,'pool':pool}
    repair=CropMixRepair(ctx);rng=np.random.default_rng(123)
    outputs=[]
    for _ in range(25):
        vector=[int(rng.integers(len(seq[f]))) for f in ctx['fields']]+[17,9]
        result=repair(vector);selected={f:seq[f][result[i]] for i,f in enumerate(ctx['fields'])}
        assert all(g<=0 for g in crop_mix(ctx,selected)[0])
        assert result[-2:].tolist()==[17,9]
        outputs.append(tuple(result[:10]))
    assert len(set(outputs))>5 # Repair must not replace every request with the seed.


def test_integer_search_repairs_population_before_simulation():
    from agro.optimization.nsga2 import search_integer
    repair=CropMixRepair(context());seen=[]
    def evaluate(x):
        assert repair.valid(x[:2]);seen.append(tuple(x))
        return {'F':[float(sum(x))],'G':[]}
    search_integer([[0,1],[0,1],list(range(10))],evaluate,
                   {'algorithm':'nsga2','population':6,'generations':2,'seed':42,'verbose':False},1,0,decision_repair=repair)
    assert len(seen)>6 and repair.changed>0
