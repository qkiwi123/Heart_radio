"""Analyze an explicitly confirmed natural-use window, separately from preparation."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import time


def read_jsonl(path):
    content = path.read_text(encoding='utf-8-sig')
    lines = content.splitlines()
    rows = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            # A live log can end with an incomplete write; other corruption is an error.
            if index == len(lines)-1 and not content.endswith('\n'):
                break
            raise
    return rows


def intervals(rows, start_ns, end_ns):
    moments = [r['pcMonotonicNs'] for r in rows]
    gaps = [(b-a)/1e9 for a,b in zip(moments,moments[1:])]
    edges = [start_ns]+moments+[end_ns]
    return dict(count=len(rows), medianSeconds=statistics.median(gaps) if gaps else None,
                maxBetweenEventsSeconds=max(gaps) if gaps else None,
                maxIncludingWindowEdgesSeconds=max((b-a)/1e9 for a,b in zip(edges,edges[1:])),
                intervalsOver30Seconds=sum(x>30 for x in gaps),
                adjacentWithin50ms=sum(x<.05 for x in gaps))


def analyze(run, watch_path=None, now_ns=None):
    observation=json.loads((run/'human-observation.json').read_text(encoding='utf-8-sig'))
    pc=read_jsonl(run/'pc.jsonl')
    start_ns=observation['startedPcMonotonicNs']
    planned_end_ns=start_ns+observation['plannedSeconds']*1_000_000_000
    latest_ns=pc[-1]['pcMonotonicNs'] if pc else start_ns
    now_ns=time.monotonic_ns() if now_ns is None else now_ns
    # Completed logs remain analysable after a computer reboot.
    observed_end_ns=min(planned_end_ns,max(latest_ns,now_ns))
    observed_end_ns=max(start_ns,observed_end_ns)
    rows=[r for r in pc if start_ns<=r['pcMonotonicNs']<=observed_end_ns]
    packets=[r for r in rows if r['kind']=='packet']
    events=[r for r in packets if r['packet']['kind']=='sensor_event']
    valid=[r for r in events if r['packet']['valid']]
    heartbeat=[r for r in packets if r['packet']['kind']=='heartbeat']
    previous_packets=[r for r in pc if r['kind']=='packet' and r['pcMonotonicNs']<start_ns]
    result=dict(runId=run.name, startUtc=observation['startedUtc'], plannedEndUtc=observation['plannedEndUtc'],
        plannedSeconds=observation['plannedSeconds'], observedWindowSeconds=(observed_end_ns-start_ns)/1e9,
        plannedTimeElapsed=observed_end_ns>=planned_end_ns,
        lastPacketAgeSeconds=(now_ns-next((r['pcMonotonicNs'] for r in reversed(pc) if r['kind']=='packet'),now_ns))/1e9,
        receivedPackets=len(packets), sensorEvents=len(events), validEvents=len(valid),
        invalidEvents=len(events)-len(valid), heartbeatPackets=len(heartbeat),
        validBpmRange=[min(r['packet']['bpm'] for r in valid),max(r['packet']['bpm'] for r in valid)] if valid else None,
        sensorIntervals=intervals(events,start_ns,observed_end_ns), validIntervals=intervals(valid,start_ns,observed_end_ns),
        packetIntervals=intervals(packets,start_ns,observed_end_ns),
        firstValidAfterConfirmationSeconds=(valid[0]['pcMonotonicNs']-start_ns)/1e9 if valid else None,
        lastSequenceBeforeWindow=previous_packets[-1]['packet']['sequence'] if previous_packets else None,
        sequenceDiscontinuities=[dict(sequence=r['packet']['sequence'],step=r['sequenceStep']) for r in packets if r.get('sequenceStep') not in (None,1)],
        connectionsInWindow=sum(r['kind']=='wifi_connected' for r in rows),
        disconnectsInWindow=[r for r in rows if r['kind']=='wifi_disconnected'],
        fullLogKinds=dict(Counter(r['kind'] for r in pc)),
        limitations=['User confirmation time defines the window; preparation is excluded.',
                    'Receipt intervals are not proof of exact sampling time or one-way latency.',
                    'No heart-rate callback alone is not proof of service death; compare heartbeats and watch lifecycle.',
                    'plannedTimeElapsed only describes elapsed time, not a pass verdict.'])
    if watch_path:
        watch=read_jsonl(watch_path)
        diagnostics=[r for r in watch if r['kind']=='capture_diagnostic']
        unplugged=[r for r in diagnostics if r.get('plugged')==0]
        battery=None
        if unplugged:
            first,last=unplugged[0],unplugged[-1]
            charge=[r.get('chargeCounterUah') for r in (first,last)]
            battery=dict(first=first,last=last,observedSeconds=(last['elapsedNs']-first['elapsedNs'])/1e9,
                dropPercentagePoints=first['batteryPct']-last['batteryPct'],
                chargeDropUah=charge[0]-charge[1] if all(isinstance(x,int) and x>0 for x in charge) else None,
                temperatureRangeC=[min(r['temperatureTenthsC'] for r in unplugged)/10,max(r['temperatureTenthsC'] for r in unplugged)/10],
                note='Coarse system estimates; no idle control and no full-discharge battery-life extrapolation.')
        queued=Counter(r['hex'] for r in watch if r['kind']=='wifi_packet')
        received=Counter(r['hex'] for r in pc if r['kind']=='packet')
        result['watch']=dict(file=str(watch_path),kinds=dict(Counter(r['kind'] for r in watch)),
            diagnosticCount=len(diagnostics),diagnosticPids=sorted({r['pid'] for r in diagnostics}),
            unpluggedBattery=battery,queuedNotReceived=sum((queued-received).values()),
            receivedNotQueued=sum((received-queued).values()),
            errors=[r for r in watch if r['kind'] in ('wifi_error','diagnostic_error')],
            stopEvents=[r for r in watch if r['kind'] in ('session_stop','service_destroy')])
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--watch',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=analyze(args.run,args.watch)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
