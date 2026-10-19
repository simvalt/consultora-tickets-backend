#!/usr/bin/env bash
# Solo lint y auditoria. Las pruebas permanecen en ci-server.sh para database/manual.
set -Eeuo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export BACKEND_REF="$(git -C "$repo" rev-parse HEAD)"
compose=(docker compose --project-name consultora-tickets-backend-ci --project-directory "$repo" -f "$repo/deploy/compose.backend-ci.yml")
case "${1:-}" in
  prepare)
    "${compose[@]}" config --quiet
    "${compose[@]}" --progress plain build quality
    ;;
  lint) "${compose[@]}" run --rm --no-deps quality ruff check --no-cache . ;;
  audit) "${compose[@]}" run --rm --no-deps quality pip-audit -r requirements.txt ;;
  cleanup) "${compose[@]}" down --timeout 20 ;;
  *) echo 'Uso: ci-backend.sh prepare|lint|audit|cleanup' >&2; exit 2 ;;
esac
