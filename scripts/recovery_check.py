"""Real paired-watch recovery checks. Never restarts HR, replays old samples, or uses ADB."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import time
import urllib.request

from capability_probe import request

ROOT = Path(__file__).resolve().parents[1]


def connection():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8768/state', timeout=2) as response:
            return json.load(response)
    except OSError:
        return dict(connected=False, status='bridge_unavailable')


def control(action):
    value = urllib.request.Request('http://127.0.0.1:8768/control',
        data=json.dumps(dict(action=action)).encode(),
        headers={'Content-Type': 'application/json', 'X-HeartRadio-Probe': 'request'})
    with urllib.request.urlopen(value, timeout=5) as response:
        return json.load(response)


def wait_for(predicate, seconds=45):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        state = connection()
        if predicate(state): return state
        time.sleep(.25)
    raise RuntimeError('Recovery condition timed out')


def check_recovered(before, after):
    first, second = before['watchLifecycle'], after['watchLifecycle']
    if first['sessionId'] != second['sessionId'] or second['leaseRemainingMs'] >= first['leaseRemainingMs']:
        raise RuntimeError('Recovery renewed or changed the opt-in session')
    state = request('status')
    if state.get('status') != 'ok' or state.get('activeRequest') is not None \
            or state.get('requestWakeLockHeld') is not False or state.get('heartRateCaptureRunning') is not True:
        raise RuntimeError('Idle cleanup or original HR state failed')
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--restart-computer', action='store_true')
    parser.add_argument('--stop-watch', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'evidence/capability-probe').resolve()) or output.exists():
        parser.error('Use a new evidence output file inside this project')
    output.parent.mkdir(parents=True, exist_ok=True)
    record = dict(beginWallMs=time.time_ns() // 1000000, adbUsedForTests=False, complete=False, checks=[])

    def save(): output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    def add(name, **fields):
        record['checks'].append(dict(name=name, wallMs=time.time_ns() // 1000000, **fields))
        save(); print(json.dumps(dict(check=name, status='passed')), flush=True)

    save()
    try:
        before = wait_for(lambda s: s.get('connected') and s.get('watchLifecycle'))
        inventory = request('inventory')
        if (inventory.get('model'), inventory.get('sdk'), inventory.get('firmware')) != ('OW20W2', 27, 'OW20W2_11_A.92'):
            raise RuntimeError('Unverified watch identity')
        sensor = next(s for s in inventory['sensors'] if s['type'] == 1)
        pressure = next(s for s in inventory['sensors'] if s['type'] == 6)
        begin = time.monotonic()
        control('disconnect')
        after = wait_for(lambda s: s.get('connected') and s.get('connectionSerial', 0) > before['connectionSerial'] and s.get('watchLifecycle'))
        idle = check_recovered(before, after)
        add('idle_disconnect', recoverySeconds=time.monotonic() - begin, before=before, after=after, idle=idle)

        before = connection()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request, 'sample', dict(sensorId=sensor['sensorId'], seconds=10))
            time.sleep(2)
            during = request('status')
            if during.get('activeRequest') is None or during.get('requestWakeLockHeld') is not True:
                raise RuntimeError('Fault was not injected during an active measurement')
            begin = time.monotonic()
            control('disconnect')
            result = future.result(timeout=15)
        if result.get('status') != 'disconnected':
            raise RuntimeError('Interrupted sample was incorrectly returned as successful')
        after = wait_for(lambda s: s.get('connected') and s.get('connectionSerial', 0) > before['connectionSerial'] and s.get('watchLifecycle'))
        idle = check_recovered(before, after)
        add('active_sample_disconnect', recoverySeconds=time.monotonic() - begin,
            during=during, interruptedResult=result, before=before, after=after, idle=idle)

        if args.restart_computer:
            before = connection()
            begin = time.monotonic()
            commands = [('Stop-CapabilityProbe.ps1', '-KeepWatchStandby'),
                        ('Start-CapabilityProbe.ps1', '-ComputerOnly', '-Python', 'D:/Python/python.exe', '-Seconds', '1800')]
            for name, *options in commands:
                subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                str(ROOT / 'scripts' / name), *options], check=True, timeout=20,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            after = wait_for(lambda s: s.get('connected') and s.get('watchLifecycle'))
            idle = check_recovered(before, after)
            active = json.loads((ROOT / 'runs/capability-probe-active.json').read_text(encoding='utf-8-sig'))
            add('computer_restart_without_adb', recoverySeconds=time.monotonic() - begin,
                before=before, after=after, idle=idle, restoredComputerRun=active['runDirectory'])

        result = request('sample', dict(sensorId=pressure['sensorId'], seconds=4))
        if result.get('status') != 'data_received' or result.get('listenersReleased') is not True \
                or result.get('requestWakeLockHeldAtResult') is not False:
            raise RuntimeError('New measurement failed after recovery')
        add('fresh_measurement_after_recovery', result=result)
        if args.stop_watch:
            response = control('stop_watch')
            if response.get('status') != 'stop_sent': raise RuntimeError('Stop was not sent')
            wait_for(lambda s: not s.get('connected'), seconds=5)
            observed = []
            for _ in range(20):
                state = connection(); observed.append(state)
                if state.get('connected'): raise RuntimeError('Explicit stop reconnected')
                time.sleep(.5)
            add('explicit_stop_no_reconnect', response=response, observedStates=observed)
        record['complete'] = True
    finally:
        record['endWallMs'] = time.time_ns() // 1000000
        save()


if __name__ == '__main__':
    main()
