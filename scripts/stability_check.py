"""Record a real worn phase with optional low-frequency stdio MCP calls; no ADB."""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request

from coexistence_check import read_rows, summarize
from resource_data import Activity, read_json, receiver_path

ROOT=Path(__file__).resolve().parents[1]


class MCPClient:
    def __init__(self, entry):
        self.process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-u',str(entry)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        self.responses=queue.Queue(); self.serial=0
        def read():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)
        self.reader=threading.Thread(target=read,daemon=True); self.reader.start()
        try:
            self.call('initialize',dict(protocolVersion='2025-06-18'))
            self.write(dict(jsonrpc='2.0',method='notifications/initialized'))
            tools=self.call('tools/list',{})['tools']
            if len(tools)!=6:raise RuntimeError('Unexpected MCP tool count')
        except BaseException:
            self.close(); raise

    def write(self,value):
        self.process.stdin.write((json.dumps(value)+'\n').encode());self.process.stdin.flush()

    def call(self,method,params):
        self.serial+=1
        self.write(dict(jsonrpc='2.0',id=self.serial,method=method,params=params))
        try:line=self.responses.get(timeout=60)
        except queue.Empty:raise RuntimeError('MCP response deadline exceeded')
        if line is None:raise RuntimeError('MCP adapter exited')
        value=json.loads(line)
        if value.get('id')!=self.serial or 'error' in value:raise RuntimeError('MCP response mismatch')
        return value['result']

    def tool(self,name,arguments=None):
        result=self.call('tools/call',dict(name=name,arguments=arguments or {}))
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:self.process.stdin.close()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self.process.stdout.close();self.process.stderr.close()


def bridge():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8768/state',timeout=2) as response:value=json.load(response)
        return {key:value.get(key) for key in ('connected','instanceId','connectionSerial','lastDisconnectReason',
            'serverRemainingSeconds','watchLifecycle','watchReportAgeSeconds')}
    except OSError:return dict(connected=False,status='bridge_unavailable')


def phase(args):
    output=args.output.resolve()
    if not output.is_relative_to((ROOT/'evidence/stability').resolve()):raise ValueError('Use project stability evidence directory')
    output.mkdir(parents=True,exist_ok=True)
    path=output/(args.name+'.json')
    if path.exists():raise ValueError('Choose a new phase name; existing evidence is preserved')
    activity=Activity(ROOT)
    if not activity.update(prime=True)['freshValid']:raise RuntimeError('No fresh valid heart data; phase has not begun')
    connection=bridge()
    if args.standby and not connection.get('connected'):raise RuntimeError('Standby not connected')
    client=MCPClient(args.mcp_entry) if args.sample_every else None
    meta=read_json(ROOT/'runs/active.json'); log=receiver_path(ROOT,meta)
    begin=time.monotonic_ns(); until=begin+args.seconds*1_000_000_000
    record=dict(phase=args.name,beginWallMs=time.time_ns()//1_000_000,beginMonotonicNs=begin,
        requestedSeconds=args.seconds,adbUsed=False,sourceAgeVerified=False,heartLog=str(log),
        mcpEntry=str(args.mcp_entry) if client else None,sampleEverySeconds=args.sample_every,
        connectionAtStart=connection,requests=[],observations=[],complete=False)
    def save():path.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    save(); next_progress=begin; next_sample=begin+10_000_000_000; index=0
    try:
        while time.monotonic_ns()<until:
            now=time.monotonic_ns()
            if now>=next_progress:
                snapshot=activity.update(); state=bridge()
                observation=dict(monotonicNs=now,heart=snapshot,bridge=state)
                record['observations'].append(observation);save()
                print(json.dumps(dict(event='phase_progress',phase=args.name,elapsedSeconds=(now-begin)/1e9,
                    freshHeart=snapshot['freshValid'],validEventAgeSeconds=snapshot['validEventAgeSeconds'],
                    standbyConnected=state.get('connected')),ensure_ascii=False),flush=True)
                activity.reset_counts();next_progress=now+30_000_000_000
            if client and now>=next_sample and until-now>15_000_000_000:
                choice=index%3
                name='measure_pressure' if choice==0 else 'measure_motion'
                arguments=dict(seconds=4,fresh=True)
                if choice:arguments['kind']='accelerometer' if choice==1 else 'gyroscope'
                sample_begin=time.monotonic_ns()
                result=client.tool(name,arguments);index+=1
                record['requests'].append(dict(name=name,arguments=arguments,result=result,
                    beginMonotonicNs=sample_begin,endMonotonicNs=time.monotonic_ns()))
                save()
                if result.get('ok') is not True or result.get('listenersReleased') is not True or result.get('requestWakeLockHeldAtResult') is not False:
                    raise RuntimeError('MCP measurement did not confirm valid data and cleanup; new requests stopped')
                print(json.dumps(dict(event='mcp_measurement',tool=name,status=result['status'],callbacks=result['callbacks']),ensure_ascii=False),flush=True)
                next_sample=sample_begin+args.sample_every*1_000_000_000
            if read_json(ROOT/'runs/active.json')['runId']!=meta['runId']:
                raise RuntimeError('Receiver run changed; do not combine unrelated phases')
            time.sleep(.25)
        record['complete']=True
    finally:
        end=time.monotonic_ns();record.update(endWallMs=time.time_ns()//1_000_000,endMonotonicNs=end,
            summary=summarize(read_rows(log),begin,end),connectionAtEnd=bridge())
        rows=read_rows(log)
        selected=[row for row in rows if begin<=row.get('pcMonotonicNs',0)<=end]
        previous=[row for row in rows if row.get('pcMonotonicNs',0)<begin and row.get('kind')=='packet' and
            row.get('packet',{}).get('kind')=='sensor_event' and row['packet'].get('valid') is True]
        if previous:selected.insert(0,previous[-1])
        (output/(args.name+'-heart.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in selected),encoding='utf-8')
        inside=[row['packet']['watchReceiptMs'] for row in selected if row.get('kind')=='packet' and
                begin<=row.get('pcMonotonicNs',0)<=end and row.get('packet',{}).get('watchReceiptMs')]
        record['watchReceiptWallBoundsMs']=[min(inside),max(inside)] if inside else None
        save()
        if client:client.close()
        print(json.dumps(dict(event='phase_end',phase=args.name,complete=record['complete'],summary=record['summary']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name',required=True)
    parser.add_argument('--seconds',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--standby',action='store_true')
    parser.add_argument('--sample-every',type=int,default=0)
    parser.add_argument('--mcp-entry',type=Path,default=ROOT/'agent/mcp_server.py')
    args=parser.parse_args()
    if not re.fullmatch('[A-Za-z0-9_-]{1,64}',args.name):parser.error('Invalid phase name')
    if not 30<=args.seconds<=3600:parser.error('Phase duration must be 30..3600 seconds')
    if args.sample_every and not 30<=args.sample_every<=300:parser.error('Sample interval must be 30..300 seconds')
    sys.stdout.reconfigure(encoding='utf-8');phase(args)
