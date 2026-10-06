"""Persist receiver preferences atomically; zero means unlimited."""
import json
import math
import os
from pathlib import Path
import time
import uuid


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_settings(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return dict(seconds=0, revision='default', savedMs=None, savedMonotonic=None)
    seconds = value['seconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('接收时长设置无效')
    return value


def save_settings(path, request):
    unlimited = request['unlimited']
    if type(unlimited) is not bool:
        raise ValueError('不限时必须为布尔值')
    seconds = 0
    if not unlimited:
        amount, unit = request['amount'], request['unit']
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
            raise ValueError('请输入大于零的时长')
        if unit not in ('minutes', 'hours'):
            raise ValueError('请选择分钟或小时')
        seconds = amount * (60 if unit == 'minutes' else 3600)
        if not math.isfinite(seconds) or not 1 <= seconds <= 31536000:
            raise ValueError('时长范围为 1 秒至 365 天')
    value = dict(seconds=seconds, revision=uuid.uuid4().hex,
                 savedMs=time.time_ns() // 1_000_000, savedMonotonic=time.monotonic())
    atomic_json(path, value)
    return value


class Deadline:
    def __init__(self, seconds, settings=None):
        self.revision = settings['revision'] if settings else 'command-line'
        self.reset(seconds, time.monotonic(), time.time_ns() // 1_000_000)

    def reset(self, seconds, monotonic, wall_ms):
        self.seconds = seconds
        self.ends = monotonic + seconds if seconds else None
        self.ends_ms = wall_ms + seconds * 1000 if seconds else None

    def apply(self, settings):
        if settings['revision'] == self.revision:
            return False
        self.revision = settings['revision']
        self.reset(settings['seconds'], settings['savedMonotonic'], settings['savedMs'])
        return True

    def expired(self):
        return self.ends is not None and time.monotonic() >= self.ends

    def fields(self):
        return dict(seconds=self.seconds, revision=self.revision, endsMs=self.ends_ms)
