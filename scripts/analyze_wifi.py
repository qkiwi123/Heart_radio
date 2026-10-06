"""Match explicit project logs; distinguish link status from real sensor events."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--watch', type=Path, required=True)
p.add_argument('--pc', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
def read(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
watch, pc = read(a.watch), read(a.pc)
packets = [r for r in pc if r['kind'] == 'packet']
sent = [r for r in watch if r['kind'] == 'wifi_packet']
acked = [r for r in watch if r['kind'] == 'wifi_ack']
intervals = [(b['pcMonotonicNs'] - c['pcMonotonicNs']) / 1e9 for c, b in zip(packets, packets[1:])]
valid = [r for r in packets if r['packet']['kind'] == 'sensor_event' and r['packet']['valid']]
heartbeats = [r for r in packets if r['packet']['kind'] == 'heartbeat']
hb_intervals = [(b['pcMonotonicNs'] - c['pcMonotonicNs']) / 1e9 for c, b in zip(heartbeats, heartbeats[1:])]
summary = dict(watchFile=str(a.watch), pcFile=str(a.pc),
    watchKinds=dict(Counter(r['kind'] for r in watch)), pcKinds=dict(Counter(r['kind'] for r in pc)),
    receivedPackets=len(packets), queuedPackets=len(sent), acknowledgedPackets=len(acked),
    receivedValidSensorEvents=len(valid),
    allReceivedMatchWatch=all(r['hex'] in {s['hex'] for s in sent} for r in packets) if packets else None,
    queuedNotReceived=sum((Counter(r['hex'] for r in sent)-Counter(r['hex'] for r in packets)).values()),
    sequenceGaps=[r['sequenceStep'] for r in packets if r.get('sequenceStep') not in (None, 1)],
    packetSpanSeconds=(packets[-1]['pcMonotonicNs'] - packets[0]['pcMonotonicNs']) / 1e9 if len(packets)>1 else None,
    intervalMedianSeconds=statistics.median(intervals) if intervals else None,
    intervalMaxSeconds=max(intervals) if intervals else None,
    heartbeatIntervalMaxSeconds=max(hb_intervals) if hb_intervals else None,
    bpmRange=[min(r['packet']['bpm'] for r in valid),max(r['packet']['bpm'] for r in valid)] if valid else None,
    watchErrors=[r for r in watch if r['kind']=='wifi_error'],
    stopped=any(r['kind']=='session_stop' for r in watch),
    limitation='Reception timestamps are separate clock domains; no one-way latency claim. Bench packets do not prove valid heart rate.')
a.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
