"""Analyze real JSONL logs only; synthetic unit tests are never acquisition evidence.
Usage: python scripts/analyze_logs.py evidence/device-logs/session-....jsonl
"""
import argparse
import json
import statistics
from pathlib import Path


def summarize(rows):
    events = [r for r in rows if r['kind'] == 'event']
    valid = [r for r in events if r.get('valid')]
    start = next((r for r in rows if r['kind'] == 'session_start'), None)
    def stats(xs):
        return {'count': len(xs), 'min': min(xs) if xs else None,
                'median': statistics.median(xs) if xs else None, 'max': max(xs) if xs else None}
    def intervals(rs, key):
        return stats([(b[key]-a[key])/1e9 for a,b in zip(rs, rs[1:])])
    segments=[]
    for r in rows:
        screen = r.get('screenInteractive')
        if not segments or segments[-1]['interactive'] != screen:
            segments.append({'interactive':screen,'startNs':r['elapsedNs'],'endNs':r['elapsedNs'],'events':[]})
        seg=segments[-1]; seg['endNs']=r['elapsedNs']
        if r['kind']=='event': seg['events'].append(r)
    screen_results=[]
    for s in segments:
        es=s.pop('events'); vs=[e for e in es if e.get('valid')]
        screen_results.append({**s,'observedSpanSeconds':(s['endNs']-s['startNs'])/1e9,
          'callbacks':len(es),'validReadings':len(vs), 'eventIntervalsSeconds':intervals(es,'eventNs'),
          'arrivalIntervalsSeconds':intervals(es,'arrivalNs'),
          'deliveryDelayMs':stats([e['deliveryDelayMs'] for e in es]),
          'tailWithoutEventSeconds':(s['endNs']-es[-1]['arrivalNs'])/1e9 if es else None})
    return {'fileContainsSessionStart':start is not None,'callbacks':len(events),'validReadings':len(valid),
       'firstValidWaitSeconds':(valid[0]['arrivalNs']-start['elapsedNs'])/1e9 if start and valid else None,
       'allEventIntervalsSeconds':intervals(events,'eventNs'),
       'allArrivalIntervalsSeconds':intervals(events,'arrivalNs'),
       'validEventIntervalsSeconds':intervals(valid,'eventNs'),
       'deliveryDelayMs':stats([e['deliveryDelayMs'] for e in events]),
       'screenSegments':screen_results,
       'registrations':[r for r in rows if r['kind']=='registration'],
       'failures':[r for r in rows if r['kind']=='failure'],
       'stops':[r for r in rows if r['kind']=='session_stop'],
       'note':'deliveryDelayMs is the raw arrivalNs minus eventNs difference, not independently proven latency. On this OW20W2, event timestamps showed a fixed increment unlike observed callback intervals; do not infer actual sampling frequency or latency without validating that clock. Valid readings pass value/status/timestamp-order checks but may be stale under the UI rule. Screen classification is screen state at delivery, not necessarily at sampling. Screen segment spans are bounded by logged rows. No-event tails are censored gaps, not measured inter-event intervals. No inference of process death from missing on-change events.'}

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('log'); parser.add_argument('--output'); args=parser.parse_args()
    rows=[]
    for num,line in enumerate(Path(args.log).read_text(encoding='utf-8-sig').splitlines(),1):
        if line.strip():
            try: rows.append(json.loads(line))
            except json.JSONDecodeError as e: raise SystemExit(f'Invalid JSON at line {num}; stop and re-export: {e}')
    result = json.dumps(summarize(rows), ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(result + '\n', encoding='utf-8')
    else:
        print(result)
