#!/usr/bin/env python3
"""Preparar SOLO ~/tickets-demo; no sudo, no configuracion global, no secretos en stdout."""
import json
import os
import secrets
import subprocess
from pathlib import Path

root = Path('/home/simonvalt/tickets-demo')
os.umask(0o077)
root.mkdir(exist_ok=True)
state = root / 'state'
state.mkdir(exist_ok=True)
snapshot = state / 'existing-containers.json'
if not snapshot.exists():
    ids = subprocess.check_output(['docker', 'ps', '-q'], text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids])) if ids else []
    snapshot.write_text(json.dumps([
        {'name': c['Name'], 'id': c['Id'], 'started_at': c['State']['StartedAt']}
        for c in containers
    ], indent=2) + '\n')
runtime = state / 'runtime.env'
if not runtime.exists():
    values = {
        'DATABASE_REPO_PATH': '../consultora-tickets-database',
        'POSTGRES_PASSWORD': secrets.token_urlsafe(32),
        'JWT_SECRET': secrets.token_urlsafe(48),
        'ADMIN_EMAIL': 'admin@consultora.local',
        'ADMIN_NAME': 'Administrador',
        'ADMIN_PASSWORD': secrets.token_urlsafe(24),
        'DEMO_PASSWORD': secrets.token_urlsafe(24),
        'CLOUDFLARE_TUNNEL_TOKEN': '',
    }
    runtime.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    runtime.chmod(0o600)
for name in ('backend', 'database', 'frontend'):
    target = root / f'consultora-tickets-{name}'
    if not target.exists():
        subprocess.run(['git', 'clone', '--branch', 'main', '--single-branch',
                        f'https://github.com/simvalt/consultora-tickets-{name}.git', str(target)], check=True)
print('Preparado ~/tickets-demo. Secretos en state/runtime.env (0600).')
