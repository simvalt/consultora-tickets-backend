#!/usr/bin/env python3
"""Comparar identidad y arranque de contenedores previos sin inspeccionar sus secretos."""
import argparse
import json
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--snapshot', type=Path, default=Path('/home/simonvalt/tickets-demo/state/existing-containers.json'))
parser.add_argument('--record', action='store_true')
args = parser.parse_args()
snapshot = args.snapshot
if args.record:
    ids = subprocess.check_output(['docker', 'ps', '-q'], text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids])) if ids else []
    snapshot.write_text(json.dumps([
        {'name': c['Name'], 'id': c['Id'], 'started_at': c['State']['StartedAt']}
        for c in containers
        if not c['Config'].get('Labels', {}).get('com.docker.compose.project', '').startswith('consultora-tickets-')
        and not c['Name'].startswith('/tickets-')
    ], indent=2) + '\n')
    raise SystemExit(0)
for previous in json.loads(snapshot.read_text()):
    # Usar el ID anterior detecta tambien un reemplazo del contenedor.
    output = subprocess.check_output([
        'docker', 'inspect', '--format', '{{.Id}} {{.State.StartedAt}} {{.State.Running}}', previous['id'],
    ], text=True).split()
    assert output == [previous['id'], previous['started_at'], 'true'], previous['name']
    print(f"Sin reinicio ni reemplazo: {previous['name']}")
