"""Stream the existing current-session CSV without loading a whole recording into RAM."""
import csv
import io
import json
from itertools import chain
from pathlib import Path
from diagnostic_log import diagnostic_lines

HEADER = ['watchReceiptMs_approximate','pcReceiptMs','bpm','accuracy','valid','session','sequence']


def csv_chunks(path, legacy_bluetooth=False):
    text=io.StringIO(newline='');writer=csv.writer(text)
    path=Path(path) if path is not None else None
    lines=iter(()) if path is None else diagnostic_lines(path) if legacy_bluetooth else path.open(encoding='utf-8') if path.exists() else iter(())
    try:
        first=next(lines,None)  # File access errors precede HTTP response headers.
        writer.writerow(HEADER)
        yield ('\ufeff'+text.getvalue()).encode('utf-8')
        text.seek(0);text.truncate(0)
        for line in chain((first,) if first is not None else (),lines):
            # The history index also excludes an unfinished trailing write.
            if not line.endswith('\n'): continue
            try:
                row=json.loads(line)
                if not isinstance(row,dict):continue
                if row.get('kind')=='packet' and row.get('packet',{}).get('kind')=='sensor_event':
                    p=row['packet'];writer.writerow([p['watchReceiptMs'],row['pcReceiptMs'],p['bpm'],p['accuracy'],p['valid'],p['session'],p['sequence']])
                elif legacy_bluetooth and row.get('kind')=='watch_message' and row.get('message',{}).get('event')=='heart':
                    p=row['message'];writer.writerow([p.get('watchReceiptMs'),row['pcReceiptMs'],p.get('bpm'),p.get('accuracy'),p.get('valid'),p.get('heartAttemptId'),p.get('sequence')])
            except (ValueError,KeyError,TypeError,AttributeError): continue
            if text.tell()>=16384:
                yield text.getvalue().encode('utf-8');text.seek(0);text.truncate(0)
        if text.tell():yield text.getvalue().encode('utf-8')
    finally:
        close=getattr(lines,'close',None)
        if close: close()
