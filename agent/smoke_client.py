"""Exercise the stdio adapter as a real subprocess; optional bounded watch measurement."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--measure-pressure', action='store_true')
    args = parser.parse_args()
    messages = [dict(jsonrpc='2.0', id=1, method='initialize',
                     params=dict(protocolVersion='2025-06-18', capabilities={}, clientInfo=dict(name='heartradio-smoke-client', version='1'))),
                dict(jsonrpc='2.0', method='notifications/initialized'),
                dict(jsonrpc='2.0', id=2, method='tools/list'),
                dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='get_capabilities', arguments={})),
                dict(jsonrpc='2.0', id=4, method='tools/call', params=dict(name='get_heart_rate', arguments={})),
                dict(jsonrpc='2.0', id=5, method='tools/call', params=dict(name='get_device_status', arguments={}))]
    if args.measure_pressure:
        messages.append(dict(jsonrpc='2.0', id=6, method='tools/call', params=dict(name='measure_pressure', arguments=dict(seconds=4))))
    payload = ''.join(json.dumps(message) + '\n' for message in messages).encode('utf-8')
    result = subprocess.run([sys.executable, '-u', str(Path(__file__).with_name('mcp_server.py'))],
                            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=75,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    if result.returncode: raise RuntimeError('MCP adapter did not exit cleanly')
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    expected = [message['id'] for message in messages if 'id' in message]
    if [response.get('id') for response in responses] != expected: raise RuntimeError('Incorrect response IDs or stdout pollution')
    if any('error' in response for response in responses): raise RuntimeError('MCP protocol error')
    required = {'get_capabilities','get_heart_rate','get_device_status','measure_pressure','measure_motion','cancel_measurement','send_reminder','get_reminder_status','measure_heart_rate'}
    if required - {tool['name'] for tool in responses[1]['result']['tools']}: raise RuntimeError('Missing tools')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(requests=messages, responses=responses, stderr=result.stderr.decode('utf-8')), ensure_ascii=False, indent=2), encoding='utf-8')
    for response in responses[2:]:
        value = response['result']['structuredContent']
        print(json.dumps(dict(id=response['id'], metric=value.get('metric'), status=value.get('status'), ok=value.get('ok'),
                              value=value.get('value'), unit=value.get('unit'))))
    if args.measure_pressure and responses[-1]['result']['structuredContent'].get('ok') is not True:
        raise RuntimeError('Actual pressure measurement failed; see saved evidence')


if __name__ == '__main__':
    main()
