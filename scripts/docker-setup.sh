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

if [ -f "$INSTALL_DIR/.env" ]; then
  env_preexisting=1
else
  env_preexisting=0
fi

"$INSTALL_DIR/scripts/detect-lan-host.sh" --write-env

case "$(uname -s 2>/dev/null || true)" in
  MINGW*|MSYS*|CYGWIN*)
    env_file="$INSTALL_DIR/.env"
    configured_dvr_dir="$(awk -F= '$1 == "M3U_DVR_DIR" {sub(/^[^=]*=/, ""); print; exit}' "$env_file" | tr -d '\r')"
    if [ "$env_preexisting" -eq 0 ] || [ -z "$configured_dvr_dir" ]; then
      tmp_file="${env_file}.tmp.$$"
      awk '
        BEGIN { written = 0 }
        /^M3U_DVR_DIR=/ {
          if (!written) {
            print "M3U_DVR_DIR=C:/DVR"
            written = 1
          }
          next
        }
        { print }
        END {
          if (!written) print "M3U_DVR_DIR=C:/DVR"
        }
      ' "$env_file" > "$tmp_file"
      mv "$tmp_file" "$env_file"
      configured_dvr_dir="C:/DVR"
    fi
    if [ "$configured_dvr_dir" = "C:/DVR" ]; then
      mkdir -p /c/DVR
      echo "Using C:/DVR for persistent DVR recordings."
    fi
    ;;
esac

cd "$INSTALL_DIR"
compose_files="-f docker-compose.yml"
case "$(uname -s 2>/dev/null || true)" in
  Darwin)
    echo "Note: GPU passthrough is not supported yet for Docker installs on macOS; FFmpeg will use CPU fallback."
    ;;
  *)
    if command -v nvidia-smi >/dev/null 2>&1 || command -v nvidia-smi.exe >/dev/null 2>&1; then
      compose_files="$compose_files -f docker-compose.gpu.yml"
      echo "NVIDIA GPU detected; requesting Docker GPU passthrough."
    fi
    ;;
esac
# shellcheck disable=SC2086
docker compose $compose_files up -d --build --force-recreate
# shellcheck disable=SC2086
docker compose $compose_files ps

printf '\nM3U Web Picker is ready at http://localhost:9999\n'
printf 'Installed in %s\n' "$INSTALL_DIR"
