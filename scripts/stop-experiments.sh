#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

RUNTIME_DIR="${TMPDIR:-/tmp}"
PID_FILE="$RUNTIME_DIR/m3u-web-picker-hdhr-discovery.pid"

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    case "$command_line" in
      *hdhr_discovery_host.py*)
        kill -TERM "$pid" 2>/dev/null || true
        ;;
    esac
  fi
  rm -f "$PID_FILE"
fi

docker compose down --remove-orphans
