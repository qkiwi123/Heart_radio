"""Observe a human Wi-Fi off/on test; never changes Wi-Fi settings or samples the watch."""
import argparse
import json
from pathlib import Path
import time
import urllib.request

from recovery_check import connection


def heart():
    with urllib.request.urlopen('http://127.0.0.1:8766/api/state', timeout=3) as response:
        value = json.load(response)
    keys = ('connected', 'valid', 'bpm', 'eventReceivedMs', 'eventAgeSeconds', 'packetAgeSeconds',
            'connections', 'disconnects', 'sequenceGaps', 'status')
    return {key: value.get(key) for key in keys}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=150, choices=range(30, 301))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.output.resolve().is_relative_to((root / 'evidence/capability-probe').resolve()) or args.output.exists():
        parser.error('Choose a new project evidence file')
    record = dict(adbUsed=False, settingsChangedByScript=False, complete=False,
                  beginWallMs=time.time_ns() // 1000000, beginMonotonicNs=time.monotonic_ns(), rows=[])
    original = connection()
    if not original.get('connected') or not original.get('watchLifecycle'):
        raise RuntimeError('Begin with a connected watch standby')
    session = original['watchLifecycle']['sessionId']
    first_offline = None
    until = time.monotonic() + args.seconds
    try:
        while time.monotonic() < until:
            state, pulse = connection(), heart()
            row = dict(wallMs=time.time_ns() // 1000000, monotonicNs=time.monotonic_ns(), bridge=state, heart=pulse)
            record['rows'].append(row)
            if not state.get('connected') and first_offline is None:
                first_offline = row
                record['firstOffline'] = row
                print(json.dumps(dict(event='watch_standby_offline')), flush=True)
            if first_offline and state.get('connected') and pulse.get('connected') and pulse.get('valid') \
                    and (pulse.get('eventReceivedMs') or 0) > first_offline['wallMs']:
                if state['watchLifecycle']['sessionId'] != session:
                    raise RuntimeError('Wi-Fi recovery restarted the opt-in session')
                record.update(complete=True, recovered=row,
                    observedOfflineUntilFreshHeartSeconds=(row['monotonicNs'] - first_offline['monotonicNs']) / 1e9)
                print(json.dumps(dict(event='watch_and_fresh_heart_recovered',
                    seconds=record['observedOfflineUntilFreshHeartSeconds'])), flush=True)
                break
            args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            time.sleep(.5)
    finally:
        record.update(endWallMs=time.time_ns() // 1000000, endMonotonicNs=time.monotonic_ns())
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    if not record['complete']:
        raise RuntimeError('No observed off/on recovery; do not report it as verified')


if __name__ == '__main__':
    main()
