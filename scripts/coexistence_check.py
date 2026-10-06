"""Record controlled HR coexistence phases without ADB or restarting the HR service."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capability_probe import request


def read_rows(path):
    rows = []
    for line in path.read_bytes().splitlines():
        try:
            rows.append(json.loads(line))
        except (ValueError, UnicodeDecodeError):
            continue  # A running receiver can have an incomplete final line.
    return rows


def summarize(rows, begin_ns, end_ns, fresh_seconds=30):
    """Freshness uses PC monotonic callback receipt, not the unverified sensor timestamp."""
    duration = (end_ns - begin_ns) / 1e9
    inside = [r for r in rows if begin_ns <= r.get('pcMonotonicNs', 0) <= end_ns]
    packets = [r for r in inside if r.get('kind') == 'packet']
    valid = [r for r in packets if r.get('packet', {}).get('kind') == 'sensor_event'
             and r['packet'].get('valid') is True]
    before = [r for r in rows if r.get('pcMonotonicNs', 0) < begin_ns
              and r.get('kind') == 'packet' and r.get('packet', {}).get('kind') == 'sensor_event'
              and r['packet'].get('valid') is True]
    arrivals = sorted(r['pcMonotonicNs'] for r in valid)
    predecessor = max((r['pcMonotonicNs'] for r in before), default=None)
    fresh_ns = int(fresh_seconds * 1e9)
    covered = 0
    covered_until = begin_ns
    for arrival in ([predecessor] if predecessor is not None else []) + arrivals:
        start = max(begin_ns, arrival, covered_until)
        stop = min(end_ns, arrival + fresh_ns)
        if stop > start:
            covered += stop - start
            covered_until = stop
    gaps = [(b - a) / 1e9 for a, b in zip(arrivals, arrivals[1:])]
    sessions = sorted(set(r['packet']['session'] for r in packets))
    invalid = sum(r['packet'].get('kind') == 'sensor_event' and not r['packet'].get('valid') for r in packets)
    last = arrivals[-1] if arrivals else predecessor
    return dict(durationSeconds=round(duration, 3), validHeartCallbacks=len(valid),
                invalidHeartCallbacks=invalid, packets=len(packets),
                heartbeatPackets=sum(r['packet'].get('kind') == 'heartbeat' for r in packets),
                bpmRange=[min(r['packet']['bpm'] for r in valid), max(r['packet']['bpm'] for r in valid)] if valid else None,
                maxBetweenValidCallbacksSeconds=round(max(gaps), 3) if gaps else None,
                initialValidAgeSeconds=round((begin_ns - predecessor) / 1e9, 3) if predecessor is not None else None,
                finalValidAgeSeconds=round((end_ns - last) / 1e9, 3) if last is not None else None,
                freshnessThresholdSeconds=fresh_seconds,
                staleSeconds=round(max(0, duration - covered / 1e9), 3),
                freshFraction=round(covered / (end_ns - begin_ns), 4) if end_ns > begin_ns else None,
                wifiConnects=sum(r.get('kind') == 'wifi_connected' for r in inside),
                wifiDisconnects=sum(r.get('kind') == 'wifi_disconnected' for r in inside),
                sequenceAnomalies=[dict(pcMonotonicNs=r['pcMonotonicNs'], sequenceStep=r['sequenceStep'],
                                        session=r['packet']['session'], sequence=r['packet']['sequence'])
                                   for r in packets if r.get('sequenceStep') not in (None, 1)],
                heartSessions=sessions, timeBasis='pc_monotonic_receipt', sourceAgeVerified=False)


def run_phase(args):
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / (args.name + '.json')
    if target.exists():
        raise RuntimeError('Phase already recorded; choose another name')
    begin = time.monotonic_ns()
    deadline = begin + int(args.seconds * 1e9)
    record = dict(phase=args.name, beginMonotonicNs=begin, beginWallMs=time.time_ns() // 1_000_000,
                  requestedSeconds=args.seconds, heartLog=str(args.heart_log), adbUsed=False,
                  samplePlan=[1, 4, 6] if args.samples else [], requests=[])
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    sensors = []
    if args.samples:
        inventory = request('inventory')
        (args.output / (args.name + '-inventory.json')).write_text(json.dumps(inventory, indent=2), encoding='utf-8')
        if inventory.get('status') != 'ok':
            raise RuntimeError('Watch inventory unavailable')
        for typ in (1, 4, 6):
            match = [s for s in inventory['sensors'] if s['type'] == typ]
            if len(match) != 1:
                raise RuntimeError('Expected exactly one standard accelerometer, gyroscope and pressure sensor')
            sensors.append(match[0])
    print(json.dumps(dict(event='phase_start', phase=args.name, seconds=args.seconds)), flush=True)
    next_progress = begin
    next_sample = begin + int(10e9)
    sample_index = 0
    try:
        while time.monotonic_ns() < deadline:
            now = time.monotonic_ns()
            if now >= next_progress:
                summary = summarize(read_rows(args.heart_log), begin, now)
                print(json.dumps(dict(event='progress', phase=args.name, **summary)), flush=True)
                next_progress = now + int(30e9)
            if sensors and now >= next_sample and deadline - now > int(15e9):
                sensor = sensors[sample_index % len(sensors)]
                started = time.monotonic_ns()
                result = request('sample', dict(sensorId=sensor['sensorId'], seconds=4))
                sample_index += 1
                file = args.output / f'{args.name}-sample-{sample_index:02}.json'
                file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                status = request('status')
                (args.output / f'{args.name}-idle-{sample_index:02}.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
                outcome = dict(type=sensor['type'], sensorId=sensor['sensorId'], beginMonotonicNs=started,
                               endMonotonicNs=time.monotonic_ns(), resultFile=file.name,
                               **{k: result.get(k) for k in ('id', 'status', 'callbacks', 'actualElapsedMs', 'pcRoundTripMs',
                                  'listenersReleased', 'requestWakeLockHeldAtResult', 'screenInteractive')},
                               idleActiveRequest=status.get('activeRequest'), idleWakeLockHeld=status.get('requestWakeLockHeld'))
                record['requests'].append(outcome)
                target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(dict(event='sample', **outcome)), flush=True)
                if result.get('status') != 'data_received' or result.get('listenersReleased') is not True \
                        or result.get('requestWakeLockHeldAtResult') is not False \
                        or status.get('status') != 'ok' or status.get('activeRequest') is not None \
                        or status.get('requestWakeLockHeld') is not False:
                    raise RuntimeError('Bounded request or idle cleanup failed; stop sampling')
                if (result.get('sensor', {}).get('name'), result.get('sensor', {}).get('type')) != (sensor['name'], sensor['type']):
                    raise RuntimeError('Returned sensor identity mismatch')
                next_sample = started + int(30e9)
            time.sleep(.2)
    finally:
        end = time.monotonic_ns()
        record.update(endMonotonicNs=end, endWallMs=time.time_ns() // 1_000_000,
                      complete=end >= deadline, summary=summarize(read_rows(args.heart_log), begin, end))
        target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        rows = read_rows(args.heart_log)
        bounded = [r for r in rows if begin <= r.get('pcMonotonicNs', 0) <= end]
        previous = [r for r in rows if r.get('pcMonotonicNs', 0) < begin and r.get('kind') == 'packet'
                    and r.get('packet', {}).get('kind') == 'sensor_event' and r['packet'].get('valid') is True]
        if previous:
            bounded.insert(0, previous[-1])
        (args.output / (args.name + '-heart.jsonl')).write_text(''.join(json.dumps(r) + '\n' for r in bounded), encoding='utf-8')
        print(json.dumps(dict(event='phase_end', **record)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', required=True)
    parser.add_argument('--seconds', type=int, required=True, choices=range(1, 601))
    parser.add_argument('--heart-log', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--samples', action='store_true')
    run_phase(parser.parse_args())


if __name__ == '__main__':
    main()
