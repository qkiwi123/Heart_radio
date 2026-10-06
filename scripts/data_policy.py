"""Saved bounds shared by daily service startup and every local data client."""
import json
from pathlib import Path
import threading
import uuid

METRICS = ('heart_rate', 'device_status', 'pressure', 'accelerometer', 'gyroscope')
DEFAULTS = dict(measurementSeconds=4, measurementCooldownSeconds=15,
                heartMaxAgeSeconds=30, deviceStatusCacheSeconds=15,
                sensorCacheSeconds=0,
                allowedMetrics=list(METRICS))
RANGES = dict(measurementSeconds=(1, 10), measurementCooldownSeconds=(15, 300),
              heartMaxAgeSeconds=(1, 30), deviceStatusCacheSeconds=(0, 300),
              sensorCacheSeconds=(0, 300))


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULTS):
        raise ValueError('请提交完整的数据读取设置。')
    for name, (low, high) in RANGES.items():
        if type(value[name]) is not int or not low <= value[name] <= high:
            raise ValueError(f'{name} 必须是 {low}～{high} 的整数。')
    allowed = value['allowedMetrics']
    if (not isinstance(allowed, list) or any(type(item) is not str or item not in METRICS for item in allowed)
            or len(allowed) != len(set(allowed))):
        raise ValueError('允许读取的指标无效或重复。')
    return dict(value, allowedMetrics=[metric for metric in METRICS if metric in allowed])


class DataPolicy:
    def __init__(self, root):
        self.path = Path(root) / 'runs' / 'data-access' / 'settings.json'
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return dict(DEFAULTS, allowedMetrics=list(METRICS))
            except (OSError, ValueError) as error:
                raise OSError('数据读取设置无法读取，请检查本地设置文件。') from error
            try:
                value.pop('pcServiceSeconds', None)  # Migrated separately; no second duration control.
                return validate(value)
            except (AttributeError, ValueError) as error:
                raise OSError('数据读取设置损坏，未恢复默认或开始采集。') from error

    def save(self, value):
        value = validate(value)
        with self.lock:
            from capability_duration import read_duration
            read_duration(self.path.parents[2])  # Preserve the old duration before removing its field.
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name('settings.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return value
