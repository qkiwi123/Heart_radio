"""Shared local tools: HR cache reads and explicitly requested bounded standard sensors."""
import json
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

TOOLS = json.loads((Path(__file__).resolve().parents[1] / 'agent' / 'tools.json').read_text(encoding='utf-8'))


def bridge_request(op, params=None, request_id=None):
    payload = dict(op=op, params=params or {})
    if request_id is not None: payload['requestId'] = request_id
    request = urllib.request.Request('http://127.0.0.1:8768/request',
        data=json.dumps(payload, allow_nan=False).encode(),
        headers={'Content-Type': 'application/json', 'X-HeartRadio-Probe': 'request'})
    try:
        with urllib.request.urlopen(request, timeout=4 if op == 'cancel' else 22) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError('Bridge response exceeds limit')
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError('Invalid bridge response')
            return value
    except (OSError, ValueError):
        return dict(status='bridge_unavailable')


def bridge_state():
    # Computer connection metadata only; no request is sent to the watch.
    try:
        with urllib.request.urlopen('http://127.0.0.1:8768/state', timeout=1) as response:
            data = response.read(8193)
        value = json.loads(data) if len(data) <= 8192 else None
        if not isinstance(value, dict): raise ValueError('Invalid connection state')
        return value
    except (OSError, ValueError):
        return dict(status='bridge_unavailable', connected=False)


