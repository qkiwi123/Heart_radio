"""Small stdio MCP adapter. All data and measurement limits belong to the local dashboard API."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
from process_registry import registered

TOOLS = json.loads(Path(__file__).with_name('tools.json').read_text(encoding='utf-8'))

VERSIONS = ('2024-11-05', '2025-06-18')


class Server:
    def __init__(self, port=8766):
        self.port = port
        self.negotiated = None
        self.ready = False

    def invoke(self, name, arguments):
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/tools',
            data=json.dumps(dict(name=name, arguments=arguments), allow_nan=False).encode(),
            headers={'Content-Type': 'application/json', 'X-HeartRadio-Action': 'tools'})
        try:
            with urllib.request.urlopen(request, timeout=50) as response:
                data = response.read(65537)
                if len(data) > 65536: raise ValueError('Local tool response exceeds limit')
                value = json.loads(data)
        except urllib.error.HTTPError as error:
            if error.code == 400: raise ValueError('Invalid tool arguments') from error
            return dict(ok=False, status='local_service_unavailable')
        except (OSError, ValueError):
            return dict(ok=False, status='local_service_unavailable')
        if not isinstance(value, dict): return dict(ok=False, status='invalid_local_result')
        return value

    def dispatch(self, message):
        request_id = message.get('id') if isinstance(message, dict) else None

        def error(code, text):
            return dict(jsonrpc='2.0', id=request_id, error=dict(code=code, message=text))

        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            return error(-32600, 'Invalid request')
        if 'id' in message and type(request_id) not in (int, str): return error(-32600, 'Invalid request ID')
        method = message['method']
        params = message.get('params', {})
        if 'id' not in message:
            if method == 'notifications/initialized' and self.negotiated: self.ready = True
            return None
        if not isinstance(params, dict): return error(-32602, 'Params must be an object')
        try:
            if method == 'initialize':
                if self.negotiated: return error(-32600, 'Already initialized')
                if not isinstance(params.get('protocolVersion'), str): raise ValueError('protocolVersion required')
                self.negotiated = params['protocolVersion'] if params['protocolVersion'] in VERSIONS else VERSIONS[-1]
                value = dict(protocolVersion=self.negotiated, capabilities=dict(tools=dict(listChanged=False)),
                             serverInfo=dict(name='heartradio-local', version='0.3.1'),
                             instructions='Health values may be absent or stale. Do not infer steps, location, SpO2 or ECG from raw channels. Measurement requires explicit watch standby; no remote app launch.')
            elif method == 'ping':
                value = {}
            elif not self.ready:
                return error(-32000, 'Initialize and send notifications/initialized first')
            elif method == 'tools/list':
                if params.get('cursor'): raise ValueError('No paginated tool list')
                value = dict(tools=TOOLS)
            elif method == 'tools/call':
                name = params.get('name')
                if name not in [tool['name'] for tool in TOOLS]: raise ValueError('Unknown tool')
                result = self.invoke(name, params.get('arguments', {}))
                value = dict(content=[dict(type='text', text=json.dumps(result, ensure_ascii=False, allow_nan=False))],
                             isError=result.get('ok') is not True)
                if self.negotiated == '2025-06-18': value['structuredContent'] = result
            else:
                return error(-32601, 'Method not found')
            return dict(jsonrpc='2.0', id=request_id, result=value)
        except (ValueError, TypeError):
            return error(-32602, 'Invalid tool parameters')
        except OSError:
            return error(-32603, 'Local operation failed')


def run(input_stream, output_stream, server):
    def send(value):
        output_stream.write((json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
        output_stream.flush()

    while True:
        line = input_stream.readline(8193)
        if not line: break
        if len(line) > 8192:
            send(dict(jsonrpc='2.0', id=None, error=dict(code=-32600, message='Request exceeds 8192 bytes')))
            break
        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            send(dict(jsonrpc='2.0', id=None, error=dict(code=-32700, message='Parse error')))
            continue
        value = server.dispatch(message)
        if value is not None: send(value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535: parser.error('Invalid local port')
    try:
        with registered(Path(__file__)):
            run(sys.stdin.buffer, sys.stdout.buffer, Server(args.port))
    except (BrokenPipeError, KeyboardInterrupt):
        pass
