#!/bin/bash
set -euo pipefail

ROOT="$HOME/Library/Application Support/M3U-Web-Picker"
APP_DIR="$ROOT/app"
VENV_DIR="$ROOT/venv"
DATA_DIR="$ROOT/data"
BACKUP_DIR="$ROOT/backups"
CAST_DIR="$ROOT/cast-hls"
HOST_ENV="$ROOT/host.env"
HOST_LOG="$ROOT/host.log"
PLIST="$HOME/Library/LaunchAgents/com.m3uwebpicker.host.plist"
SOURCE_URL="https://codeload.github.com/zschmook/m3u-web-picker/zip/refs/heads/main"
WEB_URL="http://localhost:9999"

find_python() {
    local candidate
    for candidate in python3.12 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if (3, 12) <= sys.version_info[:2] < (4, 0) else 1)' >/dev/null 2>&1; then
                command -v "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
    if command -v brew >/dev/null 2>&1; then
        echo "Python 3.12+ not found. Installing python@3.12 with Homebrew..."
        brew install python@3.12
        hash -r
        PYTHON="$(find_python || true)"
    fi
fi
if [ -z "$PYTHON" ]; then
    echo "Python 3.12+ is required. Install it from python.org or Homebrew, then run this installer again."
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    if command -v brew >/dev/null 2>&1; then
        echo "FFmpeg not found. Installing it with Homebrew..."
        brew install ffmpeg
        hash -r
    else
        echo "FFmpeg is required. Install Homebrew + ffmpeg, or install ffmpeg yourself, then run this installer again."
        exit 1
    fi
fi
FFMPEG="$(command -v ffmpeg)"

echo "Installing M3U Web Picker under: $ROOT"
mkdir -p "$ROOT" "$DATA_DIR" "$BACKUP_DIR" "$CAST_DIR" "$HOME/Library/LaunchAgents"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fL "$SOURCE_URL" -o "$TMP/source.zip"
mkdir -p "$TMP/source"
ditto -x -k "$TMP/source.zip" "$TMP/source"
SOURCE_DIR="$(find "$TMP/source" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ -z "$SOURCE_DIR" ] || [ ! -f "$SOURCE_DIR/src/app.py" ] || [ ! -f "$SOURCE_DIR/src/host_runtime.py" ]; then
    echo "Downloaded source archive is missing required application files."
    exit 1
fi

STAGING="$ROOT/.app-staging"
OLD="$ROOT/.app-old"
rm -rf "$STAGING" "$OLD"
ditto "$SOURCE_DIR" "$STAGING"
if [ -d "$APP_DIR" ]; then
    mv "$APP_DIR" "$OLD"
fi
mv "$STAGING" "$APP_DIR"
rm -rf "$OLD"

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Creating private Python environment..."
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$APP_DIR/requirements.txt"

LAN_HOST=""
DEFAULT_IFACE="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}' || true)"
if [ -n "$DEFAULT_IFACE" ]; then
    LAN_HOST="$(ipconfig getifaddr "$DEFAULT_IFACE" 2>/dev/null || true)"
fi

cat > "$HOST_ENV" <<ENV
# Managed by the M3U Web Picker macOS installer
PYTHONUNBUFFERED=1
M3U_ONBOARDING_ENABLED=true
M3U_BACKUP_ENABLED=true
M3U_DATA_DIR=$DATA_DIR
M3U_CAST_HLS_DIR=$CAST_DIR
M3U_BACKUP_CONTAINER_DIR=$BACKUP_DIR
M3U_FFMPEG=$FFMPEG
M3U_PORT=9999
M3U_EXTERNAL_PORT=9999
M3U_LAN_HOST=$LAN_HOST
BACKUP_RETENTION_DAYS=30
MASTER_REFRESH_HOUR=3
MASTER_REFRESH_MINUTE=0
ENV

"$VENV_DIR/bin/python" - "$PLIST" "$VENV_DIR/bin/python" "$APP_DIR/src/host_runtime.py" "$APP_DIR" "$HOST_ENV" "$HOST_LOG" <<'PY'
import plistlib
import sys
from pathlib import Path

plist, python, host_runtime, app_dir, host_env, log_path = sys.argv[1:]
payload = {
    "Label": "com.m3uwebpicker.host",
    "ProgramArguments": [python, host_runtime],
    "WorkingDirectory": app_dir,
    "EnvironmentVariables": {"M3U_HOST_ENV": host_env},
    "RunAtLoad": True,
    "KeepAlive": True,
    "StandardOutPath": log_path,
    "StandardErrorPath": log_path,
}
path = Path(plist)
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("wb") as handle:
    plistlib.dump(payload, handle)
PY

launchctl bootout "gui/$UID/com.m3uwebpicker.host" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl kickstart -k "gui/$UID/com.m3uwebpicker.host" >/dev/null 2>&1 || true

for _ in $(seq 1 60); do
    if curl -fsS "$WEB_URL/api/guide/ping" >/dev/null 2>&1 || curl -fsS "$WEB_URL" >/dev/null 2>&1; then
        echo "M3U Web Picker is running at $WEB_URL"
        echo "Note: GPU acceleration is not supported yet by the macOS installer; FFmpeg will use CPU fallback."
        open "$WEB_URL"
        exit 0
    fi
    sleep 1
done

echo "The host did not become reachable. Check: $HOST_LOG"
exit 1
