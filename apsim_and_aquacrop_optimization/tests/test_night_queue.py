"""Sequential queue, frozen paths, failure continuation and restart boundaries."""
from copy import deepcopy
import json


def test_extended_apsim_queue_uses_distinct_frozen_seeds():
    from agro import night_queue as module
    path=module.ROOT/'launchers/configs/apsim_extended.json'
    manifest=module.plan(path)
    assert [j['settings']['seed'] for j in manifest['jobs']]==[42,123,2026]
    for job in manifest['jobs']:
        assert job['engine']=='apsim' and job['settings']['algorithm']=='nsga2'
        assert job['snapshot']['nsga2']['seed']==job['settings']['seed']
        assert job['snapshot']['optimization']['settings']['seed']==job['settings']['seed']
        assert job['snapshot']['field_area_ha']==.1
        assert job['settings']['population']*job['settings']['generations']==200
    assert [j['settings']['seed'] for j in module.plan(path,seed=9)['jobs']]==[9,9,9]


def test_report_defaults_stay_in_runs_and_do_not_create_windows_helpers(tmp_path,monkeypatch):
    from agro import night_queue as module
    manifest=make_manifest(tmp_path,1);folder=tmp_path/'runs/batch';folder.mkdir(parents=True)
    monkeypatch.setattr(module,'ROOT',tmp_path)
    module.publish(folder,manifest)
    assert (folder/'index.html').is_file()
    assert (tmp_path/'runs/night_optimization/latest.json').is_file()
    assert not (tmp_path/'for_me').exists()
    assert not list(tmp_path.rglob('*.cmd'))
from pathlib import Path
import os
import sys

import pytest

from agro import night_queue as module


def make_manifest(tmp_path,count=3):
    settings={'population':10,'generations':4,'seed':42,'algorithm':'nsga2'}
    return {'schema_version':1,'workspace':str(module.ROOT),'population':10,'generations':4,'seed':42,
            'stage':'prepared','jobs':[{'id':f'job_{i}','engine':'aquacrop_standalone','objectives':['yield','profit'],
                'snapshot':{},'snapshot_path':str(tmp_path/f'config_{i}.json'),'settings':dict(settings),
                'stage':'pending','attempts':[]} for i in range(count)]}


def finished(argv,status='completed'):
    output=Path(argv[argv.index('--output')+1]);output.mkdir(parents=True,exist_ok=False)
    module.save(output/'optimization_summary.json',{'status':status,'evaluated':40,'successful':40,
        'feasible':1 if status=='completed' else 0,'selected_candidate':'candidate_001'})
    module.save(output/'selected_result.json',{'yield_dm_kg':123.,'net_profit_proxy':-5.})


def test_six_jobs_have_explicit_algorithms_criteria_and_frozen_absolute_paths():
    plan=module.plan(module.DEFAULT)
    assert len(plan['jobs'])==6
    assert [j['engine'] for j in plan['jobs']]==['apsim','apsim','aquacrop','aquacrop','aquacrop_standalone','aquacrop_standalone']
    assert [j['settings']['algorithm'] for j in plan['jobs']]==['nsga2','nsga3']*3
    assert [len(j['objectives']) for j in plan['jobs']]==[3,4,3,4,2,4]
    for job in plan['jobs']:
        assert Path(job['snapshot']['model_root']).is_absolute()
        if job['engine']!='aquacrop_standalone':assert Path(job['snapshot']['planning_config']).is_absolute()
        else:assert Path(job['snapshot']['command'][0]).is_absolute()
        job['snapshot_path']='snapshot.json'
        command=module.command(job,'new_output')
        assert '--run' in command and '--simulate-only' not in command
        assert command[command.index('--algorithm')+1]==job['settings']['algorithm']
        assert command[command.index('--population')+1]=='10'
        if job['settings']['algorithm']=='nsga3':assert command[-2:]==['--reference-partitions','2']


def test_failure_continues_and_resume_skips_finished_jobs_without_overwriting(tmp_path):
    folder=tmp_path/'batch';folder.mkdir();manifest=make_manifest(tmp_path)
    publish=lambda f,m:module.publish(f,m,tmp_path/'hub')
    events=[]
    def runner(argv,log,heartbeat):
        index=len(events);events.append(argv);heartbeat(12345)
        Path(log).write_text('test log',encoding='utf-8')
        if index==1:return 1
        finished(argv,'no_feasible_solution' if index==0 else 'completed');return 0
    assert module.run_batch(folder,manifest,prevent_sleep=False,runner=runner,publisher=publish)==1
    assert [j['stage'] for j in manifest['jobs']]==['no_feasible_solution','failed','completed']
    assert len(events)==3
    preserved=Path(manifest['jobs'][0]['attempts'][0]['output'])/'selected_result.json'
    original=preserved.read_bytes()
    def retry(argv,log,heartbeat):
        events.append(argv);finished(argv);return 0
    assert module.run_batch(folder,manifest,prevent_sleep=False,runner=retry,publisher=publish)==0
    assert len(events)==4 and all(j['stage'] in module.DONE for j in manifest['jobs'])
    assert len(manifest['jobs'][1]['attempts'])==2
    assert Path(manifest['jobs'][1]['attempts'][1]['output']).name=='attempt_002'
    assert preserved.read_bytes()==original
    assert (folder/'results.csv').exists()


