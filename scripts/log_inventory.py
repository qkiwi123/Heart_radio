"""Read-only log inventory; shows managed diagnostic/console archives without cleaning anything."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'receiver'))
from diagnostic_log import managed_files,policy
from log_retention import console_rows


def inventory(root=ROOT):
    root=Path(root).resolve();runs=root/'runs'
    owned={row['path'].resolve() for row in managed_files(root)}
    consoles={row['path'].resolve() for row in console_rows(root)}
    rows=[]
    if runs.exists():
        for path in runs.rglob('*'):
            if any(part.startswith(('maintenance-','audit-')) for part in path.relative_to(runs).parts):continue
            if not path.is_file() or path.is_symlink() or path.resolve()!=path.absolute():continue
            name=path.relative_to(root).as_posix()
            if path.name.endswith(('.jsonl','.jsonl.gz')):
                category=('business_history' if path.name=='pc.jsonl' and len(path.relative_to(runs).parts)==2
                          else 'managed_console' if path.resolve() in consoles else 'managed_closed_diagnostic' if path.resolve() in owned
                          else 'legacy_or_active_diagnostic_requires_review')
            elif path.name.endswith(('.stdout.txt','.stderr.txt')) or path.name in ('stdout.txt','stderr.txt'):
                category='managed_console' if path.resolve() in consoles else 'process_console_log_requires_review'
            else:continue
            try:stat=path.stat()
            except FileNotFoundError:continue  # A new managed file may rotate during a read-only scan.
            rows.append(dict(path=name,bytes=stat.st_size,modifiedAtUtc=datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat(),
                             category=category,action='retained; read-only inventory'))
    return dict(schemaVersion=1,readOnly=True,deletedFiles=0,compressedFiles=0,
                policy=policy(root),files=sorted(rows,key=lambda r:r['bytes'],reverse=True),
                totalBytes=sum(row['bytes'] for row in rows),
                note='Explicitly enrolled closed diagnostics and console archives are managed; business history, settings, reminders, evidence and maintenance-/audit- fixtures are excluded from cleanup.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path)
    args=parser.parse_args();value=inventory()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(readOnly=True,files=len(value['files']),bytes=value['totalBytes'],deletedFiles=0,output=str(args.output) if args.output else None),ensure_ascii=False))
