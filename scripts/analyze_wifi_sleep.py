"""Correlate screen broadcasts, sensor deliveries and acknowledged Wi-Fi frames.
Screen state is the watch's interactive flag, not proof of CPU wakefulness.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


def stats(values):
    return dict(count=len(values), median=statistics.median(values) if values else None,
                max=max(values) if values else None)


def analyze(watch, pc):
    stop = next((r for r in watch if r['kind'] == 'session_stop'), None)
    end = stop['elapsedNs'] if stop else watch[-1]['elapsedNs']
    rows = [r for r in watch if r['elapsedNs'] <= end]
    events = [r for r in rows if r['kind'] == 'event']
    packets = [r for r in pc if r['kind'] == 'packet']
    by_hex = {r['hex']: r for r in packets}
    periods = []
    off = None
    for row in rows:
        if row['kind'] != 'screen':
            continue
        if row.get('action') == 'android.intent.action.SCREEN_OFF' and off is None:
            off = row
        elif row.get('action') == 'android.intent.action.SCREEN_ON' and off is not None:
            start_ns, end_ns = off['elapsedNs'], row['elapsedNs']
            local = [r for r in rows if start_ns <= r['elapsedNs'] < end_ns]
            ev = [r for r in events if start_ns <= r['arrivalNs'] < end_ns]
            valid = [r for r in ev if r.get('valid')]
            sent = [r for r in local if r['kind'] == 'wifi_packet']
            received = [by_hex[r['hex']] for r in sent if r['hex'] in by_hex]
            points = [start_ns] + [r['arrivalNs'] for r in ev] + [end_ns]
            intervals = [(b['arrivalNs']-a['arrivalNs'])/1e9 for a,b in zip(ev, ev[1:])]
            after = [r for r in events if end_ns <= r['arrivalNs'] < end_ns + 15e9]
            uptime_windows = [dict(endIso=b['iso'], elapsedSeconds=(b['arrivalNs']-a['arrivalNs'])/1e9,
                                  uptimeSeconds=(b['arrivalUptimeMs']-a['arrivalUptimeMs'])/1000)
                              for a,b in zip(ev, ev[1:]) if b['arrivalNs']-a['arrivalNs'] > 10e9]
            elapsed_span = (ev[-1]['arrivalNs']-ev[0]['arrivalNs'])/1e9 if len(ev)>1 else None
            uptime_span = (ev[-1]['arrivalUptimeMs']-ev[0]['arrivalUptimeMs'])/1000 if len(ev)>1 else None
            periods.append(dict(offIso=off['iso'], onIso=row['iso'], offNs=start_ns, onNs=end_ns,
                durationSeconds=(end_ns-start_ns)/1e9, callbacks=len(ev), validEvents=len(valid),
                validBpmRange=[min(r['bpm'] for r in valid),max(r['bpm'] for r in valid)] if valid else None,
                arrivalIntervalsSeconds=stats(intervals),
                longestNoCallbackIncludingEdgesSeconds=max((b-a)/1e9 for a,b in zip(points,points[1:])),
                firstEventAfterOffSeconds=(ev[0]['arrivalNs']-start_ns)/1e9 if ev else None,
                lastEventBeforeWakeSeconds=(end_ns-ev[-1]['arrivalNs'])/1e9 if ev else None,
                adjacentDeliveriesWithin50ms=sum(x < .05 for x in intervals),
                localHeartbeatCount=sum(r['kind']=='heartbeat' for r in local),
                wifiQueued=len(sent), wifiReceivedMatching=len(received),
                wifiAcknowledged=sum(r['kind']=='wifi_ack' for r in local),
                receivedIntervalSeconds=stats([(b['pcMonotonicNs']-a['pcMonotonicNs'])/1e9 for a,b in zip(received,received[1:])]),
                wifiErrors=[r for r in local if r['kind']=='wifi_error'],
                longIntervalsWithUptime=uptime_windows,
                firstToLastEventElapsedSeconds=elapsed_span,
                firstToLastEventUptimeSeconds=uptime_span,
                estimatedDeepSleepBetweenEventEndpointsSeconds=max(0,elapsed_span-uptime_span) if elapsed_span is not None else None,
                callbacksWithin15SecondsAfterWake=len(after),
                note='PC frame attribution uses watch enqueue time; it does not establish exact screen state at PC receipt.'))
            off = None
    return dict(watchKinds=dict(Counter(r['kind'] for r in rows)), pcKinds=dict(Counter(r['kind'] for r in pc)),
                closedOffPeriods=periods, longestOffPeriod=max(periods,key=lambda r:r['durationSeconds']) if periods else None,
                anyContinuousOffAtLeast300Seconds=any(p['durationSeconds']>=300 for p in periods),
                unclosedOffSeconds=(end-off['elapsedNs'])/1e9 if off else None,
                serviceStop=stop, pcDisconnects=[r for r in pc if r['kind']=='wifi_disconnected'],
                limitations='on-change callbacks need not have a fixed period; lack of callbacks alone does not prove process death. Raw event age is not verified sampling delay. Short-interval deliveries alone do not prove hardware batching.')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--watch',type=Path,required=True)
    p.add_argument('--pc',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    def read(path):
        return [json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
    result=analyze(read(a.watch),read(a.pc))
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
