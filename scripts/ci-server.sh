#!/usr/bin/env bash
set -Eeuo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state="${TICKETS_STATE_DIR:-/home/simonvalt/tickets-demo/state}"
mkdir -p "$state"
exec 8>"$state/ci.lock"
flock -w 1800 8
compose=(docker compose --project-name consultora-tickets-ci --project-directory "$repo" -f "$repo/deploy/compose.ci.yml")
cleanup() { "${compose[@]}" down --volumes --timeout 20; }
trap cleanup EXIT
"${compose[@]}" config --quiet
COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build
"${compose[@]}" up -d --wait --wait-timeout 120 db
"${compose[@]}" run --rm --no-deps migrate
"${compose[@]}" run --rm --no-deps checks
