"""Provision only this debug app; requests use loopback API -> paired Wi-Fi TLS, never ADB."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'receiver'))
from lan import LanAdapter

PACKAGE = 'dev.local.watch2.hrprobe'


def adb(args, command, data=None):
    result = subprocess.run([args.adb, '-s', args.serial, *command], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout


def request(op, params=None, port=8768):
    payload = json.dumps(dict(op=op, params=params or {})).encode()
    value = urllib.request.Request(f'http://127.0.0.1:{port}/request', data=payload,
                                  headers={'Content-Type': 'application/json', 'X-HeartRadio-Probe': 'request'})
    with urllib.request.urlopen(value, timeout=max(75, (params or {}).get('seconds', 5) + 20)) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['provision', 'start', 'stop', 'request', 'screening'])
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--serial', help='ADB serial required for device maintenance')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--api-port', type=int, default=8768)
    parser.add_argument('--op', default='inventory')
    parser.add_argument('--sensor-id', type=int)
    parser.add_argument('--provider', default='gps')
    parser.add_argument('--seconds', type=int, default=4)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--vendor', action='store_true', help='also screen visible vendor sensor types')
    parser.add_argument('--manual-start', action='store_true', help='open the watch page without starting its bridge')
    args = parser.parse_args()
    if args.action in ('provision', 'start', 'stop'):
        if not args.serial:
            parser.error('--serial is required for device maintenance')
        for prop, expected in [('ro.product.model', 'OW20W2'), ('ro.product.brand', 'OPPO'), ('ro.build.version.sdk', '27')]:
            if adb(args, ['shell', 'getprop', prop]).decode().strip() != expected:
                raise RuntimeError('Watch identity mismatch')
    if args.action == 'provision':
        directory = ROOT / '.local-wifi'
        config = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
        host = LanAdapter(directory, config['host']).address()
        if not host:
            raise RuntimeError('Selected LAN unavailable')
        value = dict(host=host, port=args.port, pin=config['pin'], token=config['token'])
        # Credentials go only through stdin to this app's private file; never argv, reports or stdout.
        adb(args, ['shell', f"run-as {PACKAGE} sh -c 'cat > files/capability-bridge.json'"], json.dumps(value).encode())
        print(json.dumps(dict(status='provisioned', host=host, port=args.port)))
    elif args.action == 'start':
        command = ['shell', 'am', 'start', '-W', '-n', PACKAGE + '/com.example.heart_radio.CapabilityActivity']
        if not args.manual_start:
            command.extend(['--ez', 'start_bridge', 'true'])
        adb(args, command)
        print('Capability page opened; tap its start button to connect.' if args.manual_start
              else 'Capability bridge screen started; no sensor measurement requested.')
    elif args.action == 'stop':
        adb(args, ['shell', f'run-as {PACKAGE} am stopservice --user 0 -n {PACKAGE}/com.example.heart_radio.CapabilityBridgeService'])
        print('Capability test stop requested.')
    elif args.action == 'request':
        params = {}
        if args.op == 'sample':
            if args.sensor_id is None:
                parser.error('sample requires --sensor-id')
            params = dict(sensorId=args.sensor_id, seconds=args.seconds)
        elif args.op == 'locate':
            params = dict(provider=args.provider, seconds=args.seconds)
        value = request(args.op, params, args.api_port)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: value[k] for k in ('id', 'status', 'callbacks', 'listenersReleased', 'pcRoundTripMs', 'activeRequest') if k in value}))
    else:
        if args.output is None:
            parser.error('screening requires --output directory')
        args.output.mkdir(parents=True, exist_ok=False)
        inventory = request('inventory', port=args.api_port)
        (args.output / 'inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
        if inventory.get('status') != 'ok':
            raise RuntimeError('Inventory failed')
        summary = []
        for sensor in inventory['sensors']:
            if sensor['type'] >= 65536 and not args.vendor:
                continue
            result = request('sample', dict(sensorId=sensor['sensorId'], seconds=args.seconds), args.api_port)
            if 'sensor' in result and (result['sensor']['name'], result['sensor']['type']) != (sensor['name'], sensor['type']):
                raise RuntimeError('Returned sensor does not match inventory; stop rather than record incorrect capability')
            (args.output / f"sensor-{sensor['sensorId']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            row = dict(name=sensor['name'], type=sensor['type'], sensorId=sensor['sensorId'],
                       status=result.get('status'), callbacks=result.get('callbacks'), released=result.get('listenersReleased'))
            summary.append(row)
            (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if result.get('status') in ('watch_offline', 'disconnected', 'request_timeout'):
                raise RuntimeError('Bridge lost; stop screening rather than queue old requests')


if __name__ == '__main__':
    main()
