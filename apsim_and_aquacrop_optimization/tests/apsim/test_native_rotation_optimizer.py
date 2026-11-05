"""Scientific boundaries for native-history management optimization."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agro.apsim.native_rotation_optimizer import prepare, score, load_config
from agro.apsim.multiyear_rotation_optimizer import decode_vector, variable_spec
from agro.adapters.rotation_management import walk, manager, parameters
from agro.contracts import FieldYearResult, SimulationResult

ROOT=Path(__file__).resolve().parents[2]
CONFIG=ROOT/'models/apsim/configs/multiyear_rotation_native_2020_2023.json'


def test_native_patch_preserves_history_dates_and_mixture_components():
    config=load_config(CONFIG)
    source=json.loads((Path(config['model_root'])/'simulation.apsimx').read_text(encoding='utf-8'))
    original=deepcopy(source)
    policies=decode_vector(config,[0]*len(variable_spec(config)))
    policies['spring_wheat'].update(cultivar='Zlata',population=500.,depth=50.,row_spacing=125.)
    patched,changes=prepare(source,config,policies)
    assert source==original
    assert changes and all(c['year'] in config['evaluation_years'] for c in changes)
    before={n['Name']:n for n in walk(source) if n.get('$type')=='Models.Core.Zone, Models'}
    for zone in [n for n in walk(patched) if n.get('$type')=='Models.Core.Zone, Models']:
        rp=parameters(manager(zone,'RotationManager'))
        old=parameters(manager(before[zone['Name']],'RotationManager'))
        for key in ['RotationCrops','RotationUses','RotationSowingDates','RotationHarvestDates','PastureAges']:
            assert rp[key]==old[key]
        for key in ['RotationCultivars','RotationPopulations','RotationSowingDepths','RotationRowSpacings']:
            assert [v.strip() for v in rp[key].split(',')][:16]==[v.strip() for v in old[key].split(',')][:16]
        for key in ['RotationCultivars','RotationPopulations','RotationSowingDepths','RotationRowSpacings']:
            for i,crops in enumerate(old['RotationCrops'].split(',')):
                for component,crop in enumerate(crops.strip().split('+')):
                    if crop in ['RedClover','PlantainForage','Mustard','Rape']:
                        assert rp[key].split(',')[i].strip().split(';')[component]==old[key].split(',')[i].strip().split(';')[component]
        assert any(c['Name']==f'OptSoil_{zone["Name"]}' for c in zone['Children'])
        assert any(c['Name'].startswith('HarvestReport_') for c in zone['Children'])
        assert not any(c['Name'].startswith('PhenologyReport_') for c in zone['Children'])


def test_score_uses_dry_mass_area_actual_applications_and_own_humic_stock():
    config=load_config(CONFIG)
    baseline=SimulationResult('apsim',(FieldYearResult('Field1',2020,2.,{'Wheat':1000.},
                               {'mineral_n_end_kg_ha':100.}),))
    candidate=SimulationResult('apsim',(FieldYearResult('Field1',2020,2.,{'Wheat':1200.},
                               {'mineral_n_end_kg_ha':80.}),))
    audit={'applications':[{'field':'Field1','year':2020,'dose_kg_n_ha':30.,'form':'NO3N'}],
           'humic':{'Field1':{'start_kg_ha':100.,'end_kg_ha':99.}}}
    row=score(candidate,audit,baseline,{},config)
    assert row['yield_dm_kg']==2400.
    assert row['net_profit_proxy']==pytest.approx((1200.*14.3-30.*323.53-480.-23000.)*2.)
    assert row['mineral_n_shortfall']==pytest.approx(.2)
    assert row['F']==[-row['yield_dm_kg'],-row['net_profit_proxy'],row['mineral_n_shortfall']]
    assert row['G']==[.01]
    assert not row['feasible']


def test_humic_gain_on_other_fields_cannot_compensate_for_a_loss():
    config=load_config(CONFIG)
    result=SimulationResult('apsim',(FieldYearResult('Field1',2020,1.,{'Wheat':1.},
                              {'mineral_n_end_kg_ha':1.}),))
    audit={'applications':[], 'humic':{'Field1':{'start_kg_ha':100.,'end_kg_ha':90.},
                                     'Field2':{'start_kg_ha':100.,'end_kg_ha':500.}}}
    row=score(result,audit,result,{},config)
    assert row['G']==[.1,-4.]
    assert not row['feasible']


def test_existing_workflow_dispatches_new_config_without_legacy_replay(monkeypatch):
    from agro.apsim.workflows import run_multiyear_rotation_optimizer as workflow
    from agro.apsim import native_rotation_optimizer
    calls=[]
    monkeypatch.setattr(native_rotation_optimizer,'run',lambda path:calls.append(path) or 0)
    monkeypatch.setattr(workflow,'load_config',lambda path:pytest.fail('Legacy replay was selected'))
    assert workflow.main(['--run','--config',str(CONFIG)])==0
    assert calls==[CONFIG.resolve()]