def test_interrupt_stops_queue_and_preserves_pending_jobs(tmp_path):
    manifest=make_manifest(tmp_path);folder=tmp_path/'batch';folder.mkdir()
    def runner(*args):raise KeyboardInterrupt
    assert module.run_batch(folder,manifest,prevent_sleep=False,runner=runner,
                            publisher=lambda f,m:module.save(f/'status.json',m))==130
    assert manifest['stage']=='interrupted'
    assert [j['stage'] for j in manifest['jobs']]==['interrupted','pending','pending']


def test_child_exit_zero_requires_completed_optimization_artifacts(tmp_path):
    manifest=make_manifest(tmp_path,1);folder=tmp_path/'batch';folder.mkdir()
    assert module.run_batch(folder,manifest,prevent_sleep=False,runner=lambda *args:0,
                            publisher=lambda f,m:module.save(f/'status.json',m))==1
    assert manifest['jobs'][0]['stage']=='failed'


def test_resume_rejects_active_orphan_and_recovers_finished_artifacts(tmp_path,monkeypatch):
    manifest=make_manifest(tmp_path,1);job=manifest['jobs'][0]
    output=tmp_path/'child';job.update(stage='running',attempts=[{'pid':12345,'output':str(output)}])
    monkeypatch.setattr(module,'process_alive',lambda pid:True)
    with pytest.raises(RuntimeError,match='still active'):module.recover_interrupted(manifest)
    finished(['--output',str(output)])
    monkeypatch.setattr(module,'process_alive',lambda pid:False)
    module.recover_interrupted(manifest)
    assert job['stage']=='completed' and job['summary']['evaluated']==40


def test_os_lock_prevents_overlapping_queues_and_releases_after_exception(tmp_path):
    with pytest.raises(RuntimeError,match='test failure'):
        with module.queue_lock(tmp_path):
            with pytest.raises(RuntimeError,match='already active'):
                with module.queue_lock(tmp_path):pass
            raise RuntimeError('test failure')
    with module.queue_lock(tmp_path):pass


def test_run_child_captures_output_and_waits_for_process(tmp_path):
    marker=tmp_path/'finished.txt';log=tmp_path/'console.log';heartbeats=[]
    code=module.run_child([sys.executable,'-u','-c',
        'from pathlib import Path; print("line one"); Path('+repr(str(marker))+').write_text("done")'],log,heartbeats.append)
    assert code==0 and marker.read_text()=='done'
    assert 'line one' in log.read_text(encoding='utf-8') and heartbeats


@pytest.mark.skipif(os.name!='nt',reason='Windows process-tree cancellation')
def test_interrupt_terminates_owned_child_process_tree(tmp_path):
    parents=[];log=tmp_path/'cancel.log'
    def heartbeat(pid):
        parents.append(pid)
        if len(parents)>1:raise KeyboardInterrupt
    code='import subprocess,sys,time; child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); print(child.pid,flush=True); time.sleep(60)'
    with pytest.raises(KeyboardInterrupt):module.run_child([sys.executable,'-u','-c',code],log,heartbeat)
    assert not module.process_alive(parents[0])
    assert not module.process_alive(int(log.read_text(encoding='utf-8').strip()))


def test_dry_run_creates_no_batch_and_prepare_only_starts_no_process(tmp_path,monkeypatch):
    output=tmp_path/'batch'
    assert module.main(['--dry-run','--output',str(output)])==0
    assert not output.exists()
    monkeypatch.setattr(module,'preflight',lambda *args:None)
    original=module.publish
    monkeypatch.setattr(module,'publish',lambda f,m:original(f,m,tmp_path/'hub'))
    def forbidden(*args,**kwargs):raise AssertionError('Prepared queue started a simulation')
    monkeypatch.setattr(module.subprocess,'Popen',forbidden)
    assert module.main(['--prepare-only','--output',str(output)])==0
    manifest=module.load_batch(output)
    assert manifest['stage']=='prepared' and len(manifest['jobs'])==6
    assert all(job['stage']=='pending' for job in manifest['jobs'])
    config=Path(manifest['jobs'][0]['snapshot_path']);value=module.read(config);value['nsga2']['seed']=9;module.save(config,value)
    with pytest.raises(ValueError,match='Frozen configuration changed'):module.load_batch(output)
