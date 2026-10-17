#!/usr/bin/env python3
"""Verificar la demo sin imprimir contrasenas, cookies ni tokens."""
import argparse
import json
import subprocess
from pathlib import Path

root = Path('/home/simonvalt/tickets-demo')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--public', action='store_true', help='Verificar HTTPS a traves del tunel nuevo')
args = parser.parse_args()
values = dict(line.split('=', 1) for line in (root / 'state/runtime.env').read_text().splitlines() if '=' in line)
check = r'''
import json
import sys
import urllib.error
import urllib.request

credentials = json.load(sys.stdin)
base = credentials.pop('_api_url')

def request(path, method='GET', body=None, headers=None):
    req = urllib.request.Request(base + path, method=method,
        headers={'User-Agent': 'Tickets-Deployment-Check/1.0', **(headers or {})},
        data=json.dumps(body).encode() if body is not None else (b'' if method == 'POST' else None))
    if body is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    return response.status, response.headers, response.read()

assert request('/health')[0] == 200, 'salud publica'
for path in ('/docs', '/openapi.json'):
    assert request(path)[0] == 404, path
for origin, expected in [('https://tickets.sivalt.com', 200), ('https://example.org', 400)]:
    status, headers, _ = request('/auth/login', 'OPTIONS', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type,x-requested-with',
    })
    assert status == expected, 'CORS status'
    assert headers.get('Access-Control-Allow-Origin') == (origin if expected == 200 else None), 'CORS origin'
status, headers, body = request('/auth/login', 'POST', credentials)
assert status == 200, 'login'
cookie = headers['Set-Cookie']
for flag in ('HttpOnly', 'Secure', 'SameSite=lax', 'Path=/api/auth'):
    assert flag in cookie, flag
assert headers['Cache-Control'] == 'no-store'
token = json.loads(body)['access_token']
assert request('/auth/me', headers={'Authorization': 'Bearer ' + token})[0] == 200
session = {'Cookie': cookie.split(';', 1)[0], 'X-Requested-With': 'XMLHttpRequest'}
status, headers, _ = request('/auth/refresh', 'POST', headers=session)
assert status == 200, 'refresh'
session['Cookie'] = headers['Set-Cookie'].split(';', 1)[0]
assert request('/auth/logout', 'POST', headers=session)[0] == 204, 'logout'
assert request('/auth/refresh', 'POST', headers=session)[0] == 401, 'sesion revocada'
print('OK: salud, CORS permitido/denegado, cookie segura, login, refresh, logout y docs ocultos.')
'''
subprocess.run([
    'docker', 'exec', '-i', 'consultora-tickets-demo-backend-1', 'python', '-c', check,
], input=json.dumps({
    'email': values['ADMIN_EMAIL'], 'password': values['ADMIN_PASSWORD'],
    '_api_url': 'https://tickets-api.sivalt.com/api' if args.public else 'http://127.0.0.1:8000/api',
}), text=True, check=True)
