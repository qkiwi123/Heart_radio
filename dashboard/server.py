"""Loopback-only dashboard with optional desktop displays; receiver stays independent."""
import argparse
import csv
import io
from build_identity import server_build
import os
import json
from project_shutdown import shutdown_project
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
import sqlite3
import sys
from urllib.parse import urlsplit, parse_qs
from state import LogReader
from widgets import Widgets
from history import HistoryStore, period
from data_access import DataAccess, TOOLS
from current_export import csv_chunks as current_csv_chunks
from transport_access import transport
from reminders import Reminders
from reminder_sync import ReminderSync
import reminder_tools
from diagnostic_log import Maintenance

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'receiver'))
from control import read_settings, save_settings
sys.path.insert(0, str(ROOT / 'scripts'))
from capability_service import CapabilityService
capability_service = CapabilityService(ROOT)
SETTINGS = ROOT / 'runs' / 'receiver-settings.json'
settings_lock = threading.Lock()
STATIC = Path(__file__).resolve().parent / 'static'
reader = LogReader(ROOT)
lock = threading.Lock()
widgets = Widgets(ROOT)
history = HistoryStore(ROOT)
SERVER_BUILD = server_build(ROOT)


def heart_snapshot():
    def wifi_snapshot():
        with lock:return reader.refresh()
    return transport.heart_snapshot(wifi_snapshot)


data_access = DataAccess(ROOT, heart_snapshot,reminder_policy=transport.reminder_policy_state)
transport.on_gate_closed=data_access.cancel
reminders=Reminders(ROOT,lambda op,params:transport.request(op,params),policy=transport.reminder_limits)
transport.on_reminder_receipt=reminders.observe
reminder_sync=ReminderSync(reminders,transport.reminder_sync_context)

