"""Loopback browser regression fixture using the actual reminder HTML/JS and request validators.

All requests stay in memory. This process has no watch transport, persistent policy or delivery.
Run with the project Python, then open http://127.0.0.1:8777/ in a browser.
"""
import argparse
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'receiver'))
from reminder_protocol import normalize,TEMPLATES
from reminder_policy import DEFAULTS,validate_settings,violation


def main(port,offline=False):
    state=dict(limits=dict(DEFAULTS),reminders=[],sends=[],queries=[],policyWrites=[])
    lock=threading.RLock()
    def policy():return dict(limits=dict(state['limits']),peerConfirmed=True,syncStatus='测试设置已保存',storageError=None)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,value,mime='application/json; charset=utf-8',code=200):
            data=(json.dumps(value,ensure_ascii=False).encode() if mime.startswith('application/json') else value)
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_GET(self):
            path=urlsplit(self.path).path
            with lock:
                if path=='/':
                    html=(ROOT/'dashboard/static/index.html').read_text(encoding='utf-8')
                    panel=re.search(r'<section\b[^>]*\bid="sendReminder"[^>]*>(.*?)</section>',html,re.S)[1]
                    html='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>提醒表单回归测试</title><link rel="stylesheet" href="/style.css"></head><body><main><p>表单回归测试 · 不连接手表，不修改真实提醒设置</p><section class="card reminder-panel">'+panel+'</section></main><script src="/reminders.js"></script></body></html>'
                    self.reply(html.encode(),'text/html; charset=utf-8')
                elif path in ('/style.css','/reminders.js'):self.reply((ROOT/'dashboard/static'/path[1:]).read_bytes(),'text/css' if path.endswith('.css') else 'text/javascript')
                elif path=='/api/connection':self.reply(dict(connected=False,agentEnabled=None) if offline else dict(connected=True,agentEnabled=True,agentInteraction=dict(peerConfirmed=True),reminderPolicy=policy()))
                elif path=='/api/reminder-policy':self.reply(policy())
                elif path=='/api/reminders':self.reply(dict(templates=TEMPLATES,defaultTemplate='strong',storageError=None,reminders=state['reminders']))
                elif path=='/api/fixture':self.reply(copy.deepcopy(state))
                else:self.send_error(404)
        def do_POST(self):
            path=urlsplit(self.path).path
            if path=='/api/fixture-stop':
                self.reply(dict(stopped=True));threading.Thread(target=self.server.shutdown,daemon=True).start();return
            try:
                length=int(self.headers.get('Content-Length','0'));assert 0<length<=8192
                value=json.loads(self.rfile.read(length))
                with lock:
                    if path=='/api/reminder-policy':
                        state['limits']=validate_settings(value['settings']);state['policyWrites'].append(dict(state['limits']));self.reply(policy())
                    elif path=='/api/reminders' and value['action']=='send':
                        raw=value['reminder'];params=normalize(raw)
                        denied=violation(params,state['limits'])
                        if denied:self.reply(denied);return
                        row=dict(reminderId=params['reminderId'],params=params,status='issued',createdAtMs=1,receiptAgeSeconds=0,receipt=dict(vibrationIssued=params['vibrate'],screenLaunchRequested=params['screen'],soundRequested=params['sound'],soundIssued=params['sound'],soundStatus='requested_via_system' if params['sound'] else 'not_requested'))
                        state['sends'].append(copy.deepcopy(raw));state['reminders'].insert(0,row);self.reply(row)
                    elif path=='/api/reminders' and value['action']=='status':
                        state['queries'].append(value['reminderId']);self.reply(next(row for row in state['reminders'] if row['reminderId']==value['reminderId']))
                    else:self.send_error(404)
            except (AssertionError,KeyError,TypeError,ValueError,StopIteration) as error:self.reply(dict(error=str(error)),code=400)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'Reminder form fixture ready on 127.0.0.1:{server.server_port}; no real watch',flush=True)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8777);parser.add_argument('--offline',action='store_true')
    args=parser.parse_args();main(args.port,args.offline)
