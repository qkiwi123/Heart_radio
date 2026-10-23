"""One source identity shared by the running dashboard and its Windows launcher."""
import hashlib
from pathlib import Path


def runtime_sources(root):
    root = Path(root)
    sources = [path.relative_to(root).as_posix()
               for folder in ('dashboard', 'receiver')
               for path in (root / folder).glob('*.py')
               if not path.name.startswith('test_')]
    sources.extend(('agent/tools.json', 'scripts/capability_service.py',
                    'scripts/capability_duration.py', 'scripts/data_policy.py',
                    'scripts/resource_native.py'))
    return sorted(sources)


def server_build(root):
    root = Path(root)
    digest = hashlib.sha256()
    for name in runtime_sources(root):
        data = (root / name).read_bytes()
        digest.update(name.encode('utf-8') + b'\0')
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)
    return digest.hexdigest()


if __name__ == '__main__':
    print(server_build(Path(__file__).resolve().parents[1]))
