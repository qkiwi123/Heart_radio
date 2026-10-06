"""Export an explicitly reviewed source-only snapshot; never push or rewrite local history."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BRANCH = 'codex/github-public'
MESSAGE = 'Publish source-only HeartRadio snapshot'
BLOCKED_PARTS = {'evidence', 'runs', '.local-wifi', '.venv', '.idea', '.git', '__pycache__', 'build', 'backups'}
BLOCKED_NAMES = {'local.properties', 'TEST-REPORT.md', 'INTEGRATION-REPORT.md', 'FINAL-ACCEPTANCE.md'}
SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{40,}')


def git(*args, data=None, env=None):
    result = subprocess.run(['git', *args], cwd=ROOT, input=data, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout


def safe_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError(f'Unsafe export path: {name}')
    if set(path.parts) & BLOCKED_PARTS or path.name in BLOCKED_NAMES:
        raise ValueError(f'Private/generated path is not publishable: {name}')
    if path.suffix.lower() in {'.pem', '.key', '.p12', '.jks', '.apk', '.jsonl', '.sqlite', '.db'}:
        raise ValueError(f'Private/generated file type is not publishable: {name}')
    return name


def checked_bytes(name):
    safe_path(name)
    path = ROOT / name
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
        raise ValueError(f'Missing file or unsafe link: {name}')
    return path.read_bytes()


def scan(files):
    for name, content in files.items():
        safe_path(name)
        if SECRET.search(content):
            raise ValueError(f'Possible credential in {name}; review without printing its contents')
        # Only the deliberately generated fixture is allowed as a data file.
        if name.endswith('.csv') and name != 'app/src/test/resources/synthetic-session.csv':
            raise ValueError(f'Unreviewed data fixture: {name}')


def public_parent(allowed):
    result = subprocess.run(['git', 'rev-parse', '--verify', f'refs/heads/{BRANCH}'], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        return None
    head = result.stdout.decode().strip()
    for row in git('rev-list', '--parents', head).decode().splitlines():
        if len(row.split()) > 2:
            raise ValueError('Public history must be a separate linear history, without merges')
        commit = row.split()[0]
        subject = git('show', '-s', '--format=%s', commit).decode().strip()
        email = git('show', '-s', '--format=%ae%n%ce', commit).decode().splitlines()
        if subject != MESSAGE or any(not value.endswith('@users.noreply.github.com') for value in email):
            raise ValueError('Existing public branch is not a managed source-only history')
        paths = git('ls-tree', '-r', '--name-only', '-z', commit).decode().rstrip('\0').split('\0')
        if not set(paths).issubset(allowed):
            raise ValueError('Existing public history has unreviewed files')
        scan({name: git('show', f'{commit}:{name}') for name in paths})
    return head


def export(create_branch=False, author='HeartRadio <heartradio@users.noreply.github.com>'):
    manifest = json.loads((ROOT / 'publish/manifest.json').read_text(encoding='utf-8'))
    files = {name: checked_bytes(name) for name in manifest['sources']}
    for target, rule in manifest['overrides'].items():
        safe_path(target)
        content = checked_bytes(rule['template'])
        if rule.get('sourceSha256'):
            original = checked_bytes(target)
            if hashlib.sha256(original).hexdigest() != rule['sourceSha256'] and original != content:
                raise ValueError(f'{target} changed: review/update the public override before publishing')
        files[target] = content
    # A fixture generated from arithmetic, without reading any personal recording.
    lines = ['# Synthetic regression data; bpm,accuracy,eventNs,arrivalNs,wallMs']
    for index in range(442):
        event = 2_000_000_000 + index * 1_000_000_000
        delay = 2_000_000_000 if index < 11 else 40_000_000_000
        lines.append(f'{60 + index % 20 if index < 441 else 0},{3 if index < 441 else -1},{event},{event + delay},{1000 + index * 1000}')
    files['app/src/test/resources/synthetic-session.csv'] = ('\n'.join(lines) + '\n').encode()
    scan(files)
    parent = public_parent(set(files))
    source = git('rev-parse', 'HEAD').decode().strip()
    if create_branch and git('status', '--porcelain').strip():
        raise ValueError('Commit local source changes first; previews may be generated before committing')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = ROOT / 'runs' / f'github-public-{stamp}'
    directory.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    # These source links and audit details stay beside, never inside, the published tree.
    audit = dict(sourceCommit=source, preview=str(directory), files=len(files),
                 bytes=sum(map(len, files.values())), branch=BRANCH, parent=parent, pushed=False,
                 hashes={name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())})
    if create_branch:
        match = re.fullmatch(r'([^<>\r\n]+) <([^<>\s]+@users\.noreply\.github\.com)>', author)
        if not match:
            raise ValueError('Use a public noreply author, never your private email')
        with tempfile.TemporaryDirectory(prefix='public-index-', dir=ROOT / 'runs') as temporary:
            env = os.environ.copy()
            env.update(GIT_INDEX_FILE=str(Path(temporary) / 'index'),
                       GIT_AUTHOR_NAME=match[1], GIT_AUTHOR_EMAIL=match[2],
                       GIT_COMMITTER_NAME=match[1], GIT_COMMITTER_EMAIL=match[2])
            entries = []
            for name, content in sorted(files.items()):
                blob = git('hash-object', '-w', '--stdin', data=content).decode().strip()
                mode = '100755' if name == 'gradlew' else '100644'
                entries.append(f'{mode} {blob}\t{name}\0'.encode('utf-8'))
            git('read-tree', '--empty', env=env)
            git('update-index', '-z', '--index-info', data=b''.join(entries), env=env)
            tree = git('write-tree', env=env).decode().strip()
            old_tree = git('rev-parse', f'{parent}^{{tree}}').decode().strip() if parent else None
            if tree == old_tree:
                commit = parent
            else:
                args = ['commit-tree', tree]
                if parent:
                    args.extend(['-p', parent])
                commit = git(*args, data=(MESSAGE + '\n').encode(), env=env).decode().strip()
                git('update-ref', f'refs/heads/{BRANCH}', commit, parent or '0' * 40)
            audit.update(publicCommit=commit, tree=tree)
    report = directory.with_suffix('.audit.json')
    report.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {key: audit[key] for key in ('preview', 'files', 'bytes', 'branch', 'parent', 'pushed', 'publicCommit') if key in audit}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--create-branch', action='store_true', help='Create/update only the isolated local public branch; never push')
    parser.add_argument('--author', default='HeartRadio <heartradio@users.noreply.github.com>')
    args = parser.parse_args()
    print(json.dumps(export(args.create_branch, args.author), ensure_ascii=False))
