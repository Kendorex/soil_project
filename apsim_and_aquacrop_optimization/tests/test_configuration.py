import json
from pathlib import Path
from agro.configuration import resolved_config


def test_resolved_snapshot_can_be_moved_without_changing_paths(tmp_path,monkeypatch):
    monkeypatch.delenv('APSIM_MODELS',raising=False)
    source=tmp_path/'profiles/source.json';source.parent.mkdir()
    source.write_text(json.dumps({'engine':'aquacrop','model_root':'../model','command':['../engine/runner.exe']}))
    config=resolved_config(source);copy=tmp_path/'other/config.json';copy.parent.mkdir()
    copy.write_text(json.dumps(config))
    assert resolved_config(copy)==config
    assert config['model_root']==str(tmp_path/'model')


def test_apsim_environment_override_is_explicit(tmp_path,monkeypatch):
    path=tmp_path/'config.json';path.write_text(json.dumps({'engine':'apsim','command':['configured.exe']}))
    monkeypatch.setenv('APSIM_MODELS','override.exe')
    assert resolved_config(path)['command']==['configured.exe']
    assert resolved_config(path,prefer_environment=True)['command']==['override.exe']
