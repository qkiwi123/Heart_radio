"""Small stdio MCP adapter. All data and measurement limits belong to the local dashboard API."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request
from process_registry import registered

TOOLS = json.loads(Path(__file__).with_name('tools.json').read_text(encoding='utf-8'))

VERSIONS = ('2024-11-05', '2025-06-18')
SERVER_VERSION = '0.13.0'
INSTRUCTIONS = (
    '当需要了解用户的佩戴、睡眠报告、活动情况或相关传感器数据时，可以通过 HeartRadio 读取手表观测。'
    '当需要及时引起用户注意时，可以在用户授权的范围内向手表发送即时提醒。\n\n'
    '在已获授权的任务中，主动用手表提醒用户介入前，先调用 get_user_state({"metrics":[]}) 确认在腕。'
    '离腕或无法确认佩戴时，把待处理事项留在电脑上。用户明确要求立即发送时，可以直接调用 send_reminder。\n\n'
    'Health values may be absent or stale. Sleep reports reflect the most recent vendor event and its time. '
    'Device evidence may differ from the person’s actual state. Unknown is an expected result and must not be automatically retried. '
    'New watch operations require synchronized Agent interaction. Tools cannot enable interaction or continuous heart capture. '
    'Reminders are immediate effects, never automatically replayed. Reuse the same reminder_id when checking uncertain sends. '
    'Issued or displayed does not mean human acknowledgement.'
)
LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def validate(value, schema):
    kind = schema['type']
    if kind == 'object':
        if not isinstance(value, dict) or set(value) - set(schema.get('properties', {})):
            raise ValueError('Unknown or invalid object fields')
        if missing := set(schema.get('required', [])) - value.keys():
            raise ValueError('Missing required arguments: ' + ', '.join(sorted(missing)))
        exclusive = schema.get('not', {}).get('required')
        if exclusive and all(key in value for key in exclusive):
            raise ValueError('Use only one of: ' + ', '.join(exclusive))
        for key, item in value.items(): validate(item, schema['properties'][key])
    elif kind == 'string':
        import re
        if not isinstance(value, str) or not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 8192):
            raise ValueError('Invalid string length')
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value): raise ValueError('Invalid string pattern')
        if any(0xd800 <= ord(c) <= 0xdfff for c in value): raise ValueError('Invalid Unicode')
    elif kind == 'boolean':
        if type(value) is not bool: raise ValueError('Boolean required')
    elif kind == 'integer':
        if type(value) is not int or not schema['minimum'] <= value <= schema['maximum']: raise ValueError('Integer outside bounds')
    elif kind == 'array':
        if not isinstance(value,list) or not schema['minItems']<=len(value)<=schema['maxItems']:raise ValueError('Invalid array length')
        for item in value:validate(item,schema['items'])
        if schema.get('uniqueItems') and len(value)!=len(set(value)):raise ValueError('Duplicate array items')
    else: raise ValueError('Unsupported schema type')
    if 'enum' in schema and value not in schema['enum']: raise ValueError('Invalid enum value')


def local_failure(name, arguments):
    if name == 'send_reminder':
        return dict(ok=False, status='outcome_unknown_local_failure', reminderId=arguments['reminder_id'],
                    automaticallyRetried=False, nextAction='Query this reminder_id; do not send a new ID to retry')
    return dict(ok=False, status='local_service_unavailable')


class Server:
    def __init__(self, port=8766):
        self.port = port
        self.negotiated = None
        self.ready = False

    def invoke(self, name, arguments):
        tool = next((tool for tool in TOOLS if tool['name'] == name), None)
        if tool is None: raise ValueError('Unknown tool')
        validate(arguments, tool['inputSchema'])
        # Make wear-only explicit so an older dashboard cannot interpret {} as all metrics.
        if name == 'get_user_state' and not arguments:
            arguments = {'metrics': []}
        request = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/tools',
            data=json.dumps(dict(name=name, arguments=arguments), allow_nan=False).encode(),
            headers={'Content-Type': 'application/json', 'X-HeartRadio-Action': 'tools'})
        try:
            # The outer wait must include the initial wear query and the full pressure
            # transport deadline, which itself includes the watch's wear check and page.
            timeout = (105 if 'stress' in arguments.get('metrics', []) else 65) if name == 'get_user_state' else 50
            with LOCAL_HTTP.open(request, timeout=timeout) as response:
                data = response.read(65537)
                if len(data) > 65536: raise ValueError('Local tool response exceeds limit')
                value = json.loads(data)
        except urllib.error.HTTPError as error:
            if error.code == 400: raise ValueError('Invalid tool arguments') from error
            return local_failure(name, arguments)
        except (OSError, ValueError):
            return local_failure(name, arguments)
        if not isinstance(value, dict): return local_failure(name, arguments) if name == 'send_reminder' else dict(ok=False, status='invalid_local_result')
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
                             serverInfo=dict(name='heartradio-local', version=SERVER_VERSION),
                             instructions=INSTRUCTIONS)
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
        except ValueError as exc:
            return error(-32602, str(exc))
        except TypeError:
            return error(-32602, 'Invalid tool parameters')
        except OSError:
            return error(-32603, 'Local operation failed')


def run(input_stream, output_stream, server):
    output_lock=threading.Lock()
    pending=threading.BoundedSemaphore(8)
    def send(value):
        with output_lock:
            output_stream.write((json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
            output_stream.flush()

    def invoke(message,limited):
        try:
            value=server.dispatch(message)
            if value is not None:send(value)
        finally:
            if limited:pending.release()

    with ThreadPoolExecutor(max_workers=8) as workers, ThreadPoolExecutor(max_workers=1) as control:
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
            if isinstance(message,dict) and message.get('method')=='tools/call' and server.ready:
                params=message.get('params',{})
                if isinstance(params,dict) and params.get('name')=='cancel_measurement':
                    control.submit(invoke,message,False)
                elif pending.acquire(blocking=False):workers.submit(invoke,message,True)
                else:send(dict(jsonrpc='2.0',id=message.get('id'),error=dict(code=-32000,message='Too many active tool calls')))
            else:
                value=server.dispatch(message)
                if value is not None:send(value)


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
