"""Scientific boundaries of source patching and explicit management transfer."""
from copy import deepcopy
import json
import datetime as dt
from pathlib import Path
import sqlite3

import pytest

from agro.adapters.rotation_management import apply_policies, walk, manager, parameters, read_outputs
from agro.adapters.transferred_response import apply_response
from agro.adapters.aquacrop import AquaCropBackend
from agro.contracts import FieldYearResult, SimulationResult

ROOT=Path(__file__).resolve().parents[1]


def result(engine, mass, area=1):
    return SimulationResult(engine,(FieldYearResult('Field1',2020,area,{'Wheat':mass},
                             {'mineral_n_end_kg_ha':10.}),))


def test_transfer_is_explicit_identity_at_reference_and_preserves_units():
    native=result('aquacrop',3000.)
    reference=result('apsim',2000.)
    same=apply_response(native,reference,reference,'reference')
    assert sum(same.fields[0].harvested_dm_kg_ha.values())==3000.
    adjusted=apply_response(native,reference,result('apsim',2500.),'trial')
    assert sum(adjusted.fields[0].harvested_dm_kg_ha.values())==3500.
    assert adjusted.engine=='aquacrop_with_apsim_management_response'
    assert adjusted.provenance['soil_metrics_source']=='apsim'


def test_negative_transfer_is_clipped_and_audited():
    adjusted=apply_response(result('aquacrop',100.),result('apsim',2000.),result('apsim',0.),'trial')
    assert sum(adjusted.fields[0].harvested_dm_kg_ha.values())==0.
    assert adjusted.provenance['clipped_negative_predictions'][0]['unclipped_yield_kg_ha']==-1900.


def test_transfer_rejects_mismatched_area():
    with pytest.raises(ValueError,match='areas'):
        apply_response(result('aquacrop',100.,2),result('apsim',100.),result('apsim',100.),'trial')


def test_native_aquacrop_cannot_silently_apply_fertilizer_or_depth():
    with pytest.raises(ValueError,match='only population_m2'):
        AquaCropBackend(ROOT/'models/aquacrop',['engine'],[2020],
                        rotation_populations={'spring_wheat':{'depth_mm':40}})


def test_source_rotation_patch_preserves_source_and_2019():
    source=json.loads((ROOT/'models/aquacrop/configs/rotation_fixed.apsimx').read_text(encoding='utf-8-sig'))
    original=deepcopy(source)
    patched,changes=apply_policies(source,{'spring_wheat':{
        'population_m2':500.,'depth_mm':60.,'dose_kg_n_ha':90.,'form':'UreaN'}},[2020,2021,2022,2023])
    assert source==original
    assert changes and all(c['year']>=2020 and c['policy']=='spring_wheat' for c in changes)
    before={n['Name']:n for n in walk(source) if n.get('$type')=='Models.Core.Zone, Models'}
    for zone in [n for n in walk(patched) if n.get('$type')=='Models.Core.Zone, Models']:
        rp=parameters(manager(zone,'RotationManager'))
        original_rp=parameters(manager(before[zone['Name']],'RotationManager'))
        for key in ('RotationCrops','RotationCultivars','RotationSowingDates','RotationHarvestDates'):
            assert rp[key]==original_rp[key]
        for key in ('RotationPopulations','RotationSowingDepths'):
            assert rp[key].split(',')[0].strip()==original_rp[key].split(',')[0].strip()


@pytest.mark.parametrize('scale',[1,10])
def test_humic_report_units_are_verified_and_normalized(tmp_path,scale):
    database=tmp_path/'candidate.db'
    expression='sum([Nutrient].Humic.C)'+(' * 10' if scale==10 else '')+' as HumicCKgHa'
    database.with_suffix('.apsimx').write_text(json.dumps({
        'Name':'ManagementSoil','VariableNames':[expression]}),encoding='utf-8')
    source={'Children':[
        {'$type':'Models.Clock, Models','Start':'2019-01-01'},
        {'$type':'Models.Core.Zone, Models','Name':'Field1','Area':1,'Children':[
            {'Name':'RotationManager','Parameters':[{'Key':'RotationHarvestDates','Value':'31-Aug, 31-Aug'}]}]}]}
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE _Messages (MessageType INTEGER)')
        conn.execute('CREATE TABLE HarvestReport (Zone TEXT, Date TEXT, Year INTEGER, Crop TEXT, YieldDMKgHa REAL)')
        conn.execute("INSERT INTO HarvestReport VALUES ('Field1','2020-08-31',2020,'Wheat',1000)")
        conn.execute('CREATE TABLE ManagementSoil (Zone TEXT, Date TEXT, MineralN REAL, HumicCKgHa REAL)')
        start=dt.date(2019,1,1)
        days=(dt.date(2021,1,1)-start).days
        conn.executemany('INSERT INTO ManagementSoil VALUES (?,?,?,?)',
            [('Field1',(start+dt.timedelta(days=i)).isoformat(),20.,35000.*scale) for i in range(days)])
    result,humic=read_outputs(database,source,[2020])
    assert result.fields[0].metrics['mineral_n_end_kg_ha']==20.
    assert humic['Field1']=={'start_kg_ha':35000.,'end_kg_ha':35000.}
