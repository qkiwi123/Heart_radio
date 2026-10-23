"""Manual/automatic connection routing behind the shared data service. Never starts watch HR."""
import json
from pathlib import Path
import sys
import threading
import time
import urllib.request
import uuid
import os
import subprocess
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_URL = 'http://127.0.0.1:8768'
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
sys.path.insert(0, str(ROOT / 'receiver'))
from rfcomm_probe import Probe
from state import State
from transport_policy import MODES,allowed,choose
from control import Deadline,read_settings,atomic_json
from interaction_sync import InteractionStore, validate_wire
from offline_timeout import OfflineTimeout
from reminder_policy import PolicyStore,PolicyRegister,validate_wire as validate_policy_wire,validate_settings as validate_reminder_settings,stricter as stricter_limits,violation as policy_violation


class HeartCache:
    def __init__(self, record=None):
        self.lock = threading.RLock()
        self.state = State()
        self.started = time.monotonic()
        self.state.consume(dict(kind='server_start',pcReceiptMs=time.time_ns()//1_000_000,pcMonotonicNs=time.monotonic_ns()))
        self.connected = False
        self.receiving = True
        self.control = {}
        self.last_sequence = None
        self.control_at = None
        self.record=record

    def consume(self,row):
        self.state.consume(row)
        if self.record:self.record(row)

    def observe(self, row):
        with self.lock:
            now = dict(pcReceiptMs=time.time_ns()//1_000_000, pcMonotonicNs=time.monotonic_ns())
            event = row.get('event')
            if event == 'hello':
                self.connected = True
                self.last_sequence = None
                self.consume(dict(now, kind='wifi_connected', target='paired_bluetooth'))
            elif event == 'disconnected':
                self.connected = False
                self.control = {}
                self.consume(dict(now, kind='wifi_disconnected'))
            if 'heartRunning' in row:
                self.control = dict(row)
                self.control_at = time.monotonic()
                # A passive state packet invalidates old values, but never creates a sample.
                if not row.get('heartRunning') or row.get('heartState') != 'ready':
                    if self.state.current_allowed:self.state.segment+=1
                    self.state.current_allowed = False
                    self.state.invalid_reason = row.get('heartStateMessage', '等待心率数据')
            if event == 'heart' and self.receiving:
                if row.get('heartAttemptId') != self.state.session:self.last_sequence=None
                sequence = row.get('sequence')
                step = sequence-self.last_sequence if self.last_sequence is not None and isinstance(sequence,int) else None
                self.last_sequence = sequence
                packet = dict(kind='sensor_event', session=row.get('heartAttemptId'), valid=row.get('valid') is True,
                              sequence=sequence,
                              running=True, registered=True, bpm=row.get('bpm'), accuracy=row.get('accuracy'),
                              watchReceiptMs=row.get('watchReceiptMs'))
                self.consume(dict(now, kind='packet', packet=packet, sequenceStep=step))

    def set_receiving(self, enabled):
        with self.lock:
            self.receiving = enabled
            self.state.segment+=1
            self.state.current_allowed = False
            self.state.last_event = None
            self.last_sequence = None

    def snapshot(self):
        with self.lock:
            value = self.state.snapshot()
            valid = (self.connected and self.receiving and self.state.current_allowed
                     and value['eventAgeSeconds'] is not None and value['eventAgeSeconds'] <= 30)
            value.update(connected=self.connected, valid=valid,
                         bpm=self.state.last_event['packet']['bpm'] if valid else None,
                         source='continuous_heart_cache_rfcomm', sourceAgeVerified=False,
                         receiverStopped=not self.receiving,
                         heartDiagnostic=self.control.get('heartDiagnostic'),
                         transport='rfcomm', runId='bluetooth',durationSeconds=0,elapsedSeconds=time.monotonic()-self.started)
            if not self.receiving: value['status']='电脑心率接收已关闭'
            elif not self.connected: value['status']='蓝牙连接未建立'
            elif valid: value['status']='正在接收心率'
            elif value['eventAgeSeconds'] is not None and value['eventAgeSeconds'] > 30: value['status']='超过30秒无新心率'
            else: value['status']=self.state.invalid_reason
            return value


class TransportAccess:
    def __init__(self, root=ROOT):
        self.root=Path(root)
        self.mode='wifi'  # Existing launchers and callers retain their original route.
        self.probe=None
        self.thread=None
        self.agent_enabled=False
        self.cache=HeartCache()
        self.lock=threading.RLock()
        self.route_lock=threading.RLock()
        self.managed=False
        self.preference_path=self.root/'runs/connection-settings.json'
        self.configured_mode='auto_bluetooth'
        self.enabled_on_start=True
        if self.preference_path.exists():
            prefs=json.loads(self.preference_path.read_text(encoding='utf-8'))
            if prefs.get('mode') not in MODES or type(prefs.get('enabledOnStart')) is not bool:raise ValueError('Connection settings invalid')
            self.configured_mode=prefs['mode'];self.enabled_on_start=prefs['enabledOnStart']
        self.manager_thread=None;self.manager_stop=threading.Event()
        self.selected=None;self.selection_id=None;self.selected_key=None;self.selected_ms=0
        self.waiting_since=time.monotonic();self.message='连接尚未开启'
        self.candidates={};self.local=threading.local();self.sample_routes={}
        self.log_lock=threading.RLock();self.normalized=None;self.run_id=None
        self.receive_limit=None;self.agent_limit=None;self.backing_error=None
        self.prepare_thread=None;self.on_gate_closed=None
        self.wifi_owned=False
        self.prepare_requested=threading.Event();self.wifi_heart_session=None
        self.interaction=None;self.duration_revision=None
        self.sync_fingerprint=None;self.sync_token=None;self.sync_at=0;self.sync_error=None;self.peer_enabled=None
        self.on_reminder_receipt=None
        self.reminder_policy=None;self.policy_token=None;self.policy_fingerprint=None;self.policy_sync_at=0
        self.broadcast=None;self.broadcast_token=None;self.broadcast_version=None;self.broadcast_sync_at=0
        self.broadcast_peer={};self.broadcast_error=None
        self.offline=OfflineTimeout()
        self.offline.start(time.monotonic())
        self.on_offline_timeout=None
        self.session_path=self.root/'runs/project-session.json'
        self.session=dict(suspended=False,reason=None,timeoutSeconds=600)
        if self.session_path.exists():
            self.session=json.loads(self.session_path.read_text(encoding='utf-8'))
            if type(self.session.get('suspended')) is not bool:raise ValueError('Invalid project session')
            if self.session['suspended']:self.cache.receiving=self.session.get('heartReceiveEnabled',True)
            if self.session['suspended']:self.managed=True;self.mode='none'

    def _ensure_broadcast(self):
        if self.broadcast is None:self.broadcast=InteractionStore(self.root/'runs/heart-broadcast.json',label='心率广播')

    def _broadcast_key(self):
        return tuple(sorted(e['id'] for e in self.broadcast.register.active()))

    def _broadcast_receive_change(self,before):
        enabled=self.broadcast.enabled
        if self._broadcast_key()==before:return
        self.cache.set_receiving(enabled)
        if enabled:
            settings=read_settings(self.root/'runs/receiver-settings.json')
            self.receive_limit=Deadline(settings['seconds'],settings);self.prepare_requested.set()
        else:self._stop_wifi_reception()

    def _edit_broadcast(self,enabled):
        with self.lock:
            self._ensure_broadcast()
            if enabled and self.broadcast.error:raise ValueError('请先关闭心率广播以修复保存状态')
            before=self._broadcast_key()
            self.broadcast.transact(lambda r:r.edit(enabled,force=enabled))
            self.broadcast_version=None;self.broadcast_error=None
            self._broadcast_receive_change(before)

    def _broadcast_state(self):
        with self.lock:
            self._ensure_broadcast();store=self.broadcast
            applied=(self.broadcast_peer.get('running') is True and self.broadcast_peer.get('registered') is True) if store.enabled else self.broadcast_peer.get('running') is False
            confirmed=(not store.error and self.selected is not None and self.broadcast_token==self._token()
                       and self.broadcast_version==store.register.fingerprint() and time.monotonic()-self.broadcast_sync_at<5 and applied)
            error=store.error or self.broadcast_error
            return dict(enabled=store.enabled,desiredEnabled=store.register.enabled,peerConfirmed=bool(confirmed),pending=not confirmed,
                        running=self.broadcast_peer.get('running') if self.broadcast_token==self._token() else None,
                        registered=self.broadcast_peer.get('registered') if self.broadcast_token==self._token() else None,
                        version=store.register.fingerprint(),storageError=store.error,syncError=self.broadcast_error,
                        effectError=self.broadcast_peer.get('effectError',''),syncSupported=True,
                        syncStatus=error or self.broadcast_peer.get('effectError') or ('两端已同步' if confirmed else '本端已设置，待同步'))

    def _sync_broadcast(self,token):
        with self.lock:
            self._ensure_broadcast()
            if not token[0] or token!=self._token() or self.broadcast.error:return
            identity=uuid.uuid4().hex
            params=dict(selectionId=token[2],syncId=identity,state=self.broadcast.register.wire())
        try:reply=self._raw(token[0],'sync_broadcast',params)
        except (OSError,ValueError):return
        with self.lock:
            if token!=self._token():return
            if reply.get('status')!='ok' or reply.get('syncId')!=identity:
                self.broadcast_error='等待手表支持并确认心率同步';return
            try:
                from interaction_sync import InteractionRegister
                peer=reply['broadcast'];state=validate_wire(peer['register'])
                if any(e and e['ttlMs'] is not None for e in state.values()):raise ValueError('Invalid broadcast lease')
                check=InteractionRegister('pc');check.merge(state)
                lost=(state.get('pc') or {}).get('seq',0)>(self.broadcast.register.events['pc'] or {}).get('seq',0)
                before=self._broadcast_key()
                def merge(register):
                    register.merge(state)
                    if lost:register.edit(False)
                self.broadcast.transact(merge);self._broadcast_receive_change(before)
                self.broadcast_token=token;self.broadcast_version=check.fingerprint();self.broadcast_sync_at=time.monotonic()
                self.broadcast_peer=peer;self.broadcast_error=None
            except (OSError,ValueError,KeyError,TypeError):self.broadcast_error='心率广播同步尚未确认'

    def _ensure_interaction(self):
        if self.interaction is None:
            self.interaction=InteractionStore(self.root/'runs/agent-interaction.json')
            self.duration_revision=self._duration()['revision']
            self.agent_enabled=self.interaction.enabled and not self.session['suspended']

    def _ensure_reminder_policy(self):
        if self.reminder_policy is None:self.reminder_policy=PolicyStore(self.root/'runs/reminder-policy.json')

    def reminder_limits(self):
        with self.lock:self._ensure_reminder_policy();return self.reminder_policy.register.effective()

    def reminder_policy_state(self):
        with self.lock:
            self._ensure_reminder_policy();policy=self.reminder_policy
            confirmed=(not policy.error and self.selected is not None and self.policy_token==self._token()
                       and self.policy_fingerprint==policy.register.fingerprint() and time.monotonic()-self.policy_sync_at<5)
            return dict(limits=policy.register.effective(),state=policy.register.wire(),version=policy.register.fingerprint(),
                        peerConfirmed=bool(confirmed),storageError=policy.error,
                        syncStatus=policy.error or ('两端已同步' if confirmed else '本端已设置，待同步'))

    def edit_reminder_policy(self,value):
        value=validate_reminder_settings(value)
        with self.lock:
            self._ensure_reminder_policy();self.reminder_policy.transact(lambda r:r.edit(value));self.policy_fingerprint=None
        return self.reminder_policy_state()

    def _sync_reminder_policy(self,token):
        with self.lock:
            self._ensure_reminder_policy()
            if token!=self._token() or self.reminder_policy.error:return
            identity=uuid.uuid4().hex
            params=dict(selectionId=token[2],syncId=identity,state=self.reminder_policy.register.wire())
        try:reply=self._raw(token[0],'sync_reminder_policy',params)
        except (OSError,ValueError):return
        with self.lock:
            if token!=self._token() or reply.get('status')!='ok' or reply.get('syncId')!=identity:return
            try:
                wire=validate_policy_wire(reply['policy']['state']);remote=PolicyRegister('pc');remote.merge(wire)
                lost=(wire.get('pc') or {}).get('seq',0)>(self.reminder_policy.register.events['pc'] or {}).get('seq',0)
                before=self.reminder_policy.register.effective()
                def merge(register):
                    register.merge(wire)
                    if lost:register.edit(stricter_limits(before,register.effective()))
                self.reminder_policy.transact(merge)
                self.policy_token=token;self.policy_sync_at=time.monotonic()
                self.policy_fingerprint=remote.fingerprint()
            except (OSError,ValueError,KeyError,TypeError):self.policy_fingerprint=None

    def _duration(self):
        sys.path.insert(0,str(self.root/'scripts'))
        from capability_duration import read_duration
        return read_duration(self.root)

    def _gate_change(self,operation,repair=False):
        with self.lock:
            self._ensure_interaction();before=self.agent_enabled;fingerprint=self.interaction.register.fingerprint()
            if self.interaction.error and not repair:return
            try:self.interaction.transact(operation)
            finally:
                self.agent_enabled=self.interaction.enabled and not self.session['suspended']
                if fingerprint!=self.interaction.register.fingerprint() or self.interaction.error:self.sync_fingerprint=None
                if before and not self.agent_enabled and self.on_gate_closed:
                    threading.Thread(target=self.on_gate_closed,name='interaction-close',daemon=True).start()

    def _edit_interaction(self,enabled,force=False):
        self._ensure_interaction()
        if enabled and self.interaction.error:raise ValueError('请先关闭交互以修复保存状态')
        settings=self._duration();self.duration_revision=settings['revision']
        ttl=int(settings['seconds']*1000) or None
        self._gate_change(lambda r:r.edit(enabled,ttl,force),repair=not enabled)

    def _sync_confirmed(self):
        return (self.interaction is not None and self.interaction.error is None and self.selected is not None
                and self.sync_token==self._token() and self.sync_fingerprint==self.interaction.register.fingerprint()
                and time.monotonic()-self.sync_at<5)

    def _sync_once(self):
        self._sync_broadcast(self._token())
        with self.lock:
            self._ensure_interaction();token=self._token()
            if not token[0] or self.interaction.error:return
            identity=uuid.uuid4().hex
            params=dict(selectionId=token[2],syncId=identity,state=self.interaction.register.wire())
        try:reply=self._raw(token[0],'sync_interaction',params)
        except (OSError,ValueError):
            with self.lock:self.sync_fingerprint=None;self.sync_error='sync_transport_error'
            return
        with self.lock:
            if token!=self._token():return
            if reply.get('status')!='ok' or reply.get('syncId')!=identity:
                self.sync_fingerprint=None;self.sync_error=reply.get('status','invalid_sync_reply');return
            remote=reply.get('interaction',{})
            try:
                state=validate_wire(remote.get('register'))
                from interaction_sync import InteractionRegister
                check=InteractionRegister('pc');check.merge(state)
            except (ValueError,KeyError,TypeError,AttributeError):
                self.sync_fingerprint=None;self.sync_error='invalid_sync_reply';return
            remote_version=check.fingerprint()
            lost_history=(state.get('pc') or {}).get('seq',0)>(self.interaction.register.events['pc'] or {}).get('seq',0)
            def apply(register):
                register.merge(state)
                if lost_history:register.edit(False)
            try:self._gate_change(apply)
            except (OSError,ValueError):
                self.sync_fingerprint=None;self.sync_error='sync_application_failed';return
            self.peer_enabled=remote.get('enabled') is True
            self.sync_token=token;self.sync_at=time.monotonic();self.sync_error=None
            self.sync_fingerprint=remote_version if remote_version==self.interaction.register.fingerprint() else None
        self._sync_reminder_policy(token)

    def _interaction_state(self):
        with self.lock:
            self._ensure_interaction();confirmed=self._sync_confirmed()
            return dict(enabled=self.agent_enabled,desiredEnabled=self.interaction.register.enabled,
                        peerEnabled=self.peer_enabled,peerConfirmed=confirmed,pending=not confirmed,
                        version=self.interaction.register.fingerprint(),remainingMs=self.interaction.register.remaining(),
                        activeDurationMs=min((e['ttlMs'] for e in self.interaction.register.active() if e['ttlMs'] is not None),default=0) if self.agent_enabled else 0,
                        storageError=self.interaction.error,syncError=self.sync_error,
                        syncStatus=self.interaction.error or ('两端已同步' if confirmed else '本端已设置，待同步'))

    def _preferences(self):
        atomic_json(self.preference_path,dict(mode=self.configured_mode,enabledOnStart=self.enabled_on_start))

    def _write(self,row):
        with self.log_lock:
            if self.normalized:self.normalized.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')

    def _observe(self,row):
        if row.get('event')=='reminder_receipt':
            if self.selected=='rfcomm' and row.get('selectionId')==self.selection_id and self.on_reminder_receipt:self.on_reminder_receipt(row)
            return
        if row.get('event')=='heart' and (self.selected!='rfcomm' or row.get('selectionId')!=self.selection_id):return
        self.cache.observe(row)

    def _start_bluetooth(self):
        if self.manager_stop.is_set():return
        if self.thread and self.thread.is_alive():return
        config=json.loads((self.root/'.local-wifi/rfcomm-probe.json').read_text(encoding='utf-8'))
        receiving=self.cache.receiving
        now=datetime.now()
        for offset in range(1000):
            run_id=now.strftime('%Y%m%d-%H%M%S-')+f'{(now.microsecond//1000+offset)%1000:03}'
            directory=self.root/'runs'/run_id
            try:directory.mkdir(parents=True);break
            except FileExistsError:continue
        else:raise OSError('Could not allocate a new receive session')
        with self.log_lock:
            if self.normalized:self.normalized.close()
            self.normalized=(directory/'pc.jsonl').open('x',encoding='utf-8',buffering=1)
            self.run_id=run_id
        self.cache=HeartCache(self._write);self.cache.receiving=receiving
        self._write(self.cache.state.server_start)
        self.probe=Probe(config,directory/'bluetooth.jsonl',message_observer=self._observe,record_heart=lambda:self.cache.receiving)
        probe=self.probe
        if self.manager_stop.is_set():probe.stopping.set()
        def run():
            try:probe.run(None,persistent=True)
            finally:probe.stream.close()
        self.thread=threading.Thread(target=run,name='heartradio-bluetooth',daemon=True);self.thread.start()

    def _prepare_wifi(self,stop_event):
        # Existing Wi-Fi implementations and owned-process guards are reused. No radio/network changes.
        if stop_event.is_set() or not (self.root/'.local-wifi/config.json').is_file():return
        self.wifi_owned=True
        if self.cache.receiving:
            with (self.root/'runs/connection-wifi-launch.log').open('ab') as output:
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'scripts/Start-Receiver.ps1')],
                                      cwd=self.root,stdout=output,stderr=subprocess.STDOUT,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode:raise OSError('Wi-Fi接收器尚未就绪，蓝牙仍会独立尝试')
        if stop_event.is_set():return
        sys.path.insert(0,str(self.root/'scripts'))
        from capability_service import CapabilityService
        CapabilityService(self.root).start(connection_only=True)

    def _drop_route(self):
        with self.lock:
            self.sync_fingerprint=None;self.sync_token=None;self.peer_enabled=None
            self.selected=None;self.selected_key=None;self.selection_id=None;self.mode='none';self.selected_ms=0
            self.cache.observe(dict(event='disconnected'));self.waiting_since=time.monotonic()

    def _token(self):
        return (self.selected,self.selected_key,self.selection_id)

    def reminder_sync_context(self):
        """No connection/start side effects; reuse the already-confirmed interaction gate."""
        with self.lock:
            if not self.managed or not self.agent_enabled or not self._sync_confirmed():return None
            if not self.candidates.get(self.selected,{}).get('connected'):return None
            return (self._token(),self.interaction.register.fingerprint())

    def _candidate_states(self):
        bt=self.probe.state() if self.probe else {}
        wifi=self.wifi_state() if 'wifi' in allowed(self.configured_mode) else {}
        return {'rfcomm':dict(connected=bt.get('connected') is True,key=bt.get('connectionSerial'),watch=bt.get('lastWatch')),
                'wifi':dict(connected=wifi.get('connected') is True,key=(wifi.get('instanceId'),wifi.get('connectionSerial')),watch=wifi.get('watchLifecycle'),serverRunning=wifi.get('status')!='bridge_unavailable',reminderReceipts=wifi.get('reminderReceipts',[]))}

    def _raw(self,route,op,params=None,request_id=None):
        if route=='rfcomm':
            if not self.probe:return dict(status='watch_offline')
            value=dict(op=op,params=params or {})
            if request_id is not None:value['requestId']=request_id
            return self.probe.request(value)
        return self.wifi_request(op,params,request_id)

    def _route_once(self,stop_event=None):
        with self.route_lock:self._route_once_locked(stop_event)

    def _route_once_locked(self,stop_event=None):
        candidates=self._candidate_states();self.candidates=candidates
        if 'wifi' in allowed(self.configured_mode) and candidates['wifi'].get('serverRunning') is False:self.prepare_requested.set()
        active=self.selected
        if active and (not candidates[active]['connected'] or candidates[active]['key']!=self.selected_key):self._drop_route();active=None
        candidate=choose(self.configured_mode,{k:v['connected'] for k,v in candidates.items()},active,time.monotonic()-self.waiting_since)
        if candidate and candidate!=active:
            key=candidates[candidate]['key'];identity=uuid.uuid4().hex
            reply=self._raw(candidate,'select_transport',dict(selectionId=identity))
            if reply.get('status')=='ok' and reply.get('selectionId')==identity and not (stop_event or self.manager_stop).is_set():
                with self.lock:
                    self.selected=candidate;self.mode=candidate;self.selected_key=key;self.selection_id=identity;self.selected_ms=time.time_ns()//1_000_000
                    self.wifi_heart_session=reply.get('wifiHeartSession')
                    self.cache.observe(dict(event='disconnected'))
                    if candidate=='rfcomm':self.cache.observe(dict(event='hello'))
                self.message='蓝牙已连接' if candidate=='rfcomm' else 'Wi-Fi已连接'
            else:self.message='等待手表支持并确认连接方式'
        elif not active:self.message='等待手表或可用无线连接；会自动重试'
        if self.selected=='wifi' and self.on_reminder_receipt:
            for row in candidates['wifi'].get('reminderReceipts',[]):
                if row.get('selectionId')==self.selection_id:self.on_reminder_receipt(row)

    def _limits(self):
        if self.receive_limit:
            self.receive_limit.apply(read_settings(self.root/'runs/receiver-settings.json'))
            if self.receive_limit.expired() and self.cache.receiving:
                self.cache.set_receiving(False);self._stop_wifi_reception()
        if self.managed:
            with self.lock:
                self._ensure_interaction()
                try:self._gate_change(lambda r:r.expire())
                except OSError:pass  # State exposes the error; fresh operations fail closed.
                settings=self._duration()
                if settings['revision']!=self.duration_revision:
                    self.duration_revision=settings['revision']
                    if self.interaction.enabled:self._edit_interaction(True,force=True)

    def _stop_wifi_reception(self):
        path=self.root/'runs/active.json'
        if not path.exists():return
        info=json.loads(path.read_text(encoding='utf-8-sig'))
        stop=Path(info['stopFile']).resolve()
        if not stop.is_relative_to((self.root/'runs').resolve()) or stop.name!='stop.request':raise ValueError('Invalid project receiver stop path')
        stop.touch(exist_ok=True)

    def start(self):
        with self.lock:
            self.managed=True
            if self.manager_thread and self.manager_thread.is_alive():return
            if self.session['suspended']:
                resumed=dict(self.session,suspended=False,resumedAtMs=time.time_ns()//1_000_000)
                atomic_json(self.session_path,resumed);self.session=resumed
            self.offline.start(time.monotonic())
            self.manager_stop=threading.Event();stop_event=self.manager_stop;self.waiting_since=time.monotonic()
            self.prepare_requested.set()
            self.mode='none'
            if self.receive_limit is None:
                value=read_settings(self.root/'runs/receiver-settings.json');self.receive_limit=Deadline(value['seconds'],value)
            def run():
                retry_at=0
                while not stop_event.is_set():
                    try:
                        if 'rfcomm' in allowed(self.configured_mode):self._start_bluetooth()
                        if 'wifi' in allowed(self.configured_mode) and self.prepare_requested.is_set() and not (self.prepare_thread and self.prepare_thread.is_alive()) and time.monotonic()>=retry_at:
                            # Prepare on a separate thread so an unavailable Wi-Fi service cannot delay Bluetooth.
                            self.prepare_requested.clear()
                            def prepare():
                                nonlocal retry_at
                                try:self._prepare_wifi(stop_event);self.backing_error=None
                                except (OSError,ValueError,subprocess.SubprocessError) as exc:
                                    self.backing_error=str(exc);self.prepare_requested.set();retry_at=time.monotonic()+15
                            self.prepare_thread=threading.Thread(target=prepare,name='heartradio-wifi-prepare',daemon=True);self.prepare_thread.start()
                        self._limits();self._route_once(stop_event)
                        if not stop_event.is_set():self._sync_once()
                        self._check_offline_timeout()
                    except (OSError,ValueError,KeyError) as exc:self.message='等待连接配置或无线恢复：'+str(exc)
                    stop_event.wait(1)
            self.manager_thread=threading.Thread(target=run,name='heartradio-routing',daemon=True);self.manager_thread.start()

    def _check_offline_timeout(self):
        with self.lock:
            now=time.monotonic()
            connected=bool(self.selected and self.candidates.get(self.selected,{}).get('connected'))
            if not self.offline.expired(now,connected):return
            self.session=dict(suspended=True,reason='offline_timeout',timeoutSeconds=600,
                              stoppedAtMs=time.time_ns()//1_000_000,heartReceiveEnabled=self.cache.receiving)
            # Preserve shared business registers and their original finite deadlines.
            try:atomic_json(self.session_path,self.session)
            except OSError:self.session['storageError']='自动停止状态保存失败，业务偏好仍保留'
            self.agent_enabled=False;self.manager_stop.set()
            self.message='未连接满10分钟，全部项目服务即将停止；请用01重新启动'
        if self.on_gate_closed:self.on_gate_closed()
        if self.on_offline_timeout:
            threading.Thread(target=self.on_offline_timeout,name='offline-shutdown',daemon=True).start()

    def _stop_bluetooth_adapter(self):
        probe=self.probe
        if probe:
            probe.stopping.set();probe.disconnected()
            if probe.client:probe.client.close()
        if self.thread:self.thread.join(3)

    def _stop_wifi_adapter(self):
        if not self.wifi_owned:return
        from capability_service import CapabilityService
        CapabilityService(self.root).stop(keep_standby=True)
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'scripts/Stop-Receiver.ps1')],
                              cwd=self.root,capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if result.returncode:raise OSError('Wi-Fi接收停止尚未确认')
        self.wifi_owned=False

    def _change_mode(self,mode):
        live=self.managed and self.manager_thread and self.manager_thread.is_alive() and not self.manager_stop.is_set()
        preparing=self.prepare_thread and self.prepare_thread.is_alive()
        if live and not preparing:
            with self.route_lock:
                candidates=self._candidate_states()
                if mode.startswith('auto_') or candidates.get(mode,{}).get('connected'):
                    previous=(self.configured_mode,self.enabled_on_start)
                    self.configured_mode=mode;self.enabled_on_start=True
                    try:self._preferences()
                    except (OSError,ValueError):
                        self.configured_mode,self.enabled_on_start=previous;raise
                    # A user-requested preference change reselects; authenticated sockets stay open.
                    self._drop_route();self.prepare_requested.set();self._route_once_locked(self.manager_stop)
                    if mode=='wifi':self._stop_bluetooth_adapter()
                    elif mode=='rfcomm':self._stop_wifi_adapter()
                    return self.state()
        self.stop();self.configured_mode=mode;self.enabled_on_start=True;self._preferences();self.start();return self.state()

    def stop(self):
        self.manager_stop.set();self._drop_route()
        probe=self.probe
        if probe:
            probe.stopping.set();probe.disconnected()
            if probe.client:probe.client.close()
        if self.manager_thread:self.manager_thread.join(3)
        if self.thread:self.thread.join(3)
        if self.prepare_thread:self.prepare_thread.join(30)
        if self.managed and self.wifi_owned:
            from capability_service import CapabilityService
            CapabilityService(self.root).stop(keep_standby=True)
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(self.root/'scripts/Stop-Receiver.ps1')],
                                  cwd=self.root,capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if result.returncode:raise OSError('Wi-Fi接收停止尚未确认')
            self.wifi_owned=False
        with self.log_lock:
            if self.normalized:
                self._write(dict(kind='server_stop',pcReceiptMs=time.time_ns()//1_000_000,pcMonotonicNs=time.monotonic_ns()))
                self.normalized.close();self.normalized=None

    def connection_token(self):
        with self.lock:return self._token() if self.managed else None

    def heart_snapshot(self,wifi_snapshot):
        if not self.managed:return self.cache.snapshot() if self.mode=='rfcomm' else wifi_snapshot()
        with self.lock:
            selected=self.selected;selected_ms=self.selected_ms
        value=self.cache.snapshot() if selected!='wifi' else wifi_snapshot()
        if not selected or not self.cache.receiving or (selected=='wifi' and ((value.get('eventReceivedMs') or 0)<selected_ms or value.get('eventSession')!=self.wifi_heart_session)):
            value.update(valid=False,bpm=None)
            if not selected:value.update(connected=False,status=self.message)
            elif not self.cache.receiving:value.update(receiverStopped=True,status='电脑心率接收已停止')
            else:value['status']='连接已恢复，等待新的心率事件'
        value.update(transport=selected,source='continuous_heart_cache_'+(selected or 'offline'))
        if selected=='rfcomm':value['runId']=self.run_id
        if self.receive_limit:
            value['durationSeconds']=self.receive_limit.seconds
            value['receiverLimit']=self.receive_limit.fields()
        return value

    def state(self):
        if self.managed:
            self._limits()
            interaction=self._interaction_state()
            running=bool(self.manager_thread and self.manager_thread.is_alive() and not self.manager_stop.is_set())
            return dict(mode=self.mode,configuredMode=self.configured_mode,enabledOnStart=self.enabled_on_start,connectionRunning=running,
                        connected=bool(self.selected and self.candidates.get(self.selected,{}).get('connected')),transport=self.selected,
                        selectionId=self.selection_id,agentEnabled=self.agent_enabled,heartReceiveEnabled=self.cache.receiving,
                        bluetoothRunId=self.run_id,bluetoothConnectionSerial=self.probe.connection_serial if self.probe else 0,
                        bluetoothFailures=self.probe.failures if self.probe else 0,
                        watchControl=dict(self.cache.control) if self.selected=='rfcomm' else (self.candidates.get('wifi',{}).get('watch') or {}),
                        candidates=self.candidates,status=self.message,backingError=self.backing_error,
                        automaticRouting=True,switchSyncSupported=True,agentInteraction=interaction,reminderPolicy=self.reminder_policy_state(),
                        heartBroadcast=self._broadcast_state(),projectSession=dict(self.session,remainingSeconds=self.offline.remaining(time.monotonic(),bool(self.selected))),
                        agentRemainingSeconds=interaction['remainingMs']/1000 if interaction['remainingMs'] is not None else None)
        if self.mode=='rfcomm':
            result=self.probe.state() if self.probe else dict(connected=False)
            return dict(result, transport='rfcomm', mode='rfcomm', connectionRunning=bool(self.thread and self.thread.is_alive()),
                        agentEnabled=self.agent_enabled, heartReceiveEnabled=self.cache.receiving,
                        watchControl=dict(self.cache.control), watchControlAgeSeconds=time.monotonic()-self.cache.control_at if self.cache.control_at else None, switchSyncSupported=False, automaticRouting=False,projectSession=dict(self.session))
        return dict(self.wifi_state(), transport='wifi', mode='wifi', agentEnabled=None,configuredMode=self.configured_mode,enabledOnStart=self.enabled_on_start,
                    switchSyncSupported=False, automaticRouting=False,projectSession=dict(self.session))

    @staticmethod
    def wifi_state():
        try:
            with LOCAL_HTTP.open(BRIDGE_URL + '/state',timeout=1) as response:
                raw=response.read(262145)
                if len(raw)>262144:raise ValueError('Oversize bridge state')
                return json.loads(raw)
        except (OSError,ValueError): return dict(status='bridge_unavailable',connected=False)

    def request(self, op, params=None, request_id=None):
        if self.managed:
            self._limits()
            if op!='cancel' and not self.agent_enabled:return dict(status='interaction_disabled')
            if op!='cancel' and not self._sync_confirmed():return dict(status='interaction_sync_pending')
            if op=='remind':
                policy=self.reminder_policy_state()
                if policy['storageError']:return dict(status='reminder_policy_storage_error')
                if not policy['peerConfirmed']:return dict(status='reminder_policy_sync_pending')
                from reminder_protocol import normalize
                rejected=policy_violation(normalize(params),policy['limits'])
                if rejected:return rejected
            with self.lock:token=self._token()
            expected=None
            if op in ('sample','heart_sample','motion_window'):expected=getattr(self.local,'inventory_route',None);self.local.inventory_route=None
            if op=='cancel':expected=self.sample_routes.get((params or {}).get('targetId'))
            if not token[0] or (expected is not None and expected!=token):return dict(status='outcome_unknown_disconnected' if op in ('sample','heart_sample','motion_window','cancel') else 'watch_offline')
            if op=='inventory':self.local.inventory_route=token
            if op in ('sample','heart_sample','motion_window') and request_id:self.sample_routes[request_id]=token
            try:
                result=self._raw(token[0],op,params,request_id)
                with self.lock:
                    if token!=self._token():return dict(status='outcome_unknown_disconnected',id=request_id)
                return result
            finally:
                if op in ('sample','heart_sample','motion_window') and request_id:self.sample_routes.pop(request_id,None)
        if self.mode=='rfcomm':
            if op!='cancel' and not self.agent_enabled:return dict(status='interaction_disabled')
            if not self.probe:return dict(status='watch_offline')
            value=dict(op=op,params=params or {})
            if request_id is not None:value['requestId']=request_id
            return self.probe.request(value)
        # A view-only dashboard cannot treat a connection-only listener as permission.
        if self.wifi_state().get('connectionOnly') is True and op!='cancel':return dict(status='interaction_disabled')
        return self.wifi_request(op,params,request_id)

    @staticmethod
    def wifi_request(op,params=None,request_id=None):
        payload=dict(op=op,params=params or {})
        if request_id is not None:payload['requestId']=request_id
        request=urllib.request.Request(BRIDGE_URL + '/request',
            data=json.dumps(payload,allow_nan=False).encode(),
            headers={'Content-Type':'application/json','X-HeartRadio-Probe':'request'})
        try:
            with LOCAL_HTTP.open(request,timeout=4 if op=='cancel' else 40 if op=='motion_window' else 22) as response:
                data=response.read(65537)
            if len(data)>65536:raise ValueError('Oversize result')
            value=json.loads(data)
            return value if isinstance(value,dict) else dict(status='invalid_result')
        except (OSError,ValueError):return dict(status='bridge_unavailable')

    def control(self, value):
        if isinstance(value,dict) and value.get('action')=='set_mode':
            if set(value)!={'action','mode'} or value['mode'] not in MODES:raise ValueError('Invalid connection mode')
            return self._change_mode(value['mode'])
        if isinstance(value,dict) and set(value)=={'action'} and value['action'] in ('start','start_if_enabled','shutdown'):
            if value['action']=='shutdown':
                if self.managed:
                    self._edit_interaction(False)
                    if self.selected:self._sync_once()
                elif self.selected:self._raw(self.selected,'stop_bridge')
                self.agent_enabled=False;self.stop()
            elif value['action']=='start':self.enabled_on_start=True;self._preferences();self.start()
            elif self.enabled_on_start:self.start()
            return self.state()
        if not isinstance(value,dict) or set(value)!={'action'}:raise ValueError('Invalid connection action')
        action=value['action']
        if action not in ('start_bluetooth','stop','use_legacy_wifi','agent_on','agent_off','heart_receive_on','heart_receive_off','heart_broadcast_on','heart_broadcast_off'):
            raise ValueError('Unknown connection action')
        if action in ('heart_broadcast_on','heart_broadcast_off'):
            if not self.managed:self.managed=True;self.mode='none'
            self._edit_broadcast(action=='heart_broadcast_on');return self.state()
        if action in ('agent_on','agent_off','heart_receive_on','heart_receive_off') and not self.managed and self.mode!='rfcomm':
            self.managed=True;self.mode='none'  # Save offline without creating a connection.
        if self.managed:
            self._limits()
            if action in ('start_bluetooth','use_legacy_wifi'):
                return self.control(dict(action='set_mode',mode='rfcomm' if action=='start_bluetooth' else 'wifi'))
            if action=='stop':
                self.enabled_on_start=False;self._preferences();self.stop()
            elif action in ('agent_on','agent_off'):
                self._edit_interaction(action=='agent_on')
            else:
                was_receiving=self.cache.receiving
                if was_receiving!=(action=='heart_receive_on'):self.cache.set_receiving(action=='heart_receive_on')
                if not self.cache.receiving:self._stop_wifi_reception()
                if self.cache.receiving and not was_receiving:
                    settings=read_settings(self.root/'runs/receiver-settings.json');self.receive_limit=Deadline(settings['seconds']);self.receive_limit.revision=settings['revision']
                    self.prepare_requested.set()
            return self.state()
        with self.lock:
            if action=='start_bluetooth':
                if self.thread and self.thread.is_alive():return self.state()
                config=json.loads((self.root/'.local-wifi/rfcomm-probe.json').read_text(encoding='utf-8'))
                directory=self.root/'runs'/'connection-stage1'/uuid.uuid4().hex
                directory.mkdir(parents=True)
                receiving=self.cache.receiving
                self.cache=HeartCache()
                self.cache.receiving=receiving
                self.probe=Probe(config,directory/'pc.jsonl',message_observer=self.cache.observe)
                self.mode='rfcomm'
                probe=self.probe
                def run():
                    try:probe.run(3600)  # Deliberate stage-1 verification window, not a daily-service promise.
                    finally:probe.stream.close()
                self.thread=threading.Thread(target=run,name='stage1-rfcomm',daemon=True)
                self.thread.start()
            elif action in ('stop','use_legacy_wifi'):
                if self.probe:
                    self.probe.stopping.set()
                    self.probe.disconnected()
                    if self.probe.client:self.probe.client.close()
                if action=='use_legacy_wifi':self.mode='wifi'
            elif action in ('agent_on','agent_off'):
                if self.mode!='rfcomm':raise ValueError('The new local gate currently belongs to the stage-1 Bluetooth path')
                self.agent_enabled=action=='agent_on'
            else:
                if self.mode!='rfcomm':raise ValueError('Use the existing receiver controls for Wi-Fi')
                self.cache.set_receiving(action=='heart_receive_on')
            return self.state()


transport=TransportAccess()
