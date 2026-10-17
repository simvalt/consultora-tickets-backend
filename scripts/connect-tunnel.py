#!/usr/bin/env python3
"""Conectar SOLO el nuevo tunel de Tickets; recibe su token por stdin o archivo temporal."""
import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--token-file', type=Path)
args = parser.parse_args()
token = (args.token_file.read_text() if args.token_file else sys.stdin.read()).strip()
assert 40 < len(token) < 8192 and '\n' not in token, 'Token invalido'
payload = json.loads(base64.b64decode(token + '=' * (-len(token) % 4)))
assert all(payload.get(key) for key in ('a', 't', 's')), 'Token de tunel invalido'
root = Path('/home/simonvalt/tickets-demo')
state = root / 'state'
runtime = state / 'runtime.env'
os.umask(0o077)
lines = [line for line in runtime.read_text().splitlines() if not line.startswith('CLOUDFLARE_TUNNEL_TOKEN=')]
temporary = state / 'runtime.env.tmp'
temporary.write_text('\n'.join([*lines, 'CLOUDFLARE_TUNNEL_TOKEN=' + token]) + '\n')
temporary.chmod(0o600)
temporary.replace(runtime)
refs = dict(line.split('=', 1) for line in (state / 'release.env').read_text().splitlines() if '=' in line)
repo = root / 'consultora-tickets-backend'
subprocess.run([
    'docker', 'compose', '--project-name', 'consultora-tickets-demo', '--project-directory', str(repo),
    '--env-file', str(runtime), '-f', str(repo / 'deploy/compose.server.yml'), '--profile', 'public',
    'up', '-d', '--no-deps', 'cloudflared',
], env={**os.environ, **refs}, check=True)
if args.token_file:
    args.token_file.unlink()
print('Conector nuevo de Tickets iniciado. No se reinicio la API ni ningun servicio previo.')
