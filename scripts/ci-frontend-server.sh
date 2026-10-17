#!/usr/bin/env bash
set -Eeuo pipefail
root=/home/simonvalt/tickets-demo
exec 8>"$root/state/frontend-ci.lock"
flock -w 1800 8
docker run --rm --name tickets-frontend-check \
  --cpus=1.5 --memory=2g --pids-limit=256 --user 1000:1000 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  -e npm_config_cache=/tmp/npm -e API_URL=https://tickets-api.sivalt.com/api -e CI=true \
  -v "$root/consultora-tickets-frontend:/workspace" -w /workspace \
  node:22.23.2-alpine sh scripts/ci.sh