class DataAccess:
    def __init__(self, root, heart_snapshot, bridge=bridge_request, connection=bridge_state):
        self.directory = Path(root) / 'runs' / 'data-access'
        self.heart_snapshot = heart_snapshot
        self.bridge = bridge
        self.connection = connection
        self.policy = DataPolicy(root)
        self.measure_lock = threading.Lock()
        self.active_lock = threading.Lock()
        self.active = None
        self.cancel_event = None
        self.status_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.last_measure_end = -math.inf
        self.cache = {}
        try:
            value = json.loads((self.directory / 'latest.json').read_text(encoding='utf-8'))
            if isinstance(value, dict): self.cache = value
        except (OSError, ValueError):
            pass
        try:
            ended_ms = json.loads((self.directory / 'cooldown.json').read_text(encoding='utf-8'))['endedAtMs']
            age = max(0, min(300, (time.time_ns() // 1_000_000 - ended_ms) / 1000))
            self.last_measure_end = time.monotonic() - age
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def measurement_state(self):
        with self.active_lock:
            return dict(self.active) if self.active else None

    def cancel(self, request_id=None):
        with self.active_lock:
            active = dict(self.active) if self.active else None
            if not active or request_id is not None and request_id != active['requestId']:
                return dict(schemaVersion=1, ok=False, status='no_active_measurement')
            self.cancel_event.set()
            self.active['cancelRequested'] = True
        # During inventory no sensor has started. The worker checks this flag before sampling.
        if active['phase'] == 'preparing':
            return dict(schemaVersion=1, ok=True, status='cancel_requested', requestId=active['requestId'])
        until = time.monotonic() + 3
        while time.monotonic() < until:
            raw = self.bridge('cancel', dict(targetId=active['requestId']))
            if raw.get('status') != 'not_active':
                return dict(schemaVersion=1, ok=raw.get('status') == 'ok',
                            status='cancel_requested' if raw.get('status') == 'ok' else raw.get('status', 'error'),
                            requestId=active['requestId'])
            current = self.measurement_state()
            if not current or current['requestId'] != active['requestId']:
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

    def capabilities(self):
        settings = self.policy.read()
        return dict(schemaVersion=1, ok=True, status='ok',
            verifiedDevice=dict(model='OW20W2', sdk=27, firmware='OW20W2_11_A.92'),
            available=['heart_rate', 'device_status', 'pressure', 'accelerometer', 'gyroscope'],
            unavailable=dict(location='No fresh GPS fix in the real test; not exposed as a default tool',
                             steps='No standard step callbacks during walking tests',
                             wear_state='No validated wear-state interpretation',
                             light='Cover/uncover did not change the standard lux value',
                             spo2='Vendor raw callbacks have no validated conversion',
                             ecg='Vendor raw callbacks have no validated conversion'),
            enabled=settings['allowedMetrics'], settings=settings, measurement=self.measurement_state(),
            limits=dict(maxMeasurementSeconds=10, measurementCooldownSeconds=settings['measurementCooldownSeconds'],
                        maxHeartReceiptAgeSeconds=settings['heartMaxAgeSeconds'], bridgeStandbyDefaultMinutes=30,
                        maxBridgeStandbySeconds=31536000, bridgeStandbyUnlimitedSupported=True,
                        watchDurationFollowComputerSupported=True),
            lifecycle='Explicit standby with a saved duration; unexpected disconnect rediscovers the paired computer; explicit stop does not reconnect',
            recovery=dict(networkReconnect=True, renewsStandbyDeadline=False, replaysMeasurements=False,
                          systemStickyRestart='implemented; vendor process-reclaim behavior not validated',
                          forceStopRecovery=False, rebootAutostart=False, changedComputerAddressDiscovery=True),
            sourceAgeVerified=False, longTermStabilityVerified=False, batteryCostMeasured=False)

    def heart_rate(self, maximum_age):
        state = self.heart_snapshot()
        age = state.get('eventAgeSeconds')
        ok = state.get('valid') is True and age is not None and age <= maximum_age
        status = 'ok' if ok else 'disconnected' if not state.get('connected') else 'stale' if age is not None and age > maximum_age else 'no_valid_data'
        return dict(schemaVersion=1, metric='heart_rate', ok=ok, status=status, value=state.get('bpm') if ok else None,
                    unit='bpm', receiptAgeSeconds=age, maximumReceiptAgeSeconds=maximum_age,
                    receivedAtMs=state.get('eventReceivedMs', state['nowMs'] - int(age * 1000) if age is not None else None),
                    sourceTimestampMs=state.get('measurementMs'), sourceAgeVerified=False,
                    sourceTimestampBasis='watch_receipt_approximation',
                    source='original_wifi_heart_rate_v1_cache', timeBasis='pc_receipt',
                    quality=dict(accuracy=state.get('accuracy'), reason=state.get('status')))

    def save(self, key, value):
        with self.cache_lock:
            self.cache[key] = value
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / 'latest.tmp'
            temporary.write_text(json.dumps(self.cache, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temporary.replace(self.directory / 'latest.json')
            with (self.directory / 'tools.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
        return value

    def cached(self):
        settings = self.policy.read()
        with self.cache_lock:
            values = json.loads(json.dumps(self.cache))
        now = time.time_ns() // 1_000_000
        for value in values.values():
            raw_age = (now - value['receivedAtMs']) / 1000
            value['cacheAgeSeconds'] = max(0, raw_age)
            value['cached'] = True
            limit = settings['deviceStatusCacheSeconds'] if value['metric'] == 'device_status' else settings['sensorCacheSeconds']
            value['cacheReusable'] = value.get('ok') is True and limit > 0 and 0 <= raw_age <= limit
        values = {key:value for key,value in values.items() if key in settings['allowedMetrics']}
        return dict(schemaVersion=1, heartRate=self.heart_rate(settings['heartMaxAgeSeconds']) if 'heart_rate' in settings['allowedMetrics'] else None,
                    measurements=values, bridge=self.connection(), measurement=self.measurement_state(), settings=settings)

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
                  'wifiAvailable', 'heartRateCaptureRunning', 'activeRequest', 'requestWakeLockHeld', 'bridgeLifecycle')
        value = dict(schemaVersion=1, metric='device_status', ok=ok, status=raw.get('status', 'error'),
                     receivedAtMs=time.time_ns() // 1_000_000, source='android_public_device_state',
                     sourceAgeVerified=False, data={key: raw.get(key) for key in fields} if ok else None)
        if ok:
            temp = raw.get('batteryTemperatureTenthsC')
            value['data']['batteryTemperatureC'] = temp / 10 if isinstance(temp, (int, float)) and temp >= 0 else None
        return self.save('device_status', value)

    def measure(self, metric, seconds, settings, fresh=False):
        cached = None if fresh else self.recent(metric, settings['sensorCacheSeconds'])
        if cached and cached.get('requestedSeconds') == seconds: return cached
        if not self.measure_lock.acquire(blocking=False):
            return dict(schemaVersion=1, metric=metric, ok=False, status='busy')
        try:
            remaining = settings['measurementCooldownSeconds'] - (time.monotonic() - self.last_measure_end)
            if remaining > 0:
                return dict(schemaVersion=1, metric=metric, ok=False, status='rate_limited', retryAfterSeconds=math.ceil(remaining))
            request_id = uuid.uuid4().hex
            with self.active_lock:
                self.cancel_event = threading.Event()
                self.active = dict(requestId=request_id, metric=metric, seconds=seconds, phase='preparing',
                                   startedAtMs=time.time_ns() // 1_000_000, cancelRequested=False)
            inventory = self.bridge('inventory')
            with self.active_lock:
                if self.cancel_event.is_set():
                    return dict(schemaVersion=1, metric=metric, ok=False, status='cancelled', requestId=request_id,
                                listenersReleased=True, requestWakeLockHeldAtResult=False)
            if inventory.get('status') != 'ok':
                return dict(schemaVersion=1, metric=metric, ok=False, status=inventory.get('status', 'error'))
            if inventory.get('model') != 'OW20W2' or inventory.get('sdk') != 27:
                return dict(schemaVersion=1, metric=metric, ok=False, status='unverified_device')
            if inventory.get('firmware') != 'OW20W2_11_A.92':
                return dict(schemaVersion=1, metric=metric, ok=False, status='unverified_firmware')
            typ, width, unit = {'pressure': (6, 1, 'hPa'), 'accelerometer': (1, 3, 'm/s^2'), 'gyroscope': (4, 3, 'rad/s')}[metric]
            sensors = [sensor for sensor in inventory['sensors'] if sensor['type'] == typ]
            if len(sensors) != 1:
                return dict(schemaVersion=1, metric=metric, ok=False, status='not_available')
            sensor = sensors[0]
            with self.active_lock:
                if self.cancel_event.is_set():
                    return dict(schemaVersion=1, metric=metric, ok=False, status='cancelled', requestId=request_id,
                                listenersReleased=True, requestWakeLockHeldAtResult=False)
                self.active['phase'] = 'measuring'
            raw = self.bridge('sample', dict(sensorId=sensor['sensorId'], seconds=seconds), request_id=request_id)
            self.last_measure_end = time.monotonic()
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = self.directory / 'cooldown.tmp'
            temporary.write_text(json.dumps(dict(endedAtMs=time.time_ns() // 1_000_000)), encoding='utf-8')
            temporary.replace(self.directory / 'cooldown.json')
            ok = (raw.get('status') == 'data_received' and raw.get('listenersReleased') is True
                  and raw.get('requestWakeLockHeldAtResult') is False)
            status = raw.get('status', 'error')
            if raw.get('status') == 'data_received' and not ok:
                status = 'cleanup_unconfirmed'
            if ok and (raw.get('sensor', {}).get('name'), raw.get('sensor', {}).get('type')) != (sensor['name'], typ):
                ok = False
                status = 'sensor_mismatch'
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
                         value=statistics.mean(s['values'][0] for s in samples) if ok and metric == 'pressure' else None,
                         valueMeaning='mean_of_retained_samples' if metric == 'pressure' else 'raw_watch_axes',
                         quality='standard_api_units; no independent calibration',
                         listenersReleased=raw.get('listenersReleased'), requestWakeLockHeldAtResult=raw.get('requestWakeLockHeldAtResult'))
            return self.save(metric, value)
        finally:
            with self.active_lock:
                self.active = None
                self.cancel_event = None
            self.measure_lock.release()

    def call(self, name, arguments):
        if not isinstance(arguments, dict): raise ValueError('Tool arguments must be an object')
        tool = next((tool for tool in TOOLS if tool['name'] == name), None)
        if tool is None: raise ValueError('Unknown tool')
        if set(arguments) - set(tool['inputSchema']['properties']): raise ValueError('Unknown argument')
        for key, item in arguments.items():
            spec = tool['inputSchema']['properties'][key]
            if spec['type'] == 'integer' and (type(item) is not int or not spec['minimum'] <= item <= spec['maximum']):
                raise ValueError(f'{key} outside allowed integer range')
            if spec['type'] == 'boolean' and type(item) is not bool: raise ValueError(f'Invalid {key}')
            if spec['type'] == 'string' and (type(item) is not str or 'pattern' in spec and not re.fullmatch(spec['pattern'], item)):
                raise ValueError(f'Invalid {key}')
            if 'enum' in spec and item not in spec['enum']: raise ValueError(f'Invalid {key}')
        if name == 'get_capabilities': return self.capabilities()
        if name == 'cancel_measurement': return self.cancel(arguments.get('request_id'))
        settings = self.policy.read()
        metric = 'heart_rate' if name == 'get_heart_rate' else 'device_status' if name == 'get_device_status' else 'pressure' if name == 'measure_pressure' else arguments.get('kind', 'accelerometer')
        if metric not in settings['allowedMetrics']:
            return dict(schemaVersion=1, metric=metric, ok=False, status='disabled_by_settings')
        if name == 'get_heart_rate': return self.heart_rate(min(arguments.get('max_age_seconds', settings['heartMaxAgeSeconds']), settings['heartMaxAgeSeconds']))
        if name == 'get_device_status': return self.device_status(settings, arguments.get('fresh', False))
        metric = 'pressure' if name == 'measure_pressure' else arguments.get('kind', 'accelerometer')
        return self.measure(metric, arguments.get('seconds', settings['measurementSeconds']), settings, arguments.get('fresh', False))
