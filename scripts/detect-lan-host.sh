#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

private_ipv4_from_linux_route() {
  if ! command -v ip >/dev/null 2>&1; then
    return 1
  fi
  ip route get 1.1.1.1 2>/dev/null \
    | awk '{for (i = 1; i <= NF; i++) if ($i == "src" && (i + 1) <= NF) {print $(i + 1); exit}}'
}

detect_lan_host() {
  case "$(uname -s 2>/dev/null || true)" in
    Darwin)
      iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
      if [ -z "${iface:-}" ]; then
        iface="en0"
      fi
      ipconfig getifaddr "$iface" 2>/dev/null || true
      ;;
    Linux)
      private_ipv4_from_linux_route || true
      ;;
    *)
      return 1
      ;;
  esac
}

ip="$(detect_lan_host)"
if [ -z "${ip:-}" ]; then
  echo "Could not determine an IPv4 LAN address." >&2
  exit 1
fi

case "${1:-}" in
  "")
    printf '%s\n' "$ip"
    ;;
  --write-env)
    env_file="$REPO_DIR/.env"
    if [ ! -f "$env_file" ]; then
      if [ -f "$REPO_DIR/.env.example" ]; then
        cp "$REPO_DIR/.env.example" "$env_file"
      else
        : > "$env_file"
      fi
    fi

    tmp_file="${env_file}.tmp.$$"
    awk -v ip="$ip" '
      BEGIN { written = 0 }
      /^M3U_LAN_HOST=/ {
        if (!written) {
          print "M3U_LAN_HOST=" ip
          written = 1
        }
        next
      }
      { print }
      END {
        if (!written) print "M3U_LAN_HOST=" ip
      }
    ' "$env_file" > "$tmp_file"
    mv "$tmp_file" "$env_file"
    printf 'Saved M3U_LAN_HOST=%s to %s\n' "$ip" "$env_file"
    ;;
  *)
    echo "Usage: $0 [--write-env]" >&2
    exit 2
    ;;
esac
