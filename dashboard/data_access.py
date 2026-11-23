"""Shared local tools: HR cache reads and explicitly requested bounded standard sensors."""
import json
from concurrent.futures import ThreadPoolExecutor
import math
from pathlib import Path
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from data_policy import DataPolicy
from user_state import STATE_METRICS, classify, heart_summary, motion_bins, selected_state_metrics

TOOLS = json.loads((Path(__file__).resolve().parents[1] / 'agent' / 'tools.json').read_text(encoding='utf-8'))


from transport_access import transport
from diagnostic_log import DiagnosticLog


def bridge_request(op, params=None, request_id=None):
    return transport.request(op,params,request_id)


def bridge_state():
    return transport.state()


class DataAccess:
    def __init__(self, root, heart_snapshot, bridge=bridge_request, connection=bridge_state, reminder_policy=None, wear_permitted=None):
        self.directory = Path(root) / 'runs' / 'data-access'
        self.heart_snapshot = heart_snapshot
        self.bridge = bridge
        self.connection = connection
        self.wear_permitted = wear_permitted
        from reminder_policy import DEFAULTS
        self.reminder_policy=reminder_policy or (lambda:dict(limits=dict(DEFAULTS),peerConfirmed=False))
        self.policy = DataPolicy(root)
        self.active_lock = threading.Lock()
        self.active = {}
        self.status_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.last_measure_end = {}
        self.cache = {}
        self.diagnostic_log = None
        self.diagnostic_error = None
        self.state_lock=threading.Lock()
        try:
            value = json.loads((self.directory / 'latest.json').read_text(encoding='utf-8'))
            if isinstance(value, dict): self.cache = value
        except (OSError, ValueError):
            pass
        try:
            saved = json.loads((self.directory / 'cooldown.json').read_text(encoding='utf-8'))
            ended = saved.get('endedAtMsByMetric', {metric:saved.get('endedAtMs',0) for metric in ('heart_rate','pressure','accelerometer','gyroscope')})
            for metric, ended_ms in ended.items():
                age = max(0, min(300, (time.time_ns() // 1_000_000 - ended_ms) / 1000))
                self.last_measure_end[metric] = time.monotonic() - age
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def measurement_state(self):
        with self.active_lock:
            rows = [{key:value for key,value in entry.items() if key not in ('cancel_event','done','result','error')} for entry in self.active.values()]
            return dict(rows[0], requests=rows, count=len(rows)) if rows else None

    def cancel(self, request_id=None):
        with self.active_lock:
            targets = [entry for entry in self.active.values() if request_id is None or request_id == entry['requestId']]
            if not targets:
                return dict(schemaVersion=1, ok=False, status='no_active_measurement')
            for entry in targets:
                entry['cancel_event'].set()
                entry['cancelRequested'] = True
        if len(targets)==1:return self._cancel_one(targets[0])
        with ThreadPoolExecutor(max_workers=4) as workers:
            results=list(workers.map(self._cancel_one,targets))
        return dict(schemaVersion=1, ok=all(row['ok'] for row in results),
                    status='cancel_requested' if all(row['ok'] for row in results) else 'partial_cancel', results=results)

    def _cancel_one(self, active):
        if active['phase'] == 'preparing':
            return dict(schemaVersion=1, ok=True, status='cancel_requested', requestId=active['requestId'])
        until = time.monotonic() + 3
        while time.monotonic() < until:
            raw = self.bridge('cancel', dict(targetId=active['requestId']))
            if raw.get('status') != 'not_active':
                return dict(schemaVersion=1, ok=raw.get('status') == 'ok',
                            status='cancel_requested' if raw.get('status') == 'ok' else raw.get('status', 'error'),
                            requestId=active['requestId'])
            if active['done'].is_set():
                break
            time.sleep(.05)
        return dict(schemaVersion=1, ok=False, status='measurement_already_finished', requestId=active['requestId'])

    def recent(self, metric, maximum_age):
        if maximum_age <= 0: return None
        with self.cache_lock:
            value = self.cache.get(metric)
            age = (time.time_ns() // 1_000_000 - value['receivedAtMs']) / 1000 if value else -1
            if value and value.get('ok') is True and 0 <= age <= maximum_age:
                return dict(value, cached=True, cacheAgeSeconds=age)

    def on_demand_state(self):
        state = self.connection()
        selected = {'rfcomm': 'bluetooth', 'wifi': 'wifi'}.get(state.get('transport'))
        if not selected or not state.get('connected'):
            reason = 'bridge_unavailable' if state.get('status') == 'bridge_unavailable' else 'watch_offline'
            return dict(transport=None, ready=False, reason=reason)
        if state.get('agentEnabled') is False or state.get('connectionOnly') is True:
            return dict(transport=selected, ready=False, reason='interaction_disabled')
        if state.get('agentEnabled') is not True:
            return dict(transport=selected, ready=False, reason='interaction_unconfirmed')
        if state.get('automaticRouting') and not state.get('agentInteraction', {}).get('peerConfirmed'):
            return dict(transport=selected, ready=False, reason='interaction_sync_pending')
        return dict(transport=selected, ready=True, reason=None)

    def capabilities(self, compact=False):
        settings = self.policy.read()
        reminder_policy=self.reminder_policy()
        result = dict(schemaVersion=1, ok=True, status='ok', onDemand=self.on_demand_state(),
            verifiedDevice=dict(model='OW20W2', sdk=27, firmware='OW20W2_11_A.92'),
            available=['heart_rate', 'device_status', 'pressure', 'accelerometer', 'gyroscope'],
            reminders=dict(available=True, templates=['strong','standard','light','silent','notification_only'],
                           requiresSynchronizedAgentInteraction=True, maxBodyCharacters=500,
                           maxSequenceMs=reminder_policy['limits']['maxVibrationMs'],maxScreenSeconds=reminder_policy['limits']['maxScreenSeconds'],
                           technicalMaxSequenceMs=10000,userPolicy=reminder_policy,automaticallyRetried=False,
                           deliveryIsNotHumanAcknowledgement=True),
            onDemandHeartRate=dict(tool='measure_heart_rate',defaultWaitSeconds=15,maxWaitSeconds=15,
                                  startsContinuousBroadcast=False,sharedPhysicalListener=True),
            experimentalStress=dict(tool='get_user_state', metric='stress', source='stock_pressure_page', wakesScreen=True,
                                    requiresAccessibility=True, requiresStillWrist=False, steadyWristRecommended=True, historyAvailable=True,
                                    nativeHistoryAvailable=False, historySource='pc_archive',
                                    validatesRest=False, automaticallyRetried=False),
            concurrentReading=dict(maxMeasurements=4,independentMetrics=True,sameMetricInFlightShared=True,
                                   cooldownScope='same_metric',batchTool='read_watch_data',batchDefaultHeartWaitSeconds=5),
            userState=dict(tool='get_user_state',observationSeconds=30,requiredMetrics=['wear'],
                selectableMetrics=list(STATE_METRICS), activityTierRequires=['accelerometer','gyroscope'],
                 source='local_heuristic',accuracy='unmeasured',wearSource='watch_native',
                 requiresConfirmedWearForNewSampling=True,
                historyQuery=False,startsContinuousHeartBroadcast=False,requiresWatchMotionWindowVersion=1),
            unavailable=dict(location='No fresh GPS fix in the real test; not exposed as a default tool',
                             steps='No standard step callbacks during walking tests',
                             light='Cover/uncover did not change the standard lux value',
                             spo2='Vendor raw callbacks have no validated conversion',
                             ecg='Vendor raw callbacks have no validated conversion'),
            enabled=settings['allowedMetrics'], settings=settings, measurement=self.measurement_state(),
            limits=dict(maxMeasurementSeconds=10,maxHeartMeasurementSeconds=15, measurementCooldownSeconds=settings['measurementCooldownSeconds'],
                        maxHeartReceiptAgeSeconds=settings['heartMaxAgeSeconds'], bridgeStandbyDefaultMinutes=30,
                        maxBridgeStandbySeconds=31536000, bridgeStandbyUnlimitedSupported=True,
                        watchDurationFollowComputerSupported=True),
            lifecycle='Connection, continuous heart capture and Agent permission are independent; current automatic routing requires daily PC startup',
            recovery=dict(networkReconnect=True, renewsStandbyDeadline=False, replaysMeasurements=False,
                          systemStickyRestart='new connection owner requires app reopening after process death; no reboot or force-stop recovery promise',
                          forceStopRecovery=False, rebootAutostart=False, changedComputerAddressDiscovery=True),
            sourceAgeVerified=False, longTermStabilityVerified=False, batteryCostMeasured=False)
        if compact:
            for field in ('unavailable', 'lifecycle', 'recovery', 'longTermStabilityVerified', 'batteryCostMeasured'):
                result.pop(field)
            result['reminders']['userPolicy'] = {key: reminder_policy[key]
                for key in ('limits', 'peerConfirmed', 'syncStatus', 'storageError') if key in reminder_policy}
            measurement = result['measurement']
            if measurement is not None:
                result['measurement'] = {'count': measurement['count'], 'requests': [
                    {key: row[key] for key in ('requestId', 'metric', 'seconds', 'phase', 'cancelRequested') if key in row}
                    for row in measurement['requests']
                ]}
        return result

    def heart_rate(self, maximum_age):
        state = self.heart_snapshot()
        age = state.get('eventAgeSeconds')
        ok = state.get('valid') is True and age is not None and age <= maximum_age
        if not ok:
            once=self.recent('heart_rate_once',maximum_age)
            if once:return dict(once,status='ok',receiptAgeSeconds=once['cacheAgeSeconds'],maximumReceiptAgeSeconds=maximum_age)
        status = 'ok' if ok else 'disconnected' if not state.get('connected') else 'stale' if age is not None and age > maximum_age else 'no_valid_data'
        return dict(schemaVersion=1, metric='heart_rate', ok=ok, status=status, value=state.get('bpm') if ok else None,
                    unit='bpm', receiptAgeSeconds=age, maximumReceiptAgeSeconds=maximum_age,
                    receivedAtMs=state.get('eventReceivedMs', state['nowMs'] - int(age * 1000) if age is not None else None),
                    sourceTimestampMs=state.get('measurementMs'), sourceAgeVerified=False,
                    sourceTimestampBasis='watch_receipt_approximation',
                    source=state.get('source','original_wifi_heart_rate_v1_cache'), timeBasis='pc_receipt',
                    quality=dict(accuracy=state.get('accuracy'), reason=state.get('status')),
                    nextAction=None if ok else 'Use measure_heart_rate for an explicit bounded fresh reading; it does not enable broadcast')

    def save(self, key, value):
        with self.cache_lock:
            self.cache[key] = value
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / 'latest.tmp'
            temporary.write_text(json.dumps(self.cache, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temporary.replace(self.directory / 'latest.json')
            try:
                if self.diagnostic_log is None:
                    self.diagnostic_log = DiagnosticLog(self.directory/'diagnostics'/('tools-'+uuid.uuid4().hex+'.jsonl'),keep_open=False)
                self.diagnostic_log.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
                self.diagnostic_error = self.diagnostic_log.error
            except (OSError, ValueError) as error: self.diagnostic_error = str(error)
        return value

    def close(self):
        with self.cache_lock:
            if self.diagnostic_log: self.diagnostic_log.close()

    def cached(self):
        settings = self.policy.read()
        with self.cache_lock:
            values = json.loads(json.dumps(self.cache))
        now = time.time_ns() // 1_000_000
        for value in values.values():
            raw_age = (now - value['receivedAtMs']) / 1000
            value['cacheAgeSeconds'] = max(0, raw_age)
            value['cached'] = True
            limit = settings['deviceStatusCacheSeconds'] if value['metric'] == 'device_status' else settings['heartMaxAgeSeconds'] if value['metric']=='heart_rate' else settings['sensorCacheSeconds']
            value['cacheReusable'] = value.get('ok') is True and limit > 0 and 0 <= raw_age <= limit
        values = {key:value for key,value in values.items() if value['metric'] in settings['allowedMetrics']}
        return dict(schemaVersion=1, heartRate=self.heart_rate(settings['heartMaxAgeSeconds']) if 'heart_rate' in settings['allowedMetrics'] else None,
                    measurements=values, bridge=self.connection(), measurement=self.measurement_state(), settings=settings,
                    diagnosticLoggingError=self.diagnostic_error)

    def device_status(self, settings, fresh=False):
        # Share this cache across the dashboard and every MCP client.
        with self.status_lock:
            cached = None if fresh else self.recent('device_status', settings['deviceStatusCacheSeconds'])
            if cached: return cached
            return self.query_device_status()

    def query_device_status(self):
        raw = self.bridge('status')
        ok = raw.get('status') == 'ok'
        fields = ('batteryPct', 'chargingStatus', 'plugged', 'chargeCounterUah', 'screenInteractive',
                  'wifiAvailable', 'heartRateCaptureRunning', 'heartSensorOwners','heartSensorActive','activeRequest', 'activeRequests','maxConcurrentMeasurements','requestWakeLockHeld', 'bridgeLifecycle', 'agentInteraction')
        value = dict(schemaVersion=1, metric='device_status', ok=ok, status=raw.get('status', 'error'),
                     receivedAtMs=time.time_ns() // 1_000_000, source='android_public_device_state',
                     sourceAgeVerified=False, data={key: raw.get(key) for key in fields} if ok else None)
        if ok:
            temp = raw.get('batteryTemperatureTenthsC')
            value['data']['batteryTemperatureC'] = temp / 10 if isinstance(temp, (int, float)) and temp >= 0 else None
        return self.save('device_status', value)

    def measure_stress(self, wearer_state):
        result = dict(schemaVersion=1, metric='stress', experimental=True, ok=False,
                      reportedWearerState=wearer_state, restVerified=False, accuracyVerified=False)
        if wearer_state == 'moving':
            return dict(result, status='skipped_moving')
        if self.wear_permitted is None or not self.wear_permitted():
            return dict(result, status='disabled_by_settings')
        identity = uuid.uuid4().hex
        entry = dict(requestId=identity, metric='stress', phase='measuring',
                     startedAtMs=time.time_ns()//1_000_000, cancelRequested=False,
                     cancel_event=threading.Event(), done=threading.Event())
        with self.active_lock:
            if 'stress' in self.active:
                return dict(result, status='busy')
            self.active['stress'] = entry
        result['requestId'] = identity
        try:
            raw = self.bridge('health_stress', {}, request_id=identity)
            if entry['cancel_event'].is_set():
                return dict(result, status='cancelled')
            if not self.wear_permitted():
                return dict(result, status='disabled_by_settings')
            status = raw.get('status', 'invalid_result')
            if raw.get('id') == identity and raw.get('op') == 'health_stress':
                if raw.get('pageStage') in ('measuring_seen', 'stock_page_seen', 'stock_page_unobserved'):
                    result['pageStage'] = raw['pageStage']
                if type(raw.get('enterAttempted')) is bool:
                    result['enterAttempted'] = raw['enterAttempted']
                if type(raw.get('recentMeasureAttempted')) is bool:
                    result['recentMeasureAttempted'] = raw['recentMeasureAttempted']
            if status != 'ok':
                if status == 'stock_no_valid_reading':
                    if result.get('pageStage') != 'measuring_seen':
                        return dict(result, status='invalid_result')
                    return dict(result, status=status, message='原厂提示“未测得有效值”，本次测量失败。')
                if status == 'no_fresh_result':
                    return dict(result, status=status, message='本次测量失败，等待 65 秒仍未取得新分数。')
                return dict(result, status='watch_update_required' if status == 'unknown_operation' else status)
            start, end, score = raw.get('measuringSeenAtMs'), raw.get('observedAtMs'), raw.get('score')
            received = time.time_ns()//1_000_000
            if (raw.get('id') != identity or raw.get('op') != 'health_stress' or raw.get('source') != 'stock_pressure_page'
                    or raw.get('pageStage') != 'measuring_seen'
                    or type(score) is not int or not 1 <= score <= 100 or type(start) is not int or type(end) is not int
                    or not 0 <= end-start <= 65_000 or not entry['startedAtMs']-120_000 <= start <= end <= received+120_000):
                return dict(result, status='invalid_result')
            return dict(result, ok=True, status='ok', score=score, source=raw['source'],
                        measuringSeenAtMs=start, observedAtMs=end, receivedAtMs=received)
        finally:
            with self.active_lock:
                self.active.pop('stress', None)
                entry['done'].set()

    def measure(self, metric, seconds, settings, fresh=False, state_window=False):
        if state_window and (metric not in ('accelerometer','gyroscope') or seconds!=30):raise ValueError('Invalid state window')
        cached = None if fresh or metric=='heart_rate' else self.recent(metric, settings['sensorCacheSeconds'])
        if cached and cached.get('requestedSeconds') == seconds: return cached
        with self.active_lock:
            entry = self.active.get(metric)
            joined = entry is not None
            if joined and entry['seconds'] != seconds:
                return dict(schemaVersion=1,metric=metric,ok=False,status='busy',requestId=entry['requestId'])
            if not joined:
                remaining = settings['measurementCooldownSeconds'] - (time.monotonic() - self.last_measure_end.get(metric,-math.inf))
                if remaining > 0:
                    return dict(schemaVersion=1, metric=metric, ok=False, status='rate_limited', retryAfterSeconds=math.ceil(remaining))
                entry = dict(requestId=uuid.uuid4().hex,metric=metric,seconds=seconds,stateWindow=state_window,phase='preparing',
                             startedAtMs=time.time_ns()//1_000_000,cancelRequested=False,
                             cancel_event=threading.Event(),done=threading.Event(),result=None,error=None)
                self.active[metric]=entry
        if joined:
            if not entry['done'].wait(48):return dict(schemaVersion=1,metric=metric,ok=False,status='shared_measurement_timeout')
            if entry['error'] is not None:raise entry['error']
            return dict(entry['result'],sharedInFlight=True)
        request_id = entry['requestId']
        def finish(value):
            entry['result']=value
            return value
        try:
            inventory = self.bridge('inventory')
            with self.active_lock:
                if entry['cancel_event'].is_set():
                    return finish(dict(schemaVersion=1, metric=metric, ok=False, status='cancelled', requestId=request_id,
                                listenersReleased=True, requestWakeLockHeldAtResult=False))
            if inventory.get('status') != 'ok':
                return finish(dict(schemaVersion=1, metric=metric, ok=False, status=inventory.get('status', 'error')))
            if inventory.get('model') != 'OW20W2' or inventory.get('sdk') != 27:
                return finish(dict(schemaVersion=1, metric=metric, ok=False, status='unverified_device'))
            if inventory.get('firmware') != 'OW20W2_11_A.92':
                return finish(dict(schemaVersion=1, metric=metric, ok=False, status='unverified_firmware'))
            if state_window and inventory.get('motionWindowVersion')!=1:
                return finish(dict(schemaVersion=1,metric=metric,ok=False,status='watch_update_required'))
            typ, width, unit = {'heart_rate':(21,1,'bpm'),'pressure': (6, 1, 'hPa'), 'accelerometer': (1, 3, 'm/s^2'), 'gyroscope': (4, 3, 'rad/s')}[metric]
            sensors = [sensor for sensor in inventory['sensors'] if sensor['type'] == typ]
            if len(sensors) != 1:
                return finish(dict(schemaVersion=1, metric=metric, ok=False, status='not_available'))
            sensor = sensors[0]
            with self.active_lock:
                if entry['cancel_event'].is_set():
                    return finish(dict(schemaVersion=1, metric=metric, ok=False, status='cancelled', requestId=request_id,
                                listenersReleased=True, requestWakeLockHeldAtResult=False))
                entry['phase'] = 'measuring'
            raw = self.bridge('motion_window' if state_window else 'heart_sample' if metric=='heart_rate' else 'sample', dict(sensorId=sensor['sensorId'], seconds=seconds), request_id=request_id)
            with self.active_lock:
                self.last_measure_end[metric] = time.monotonic()
                now=time.time_ns()//1_000_000
                ends={key:now-int((time.monotonic()-value)*1000) for key,value in self.last_measure_end.items()}
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary = self.directory / 'cooldown.tmp'
                temporary.write_text(json.dumps(dict(endedAtMsByMetric=ends)), encoding='utf-8')
                temporary.replace(self.directory / 'cooldown.json')
            ok = (raw.get('status') == 'data_received' and raw.get('listenersReleased') is True
                  and raw.get('requestWakeLockHeldAtResult') is False)
            status = raw.get('status', 'error')
            if raw.get('status') == 'data_received' and not ok:
                status = 'cleanup_unconfirmed'
            if ok and (raw.get('sensor', {}).get('name'), raw.get('sensor', {}).get('type')) != (sensor['name'], typ):
                ok = False
                status = 'sensor_mismatch'
            if state_window:
                # Ephemeral observation only: do not create state history or replace old short-window caches.
                fields=('summaryVersion','watchBeginElapsedNs','actualElapsedMs','callbacks','sampleCount','invalidEvents',
                    'truncated','rms','arrivalSpanMs','maximumArrivalGapMs','bins','listenersReleased','requestWakeLockHeldAtResult')
                return finish(dict(metric=metric,ok=ok,status=status,requestedSeconds=seconds,sensor=dict(name=sensor['name'],type=typ),
                    **{key:raw.get(key) for key in fields}))
            samples = []
            if ok:
                for sample in raw.get('samples', []):
                    values = sample.get('values', [])[:width]
                    if len(values) == width and all(type(n) in (int, float) and math.isfinite(n) for n in values):
                        samples.append(dict(values=values, accuracy=sample.get('accuracy'), eventNs=sample.get('eventNs'),
                                            watchReceiptWallMs=sample.get('watchReceiptWallMs')))
                if not samples:
                    ok = False
                    status = 'no_valid_samples'
            value = dict(schemaVersion=1, metric=metric, ok=ok, status=status, unit=unit,
                         receivedAtMs=time.time_ns() // 1_000_000, source='android_public_sensor',
                         sourceAgeVerified=False, sensor=dict(name=sensor['name'], type=typ),
                         requestId=raw.get('id'), requestedSeconds=seconds, actualElapsedMs=raw.get('actualElapsedMs'),
                         callbacks=raw.get('callbacks', 0), retainedSamples=len(samples), samples=samples,
                         value=(samples[0]['values'][0] if metric=='heart_rate' else statistics.mean(s['values'][0] for s in samples) if metric=='pressure' else None) if ok else None,
                         valueMeaning='first_valid_callback_after_request' if metric=='heart_rate' else 'mean_of_retained_samples' if metric == 'pressure' else 'raw_watch_axes',
                         quality='standard_api_units; no independent calibration',
                         listenersReleased=raw.get('listenersReleased'), requestWakeLockHeldAtResult=raw.get('requestWakeLockHeldAtResult'))
            if metric=='heart_rate':value.update(source='on_demand_heart_rate',continuousHeartPreserved=raw.get('continuousHeartPreserved'),heartSensorOwnersAfter=raw.get('heartSensorOwnersAfter'))
            return finish(self.save('heart_rate_once' if metric=='heart_rate' else metric, value))
        except Exception as error:
            entry['error']=error
            raise
        finally:
            with self.active_lock:
                self.active.pop(metric,None)
                entry['done'].set()

    def read_watch_data(self, arguments, settings):
        metrics = arguments['metrics']
        heart_wait = arguments.get('heart_wait_seconds', arguments.get('heart_seconds', 5))
        begin=time.monotonic();started=time.time_ns()//1_000_000
        def read(metric):
            try:
                if metric not in settings['allowedMetrics']:
                    return dict(schemaVersion=1,metric=metric,ok=False,status='disabled_by_settings')
                if metric=='device_status':return self.device_status(settings,arguments.get('fresh',False))
                if metric=='heart_rate' and not arguments.get('fresh',False):
                    cached=self.heart_rate(settings['heartMaxAgeSeconds'])
                    if cached['ok']:return cached
                return self.measure(metric,heart_wait if metric=='heart_rate' else arguments.get('seconds',settings['measurementSeconds']),settings,arguments.get('fresh',False))
            except (OSError,ValueError) as error:
                return dict(schemaVersion=1,metric=metric,ok=False,status='read_error',error=type(error).__name__)
        with ThreadPoolExecutor(max_workers=5) as workers:
            values=dict(zip(metrics,workers.map(read,metrics)))
        if arguments.get('compact', False):
            values = {metric: self.compact_reading(metric, value) for metric, value in values.items()}
        elif values.get('device_status', {}).get('data') is not None:
            values['device_status'] = self.label_device_status(values['device_status'])
        succeeded=[metric for metric,value in values.items() if value.get('ok') is True]
        return dict(schemaVersion=1,metric='watch_snapshot',ok=bool(succeeded),complete=len(succeeded)==len(metrics),
                    status='ok' if len(succeeded)==len(metrics) else 'partial' if succeeded else 'no_data',
                    requestedMetrics=metrics,results=values,failedMetrics=[metric for metric in metrics if metric not in succeeded],
                    requestedAtMs=started,completedAtMs=time.time_ns()//1_000_000,elapsedMs=round((time.monotonic()-begin)*1000),
                    onDemand=self.on_demand_state(), sourceAgeVerified=False,
                    interpretation='device readings only; no validated scene classification')

    @staticmethod
    def label_device_status(value):
        if value.get('data') is None:
            return value
        return dict(value, data=dict(value['data'], bridgeLifecycleScope='wifi'))

    @staticmethod
    def compact_reading(metric, value):
        result = dict(value)
        samples = result.pop('samples', None)
        if metric in ('accelerometer', 'gyroscope') and result['ok'] and samples:
            axes = list(zip(*(sample['values'] for sample in samples)))
            result['axisSummary'] = dict(min=[min(axis) for axis in axes], max=[max(axis) for axis in axes])
            result['valueMeaning'] = 'min_max_of_retained_watch_xyz_samples'
        if metric == 'device_status' and result.get('data') is not None:
            data = result['data']
            result['data'] = {key: data[key] for key in (
                'batteryPct', 'chargingStatus', 'plugged', 'batteryTemperatureC', 'screenInteractive',
                'wifiAvailable', 'heartRateCaptureRunning', 'activeRequests', 'requestWakeLockHeld',
            ) if key in data}
        return result

    def user_state(self, settings, metrics, *, wear_confirmed):
        metrics = tuple(metric for metric in metrics if metric != 'sleep')
        if not self.state_lock.acquire(blocking=False):
            return dict(ok=False,status='busy',tier=None,label='暂不判断')
        begin = time.time_ns()//1_000_000
        heart = []
        allowed = set(settings['allowedMetrics'])

        def observe(metric):
            if metric not in allowed:
                return dict(ok=False, status='disabled_by_settings')
            try:
                if metric == 'heart_rate':
                    cached = self.heart_rate(settings['heartMaxAgeSeconds'])
                    if cached['ok'] or not wear_confirmed:
                        return cached
                    return self.measure(metric, 15, settings, True)
                if not wear_confirmed:
                    return dict(ok=False, status='wear_unconfirmed')
                return self.measure(metric, 30, settings, True, state_window=True)
            except (OSError, ValueError):
                return dict(ok=False, status='read_error')

        try:
            with ThreadPoolExecutor(max_workers=3) as workers:
                futures = {metric: workers.submit(observe, metric) for metric in metrics}
                while any(not future.done() for future in futures.values()):
                    if 'heart_rate' in futures and 'heart_rate' in allowed:
                        heart.append(self.heart_rate(settings['heartMaxAgeSeconds']))
                    time.sleep(.5)
                raw = {metric: future.result() for metric, future in futures.items()}
            end = time.time_ns()//1_000_000
            allowed = set(self.policy.read()['allowedMetrics'])
            for metric in raw.keys() - allowed:
                raw[metric] = dict(ok=False, status='disabled_by_settings')
            heart_selected = 'heart_rate' in raw and 'heart_rate' in allowed
            heart = heart + [raw['heart_rate']] if heart_selected else None
            if wear_confirmed and {'accelerometer', 'gyroscope'} <= raw.keys():
                result = classify(raw['accelerometer'], raw['gyroscope'], heart, begin, end)
            else:
                result = dict(ok=True, status='observed', tier=None, label='暂不判断', completedAtMs=end)
            results = {}
            for metric, reading in raw.items():
                value = dict(ok=reading['ok'], status=reading['status'])
                if metric == 'heart_rate' and value['ok']:
                    value.update({key: reading.get(key) for key in
                                  ('value', 'unit', 'source', 'receivedAtMs', 'sourceAgeVerified')})
                elif value['ok']:
                    try:
                        bins = motion_bins(reading, 1 if metric == 'accelerometer' else 4)
                        value.update(rms=round(reading['rms'], 3), validSeconds=len(bins), observationSeconds=30)
                    except (ValueError, TypeError, KeyError) as error:
                        value.update(ok=False, status=str(error)[:80])
                results[metric] = value
            if heart_selected and 'heartRate' not in result:
                result['heartRate'] = heart_summary(heart, begin, end)
            result['results'] = results
            if results and any(not item['ok'] for item in results.values()):
                has_data = any(item['ok'] for item in results.values())
                result.update(ok=has_data, status='partial' if has_data else 'insufficient_data')
            return result
        finally:self.state_lock.release()

    def call(self, name, arguments, *, wear_confirmed=False):
        if name in ('send_reminder', 'get_reminder_status'): raise ValueError('Use the shared reminder tool entry')
        if name == 'get_user_state':
            return self.user_state(self.policy.read(), selected_state_metrics(arguments), wear_confirmed=wear_confirmed)
        if not isinstance(arguments, dict): raise ValueError('Tool arguments must be an object')
        tool = next((tool for tool in TOOLS if tool['name'] == name), None)
        if tool is None: raise ValueError('Unknown tool')
        schema = tool['inputSchema']
        if set(arguments) - set(schema['properties']): raise ValueError('Unknown argument')
        if missing := set(schema.get('required', [])) - arguments.keys():
            raise ValueError('Missing required arguments: ' + ', '.join(sorted(missing)))
        exclusive = schema.get('not', {}).get('required')
        if exclusive and all(key in arguments for key in exclusive):
            raise ValueError('Use only one of: ' + ', '.join(exclusive))
        for key, item in arguments.items():
            spec = schema['properties'][key]
            if spec['type'] == 'integer' and (type(item) is not int or not spec['minimum'] <= item <= spec['maximum']):
                raise ValueError(f'{key} outside allowed integer range')
            if spec['type'] == 'boolean' and type(item) is not bool: raise ValueError(f'Invalid {key}')
            if spec['type'] == 'array':
                if (not isinstance(item,list) or not 1<=len(item)<=5 or any(type(v) is not str or v not in spec['items']['enum'] for v in item)
                        or len(item)!=len(set(item))):raise ValueError(f'Invalid {key}')
            if spec['type'] == 'string' and (type(item) is not str or 'pattern' in spec and not re.fullmatch(spec['pattern'], item)):
                raise ValueError(f'Invalid {key}')
            if 'enum' in spec and item not in spec['enum']: raise ValueError(f'Invalid {key}')
        if name == 'get_capabilities': return self.capabilities(arguments.get('compact', False))
        if name == 'cancel_measurement': return self.cancel(arguments.get('request_id'))
        settings = self.policy.read()
        if name == 'read_watch_data':return self.read_watch_data(arguments,settings)
        metric = 'heart_rate' if name in ('get_heart_rate','measure_heart_rate') else 'device_status' if name == 'get_device_status' else 'pressure' if name == 'measure_pressure' else arguments.get('kind', 'accelerometer')
        if metric not in settings['allowedMetrics']:
            return dict(schemaVersion=1, metric=metric, ok=False, status='disabled_by_settings')
        if name == 'get_heart_rate': return self.heart_rate(min(arguments.get('max_age_seconds', settings['heartMaxAgeSeconds']), settings['heartMaxAgeSeconds']))
        if name == 'measure_heart_rate':
            return self.measure('heart_rate', arguments.get('wait_seconds', arguments.get('seconds', 15)), settings, True)
        if name == 'get_device_status':
            result = self.label_device_status(self.device_status(settings, arguments.get('fresh', False)))
            return dict(result, onDemand=self.on_demand_state())
        metric = 'pressure' if name == 'measure_pressure' else arguments.get('kind', 'accelerometer')
        return self.measure(metric, arguments.get('seconds', settings['measurementSeconds']), settings, arguments.get('fresh', False))
