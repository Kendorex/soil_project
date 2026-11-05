"""Verify exported management preservation and complete weather coverage."""
import hashlib
import json
import sqlite3

import pytest

from agro.apsim import native_simulation


def inputs(tmp_path):
    root = tmp_path / 'inputs'
    root.mkdir()
    document = {'$type': 'Models.Core.Simulation, Models', 'Name': 'History', 'Children': [
        {'$type': 'Models.Clock, Models', 'Start': '2020-01-01T00:00:00',
         'End': '2020-01-02T00:00:00'},
        {'$type': 'Models.Climate.Weather, Models', 'FileName': 'weather.met'},
        {'$type': 'Models.Core.Zone, Models', 'Name': 'Field1', 'Area': 1},
        {'$type': 'Models.Storage.DataStore, Models', 'CustomFileName': 'simulation_results.db'}]}
    (root / 'simulation.apsimx').write_text(json.dumps(document), encoding='utf-8')
    (root / 'weather.met').write_text('year day radn maxt mint rain\n'
                                    '() () (MJ/m2/d) (oC) (oC) (mm)\n'
                                    '2020 1 10 5 0 0\n2020 2 10 5 0 0\n')
    (root / 'provenance.json').write_text('{}')
    return {'model_root': str(root), 'command': ['Models']}


def test_native_run_preserves_inputs_and_records_reports(tmp_path, monkeypatch):
    config = inputs(tmp_path)
    def engine(config, model, database_path=None):
        database = database_path or model.with_suffix('.db')
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE Harvest (Yield REAL)')
            connection.execute('INSERT INTO Harvest VALUES (2)')
            connection.execute('CREATE TABLE _Messages (MessageType INTEGER, Message TEXT)')
            connection.execute("INSERT INTO _Messages VALUES (2, 'Crop sown')")
        return database
    monkeypatch.setattr(native_simulation, 'run_apsim', engine)
    output = tmp_path / 'run'
    native_simulation.run(config, output, 'config.json')
    original = (tmp_path / 'inputs/simulation.apsimx').read_bytes()
    assert (output / 'simulation.apsimx').read_bytes() == original
    execution = json.loads((output / 'execution.json').read_text())
    assert execution['status'] == 'completed'
    assert execution['input_sha256']['simulation.apsimx'] == hashlib.sha256(original).hexdigest()
    assert json.loads((output / 'simulation.json').read_text())['report_rows'] == {'Harvest': 1}
    assert (output / 'simulation_results.db').is_file()


def test_missing_weather_day_is_rejected_before_launch(tmp_path):
    config = inputs(tmp_path)
    weather = tmp_path / 'inputs/weather.met'
    weather.write_text(weather.read_text().replace('2020 2 10 5 0 0\n', ''))
    with pytest.raises(ValueError, match='every simulation day'):
        native_simulation.run(config, tmp_path / 'run', 'config.json')
    assert not (tmp_path / 'run').exists()


def test_operational_area_override_is_executed_and_source_hash_retained(tmp_path,monkeypatch):
    config=inputs(tmp_path);config['field_area_ha']=.1
    original=(tmp_path/'inputs/simulation.apsimx').read_bytes()
    def engine(cfg,model,database_path=None):
        document=json.loads(model.read_text())
        zone=next(n for n in document['Children'] if n.get('Name')=='Field1')
        assert zone['Area']==.1
        with sqlite3.connect(database_path) as con:
            con.execute('CREATE TABLE Harvest (Yield REAL)');con.execute('INSERT INTO Harvest VALUES (2)')
        return database_path
    monkeypatch.setattr(native_simulation,'run_apsim',engine)
    native_simulation.run(config,tmp_path/'run','config.json')
    assert (tmp_path/'inputs/simulation.apsimx').read_bytes()==original
    execution=json.loads((tmp_path/'run/execution.json').read_text())
    assert execution['area_changes']==[{'field':'Field1','before_ha':1,'after_ha':.1}]
    assert execution['executed_model_sha256']!=execution['input_sha256']['simulation.apsimx']


def test_engine_failure_is_recorded(tmp_path, monkeypatch):
    config = inputs(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('engine failed')
    monkeypatch.setattr(native_simulation, 'run_apsim', fail)
    with pytest.raises(RuntimeError, match='engine failed'):
        native_simulation.run(config, tmp_path / 'run', 'config.json')
    execution = json.loads((tmp_path / 'run/execution.json').read_text())
    assert execution['status'] == 'failed'
