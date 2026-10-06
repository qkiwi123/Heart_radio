"""Check this installed package without starting receivers or watch services."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def check(root):
    manifest = json.loads((root / 'VERSION.json').read_text(encoding='utf-8'))
    mismatched = [name for name, digest in manifest['files'].items()
                  if not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != digest]
    if mismatched:
        raise ValueError('Package files changed or missing: ' + ', '.join(mismatched))
    runtime = json.loads((root / 'runtime.json').read_text(encoding='utf-8'))
    frames = [dict(jsonrpc='2.0', id=1, method='initialize', params=dict(protocolVersion='2025-06-18')),
              dict(jsonrpc='2.0', method='notifications/initialized'),
              dict(jsonrpc='2.0', id=2, method='tools/list'),
              dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='get_capabilities', arguments={}))]
    process = subprocess.run([runtime['python'], '-u', str(root / 'server.py'), '--port', str(runtime['port'])],
        input=''.join(json.dumps(f) + '\n' for f in frames).encode(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    if process.returncode or [r.get('id') for r in responses] != [1, 2, 3] or any('error' in r for r in responses):
        raise ValueError('MCP handshake or response failed')
    expected = json.loads((root / 'tools.json').read_text(encoding='utf-8'))
    if responses[1]['result']['tools'] != expected:
        raise ValueError('Unexpected tool definitions')
    service = responses[2]['result']['structuredContent']
    return dict(packageValid=True, packageVersion=manifest['packageVersion'], sourceCommit=manifest['sourceCommit'],
        sourceDirty=manifest['sourceDirty'], protocolVersion=responses[0]['result']['protocolVersion'],
        tools=[t['name'] for t in expected], localService=service.get('status'),
        localServiceAvailable=service.get('ok') is True,
        note='Protocol validity does not imply a worn watch or enabled standby. No measurement requested.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        value = check(Path(__file__).resolve().parents[1])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        value = dict(packageValid=False, error=str(error))
    if args.output:
        args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value, ensure_ascii=False))
    raise SystemExit(0 if value['packageValid'] else 1)
