"""Sequential, resumable local queue for full APSIM/AquaCrop optimization runs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
import ctypes
import datetime as dt
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time

from agro.optimization.nsga2 import integer_search_settings

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'launchers/configs/night_optimization.json'
DONE={'completed','no_feasible_solution'}


def now():return dt.datetime.now().astimezone().isoformat(timespec='seconds')


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def freeze_config(path,engine,seed):
    """Resolve paths against the original location before moving a snapshot."""
    path=Path(path).resolve();config=read(path)
    def absolute(value):return str((path.parent/value).resolve())
    for key in ('model_root','output_root','planning_config','optimization_run','management_reference'):
        if key in config:config[key]=absolute(config[key])
    config['engine']=engine
    if engine=='aquacrop_standalone':
        config['command']=[absolute(config['command'][0]),*config['command'][1:]]
        config['search']['seed']=seed
    else:
        config['nsga2']['seed']=seed
        config.setdefault('optimization',{}).setdefault('settings',{})['seed']=seed
        aqua=config.setdefault('aquacrop',{})
        aqua['model_root']=absolute(aqua['model_root']) if aqua.get('model_root') else str(ROOT/'models/aquacrop')
        command=aqua.get('command',[str(ROOT/'engines/aquacrop/field-target/debug/aquacrop-field-runner.exe')])
        aqua['command']=[absolute(command[0]),*command[1:]]
    return config


def plan(path,population=None,generations=None,seed=None):
    path=Path(path).resolve();config=read(path)
    if config.get('schema_version')!=1 or not config.get('jobs'):raise ValueError('Invalid night queue configuration')
    population=config['population'] if population is None else population
    generations=config['generations'] if generations is None else generations
    seed_overridden=seed is not None
    seed=config['seed'] if seed is None else seed
    if isinstance(seed,bool) or not isinstance(seed,int) or seed<0:raise ValueError('Seed must be a nonnegative integer')
    jobs=[];ids=set()
    for item in config['jobs']:
        identifier=item['id'];engine=item['engine'];objectives=item['objectives']
        if not re.fullmatch(r'[a-z0-9_]+',identifier) or identifier in ids:raise ValueError('Job identifiers must be unique and safe')
        ids.add(identifier)
        if engine not in ('apsim','aquacrop','aquacrop_standalone'):raise ValueError('Unsupported queue engine')
        if not objectives or len(set(objectives))!=len(objectives):raise ValueError('Choose distinct criteria')
        job_seed=seed if seed_overridden else item.get('seed',seed)
        if isinstance(job_seed,bool) or not isinstance(job_seed,int) or job_seed<0:raise ValueError('Job seed must be a nonnegative integer')
        settings=integer_search_settings({'population':population,'generations':generations,'seed':job_seed,'algorithm':item['algorithm']},len(objectives))
        if settings['algorithm']=='nsga2' and len(objectives)>3:raise ValueError('Queue NSGA-II jobs support 1-3 criteria')
        source=(path.parent/item['config']).resolve();snapshot=freeze_config(source,engine,job_seed)
        allowed=({'yield','profit','water_use','water_productivity','drainage','runoff'} if engine=='aquacrop_standalone'
                 else {'yield','profit','mineral_n','humic'}|set(snapshot.get('optimization',{}).get('additional_objectives',{})))
        if set(objectives)-allowed:raise ValueError(f'Unsupported criteria for {engine}: {set(objectives)-allowed}')
        jobs.append({**item,'source_config':str(source),'source_config_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'snapshot':snapshot,'settings':settings,'stage':'pending','attempts':[]})
    return {'schema_version':1,'source_queue_config':str(path),'population':population,'generations':generations,'seed':seed,'jobs':jobs}


def command(job,output):
    if job['engine']=='aquacrop_standalone':
        result=[sys.executable,'-m','agro.aquacrop.standalone']
    else:result=[sys.executable,str(ROOT/'scripts/plan_future_rotation.py'),'--engine',job['engine']]
    result+=['--config',job['snapshot_path'],'--objectives',*job['objectives'],'--algorithm',job['settings']['algorithm'],
             '--population',str(job['settings']['population']),'--generations',str(job['settings']['generations']),
             '--output',str(output),'--run']
    if job['engine']!='aquacrop_standalone':result+=['--management-preset',job.get('management_preset','yield')]
    if job['settings']['algorithm']=='nsga3':result+=['--reference-partitions',str(job['settings']['reference_partitions'])]
    return result


def preflight(manifest):
    missing=[name for name in ('numpy','pymoo','yaml','matplotlib') if importlib.util.find_spec(name) is None]
    if missing:raise RuntimeError('Missing Python dependencies: '+', '.join(missing))
    executables=set()
    for job in manifest['jobs']:
        config=job['snapshot'];model=Path(config['model_root'])
        if not model.is_dir():raise FileNotFoundError(model)
        if job['engine']=='aquacrop_standalone':
            if not (model/'project.json').is_file():raise FileNotFoundError(model/'project.json')
            executables.add(config['command'][0])
        else:
            for key in ('planning_config','management_reference' if config.get('management_reference') else 'optimization_run'):
                if not Path(config[key]).exists():raise FileNotFoundError(config[key])
            for name in ('simulation.apsimx','weather.met'):
                if not (model/name).is_file():raise FileNotFoundError(model/name)
            native=(read(Path(config['management_reference']))['native_config'] if config.get('management_reference')
                    else read(Path(config['optimization_run'])/'config_snapshot.json'))
            runtime=native['runtime']['command'][0]
            executables.add(os.environ.get('APSIM_MODELS',runtime))
            if job['engine']=='aquacrop':executables.add(config['aquacrop']['command'][0])
    for executable in executables:
        if not Path(executable).is_file() and not shutil.which(executable):raise FileNotFoundError('Missing engine: '+executable)


@contextmanager
def queue_lock(root=ROOT):
    """OS-owned lock is released even if the launcher process is killed."""
    path=Path(root)/'runs/_night_queue.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b' ');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:raise RuntimeError('Another overnight queue is already active in this workspace') from exc
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


@contextmanager
def keep_awake(enabled=True):
    active=False
    if enabled and os.name=='nt':
        active=bool(ctypes.windll.kernel32.SetThreadExecutionState(0x80000000|0x00000001))
        if not active:print('Automatic-sleep prevention could not be enabled.',flush=True)
    try:yield
    finally:
        if active:ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


def stop_process_tree(process):
    if process.poll() is not None:return
    if os.name=='nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    else:
        import signal
        os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=15)
    except subprocess.TimeoutExpired:process.kill();process.wait()


def run_child(argv,log_path,heartbeat):
    """Tee output while the main thread remains responsive to stop/heartbeat."""
    environment=os.environ.copy();environment.update(PYTHONUNBUFFERED='1',PYTHONIOENCODING='utf-8',AGRO_NO_PAUSE='1')
    options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
    process=subprocess.Popen(argv,cwd=ROOT,env=environment,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        text=True,encoding='utf-8',errors='replace',bufsize=1,**options)
    lines=queue.Queue()
    def consume():
        try:
            for line in process.stdout:lines.put(line)
        finally:lines.put(None)
    thread=threading.Thread(target=consume,daemon=True);thread.start()
    last_update=0.
    try:
        with Path(log_path).open('w',encoding='utf-8') as log:
            heartbeat(process.pid)
            while True:
                try:line=lines.get(timeout=2)
                except queue.Empty:line=''
                if line is None:break
                if line:print(line,end='',flush=True);log.write(line);log.flush()
                if time.monotonic()-last_update>=10:heartbeat(process.pid);last_update=time.monotonic()
            return process.wait()
    except BaseException:
        stop_process_tree(process);raise
    finally:
        thread.join(timeout=2)
        if process.stdout is not None:process.stdout.close()


def publish(folder,manifest,hub=None):
    folder=Path(folder);manifest['updated_at']=now();save(folder/'status.json',manifest)
    stage_labels={'prepared':'Подготовлено','pending':'Ожидает','running':'Выполняется','completed':'Завершён',
        'no_feasible_solution':'Завершён: допустимых решений нет','failed':'Техническая ошибка','interrupted':'Прерван',
        'completed_with_errors':'Очередь завершена с ошибками'}
    rows=[];csv_rows=[]
    for job in manifest['jobs']:
        attempt=job['attempts'][-1] if job['attempts'] else {};output=Path(attempt['output']) if attempt else None
        summary=job.get('summary',{});selected=job.get('selected',{})
        links=[]
        for path,label in [(Path(attempt['log']) if attempt else None,'Журнал'),
                           (output/'index.html' if output else None,'Результаты'),(output/'status.json' if output else None,'Статус расчёта')]:
            if path and path.exists():links.append(f'<a href="{html.escape(Path(os.path.relpath(path,folder)).as_posix())}">{label}</a>')
        progress=job.get('child_status',{}).get('evaluated_unique_candidates','')
        rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [job['id'],job['settings']['algorithm'],job['settings']['seed'],', '.join(job['objectives']),
            stage_labels.get(job['stage'],job['stage']),summary.get('evaluated',progress),summary.get('feasible','')])+'<td>'+' · '.join(links)+'</td></tr>')
        csv_rows.append({'job':job['id'],'engine':job['engine'],'algorithm':job['settings']['algorithm'],'objectives':'+'.join(job['objectives']),
            'seed':job['settings']['seed'],'field_area_ha':job.get('snapshot',{}).get('field_area_ha',''),
            'stage':job['stage'],'evaluated':summary.get('evaluated',''),'successful':summary.get('successful',''),'feasible':summary.get('feasible',''),
            'yield_dm_kg':selected.get('yield_dm_kg',''),'net_profit_proxy':selected.get('net_profit_proxy',''),
            'selected_candidate':summary.get('selected_candidate',''),'output':str(output) if output else '',
            'duration_seconds':attempt.get('duration_seconds',''),'error':job.get('error','')})
    with (folder/'results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(csv_rows[0]));writer.writeheader();writer.writerows(csv_rows)
    refresh='<meta http-equiv="refresh" content="15">' if manifest['stage']=='running' else ''
    page=f'''<!doctype html><html lang="ru"><meta charset="utf-8">{refresh}<title>Ночная очередь оптимизаций</title>
<style>body{{font:16px/1.6 system-ui;max-width:1450px;margin:30px auto;padding:0 20px;background:#f2f5f7;color:#234}}section{{background:white;padding:22px;border-radius:12px;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:10px;border-bottom:1px solid #ddd;text-align:left}}code{{overflow-wrap:anywhere}}</style>
<h1>Очередь оптимизаций: {len(manifest['jobs'])} запусков</h1><section><b>{stage_labels.get(manifest['stage'],manifest['stage'])}</b><p>Обновлено: {manifest['updated_at']}. Запуски строго последовательные. Бюджет каждого: {manifest['population']} особей × {manifest['generations']} поколений. Алгоритм и seed каждого этапа указаны в таблице.</p>
<p>Цели, площадь полей и правило Humic задаются замороженными конфигами в папке configs. Серия с разными seed проверяет устойчивость поиска. Сравнение разных алгоритмов требует одинаковых целей, переменных и бюджета; наличие их в одной очереди само по себе не делает сравнение контролируемым.</p>
<p>«Допустимых решений нет» — завершённый поиск, а не техническая ошибка. Результат с нарушениями остаётся диагностическим. При технической ошибке очередь по умолчанию продолжает остальные этапы.</p><a href="results.csv">Сводная таблица CSV</a> · <a href="status.json">Общий статус JSON</a></section>
<section style="overflow:auto"><table><tr><th>Этап</th><th>Алгоритм</th><th>Seed</th><th>Цели</th><th>Состояние</th><th>Оценено</th><th>Допустимых</th><th>Просмотр</th></tr>{''.join(rows)}</table></section></html>'''
    (folder/'index.html').write_text(page,encoding='utf-8')
    hub=Path(hub or ROOT/'runs/night_optimization');hub.mkdir(parents=True,exist_ok=True)
    save(hub/'latest.json',{'batch':str(folder)})
    link=Path(os.path.relpath(folder/'index.html',hub)).as_posix()
    (hub/'index.html').write_text(f'<!doctype html><meta charset="utf-8"><h1>Ночная очередь оптимизаций</h1><a href="{html.escape(link)}">Открыть этапы, журналы и результаты</a>',encoding='utf-8')


def create_batch(plan_data,folder):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=False)
    manifest={**plan_data,'workspace':str(ROOT),'stage':'prepared','created_at':now()}
    for job in manifest['jobs']:
        path=folder/'configs'/(job['id']+'.json');save(path,job['snapshot']);job['snapshot_path']=str(path)
    save(folder/'plan.json',manifest);publish(folder,manifest);return manifest


def load_batch(folder):
    folder=Path(folder).resolve();manifest=read(folder/'status.json')
    if Path(manifest['workspace']).resolve()!=ROOT.resolve():raise ValueError('Resume the batch from its original workspace')
    for job in manifest['jobs']:
        if read(job['snapshot_path'])!=job['snapshot']:raise ValueError('Frozen configuration changed: '+job['id'])
    return manifest


def process_alive(pid):
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.restype=ctypes.c_void_p
        kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=kernel.OpenProcess(0x00100000,False,int(pid)) # SYNCHRONIZE access
        if not handle:
            if ctypes.get_last_error()==5:raise RuntimeError(f'Cannot inspect previous child PID {pid}; check it before resuming')
            return False
        try:
            state=kernel.WaitForSingleObject(handle,0)
            if state not in (0,0x102):raise RuntimeError(f'Cannot inspect previous child PID {pid}')
            return state==0x102
        finally:kernel.CloseHandle(handle)
    try:os.kill(int(pid),0);return True
    except ProcessLookupError:return False


def recover_interrupted(manifest):
    """Do not overlap an orphaned child; recover completed artifacts after parent loss."""
    for job in manifest['jobs']:
        if job['stage'] not in ('running','interrupted') or not job['attempts']:continue
        attempt=job['attempts'][-1]
        if attempt.get('pid') and process_alive(attempt['pid']):
            raise RuntimeError(f'Previous child is still active: {job["id"]}, PID {attempt["pid"]}. Stop/wait for that calculation before resuming.')
        output=Path(attempt['output'])
        if (output/'optimization_summary.json').exists() and (output/'selected_result.json').exists():
            summary=read(output/'optimization_summary.json')
            if summary['status'] in DONE:job.update(stage=summary['status'],summary=summary,selected=read(output/'selected_result.json'))


def run_batch(folder,manifest,fail_fast=False,prevent_sleep=True,runner=run_child,publisher=publish):
    folder=Path(folder).resolve();manifest['stage']='running';manifest['started_or_resumed_at']=now();publisher(folder,manifest)
    try:
        with keep_awake(prevent_sleep):
            for job in manifest['jobs']:
                if job['stage'] in DONE:continue
                attempt_number=len(job['attempts'])+1
                output=folder/'outputs'/job['id']/f'attempt_{attempt_number:03d}'
                log=folder/'logs'/f'{job["id"]}_attempt_{attempt_number:03d}.log';log.parent.mkdir(parents=True,exist_ok=True)
                attempt={'number':attempt_number,'output':str(output),'log':str(log),'started_at':now()}
                job['attempts'].append(attempt);job['stage']='running';job.pop('error',None);job.pop('child_status',None)
                argv=command(job,output);attempt['command']=argv;started=time.monotonic()
                print(f'\n=== {job["id"]}: {job["settings"]["algorithm"]}, {job["objectives"]} ===',flush=True)
                def heartbeat(pid):
                    attempt['pid']=pid;attempt['duration_seconds']=time.monotonic()-started
                    if (output/'status.json').exists():
                        try:job['child_status']=read(output/'status.json')
                        except (OSError,ValueError):pass
                    publisher(folder,manifest)
                publisher(folder,manifest)
                try:
                    code=runner(argv,log,heartbeat);attempt['returncode']=code
                    if code:raise RuntimeError(f'Process exit {code}; see {log}')
                    summary=read(output/'optimization_summary.json')
                    if summary['status'] not in DONE:raise RuntimeError('Child did not finish optimization')
                    job.update(stage=summary['status'],summary=summary,selected=read(output/'selected_result.json'))
                except KeyboardInterrupt:
                    job['stage']='interrupted';manifest['stage']='interrupted';raise
                except Exception as exc:
                    job.update(stage='failed',error=str(exc));print(f'{job["id"]}: FAILED: {exc}',flush=True)
                    if fail_fast:break
                finally:
                    attempt.update(finished_at=now(),duration_seconds=time.monotonic()-started);publisher(folder,manifest)
            manifest['stage']='completed' if all(job['stage'] in DONE for job in manifest['jobs']) else 'completed_with_errors'
    except KeyboardInterrupt:
        manifest['stage']='interrupted';print('Queue stopped. Use --resume to continue unfinished jobs.',flush=True)
        return 130
    finally:
        manifest['finished_at']=now();publisher(folder,manifest)
    return 0 if manifest['stage']=='completed' else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=DEFAULT)
    parser.add_argument('--population',type=int);parser.add_argument('--generations',type=int);parser.add_argument('--seed',type=int)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--resume',nargs='?',const='latest',help='Resume a batch directory, or the latest batch if omitted')
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--dry-run',action='store_true');mode.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--fail-fast',action='store_true');parser.add_argument('--allow-sleep',action='store_true')
    args=parser.parse_args(argv)
    if args.resume:
        if args.output or args.population is not None or args.generations is not None or args.seed is not None:
            parser.error('Resume uses the original output and frozen budget; omit overrides')
        folder=Path(read(ROOT/'runs/night_optimization/latest.json')['batch']) if args.resume=='latest' else Path(args.resume)
        manifest=load_batch(folder)
    else:
        manifest=plan(args.config,args.population,args.generations,args.seed)
        folder=args.output or ROOT/'runs/night_optimization'/dt.datetime.now().strftime('batch_%Y%m%d_%H%M%S_%f')
    if args.dry_run:
        for job in manifest['jobs']:
            preview={**job,'snapshot_path':str(Path(folder)/'configs'/(job['id']+'.json'))}
            print(json.dumps({'job':job['id'],'settings':job['settings'],'command':command(preview,Path(folder)/'outputs'/job['id']/'attempt_001')},ensure_ascii=False))
        return 0
    with queue_lock():
        if args.resume:recover_interrupted(manifest)
        preflight(manifest)
        if not args.resume:manifest=create_batch(manifest,folder)
        if args.prepare_only:print('PREPARED: '+str(folder),flush=True);return 0
        print('BATCH: '+str(folder),flush=True)
        return run_batch(folder,manifest,args.fail_fast,not args.allow_sleep)


if __name__=='__main__':raise SystemExit(main())
