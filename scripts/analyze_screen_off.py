"""Analyze closed screen-off intervals from real broadcast timestamps.
Outputs JSON; an event timestamp difference is NOT treated as verified delivery latency.
"""
import argparse
import json
import statistics
from pathlib import Path

def stats(values):
    return {'count':len(values),'min':min(values) if values else None,
            'median':statistics.median(values) if values else None,'max':max(values) if values else None}

def analyze(rows):
    start=next(r for r in rows if r['kind']=='session_start')
    stop=next((r for r in rows if r['kind']=='session_stop'), None)
    end=stop['elapsedNs'] if stop else rows[-1]['elapsedNs']
    rows=[r for r in rows if start['elapsedNs']<=r['elapsedNs']<=end]
    events=[r for r in rows if r['kind']=='event']
    valid=[r for r in events if r.get('valid')]
    off=None; periods=[]
    for r in rows:
        if r['kind']=='screen':
            if r.get('action')=='android.intent.action.SCREEN_OFF' and off is None:
                off=r
            elif r.get('action')=='android.intent.action.SCREEN_ON' and off:
                es=[e for e in events if off['elapsedNs']<=e['arrivalNs']<r['elapsedNs']]
                after=[e for e in events if r['elapsedNs']<=e['arrivalNs']<r['elapsedNs']+15e9]
                periods.append({'offIso':off['iso'],'onIso':r['iso'],
                  'offNs':off['elapsedNs'],'onNs':r['elapsedNs'],
                  'durationSeconds':(r['elapsedNs']-off['elapsedNs'])/1e9,
                  'callbacks':len(es),'validReadings':sum(e.get('valid',False) for e in es),
                  'arrivalIntervalsSeconds':stats([(b['arrivalNs']-a['arrivalNs'])/1e9 for a,b in zip(es,es[1:])]),
                  'headGapSeconds':(es[0]['arrivalNs']-off['elapsedNs'])/1e9 if es else None,
                  'tailGapSeconds':(r['elapsedNs']-es[-1]['arrivalNs'])/1e9 if es else None,
                  'callbacksWithin15SecondsAfterWake':len(after),
                  'note':'Wake window may contain another sleep transition; these are observed deliveries only.'})
                off=None
    # Inter-event gaps only within a continuous observed screen-state group.
    gaps={True:[],False:[]}; prev=None; previous_state=None
    for r in rows:
        screen=r['screenInteractive']
        if screen != previous_state: prev=None
        previous_state=screen
        if r['kind']=='event':
            if prev: gaps[screen].append((r['arrivalNs']-prev['arrivalNs'])/1e9)
            prev=r
    heartbeat=[r for r in rows if r['kind']=='heartbeat']
    return {'startIso':start['iso'],'stopIso':stop['iso'] if stop else None,
      'durationSeconds':(end-start['elapsedNs'])/1e9,
      'callbacks':len(events),'validReadings':len(valid),
      'validBpmRange':[min(e['bpm'] for e in valid),max(e['bpm'] for e in valid)] if valid else None,
      'firstValidWaitSeconds':(valid[0]['arrivalNs']-start['elapsedNs'])/1e9 if valid else None,
      'screenOffCallbacks':sum(not e['screenInteractive'] for e in events),
      'screenOffValid':sum(not e['screenInteractive'] and e['valid'] for e in events),
      'screenOnCallbacks':sum(e['screenInteractive'] for e in events),
      'screenOffArrivalIntervalsSeconds':stats(gaps[False]),
      'screenOnArrivalIntervalsSeconds':stats(gaps[True]),
      'closedOffPeriods':periods,
      'longestClosedOffPeriod':max(periods,key=lambda p:p['durationSeconds']) if periods else None,
      'finalUnclosedOffPeriodSeconds':(end-off['elapsedNs'])/1e9 if off else None,
      'allArrivalIntervalsSeconds':stats([(b['arrivalNs']-a['arrivalNs'])/1e9 for a,b in zip(events,events[1:])]),
      'rawTimestampDifferenceMs':stats([e['deliveryDelayMs'] for e in events]),
      'freshAtArrivalUnder30sRule':sum(e['valid'] and 0<=e['deliveryDelayMs']<=30000 for e in events),
      'lastEvent':events[-1] if events else None,
      'lastHeartbeat':heartbeat[-1] if heartbeat else None,
      'terminalNoCallbackSeconds':(end-events[-1]['arrivalNs'])/1e9 if events else None,
      'note':'No-contact terminal gap is not treated as proof of process death. Broadcast intervals approximate interactive state. No verified sampling delay is inferred from raw event timestamps.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('log'); p.add_argument('--output',required=True); a=p.parse_args()
    rows=[json.loads(s) for s in Path(a.log).read_text(encoding='utf-8-sig').splitlines() if s.strip()]
    Path(a.output).write_text(json.dumps(analyze(rows),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
