#!/usr/bin/env bash
# Ejecutar como simonvalt. No requiere sudo ni modifica servicios ajenos.
set -Eeuo pipefail
umask 077
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state="${TICKETS_STATE_DIR:-/home/simonvalt/tickets-demo/state}"
mkdir -p "$state/backups"
exec 9>"$state/deploy.lock"
flock -w 1800 9
env_file="$state/runtime.env"
test -s "$env_file" || { echo 'Falta state/runtime.env'; exit 1; }
export BACKEND_REF="$(git -C "$repo" rev-parse HEAD)"
export DATABASE_REF="$(git -C "$repo/../consultora-tickets-database" rev-parse HEAD)"
compose=(docker compose --project-name consultora-tickets-demo --project-directory "$repo" --env-file "$env_file" -f "$repo/deploy/compose.server.yml")
"${compose[@]}" config --quiet
# Construir antes de cambiar los servicios. No imprimir la configuracion expandida.
"${compose[@]}" --progress plain build migrate
"${compose[@]}" --progress plain build backend
previous_ref=''
if test -f "$state/release.env"; then
  previous_ref="$(sed -n 's/^BACKEND_REF=//p' "$state/release.env")"
fi
"${compose[@]}" up -d --wait --wait-timeout 180 db
if test -n "$previous_ref"; then
  backup="$state/backups/$(date -u +%Y%m%dT%H%M%SZ).dump"
  "${compose[@]}" exec -T db pg_dump -U tickets -Fc --no-owner tickets > "$backup"
  test -s "$backup"
fi
# Un fallo de migracion detiene el deploy; nunca se hace downgrade automatico.
"${compose[@]}" run --rm --no-deps migrate
if ! "${compose[@]}" up -d --no-deps --wait --wait-timeout 180 backend; then
  if test -n "$previous_ref"; then
    BACKEND_REF="$previous_ref" "${compose[@]}" up -d --no-deps --wait --wait-timeout 180 backend
    echo 'API anterior restaurada; revisar compatibilidad de la migracion y respaldo.' >&2
  fi
  exit 1
fi
"${compose[@]}" exec -T backend python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).status == 200"
printf 'BACKEND_REF=%s\nDATABASE_REF=%s\n' "$BACKEND_REF" "$DATABASE_REF" > "$state/release.env.tmp"
mv -- "$state/release.env.tmp" "$state/release.env"
echo 'Tickets: PostgreSQL, migraciones y API saludables. Servicios ajenos sin cambios.'
