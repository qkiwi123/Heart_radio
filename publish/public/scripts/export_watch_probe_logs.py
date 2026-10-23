"""Export only HeartRadio debug-app evidence from the identified OPPO watch."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'dev.local.watch2.hrprobe'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--serial', required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / 'evidence' / 'capability-probe').resolve()):
        parser.error('Output must stay within this project capability evidence directory')
    output.mkdir(parents=True, exist_ok=True)

    def read(*command):
        return subprocess.run([args.adb, '-s', args.serial, *command], check=True, timeout=30,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    identity = {}
    for prop, expected in [('ro.product.model', 'OW20W2'), ('ro.product.brand', 'OPPO'), ('ro.build.version.sdk', '27')]:
        actual = read('shell', 'getprop', prop).decode().strip()
        if actual != expected:
            raise RuntimeError('Watch identity mismatch')
        identity[prop] = actual
    identity['ro.build.display.id'] = read('shell', 'getprop', 'ro.build.display.id').decode().strip()
    (output / 'device.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')
    (output / 'watch-requests-final.jsonl').write_bytes(read('exec-out', 'run-as', PACKAGE, 'cat', 'files/capability-probe/requests.jsonl'))
    probe_files = read('shell', 'run-as', PACKAGE, 'ls', 'files/capability-probe').decode().split()
    if 'bridge-events.jsonl' in probe_files:
        (output / 'watch-bridge-events.jsonl').write_bytes(read('exec-out', 'run-as', PACKAGE, 'cat', 'files/capability-probe/bridge-events.jsonl'))
    services = read('shell', 'dumpsys', 'activity', 'services', PACKAGE)
    (output / 'activity-services-final.txt').write_bytes(services)
    service_names = sorted(set(re.findall(rb'com\.example\.heart_radio\.[A-Za-z]+Service', services)))
    power = read('shell', 'dumpsys', 'power').decode(errors='replace')
    wake_lines = [line for line in power.splitlines() if 'HeartRadio:capability-request' in line]
    (output / 'capability-wake-lock-final.json').write_text(json.dumps(dict(heldMatchingLines=wake_lines), indent=2), encoding='utf-8')
    names = read('shell', 'run-as', PACKAGE, 'ls', 'files/logs').decode().split()
    logs = sorted(n for n in names if re.fullmatch(r'session-[0-9-]+\.jsonl', n))
    if not logs:
        raise RuntimeError('No own-app HR log available')
    latest = logs[-1]
    (output / latest).write_bytes(read('exec-out', 'run-as', PACKAGE, 'cat', 'files/logs/' + latest))
    print(json.dumps(dict(exportedHeartLog=latest, services=[s.decode() for s in service_names],
                          capabilityRequestWakeLocks=len(wake_lines))))


if __name__ == '__main__':
    main()
