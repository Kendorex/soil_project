"""Models are declarative inputs; preparing a run must not mutate them."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def hashes():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT/'models').rglob('*') if p.is_file()}


def test_models_contain_no_code_graphs_databases_or_runtime_artifacts():
    forbidden={'.py','.pyc','.ps1','.cmd','.bat','.exe','.dll','.db','.db-wal','.db-shm',
               '.png','.svg','.html','.log','.zip','.gz'}
    unexpected=[str(p.relative_to(ROOT)) for p in (ROOT/'models').rglob('*')
                if p.is_file() and p.suffix.lower() in forbidden]
    assert unexpected==[]
    assert {p.name for p in (ROOT/'models/apsim').iterdir()}=={'configs','data','simulation'}
    assert {p.name for p in (ROOT/'models/aquacrop').iterdir()}=={
        'configs','DATA','fields','management','generated','project.json'}


def test_aquacrop_inputs_include_native_files_and_management_without_outputs():
    model=ROOT/'models/aquacrop'
    project=json.loads((model/'project.json').read_text(encoding='utf-8'))
    assert (model/project['source_project']).is_file()
    assert (model/'DATA/Simulation.PPn').is_file()
    assert len(project['fields'])==10
    for path in project['fields']:
        field=json.loads((model/path).read_text(encoding='utf-8'))
        schedule=json.loads((model/'management'/f"{field['id']}.json").read_text(encoding='utf-8'))
        assert schedule['events'] and len(schedule['harvests'])==5
        assert all(set(event)=={'Date','Crop','Use'} for event in schedule['harvests'])
        generated=model/'generated'/field['id']
        assert (generated/'LIST/Field.PRM').is_file()
        assert (generated/'PARAM/Field.PPn').is_file()
        assert list((generated/'DATA').glob('*.CRO'))
        assert list((generated/'DATA').glob('*.MAN'))
        for name in ['Soil.SOL','Weather.CLI','Weather.Tnx','Weather.PLU','Weather.ETo','MaunaLoa.CO2']:
            assert (generated/'DATA'/name).is_file()


def test_preparation_uses_only_inputs_and_preserves_native_model_files(tmp_path):
    before=hashes()
    output=tmp_path/'prepared'
    completed=subprocess.run([sys.executable,'-m','agro.aquacrop.cli','--field','fixed_field_01',
                              '--prepare-only','--output',str(output)],cwd=ROOT,
                              capture_output=True,text=True)
    assert completed.returncode==0,completed.stdout+completed.stderr
    assert hashes()==before
    source=ROOT/'models/aquacrop/generated/fixed_field_01'
    prepared=output/'generated/fixed_field_01'
    for path in source.rglob('*'):
        if path.is_file() and path.suffix.lower()!='.json':
            assert path.read_bytes()==(prepared/path.relative_to(source)).read_bytes()
