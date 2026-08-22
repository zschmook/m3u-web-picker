#!/bin/sh
set -eu

REPOSITORY_URL="https://github.com/zschmook/m3u-web-picker.git"
INSTALL_DIR="${M3U_PICKER_DIR:-$HOME/m3u-web-picker}"

for required_command in git docker; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    echo "$required_command was not found." >&2
    exit 1
  fi
done

if ! docker info >/dev/null 2>&1; then
  echo "Docker is installed but is not running. Start Docker Desktop and try again." >&2
  exit 1
fi

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Updating M3U Web Picker in $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull --ff-only origin main
elif [ -e "$INSTALL_DIR" ]; then
  echo "$INSTALL_DIR already exists but is not an M3U Web Picker Git checkout." >&2
  echo "Move it, remove it, or set M3U_PICKER_DIR to another location." >&2
  exit 1
else
  echo "Downloading M3U Web Picker to $INSTALL_DIR..."
  git clone --branch main --single-branch "$REPOSITORY_URL" "$INSTALL_DIR"
fi

"$INSTALL_DIR/scripts/detect-lan-host.sh" --write-env

cd "$INSTALL_DIR"
compose_files="-f docker-compose.yml"
if command -v nvidia-smi >/dev/null 2>&1 || command -v nvidia-smi.exe >/dev/null 2>&1; then
  compose_files="$compose_files -f docker-compose.gpu.yml"
  echo "NVIDIA GPU detected; requesting Docker GPU passthrough."
fi
# shellcheck disable=SC2086
docker compose $compose_files up -d --build --force-recreate
# shellcheck disable=SC2086
docker compose $compose_files ps

printf '\nM3U Web Picker is ready at http://localhost:9999\n'
printf 'Installed in %s\n' "$INSTALL_DIR"
