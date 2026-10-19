#!/usr/bin/env bash
# Instalar como ~/tickets-demo/automation/run.sh. Solo recursos exclusivos de Tickets.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/bin:/usr/bin:/bin
export TICKETS_STATE_DIR=/home/simonvalt/tickets-demo/state
[[ $# == 2 || $# == 4 ]] && [[ "$2" =~ ^[0-9a-f]{40}$ ]] || {
  echo 'Uso: run.sh backend|database COMMIT [FASE BUILD_NUMBER]' >&2; exit 2;
}
case "$1" in backend|database) component="$1" ;; *) exit 2 ;; esac
expected="$2"
slot="$TICKETS_STATE_DIR/backend-pipeline.state"
exec 7>"$TICKETS_STATE_DIR/automation.lock"
flock -w 1800 7

prepare_release() {
  release="$(mktemp -d /home/simonvalt/tickets-demo/releases/build.XXXXXXXX)"
  cd "$release"
  for name in backend database; do
    git clone --depth 1 --branch main --single-branch \
      "https://github.com/simvalt/consultora-tickets-$name.git" "$release/consultora-tickets-$name"
  done
  actual="$(git -C "$release/consultora-tickets-$component" rev-parse HEAD)"
  [[ "$actual" == "$expected" ]] || { echo 'Main avanzo; ejecutar el commit mas reciente.' >&2; exit 1; }
  backend="$release/consultora-tickets-backend"
  python3 "$backend/scripts/verify-existing-services.py" --record --snapshot "$release/before.json"
}

if [[ $# == 2 ]]; then
  # Database sigue siendo estricto. No intercalar versiones con el pipeline backend.
  [[ ! -e "$slot" ]] || { echo 'Hay un pipeline backend activo de Tickets.' >&2; exit 1; }
  prepare_release
  if [[ "$component" == database ]]; then
    TICKETS_TEST_FAILURE_MODE=strict bash "$backend/scripts/ci-server.sh"
  else
    trap 'bash "$backend/scripts/ci-backend.sh" cleanup' EXIT
    for phase in prepare lint audit; do bash "$backend/scripts/ci-backend.sh" "$phase"; done
  fi
  bash "$backend/scripts/deploy-server.sh"
  python3 "$backend/scripts/verify-existing-services.py" --snapshot "$release/before.json"
  exit 0
fi

[[ "$component" == backend && "$4" =~ ^[1-9][0-9]*$ ]] || exit 2
phase="$3"
build="$4"
case "$phase" in prepare|lint|audit|build|backup|migrate|deploy|health|verify|finish) ;; *) exit 2 ;; esac
save_slot() {
  printf '%s\n' "$build" "$expected" "$release" "$1" > "$slot.tmp"
  mv -- "$slot.tmp" "$slot"
}
if [[ "$phase" == prepare ]]; then
  [[ ! -e "$slot" ]] || { echo 'Otro pipeline backend conserva la reserva de Tickets.' >&2; exit 1; }
  prepare_release
  # La reserva persiste entre stages; flock serializa cada operacion.
  save_slot starting
  bash "$backend/scripts/ci-backend.sh" prepare
  save_slot prepare
  exit 0
fi

if [[ ! -e "$slot" && "$phase" == finish ]]; then exit 0; fi
[[ -f "$slot" ]] || { echo 'No hay una release preparada por este build.' >&2; exit 1; }
mapfile -t active < "$slot"
[[ ${#active[@]} == 4 && "${active[0]}" == "$build" && "${active[1]}" == "$expected" ]] || {
  echo 'La reserva pertenece a otro build; no se modifica.' >&2; exit 1;
}
release="${active[2]}"
[[ "$release" =~ ^/home/simonvalt/tickets-demo/releases/build\.[A-Za-z0-9]+$ && -d "$release" ]] || exit 1
backend="$release/consultora-tickets-backend"
cd "$release"
export TICKETS_PIPELINE_RELEASE="$release"
if [[ "$phase" == finish ]]; then
  status=0
  python3 "$backend/scripts/verify-existing-services.py" --snapshot "$release/before.json" || status=$?
  bash "$backend/scripts/ci-backend.sh" cleanup || status=$?
  rm -- "$slot"
  exit "$status"
fi
case "$phase" in
  lint) previous=prepare ;;
  audit) previous=lint ;;
  build) previous=audit ;;
  backup) previous=build ;;
  migrate) previous=backup ;;
  deploy) previous=migrate ;;
  health) previous=deploy ;;
  verify) previous=health ;;
esac
[[ "${active[3]}" == "$previous" ]] || { echo "Orden invalido: $phase requiere $previous." >&2; exit 1; }
case "$phase" in
  lint|audit) bash "$backend/scripts/ci-backend.sh" "$phase" ;;
  build|backup|migrate|deploy|health) bash "$backend/scripts/deploy-server.sh" "$phase" ;;
  verify) python3 "$backend/scripts/verify-existing-services.py" --snapshot "$release/before.json" ;;
esac
save_slot "$phase"
