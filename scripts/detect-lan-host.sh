#!/bin/sh
set -eu

# macOS helper for v30-experiments. It reports the IPv4 address on the
# interface carrying the default route. This is the address a Chromecast
# on the same LAN must use to reach port 1000 on this Mac.
iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
if [ -z "${iface:-}" ]; then
  iface="en0"
fi
ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
if [ -z "${ip:-}" ]; then
  echo "Could not determine an IPv4 LAN address." >&2
  exit 1
fi
printf '%s\n' "$ip"
