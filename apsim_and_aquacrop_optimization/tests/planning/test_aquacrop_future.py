"""New candidate provenance, native harvests and external soil-metric boundaries."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sqlite3

import pytest
import yaml

from agro.adapters.rotation_management import walk
from agro.contracts import FieldYearResult, SimulationResult
from agro.planning.aquacrop_future import combine, prepare_model, soil_water
from agro.planning.future_model import extend_model, source_parts

ROOT = Path(__file__).resolve().parents[2]


def candidate():
    source = json.loads((ROOT/'models/apsim/simulation/calibrated_history/simulation.apsimx').read_text(encoding='utf-8'))
    planning = yaml.safe_load((ROOT/'models/apsim/configs/rotation_planning_2025_2029.yaml').read_text(encoding='utf-8'))
    sequence = ['oats_with_underseed','perennial_grass_year1','perennial_grass_year2','spring_wheat','winter_rye']
    return extend_model(source,planning['initial_states'],{f:list(sequence) for f in planning['initial_states']},2025)[0]


def test_new_model_uses_new_soil_rotation_weather_and_continuous_bridge(tmp_path,monkeypatch):
    from agro.aquacrop import field_config
    document=candidate();original=deepcopy(document)
    _,zones=source_parts(document)
    water={field:[.25]*len(next(n for n in walk(zone) if n.get('$type')=='Models.Soils.Physical, Models')['Thickness'])
           for field,zone in zones.items()}
    settings={'model_root':str(ROOT/'models/aquacrop')}
    model=tmp_path/'model';weather=ROOT/'models/apsim/simulation/calibrated_history/weather.met'
    manifest=prepare_model(document,weather,settings,model,2024,water)
    assert document==original
    assert manifest['initial_water_ready'] and len(manifest['field_mapping'])==10
    assert (model/'configs/weather.met').read_bytes()==weather.read_bytes()
    assert not (model/'configs/rotation_fixed.apsimx').exists()
    assert not list((model/'management').iterdir()) # no stale historical event schedule
    trimmed=json.loads((model/'configs/rotation_new_calibr.apsimx').read_text(encoding='utf-8'))
    clock,new_zones=source_parts(trimmed)
    assert clock['Start'].startswith('2024-01-01') and clock['End'].startswith('2029-12-31')
    for zone in new_zones.values():
        assert next(n for n in walk(zone) if n.get('$type')=='Models.Soils.Water, Models')['InitialValues']==water[zone['Name']]
    # Build crop windows directly, without requiring observed future events.
    monkeypatch.setattr(field_config,'ROOT',model)
    from agro.aquacrop.planned_rotation import planned_rotation
    catalog=field_config.create_catalog()
    simulation=next(n for n in walk(trimmed) if n.get('$type')=='Models.Core.Simulation, Models')
    for field in field_config.registry():
        start,end,rows,segments=planned_rotation(field,simulation,new_zones[field['source_zone']],catalog)
        assert len(rows)==6 and len({r['year'] for r in rows})==6
        assert segments[0]['simulation_start']==start.isoformat()
        assert segments[-1]['simulation_end']==end.isoformat()
        for a,b in zip(segments,segments[1:]):
            assert dt.date.fromisoformat(a['simulation_end'])+dt.timedelta(days=1)==dt.date.fromisoformat(b['simulation_start'])
            assert a['crop_end']<b['crop_start']
        assert len([s for s in segments if s['harvest_dates']])==6


def test_external_soil_metrics_never_replace_or_correct_native_aquacrop_harvest():
    aqua=SimulationResult('aquacrop',(FieldYearResult('fixed_field_01',2025,2.,{'Wheat':100.}),))
    apsim=SimulationResult('apsim',(FieldYearResult('Field1',2025,2.,{'Wheat':999.}, {'mineral_n_end_kg_ha':7.}),))
    result=combine(aqua,apsim,{'fixed_field_01':'Field1'})
    assert result.engine=='aquacrop_with_apsim_soil'
    assert result.fields[0].harvested_dm_kg_ha=={'Wheat':100.}
    assert result.fields[0].metrics['mineral_n_end_kg_ha']==7.
    with pytest.raises(ValueError,match='coverage'):
        combine(aqua,SimulationResult('apsim',(FieldYearResult('Field1',2026,2.,{'Wheat':1.}),)),{'fixed_field_01':'Field1'})
    with pytest.raises(ValueError,match='area'):
        combine(aqua,SimulationResult('apsim',(FieldYearResult('Field1',2025,1.,{'Wheat':1.}),)),{'fixed_field_01':'Field1'})


def test_water_state_comes_from_exact_historical_end_date(tmp_path):
    document={'Children':[{'$type':'Models.Clock, Models'},{'Name':'Field1','$type':'Models.Core.Zone, Models','Children':[
        {'$type':'Models.Soils.Physical, Models','Thickness':[100,200]}]}]}
    database=tmp_path/'state.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE OptSoil_Field1 (Date TEXT, "SoilWater(1)" REAL, "SoilWater(2)" REAL)')
        connection.executemany('INSERT INTO OptSoil_Field1 VALUES (?,?,?)',[
            ('2023-12-30 00:00:00',.1,.2),('2023-12-31 00:00:00',.25,.35)])
    assert soil_water(database,document,2024)=={'Field1':[.25,.35]}
    with pytest.raises(ValueError,match='Missing APSIM initial water'):soil_water(database,document,2025)


def test_soil_water_report_is_opt_in_and_preserves_source():
    from agro.apsim.native_rotation_optimizer import prepare
    document=candidate();original=deepcopy(document)
    config={'evaluation_years':list(range(2025,2030))}
    plain,_=prepare(document,config,None)
    instrumented,_=prepare(document,{**config,'report_soil_water':True},None)
    variables=lambda doc:[n['VariableNames'] for n in walk(doc) if n.get('Name','').startswith('OptSoil_')]
    assert all('[Swim3].SW as SoilWater' not in row for row in variables(plain))
    assert all('[Swim3].SW as SoilWater' in row for row in variables(instrumented))
    assert document==original


def test_missing_aquacrop_executable_fails_before_apsim_or_output_creation(tmp_path,monkeypatch):
    from agro.planning import future_scenarios
    def forbidden(*args):raise AssertionError('APSIM started for an unavailable AquaCrop executable')
    monkeypatch.setattr(future_scenarios,'simulate',forbidden)
    ctx={'config':{'engine':'aquacrop','aquacrop':{'command':[str(tmp_path/'missing.exe')]}},'planning':{}}
    with pytest.raises(FileNotFoundError,match='AquaCrop executable'):
        future_scenarios.execute(ctx,'yield',tmp_path/'result',run_search=True)
    assert not (tmp_path/'result').exists()
