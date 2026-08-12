#!/bin/sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT_DIR"

LAN_HOST="${M3U_LAN_HOST:-}"
if [ -z "$LAN_HOST" ]; then
  LAN_HOST="$(sh "$ROOT_DIR/scripts/detect-lan-host.sh")"
fi
EXTERNAL_PORT="${M3U_EXTERNAL_PORT:-10000}"
export M3U_LAN_HOST="$LAN_HOST"
export M3U_EXTERNAL_PORT="$EXTERNAL_PORT"

RUNTIME_DIR="${TMPDIR:-/tmp}"
PID_FILE="$RUNTIME_DIR/m3u-web-picker-hdhr-discovery.pid"
LOG_FILE="$RUNTIME_DIR/m3u-web-picker-hdhr-discovery.log"

# Start/rebuild the experiment first. --remove-orphans cleans up the temporary
# containerized discovery experiment from older checkouts without touching data.
docker compose up -d --build --remove-orphans

helper_is_running() {
  [ -f "$PID_FILE" ] || return 1
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  case "$command_line" in
    *hdhr_discovery_host.py*) return 0 ;;
    *) return 1 ;;
  esac
}

# Repeated starts are harmless; keep the already-running helper if it belongs to
# this launcher, otherwise discard the stale pid file and start a fresh one.
if helper_is_running; then
  exit 0
fi
rm -f "$PID_FILE"

nohup env \
  M3U_LAN_HOST="$LAN_HOST" \
  M3U_EXTERNAL_PORT="$EXTERNAL_PORT" \
  python3 "$ROOT_DIR/tools/hdhr_discovery_host.py" \
    --lan-host "$LAN_HOST" \
    --external-port "$EXTERNAL_PORT" \
  >>"$LOG_FILE" 2>&1 </dev/null &
helper_pid=$!
printf '%s\n' "$helper_pid" > "$PID_FILE"

# Catch immediate bind/startup failures while otherwise staying completely quiet.
sleep 0.25
if ! kill -0 "$helper_pid" 2>/dev/null; then
  rm -f "$PID_FILE"
  echo "HDHomeRun discovery helper failed to start. Recent log:" >&2
  tail -n 20 "$LOG_FILE" >&2 || true
  exit 1
fi
