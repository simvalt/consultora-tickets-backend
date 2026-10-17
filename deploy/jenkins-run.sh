#!/usr/bin/env bash
# Punto de entrada para Jenkins. Instalar como ~/tickets-demo/automation/run.sh.
# Los jobs nunca reciben socket Docker, secretos de la demo ni sudo general.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/bin:/usr/bin:/bin
export TICKETS_STATE_DIR=/home/simonvalt/tickets-demo/state
[[ $# == 2 && "$2" =~ ^[0-9a-f]{40}$ ]] || { echo 'Uso: run.sh backend|database COMMIT'; exit 2; }
case "$1" in backend|database) component="$1" ;; *) exit 2 ;; esac
expected="$2"
exec 7>"$TICKETS_STATE_DIR/automation.lock"
flock -w 1800 7
release="$(mktemp -d /home/simonvalt/tickets-demo/releases/build.XXXXXXXX)"
# sudo conserva el cwd de Jenkins, que simonvalt no puede atravesar.
# Buildx resuelve tambien el cwd real aunque Compose use --project-directory.
cd "$release"
for name in backend database; do
  git clone --depth 1 --branch main --single-branch \
    "https://github.com/simvalt/consultora-tickets-$name.git" "$release/consultora-tickets-$name"
done
actual="$(git -C "$release/consultora-tickets-$component" rev-parse HEAD)"
[[ "$actual" == "$expected" ]] || { echo 'Main avanzo; ejecutar el build del commit mas reciente.'; exit 1; }
backend="$release/consultora-tickets-backend"
python3 "$backend/scripts/verify-existing-services.py" --record --snapshot "$release/before.json"
bash "$backend/scripts/ci-server.sh"
bash "$backend/scripts/deploy-server.sh"
python3 "$backend/scripts/verify-existing-services.py" --snapshot "$release/before.json"
