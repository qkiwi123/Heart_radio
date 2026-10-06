"""Summarize app-local acquisition around explicit lifecycle bounds, not other app usage."""
import argparse
import collections
import json
import statistics
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('log', type=Path)
p.add_argument('--leave', required=True, help='Exact ISO time of logged activity_pause')
p.add_argument('--return-at', required=True, help='Exact ISO time of logged activity_resume')
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
rows = [json.loads(line) for line in a.log.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
events = [r for r in rows if r['kind'] == 'event']
start = next(r for r in rows if r['kind'] == 'session_start')
stop = next(r for r in rows if r['kind'] == 'session_stop')
leave = next(r for r in rows if r['kind'] == 'activity_pause' and r['iso'] == a.leave)
back = next(r for r in rows if r['kind'] == 'activity_resume' and r['iso'] == a.return_at)
def stats(values):
    return dict(count=len(values), min=min(values), median=statistics.median(values), max=max(values)) if values else None
def describe(es):
    valid = [r for r in es if r['valid']]
    gaps = [(y['arrivalNs']-x['arrivalNs'])/1e9 for x,y in zip(es,es[1:])]
    return dict(callbacks=len(es), valid=len(valid), bpm=stats([r['bpm'] for r in valid]),
        arrivalIntervalsSeconds=stats(gaps), screenOffEvents=sum(not r['screenInteractive'] for r in es),
        timestampDifferenceSeconds=stats([r['timestampDifferenceMs']/1000 for r in es]),
        freshBy30SecondRule=sum(r['valid'] and 0 <= r['timestampDifferenceMs'] <= 30000 for r in es),
        firstIso=es[0]['iso'] if es else None, lastIso=es[-1]['iso'] if es else None)
phases = []
for name, left, right in [('before_leave',start,leave), ('away_from_app',leave,back), ('after_return',back,stop)]:
    es = [r for r in events if left['elapsedNs'] <= r['arrivalNs'] < right['elapsedNs']]
    phases.append(dict(name=name, startIso=left['iso'],endIso=right['iso'],
        durationSeconds=(right['elapsedNs']-left['elapsedNs'])/1e9, **describe(es)))
max_gap = max(zip(events,events[1:]), key=lambda pair: pair[1]['arrivalNs']-pair[0]['arrivalNs'])
heartbeat = [r for r in rows if r['kind']=='heartbeat']
middle = [r for r in events if leave['elapsedNs'] <= r['arrivalNs'] < back['elapsedNs']]
boundaries = []
for bound in [leave,back]:
    before = next((r for r in reversed(events) if r['arrivalNs'] < bound['elapsedNs']),None)
    after = next((r for r in events if r['arrivalNs'] >= bound['elapsedNs']),None)
    boundaries.append(dict(iso=bound['iso'],gapSeconds=(after['arrivalNs']-before['arrivalNs'])/1e9 if before and after else None))
result = dict(source=str(a.log), total=describe(events), phases=phases,
    boundaryGaps=boundaries,
    middleSpans=dict(arrivalSeconds=(middle[-1]['arrivalNs']-middle[0]['arrivalNs'])/1e9,
        uptimeSeconds=(middle[-1]['arrivalUptimeMs']-middle[0]['arrivalUptimeMs'])/1000,
        eventTimestampSeconds=(middle[-1]['eventNs']-middle[0]['eventNs'])/1e9) if middle else None,
    startIso=start['iso'],stopIso=stop['iso'],durationSeconds=(stop['elapsedNs']-start['elapsedNs'])/1e9,
    firstValidWaitSeconds=(next(r for r in events if r['valid'])['arrivalNs']-start['elapsedNs'])/1e9,
    maxGap=dict(startIso=max_gap[0]['iso'],endIso=max_gap[1]['iso'],seconds=(max_gap[1]['arrivalNs']-max_gap[0]['arrivalNs'])/1e9),
    timestampStepsNs=dict(collections.Counter(y['eventNs']-x['eventNs'] for x,y in zip(events,events[1:]))),
    eventTimestampSpanSeconds=(events[-1]['eventNs']-events[0]['eventNs'])/1e9,
    arrivalSpanSeconds=(events[-1]['arrivalNs']-events[0]['arrivalNs'])/1e9,
    uptimeSpanSeconds=(events[-1]['arrivalUptimeMs']-events[0]['arrivalUptimeMs'])/1000,
    invalidEvents=[r for r in events if not r['valid']],
    lastHeartbeat=heartbeat[-1],terminalNoEventSeconds=(stop['elapsedNs']-events[-1]['arrivalNs'])/1e9,
    lifecycle=[r for r in rows if r['kind'].startswith('activity_') or r['kind'] in ['registration','session_stop','failure']],
    newFieldsConsistent=all(abs(r['timestampDifferenceMs']-(r['arrivalNs']-r['eventNs'])/1e6)<1e-6 for r in events)
        and all(abs(y['arrivalIntervalMs']-(y['arrivalNs']-x['arrivalNs'])/1e6)<1e-6 and
                abs(y['eventTimestampStepMs']-(y['eventNs']-x['eventNs'])/1e6)<1e-6 for x,y in zip(events,events[1:])),
    note='Away-from-app is a lifecycle interval, not proof of which other app was foreground. User confirmation is recorded separately. Valid is not independently proven fresh. The after-return phase includes any post-wear tail until assistant stop. No inference of death or conflict solely from missing on-change events.')
a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k not in ['lifecycle','invalidEvents','lastHeartbeat']},ensure_ascii=True,indent=2))
