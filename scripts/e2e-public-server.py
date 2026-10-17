#!/usr/bin/env python3
"""E2E contra la demo publicada, en un navegador efimero sin acceder a otros servicios."""
import fcntl
import os
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

root = Path('/home/simonvalt/tickets-demo')
# La demo no se sustituye a mitad de la navegacion; solo bloquea deploys de Tickets.
deployment_lock = (root / 'state/deploy.lock').open('a')
fcntl.flock(deployment_lock, fcntl.LOCK_SH)
url = 'https://tickets.sivalt.com'
try:
    request = urllib.request.Request(url, headers={'User-Agent': 'Tickets-Deployment-Check/1.0'})
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
except (urllib.error.URLError, ssl.SSLError) as error:
    raise SystemExit('Frontend aun no disponible con HTTPS valido; E2E no iniciado.') from error
values = dict(line.split('=', 1) for line in (root / 'state/runtime.env').read_text().splitlines() if '=' in line)
environment = {
    **os.environ,
    'E2E_BASE_URL': url,
    'E2E_BROWSER_CHANNEL': 'chromium',
    'E2E_ADMIN_EMAIL': values['ADMIN_EMAIL'],
    'E2E_ADMIN_PASSWORD': values['ADMIN_PASSWORD'],
    'E2E_READER_PASSWORD': values['DEMO_PASSWORD'],
}
subprocess.run([
    'docker', 'run', '--rm', '--name', 'tickets-e2e-public', '--cpus=1.5', '--memory=2g',
    '--pids-limit=300', '--shm-size=256m', '--user=1000:1000', '--cap-drop=ALL',
    '--security-opt=no-new-privileges:true', '-e', 'HOME=/tmp',
    *[part for key in ('E2E_BASE_URL', 'E2E_BROWSER_CHANNEL', 'E2E_ADMIN_EMAIL',
                      'E2E_ADMIN_PASSWORD', 'E2E_READER_PASSWORD') for part in ('-e', key)],
    '-v', str(root / 'consultora-tickets-frontend') + ':/work', '-w', '/work',
    'mcr.microsoft.com/playwright:v1.64.1-noble', 'npx', '--no-install', 'playwright', 'test',
], env=environment, check=True)
