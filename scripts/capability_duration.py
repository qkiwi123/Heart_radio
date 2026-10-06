"""Use the original receiver duration rules; preserve an older saved PC duration."""
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'receiver'))
from control import Deadline, read_settings, save_settings

MAX_SECONDS = 31536000


def duration_path(root):
    return Path(root) / 'runs' / 'capability-duration-settings.json'


def read_duration(root):
    path = duration_path(root)
    if not path.exists():
        legacy = Path(root) / 'runs' / 'data-access' / 'settings.json'
        try:
            value = json.loads(legacy.read_text(encoding='utf-8'))
        except FileNotFoundError:
            value = {}
        except (OSError, ValueError) as error:
            raise OSError('旧数据设置无法读取，未替换电脑服务时长。') from error
        if not isinstance(value, dict):
            raise OSError('旧数据设置格式无效，未恢复为不限时。')
        seconds = value.get('pcServiceSeconds')
        if seconds is not None:
            if type(seconds) is not int or not 60 <= seconds <= 1800:
                raise OSError('旧电脑服务时长无效，未恢复为不限时。')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name('duration-migration.' + uuid.uuid4().hex + '.tmp')
            try:
                save_settings(temporary, dict(unlimited=False, amount=seconds / 60, unit='minutes'))
                # Publish a complete file without overwriting a concurrent explicit save.
                try: os.link(temporary, path)
                except FileExistsError: pass
            finally:
                temporary.unlink(missing_ok=True)
    return read_settings(path)
