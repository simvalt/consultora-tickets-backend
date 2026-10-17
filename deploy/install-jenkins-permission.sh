#!/usr/bin/env bash
# Unica instalacion administrativa. SOLO crea un archivo NUEVO; no cambia reglas existentes.
set -Eeuo pipefail
[[ "$EUID" == 0 ]] || { echo 'Ejecutar con sudo'; exit 1; }
target=/etc/sudoers.d/tickets-demo
[[ ! -e "$target" ]] || { echo 'tickets-demo ya existe; revisar manualmente, no se sobrescribe.'; exit 1; }
test -x /home/simonvalt/tickets-demo/automation/run.sh
test "$(stat -c %U /home/simonvalt/tickets-demo/automation/run.sh)" = simonvalt
draft="$(mktemp /tmp/tickets-sudoers.XXXXXXXX)"
trap 'rm -f -- "$draft"' EXIT
printf '%s\n' 'jenkins-agent ALL=(simonvalt) NOPASSWD: /home/simonvalt/tickets-demo/automation/run.sh' > "$draft"
visudo -cf "$draft"
install -o root -g root -m 0440 "$draft" "$target"
echo 'Instalado SOLO /etc/sudoers.d/tickets-demo. No se modifico ninguna regla anterior.'
