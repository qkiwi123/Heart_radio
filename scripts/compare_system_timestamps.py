"""Compare only the HEART_RATE dump with this app's exported log (no other apps)."""
import argparse
import json
import re
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('system_dump', type=Path)
p.add_argument('app_log', type=Path)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
events = [json.loads(line) for line in a.app_log.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
by_timestamp = {e['eventNs']: e for e in events if e.get('kind') == 'event'}
rows = []
for ts, wall in re.findall(r'\(ts=([\d.]+), wall=([\d:.]+)\)', a.system_dump.read_text(encoding='utf-8-sig')):
    ns = int(Decimal(ts) * 1_000_000_000)
    event = by_timestamp.get(ns)
    app_wall = (datetime.fromtimestamp(event['wallMs'] / 1000, timezone(timedelta(hours=8)))
                .strftime('%H:%M:%S.%f')[:-3]) if event else None
    rows.append({'systemEventNs': ns, 'systemWall': wall, 'appTimestampExactMatch': event is not None,
                 'appWall': app_wall, 'appArrivalNs': event['arrivalNs'] if event else None})
result = {'systemDump': str(a.system_dump), 'appLog': str(a.app_log),
          'count': len(rows), 'exactMatches': sum(r['appTimestampExactMatch'] for r in rows),
          'systemTimestampStepsNs': [y['systemEventNs']-x['systemEventNs'] for x,y in zip(rows, rows[1:])],
          'rows': rows,
          'interpretation': 'Matching raw timestamps demonstrate that this pattern existed in SensorService before app delivery. This does not identify the vendor component or prove actual sample latency.'}
a.output.parent.mkdir(parents=True, exist_ok=True)
a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(f"System events: {len(rows)}; exact timestamp matches: {result['exactMatches']}")
