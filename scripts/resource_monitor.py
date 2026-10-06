"""Detached, local-only resource capture with incremental durable records."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from resource_native import Windows
from resource_data import Activity, Targets, read_json
from resource_report import atomic_json, generate, process_key

MODULES = ('resource_monitor.py','resource_native.py','resource_data.py','resource_report.py')


def utc(): return datetime.now(timezone.utc).isoformat()


class Journal:
    def __init__(self, path): self.stream = path.open('a',encoding='utf-8',buffering=1)
    def write(self, value):
        self.stream.write(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n')
        self.stream.flush(); os.fsync(self.stream.fileno())
    def close(self): self.stream.close()


def lock_name(root, suffix):
    return 'Local\\HeartRadioResources_'+hashlib.sha256(str(root).lower().encode()).hexdigest()[:16]+'_'+suffix


def latest(root, directory=None):
    base = (root/'evidence'/'resource-use').resolve()
    path = Path(directory).resolve() if directory else Path(read_json(root/'runs'/'resource-test.json')['directory']).resolve()
    if path.parent != base or not (path/'conditions.json').is_file():
        raise ValueError('必须选择本项目 evidence/resource-use 下的一次资源测试目录')
    return path


def worker_alive(windows, status):
    try:
        return windows.process(status['workerPid'])['creationTicks']==status['workerCreationTicks']
    except (OSError,KeyError,TypeError): return False


def preflight(root, windows, diagnostic=False):
    targets = Targets(root,windows)
    processes, alive, issues = targets.sample()
    if not all(alive.values()) or issues:
        raise RuntimeError('接收器或仪表盘未运行、身份不符或无法读取。请先启动电脑端。')
    activity = Activity(root)
    state = activity.update(prime=True)
    if not diagnostic and not state['freshValid']:
        raise RuntimeError('最近 30 秒没有可用的有效心率事件。请正常佩戴、连接手表，确认仪表盘持续显示心率后再启动测试。未开始正式计时。')
    return targets, activity, processes, state


def start(root, minutes, interval, diagnostic):
    if not math.isfinite(minutes) or not 10 <= minutes*60 <= 86400:
        raise ValueError('测试时长应为 10 秒至 24 小时')
    if not math.isfinite(interval) or not 1 <= interval <= 60 or interval*2 > minutes*60:
        raise ValueError('采样间隔应为 1～60 秒，测试时长至少为间隔的两倍')
    windows = Windows()
    with windows.mutex(lock_name(root,'launch')):
        marker = root/'runs'/'resource-test.json'
        if marker.exists():
            try:
                prior = latest(root); status = read_json(prior/'status.json')
                if status.get('state') in ('starting','running') and worker_alive(windows,status):
                    print('资源测试已在运行：'+str(prior)); return prior
            except (OSError,ValueError,KeyError): pass
        # Check the worker mutex before creating a new run, including diagnostic runs.
        with windows.mutex(lock_name(root,'worker')): pass
        _, _, initial, state = preflight(root,windows,diagnostic)
        run_id = datetime.now().strftime('%Y%m%d-%H%M%S-%f') + ('-diagnostic' if diagnostic else '')
        directory = root/'evidence'/'resource-use'/run_id
        code = directory/'code'; code.mkdir(parents=True)
        hashes = {}
        for name in MODULES:
            source = Path(__file__).parent/name
            shutil.copyfile(source,code/name)
            hashes['scripts/'+name] = hashlib.sha256(source.read_bytes()).hexdigest()
        for name in ('desktop-widget/build/HeartRadioWidgets.exe','dashboard/server.py','dashboard/state.py','dashboard/widgets.py'):
            path = root/name
            if path.is_file(): hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        meta = read_json(root/'runs'/'active.json')
        frozen_receiver = Path(meta['logPath']).parent/'receiver'/'wifi_receive.py'
        if frozen_receiver.exists(): hashes['runningReceiver'] = hashlib.sha256(frozen_receiver.read_bytes()).hexdigest()
        conditions = dict(schema=1,root=str(root),startedUtc=utc(),durationSeconds=minutes*60,
            intervalSeconds=interval,diagnostic=diagnostic,initialProcesses=initial,initialActivity=state,
            receiverRunId=meta['runId'],codeSha256=hashes,**windows.system(),
            trackedComponents=['receiver','dashboard','desktop_widgets','capability_bridge','registered_mcp_adapter'],
            exclusions=['Agent/model processes','browser/Codex shared processes','GPU','system power','watch hardware'],
            cpuDenominator='all logical processors; 100% means the entire machine')
        atomic_json(directory/'conditions.json',conditions)
        atomic_json(directory/'status.json',dict(state='starting',updatedUtc=utc(),directory=str(directory)))
        with (directory/'worker.stdout.txt').open('w',encoding='utf-8') as out, (directory/'worker.stderr.txt').open('w',encoding='utf-8') as err:
            process = subprocess.Popen([getattr(sys,'_base_executable',sys.executable),str(code/'resource_monitor.py'),
                'worker','--root',str(root),'--directory',str(directory)],cwd=root,
                stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            status = read_json(directory/'status.json')
            if status.get('state')=='running':
                print(f"{'诊断' if diagnostic else '正式'}资源测试已开始：{minutes:g} 分钟，每 {interval:g} 秒采样。")
                print('可以关闭此窗口和 GPT；电脑与心率程序需要继续运行。')
                print('输出目录：'+str(directory))
                return directory
            if process.poll() is not None:
                raise RuntimeError(f"后台采样未启动成功：{directory}；{status.get('error','请查看 worker.stderr.txt')}")
            time.sleep(.1)
        (directory/'stop.request').touch()
        raise RuntimeError(f'后台采样未在 15 秒内确认就绪，已请求停止。检查 {directory}')


def worker(root, directory):
    windows = Windows(); conditions = read_json(directory/'conditions.json')
    status = dict(state='starting',workerPid=os.getpid(),workerCreationTicks=windows.process(os.getpid())['creationTicks'],
                  directory=str(directory),updatedUtc=utc(),diagnostic=conditions['diagnostic'])
    samples = Journal(directory/'samples.jsonl'); events = Journal(directory/'events.jsonl')
    started = time.monotonic(); started_wall = time.time(); count = 0
    def event(kind, **fields):
        events.write(dict(kind=kind,utc=utc(),elapsedSeconds=time.monotonic()-started,**fields))
    try:
        with windows.mutex(lock_name(root,'worker')):
            targets, activity, _, _ = preflight(root,windows,conditions['diagnostic'])
            # Formal measurement starts here, after preflight and module freezing.
            started = time.monotonic(); started_wall = time.time()
            conditions['startedUtc'] = utc(); atomic_json(directory/'conditions.json',conditions)
            activity.event = event
            if not conditions['diagnostic']:
                atomic_json(root/'runs'/'resource-test.json',dict(directory=str(directory)))
            status['state']='running'; status['startedUtc']=conditions['startedUtc']
            interval = conditions['intervalSeconds']; duration = conditions['durationSeconds']
            previous_keys = {}; previous_display = None; previous_fresh = None
            next_sample = started; previous_elapsed = None; previous_wall = started_wall; issue_signature = None
            event('capture_started',diagnostic=conditions['diagnostic'])
            while True:
                now = time.monotonic()
                stopped = (directory/'stop.request').exists()
                if now < next_sample and not stopped:
                    time.sleep(min(.25,next_sample-now)); continue
                elapsed = now-started; gap = previous_elapsed is not None and elapsed-previous_elapsed>interval*2.5
                if gap: event('sampling_gap',seconds=elapsed-previous_elapsed)
                wall = time.time()
                if previous_elapsed is not None and abs((wall-previous_wall)-(elapsed-previous_elapsed))>2:
                    event('wall_clock_changed',wallDeltaSeconds=wall-previous_wall,elapsedDeltaSeconds=elapsed-previous_elapsed)
                processes, alive, issues = targets.sample()
                try: activity_state = activity.update()
                except (OSError,ValueError,KeyError,TypeError) as error:
                    issues.append(dict(group='activity',error=str(error)))
                    activity_state = dict(activity.snapshot(),freshValid=False,connected=False)
                own = windows.process(os.getpid(),windows.processes().get(os.getpid(),{}).get('threads'))
                processes.append(dict(own,group='monitor'))
                keys = {process_key(p):dict(pid=p['pid'],creationTicks=p['creationTicks'],group=p['group']) for p in processes}
                for key in keys.keys()-previous_keys.keys(): event('process_seen',**keys[key])
                for key in previous_keys.keys()-keys.keys(): event('process_gone',**previous_keys[key])
                previous_keys = keys
                pids = {p['pid'] for p in processes if p['group']=='desktop_widgets'}
                display = windows.widget_visibility(pids)
                try:
                    layout = read_json(root/'runs'/'widget-layout.json')
                    display['locked'] = {key:layout.get(key,{}).get('locked',False) for key in ('current','trend')}
                except (OSError,ValueError): display['locked'] = None
                if display!=previous_display: event('widget_state',**display); previous_display=display
                if activity_state['freshValid']!=previous_fresh:
                    event('fresh_data_state',freshValid=activity_state['freshValid']); previous_fresh=activity_state['freshValid']
                signature = json.dumps(issues,sort_keys=True)
                if signature!=issue_signature and issues: event('sample_issues',issues=issues)
                issue_signature=signature
                samples.write(dict(schema=1,utc=utc(),monotonicNs=time.monotonic_ns(),elapsedSeconds=elapsed,processes=processes,
                    rootsAlive=alive,activity=activity_state,widgets=display,issues=issues))
                count += 1; activity.reset_counts(); previous_elapsed=elapsed; previous_wall=wall
                status.update(updatedUtc=utc(),elapsedSeconds=elapsed,remainingSeconds=max(0,duration-elapsed),
                    samples=count,latestActivity=activity_state,latestIssues=issues)
                atomic_json(directory/'status.json',status)
                if stopped or elapsed>=duration:
                    status['state']='stopped' if stopped else 'completed'; break
                # Do not fabricate catch-up samples after a scheduling delay or sleep.
                next_sample = min(started+duration,started+(math.floor((time.monotonic()-started)/interval)+1)*interval)
            event('capture_finished',state=status['state'])
            status['updatedUtc']=utc(); atomic_json(directory/'status.json',status)
            generate(directory)
    except BaseException as error:
        status.update(state='failed',error=f'{type(error).__name__}: {error}',updatedUtc=utc(),samples=count)
        atomic_json(directory/'status.json',status)
        event('capture_failed',error=status['error'])
        try: generate(directory)
        except Exception: traceback.print_exc()
        raise
    finally:
        samples.close(); events.close()


def manage(root, action, directory=None):
    if directory is None and not (root/'runs'/'resource-test.json').exists():
        print('尚未开始正式资源测试。请先佩戴手表并连接，再使用“开始资源测试”。')
        return
    directory = latest(root,directory); windows = Windows()
    status = read_json(directory/'status.json')
    running = status.get('state') in ('starting','running') and worker_alive(windows,status)
    if action=='stop':
        if running:
            (directory/'stop.request').touch()
            print('已请求结束资源采样；心率接收和手表采集继续运行。')
        else: print('采样进程已结束；可以重新生成报告。')
    elif action=='report':
        result=generate(directory, 'running' if running else ('interrupted' if status.get('state') in ('running','starting') else None))
        print('报告已生成：'+str(directory/'report.md'))
        if running: print('当前是临时报告；采样结束后会自动更新。')
    else:
        label={'running':'进行中','starting':'启动中','completed':'已完成','stopped':'提前结束','failed':'异常结束'}
        state=label.get(status.get('state'),'未知')
        if status.get('state') in ('running','starting') and not running: state='采样进程已中断；请重新生成报告'
        print('状态：'+state)
        print(f"已记录 {status.get('elapsedSeconds',0)/60:.2f} 分钟；样本 {status.get('samples',0)} 个")
        if running: print(f"预计剩余 {status.get('remainingSeconds',0)/60:.2f} 分钟")
        if status.get('error'): print('异常：'+status['error'])
        print('类型：'+('工具诊断，非正式测试' if status.get('diagnostic') else '正式测试'))
    print('目录：'+str(directory))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','worker','status','stop','report','check'))
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--directory',type=Path)
    parser.add_argument('--minutes',type=float,default=60)
    parser.add_argument('--interval',type=float,default=5)
    parser.add_argument('--diagnostic',action='store_true',help='explicit tool verification, not a real-data test')
    args=parser.parse_args(); root=args.root.resolve()
    if not (root/'scripts'/'Start-HeartRadio.ps1').exists(): raise ValueError('项目路径无效')
    if args.action=='start': start(root,args.minutes,args.interval,args.diagnostic)
    elif args.action=='worker': worker(root,latest(root,args.directory))
    elif args.action=='check':
        _,_,_,state=preflight(root,Windows(),args.diagnostic)
        print('启动条件通过；没有启动资源测试。最近有效事件年龄：'+str(state['validEventAgeSeconds']))
    else: manage(root,args.action,args.directory)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as error:
        print('资源测试操作失败：'+str(error),file=sys.stderr)
        sys.exit(1)
