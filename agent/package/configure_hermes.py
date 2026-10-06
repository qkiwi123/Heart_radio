"""Optional explicit configuration helper, run by the user's Agent using its Hermes Python."""
import argparse
import copy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone


def merge(config_path, entry, yaml_module, action='add', apply=False):
    original = config_path.read_bytes() if config_path.exists() else b''
    editor = yaml_module.roundtrip_yaml()
    document = editor.load(original.decode('utf-8-sig'))
    if document is None: document = {}
    if not isinstance(document, dict): raise ValueError('Config must be a YAML mapping')
    before = copy.deepcopy(dict(document))
    servers = document.get('mcp_servers')
    if servers is None:
        servers = {}
    if not isinstance(servers, dict): raise ValueError('mcp_servers must be a mapping')
    if action == 'add':
        if servers.get('heartradio') == entry:
            return dict(action='unchanged', config=str(config_path), applied=False)
        if document.get('mcp_servers') is None: document['mcp_servers'] = servers
        document['mcp_servers']['heartradio'] = entry
    else:
        if 'heartradio' not in servers:
            return dict(action='unchanged', config=str(config_path), applied=False)
        del servers['heartradio']
        if not servers: document.pop('mcp_servers', None)
    output = StringIO()
    editor.dump(document, output)
    rendered = output.getvalue()
    parsed = yaml_module.safe_load(rendered)
    if parsed != document: raise ValueError('YAML value preservation check failed')
    unchanged_before = copy.deepcopy(before)
    unchanged_after = copy.deepcopy(dict(parsed))
    for value in (unchanged_before, unchanged_after):
        if value.get('mcp_servers') is None: value.pop('mcp_servers', None)
        value.get('mcp_servers', {}).pop('heartradio', None)
        if value.get('mcp_servers') == {}: value.pop('mcp_servers', None)
    if unchanged_before != unchanged_after: raise ValueError('Unrelated config would change')
    result = dict(action=action, config=str(config_path), applied=apply,
                  beforeSha256=hashlib.sha256(original).hexdigest())
    if not apply: return result
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if (config_path.read_bytes() if config_path.exists() else b'') != original:
        raise ValueError('Config changed during preparation; run again')
    backup = None
    if original:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = config_path.with_name(config_path.name + '.heartradio-' + stamp + '.bak')
        with backup.open('xb') as stream: stream.write(original)
        os.chmod(backup, 0o600)
    fd, temporary = tempfile.mkstemp(prefix='.heartradio-', suffix='.tmp', dir=config_path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        if (config_path.read_bytes() if config_path.exists() else b'') != original:
            raise ValueError('Config changed before save; run again')
        os.replace(temporary, config_path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    result['backup'] = str(backup) if backup else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-source', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--action', choices=['add', 'remove'], default='add')
    parser.add_argument('--apply', action='store_true', help='Without this flag only validate and preview')
    args = parser.parse_args()
    if not args.hermes_source.is_absolute() or not args.config.is_absolute():
        parser.error('Hermes source and config paths must be absolute')
    sys.path.insert(0, str(args.hermes_source))
    import hermes_bootstrap
    import hermes_yaml
    root = Path(__file__).resolve().parents[1]
    example = json.loads((root / 'clients' / 'hermes.json').read_text(encoding='utf-8'))
    print(json.dumps(merge(args.config, example['mcp_servers']['heartradio'], hermes_yaml,
                           args.action, args.apply), ensure_ascii=False))


if __name__ == '__main__':
    main()
