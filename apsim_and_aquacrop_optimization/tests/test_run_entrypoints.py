import pytest
import run as entry


@pytest.mark.parametrize('model,engine,config',[
    ('apsim','apsim','future_rotation_2025_2029.json'),
    ('coupled','aquacrop','future_rotation_aquacrop_2025_2029.json')])
def test_public_command_routes_to_correct_model_without_starting_a_search(monkeypatch,model,engine,config):
    from agro.planning import future_scenarios
    calls=[]
    monkeypatch.setattr(future_scenarios,'main',lambda argv:calls.append(argv) or 0)
    assert entry.main([model,'--prepare-only','--output','runs/test'])==0
    argv=calls[0]
    assert argv[argv.index('--engine')+1]==engine
    assert argv[argv.index('--config')+1].endswith(config)
    assert '--prepare-only' in argv and '--run' not in argv


def test_public_standalone_command_does_not_call_apsim(monkeypatch):
    from agro.aquacrop import standalone
    from agro.planning import future_scenarios
    calls=[]
    monkeypatch.setattr(standalone,'main',lambda argv:calls.append(argv) or 0)
    def forbidden(*args,**kwargs):raise AssertionError('Standalone command dispatched to APSIM')
    monkeypatch.setattr(future_scenarios,'main',forbidden)
    assert entry.main(['aquacrop','--prepare-only'])==0
    assert calls==[['--prepare-only']]