def capability_status():
    if transport.managed is not True:return capability_service.status()
    connection=transport.state();settings=capability_service.duration_settings()
    return dict(running=connection['agentEnabled'],connected=connection['connected'],state='ready' if connection['agentEnabled'] else 'stopped',
                sharedInteraction=True,syncStatus=connection['agentInteraction']['syncStatus'],
                enabledOnStart=False,durationSettings=settings,durationApplied=True,durationSupported=True,durationSeconds=connection['agentInteraction']['activeDurationMs']/1000,
                configuredSeconds=settings['seconds'],remainingSeconds=connection['agentRemainingSeconds'],
                expiresAtMs=time.time_ns()//1_000_000+int(connection['agentRemainingSeconds']*1000) if connection['agentRemainingSeconds'] is not None else None)


class Handler(BaseHTTPRequestHandler):
    def reject(self, status):
        if self.command == 'POST':
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if 0 < length <= 1024: self.rfile.read(length)
            except (OSError, ValueError): pass
        self.send_error(status)

    def local_request(self):
        authority = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') not in (authority, f'localhost:{self.server.server_port}'):
            self.reject(403); return False
        origin = self.headers.get('Origin')
        if origin and origin not in ('http://'+authority, f'http://localhost:{self.server.server_port}'):
            self.reject(403); return False
        return True

    def do_POST(self):
        self.connection.settimeout(5)
        if not self.local_request(): return
        # A cross-origin form cannot send this header or application/json without preflight.
        actions = {'/api/widgets':'widgets', '/api/receiver-settings':'receiver-settings', '/api/tools':'tools',
                   '/api/capability-service':'capability-service', '/api/capability-settings':'capability-settings',
                   '/api/data-settings':'data-settings', '/api/capability-duration':'capability-duration', '/api/connection':'connection','/api/dashboard-stop':'dashboard-stop','/api/reminders':'reminders','/api/reminder-policy':'reminder-policy'}
        if self.path not in actions: self.reject(404); return
        if self.headers.get('X-HeartRadio-Action')!=actions[self.path] or self.headers.get_content_type()!='application/json':
            self.reject(403); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=(8192 if self.path in ('/api/reminders','/api/tools') else 1024 if self.path in ('/api/data-settings','/api/widgets') else 256): raise ValueError('请求过大或为空')
            request=json.loads(self.rfile.read(length))
            if self.path=='/api/reminder-policy':
                if not isinstance(request,dict) or set(request)!={'settings'}:raise ValueError('Invalid reminder settings request')
                value=transport.edit_reminder_policy(request['settings'])
            elif self.path=='/api/reminders':
                if not isinstance(request,dict):raise ValueError('Invalid reminder action')
                if request.get('action')=='send' and set(request)=={'action','reminder'}:value=reminders.send(request['reminder'])
                elif request.get('action')=='status' and set(request)=={'action','reminderId','fresh'}:value=reminders.status(request['reminderId'],request['fresh'])
                else:raise ValueError('Invalid reminder action')
            elif self.path=='/api/dashboard-stop':
                if request!={'action':'stop'}:raise ValueError('Invalid dashboard action')
                data_access.cancel();transport.stop()
                value=dict(status='stopping',pid=os.getpid())
                threading.Thread(target=self.server.shutdown,name='dashboard-shutdown',daemon=True).start()
            elif self.path == '/api/tools':
                if not isinstance(request,dict) or set(request) != {'name','arguments'}: raise ValueError('无效的工具请求')
                value=(reminder_tools.call(reminders,request['name'],request['arguments'])
                       if request['name'] in reminder_tools.NAMES else data_access.call(request['name'],request['arguments']))
            elif self.path == '/api/connection':
                if not isinstance(request,dict):raise ValueError('Invalid connection request')
                if request.get('action') in ('agent_off','stop','shutdown','use_legacy_wifi','start_bluetooth','set_mode'):data_access.cancel()
                value=transport.control(request)
            elif self.path == '/api/receiver-settings':
                with settings_lock: value=save_settings(SETTINGS, request)
            elif self.path == '/api/capability-service':
                if transport.managed is True:
                    if set(request)!={'action'} or request['action'] not in ('start','stop'):raise ValueError('Invalid interaction action')
                    if request['action']=='stop':data_access.cancel()
                    transport.control(dict(action='agent_on' if request['action']=='start' else 'agent_off'));value=capability_status()
                else:value=capability_service.control(request)
            elif self.path == '/api/capability-settings':
                value=capability_service.save(request)
            elif self.path == '/api/data-settings':
                value=data_access.policy.save(request)
            elif self.path == '/api/capability-duration':
                value=capability_service.save_duration(request)
            else:
                if set(request)=={'widget','visible'}:
                    value=widgets.set_visible(request['widget'],request['visible'])
                elif set(request)=={'widget','locked'}:
                    value=widgets.set_locked(request['widget'],request['locked'])
                elif set(request)=={'widget','layout','client_id','sequence'}:
                    value=widgets.save_layout(request['widget'],request['layout'],request['client_id'],request['sequence'])
                else:
                    raise ValueError('无效的小窗控制参数')
            data=json.dumps(value).encode('utf-8')
        except (ValueError,KeyError,TypeError) as error:
            data=json.dumps(dict(error=str(error)),ensure_ascii=False).encode('utf-8')
            self.send_response(400)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        except OSError as error:
            data=json.dumps(dict(error=str(error)),ensure_ascii=False).encode('utf-8')
            self.send_response(503)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
        self.send_response(200)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

    def do_GET(self):
        if not self.local_request(): return
        try:
            request_path=urlsplit(self.path).path
            if request_path=='/api/version':
                data=json.dumps(dict(version='1.12.0-dashboard',watchVersion='1.11.0-state',mcpPackageVersion='0.7.0',serverBuild=SERVER_BUILD,pid=os.getpid())).encode();mime='application/json; charset=utf-8'
            elif request_path=='/api/reminders/history':
                try:
                    query=parse_qs(urlsplit(self.path).query,keep_blank_values=True,max_num_fields=4)
                    if any(len(values)!=1 for values in query.values()):raise ValueError('Repeated parameter')
                    value=reminders.history({key:values[0] for key,values in query.items()})
                except ValueError as error:
                    self.send_error(400,str(error));return
                value['sync']=reminder_sync.state()
                data=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8');mime='application/json; charset=utf-8'
            elif request_path=='/api/reminders':
                data=json.dumps(reminders.state(),ensure_ascii=False,allow_nan=False).encode('utf-8');mime='application/json; charset=utf-8'
            elif request_path=='/api/reminder-policy':
                data=json.dumps(transport.reminder_policy_state(),ensure_ascii=False,allow_nan=False).encode('utf-8');mime='application/json; charset=utf-8'
            elif request_path == '/api/connection':
                data=json.dumps(transport.state(),ensure_ascii=False).encode('utf-8');mime='application/json; charset=utf-8'
            elif request_path == '/api/data':
                data=json.dumps(data_access.cached(),ensure_ascii=False,allow_nan=False).encode('utf-8'); mime='application/json; charset=utf-8'
            elif request_path == '/api/data-settings':
                data=json.dumps(data_access.policy.read(),ensure_ascii=False,allow_nan=False).encode('utf-8'); mime='application/json; charset=utf-8'
            elif request_path == '/api/capability-duration':
                data=json.dumps(capability_service.duration_settings(),ensure_ascii=False).encode('utf-8'); mime='application/json; charset=utf-8'
            elif request_path == '/api/capability-service':
                data=json.dumps(capability_status(),ensure_ascii=False,allow_nan=False).encode('utf-8'); mime='application/json; charset=utf-8'
            elif request_path == '/api/tools':
                data=json.dumps(dict(tools=TOOLS),ensure_ascii=False).encode('utf-8'); mime='application/json; charset=utf-8'
            elif request_path == '/api/receiver-settings':
                with settings_lock: settings=read_settings(SETTINGS)
                data=json.dumps(settings).encode('utf-8'); mime='application/json; charset=utf-8'
            elif request_path in ('/api/history','/api/history/catalog','/api/history/export.csv'):
                try:
                    query=parse_qs(urlsplit(self.path).query,keep_blank_values=True,max_num_fields=8)
                    if any(len(values)!=1 for values in query.values()): raise ValueError('Repeated parameter')
                    params={key:values[0] for key,values in query.items()}
                    if request_path=='/api/history/catalog':
                        if params: raise ValueError('Catalog accepts no parameters')
                        result=history.catalog()
                    elif request_path=='/api/history':
                        result=history.query(params)
                    else:
                        label=period(params)[3]
                        chunks=history.csv_chunks(params)
                        first=next(chunks)  # Validate the selection before sending HTTP headers.
                        self.send_response(200)
                        self.send_header('Content-Type','text/csv; charset=utf-8')
                        self.send_header('Cache-Control','no-store')
                        self.send_header('X-Content-Type-Options','nosniff')
                        self.send_header('Content-Disposition',f'attachment; filename="heartradio-{label}.csv"')
                        self.end_headers()
                        try:
                            self.wfile.write(first)
                            for chunk in chunks: self.wfile.write(chunk)
                        except (BrokenPipeError,ConnectionResetError): pass
                        finally: chunks.close()
                        return
                except (ValueError,KeyError,TypeError): self.send_error(400,'Invalid history selection'); return
                data=json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8')
                mime='application/json; charset=utf-8'
            elif request_path in ('/api/state','/api/widget-state'):
                state=heart_snapshot()
                state['widgets']=widgets.status()
                if request_path=='/api/state':
                    with settings_lock: state['receiverSettings']=read_settings(SETTINGS)
                if request_path=='/api/widget-state':
                    end=state.get('endedMs') or state['nowMs']
                    state['points']=[p for p in state['points'] if p['timeMs']>=end-600_000]
                data = json.dumps(state,ensure_ascii=False).encode('utf-8')
                mime = 'application/json; charset=utf-8'
            elif request_path == '/api/export.csv':
                export_rfcomm=transport.mode=='rfcomm'
                legacy_bluetooth=False
                if transport.managed is True and transport.mode!='wifi' and isinstance(transport.run_id,str) and transport.run_id:
                    log_path=transport.root/'runs'/transport.run_id/'pc.jsonl'
                elif export_rfcomm:
                    log_path=Path(transport.probe.stream.name) if transport.probe else None
                    legacy_bluetooth=True
                else:
                    with lock:
                        reader.refresh(); log_path = reader.path
                chunks=current_csv_chunks(log_path,legacy_bluetooth)
                first=next(chunks)
                self.send_response(200)
                self.send_header('Content-Type','text/csv; charset=utf-8')
                self.send_header('Content-Disposition','attachment; filename="heartradio-events.csv"')
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                try:
                    self.wfile.write(first)
                    for chunk in chunks:self.wfile.write(chunk)
                except (BrokenPipeError,ConnectionResetError):pass
                finally:chunks.close()
                return
            else:
                files={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/polish.css':'polish.css'}
                files['/history.js']='history.js'
                files['/data.js']='data.js'
                files['/connection.js']='connection.js'
                files['/reminders.js']='reminders.js'
                files['/reminder-history.js']='reminder-history.js'
                files['/navigation.js']='navigation.js'
                files['/pages.css']='pages.css'
                name=files.get(request_path)
                if not name: self.send_error(404); return
                data=(STATIC/name).read_bytes()
                mime={'html':'text/html','js':'text/javascript','css':'text/css'}[name.rsplit('.',1)[1]]+'; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if request_path == '/api/export.csv': self.send_header('Content-Disposition','attachment; filename="heartradio-events.csv"')
            self.end_headers(); self.wfile.write(data)
        except (OSError,ValueError,KeyError,sqlite3.Error):
            self.send_error(503,'Local log is temporarily unavailable')
    def log_message(self,*args): pass


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=8766);a=p.parse_args()
    widgets.port=a.port
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    maintenance=Maintenance(ROOT);maintenance.start()
    def offline_shutdown():
        shutdown_project(ROOT,transport,data_access,capability_service,widgets,server)
    transport.on_offline_timeout=offline_shutdown
    monitor_stop=threading.Event()
    def monitor_project():
        while not monitor_stop.wait(1):
            transport._check_offline_timeout();reminder_sync.tick()
    threading.Thread(target=monitor_project,name='project-idle-monitor',daemon=True).start()
    print(f'HeartRadio dashboard http://127.0.0.1:{a.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        monitor_stop.set()
        reminder_sync.stop()
        maintenance.stop()
        try:transport.stop()
        finally:widgets.close();server.server_close();data_access.close()
