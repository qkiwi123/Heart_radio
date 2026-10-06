"""Create local TLS credentials once; never include these files in source/evidence."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import ssl
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--openssl', required=True)
    p.add_argument('--directory', type=Path, default=Path('.local-wifi'))
    args = p.parse_args()
    ipaddress.IPv4Address(args.host)
    if not 1024 <= args.port <= 65535:
        p.error('port out of range')
    args.directory.mkdir(exist_ok=True, parents=True)
    config_file = args.directory / 'config.json'
    if config_file.exists():
        raise SystemExit('Credentials already exist; use existing files, do not overwrite silently.')
    cert, key = args.directory / 'cert.pem', args.directory / 'key.pem'
    if cert.exists() or key.exists():
        raise SystemExit('Certificate files already exist. Choose a new directory.')
    subprocess.run([args.openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '365',
                    '-subj', '/CN=HeartRadio local receiver'], check=True, capture_output=True)
    pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    config = dict(host=args.host, port=args.port, pin=pin, token=secrets.token_hex(32))
    config_file.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    (args.directory / 'connection-code.txt').write_text(
        f'{args.host}:{args.port}|{pin}|{config["token"]}', encoding='utf-8')
    print(f'Created local credentials in {args.directory}; token is not printed.')


if __name__ == '__main__':
    main()
