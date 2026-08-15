#!/bin/sh
set -eu

# macOS helper: print the IPv4 address on the interface carrying the default
# route. Use this value for M3U_LAN_HOST when LAN clients need to reach Picker.
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
