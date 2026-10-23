"""Preview or explicitly run bounded project-log maintenance. No sensor or reminder operations."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'receiver'))
from diagnostic_log import atomic_json
from log_retention import maintain_project,settings,record_report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Without this flag only preview')
    parser.add_argument('--max-batches',type=int,default=1)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 1<=args.max_batches<=1000:parser.error('max-batches must be 1..1000')
    if args.apply:settings(ROOT,create=True)
    reports=[]
    for _ in range(args.max_batches if args.apply else 1):
        report=maintain_project(ROOT,dry_run=not args.apply);reports.append(report)
        if not report.get('moreWork') or not report['actions']:break
    value=dict(completedAtUtc=datetime.now(timezone.utc).isoformat(),applied=args.apply,batches=len(reports),
               actions=[a for r in reports for a in r['actions']],lastReport=reports[-1])
    if args.output:atomic_json(args.output,value)
    if args.apply:record_report(ROOT,value)
    print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
