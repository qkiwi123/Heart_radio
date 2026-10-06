"""Generate the standalone MCP deployment; never register it with an Agent."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION = '0.3.1'


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.heartradio-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream: stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def export(destination, python, port=8766, allow_dirty=False):
    destination = destination.resolve()
    python = python.resolve()
    if not python.is_file(): raise ValueError('Python executable does not exist')
    if not 1 <= port <= 65535: raise ValueError('Invalid local port')
    if destination == ROOT or destination in ROOT.parents:
        raise ValueError('Deployment must not replace the project or its parent')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    if dirty and not allow_dirty: raise ValueError('Commit source changes first, or explicitly use --allow-dirty for development')
    previous = None
    if destination.exists() and any(destination.iterdir()):
        manifest = destination / 'VERSION.json'
        if not manifest.is_file(): raise ValueError('Nonempty destination is not a managed HeartRadio package')
        previous = json.loads(manifest.read_text(encoding='utf-8'))
        if previous.get('package') != 'heartradio': raise ValueError('Destination belongs to a different package')
    command = str(python).replace('\\', '/')
    server = str(destination / 'server.py').replace('\\', '/')
    entry = dict(command=command, args=['-u', server, '--port', str(port)],
                 supports_parallel_tool_calls=False, timeout=60, connect_timeout=15,
                 sampling=dict(enabled=False))
    files = {}
    for name, source in {
        'server.py': 'agent/mcp_server.py', 'process_registry.py': 'agent/process_registry.py', 'tools.json': 'agent/tools.json',
        'README.md': 'agent/package/README.md', 'scripts/check.py': 'agent/package/check.py',
        'scripts/configure_hermes.py': 'agent/package/configure_hermes.py'
    }.items(): files[name] = (ROOT / source).read_bytes()
    files['runtime.json'] = json_bytes(dict(python=command, port=port,
        endpoint=f'http://127.0.0.1:{port}/api/tools', sourceProject=str(ROOT),
        transport='stdio', automaticAgentRegistration=False))
    files['clients/hermes.json'] = json_bytes(dict(mcp_servers=dict(heartradio=entry)))
    files['clients/hermes.yaml.example'] = ('mcp_servers:\n  heartradio: ' + json.dumps(entry) + '\n').encode('utf-8')
    files['clients/generic.json.example'] = json_bytes(dict(mcpServers=dict(heartradio=dict(command=command, args=entry['args']))))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    if previous:
        backup = destination / 'backups' / stamp
        for name in [*previous.get('files', {}), 'VERSION.json']:
            # Old manifests are data, never unrestricted filesystem paths.
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts or not (destination / rel).resolve().is_relative_to(destination):
                raise ValueError('Unsafe previous manifest path')
            source = destination / rel
            if source.is_file(): atomic_write(backup / rel, source.read_bytes())
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    version = dict(package='heartradio', packageVersion=PACKAGE_VERSION, sourceCommit=commit,
                   sourceDirty=dirty, generatedAtUtc=stamp, protocols=['2024-11-05', '2025-06-18'], files=hashes)
    for name, content in files.items(): atomic_write(destination / name, content)
    # Publish manifest last. A reader rejects a partially updated package by hash.
    atomic_write(destination / 'VERSION.json', json_bytes(version))
    index = destination.parent / 'README.md'
    before = index.read_text(encoding='utf-8-sig') if index.exists() else '# 本机 MCP 管理目录\n\n此目录用于统一存放可接入本机 Agent 的工具包。各包由对应项目导出；新增 Agent 先阅读包内 README，再合并自己的配置。\n'
    start, end = '<!-- heartradio:start -->', '<!-- heartradio:end -->'
    block = f'{start}\n\n- [手表心率与按需读取](./{destination.name}/README.md)：原心率缓存、设备状态、短时气压及运动原始数据。安装由用户手动启动 Agent 后完成，目录导出不会自动注册。\n\n{end}'
    if start in before and end in before:
        a, b = before.index(start), before.index(end) + len(end)
        before = before[:a] + block + before[b:]
    else:
        before = before.rstrip() + '\n\n' + block + '\n'
    atomic_write(index, before.encode('utf-8'))
    return dict(destination=str(destination), packageVersion=PACKAGE_VERSION, sourceCommit=commit,
                sourceDirty=dirty, files=len(files), agentConfigModified=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, default=Path(r'D:\AIWorkplace\MCP\heartradio'))
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--allow-dirty', action='store_true')
    args = parser.parse_args()
    print(json.dumps(export(args.destination, args.python, args.port, args.allow_dirty), ensure_ascii=False))
