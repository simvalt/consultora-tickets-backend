#!/bin/sh
# Solo las aserciones fallidas pueden ser advertencia; errores de ejecucion siguen bloqueando.
set -eu
test_mode="${TICKETS_TEST_FAILURE_MODE:-strict}"
case "$test_mode" in
  strict|warn) ;;
  *) echo 'TICKETS_TEST_FAILURE_MODE debe ser strict o warn.' >&2; exit 2 ;;
esac

ruff check .
pip-audit -r requirements.txt
if pytest --junitxml=/tmp/backend-tests.xml; then
  exit 0
else
  test_status=$?
fi

# pytest: 1 = pruebas fallidas. Interrupciones, errores internos, uso incorrecto
# y ausencia de pruebas no se convierten en un despliegue con advertencias.
if [ "$test_mode" = warn ] && [ "$test_status" -eq 1 ]; then
  echo 'ADVERTENCIA: pytest reporto pruebas fallidas (no bloqueantes para Tickets-backend).' >&2
  exit 42
fi
exit "$test_status"
