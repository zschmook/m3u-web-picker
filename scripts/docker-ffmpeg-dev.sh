#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

"$SCRIPT_DIR/detect-lan-host.sh" --write-env
cd "$REPO_DIR"

if command -v nvidia-smi >/dev/null 2>&1 || command -v nvidia-smi.exe >/dev/null 2>&1; then
  echo "NVIDIA GPU detected; requesting Docker GPU passthrough."
  docker compose -f docker-compose.dev.yml -f docker-compose.gpu.yml up -d --build --force-recreate
  docker compose -f docker-compose.dev.yml -f docker-compose.gpu.yml ps
else
  echo "No NVIDIA command detected; starting without GPU passthrough so CPU fallback can be tested."
  docker compose -f docker-compose.dev.yml up -d --build --force-recreate
  docker compose -f docker-compose.dev.yml ps
fi

printf '\nFFmpeg experiment is ready at http://localhost:9998\n'
