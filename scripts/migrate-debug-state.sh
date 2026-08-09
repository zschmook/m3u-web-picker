#!/usr/bin/env bash
set -euo pipefail

container_name="${1:-m3u-picker-sports-test}"
destination="${2:-./debug-data}"

if ! docker inspect "$container_name" >/dev/null 2>&1; then
  echo "Container '$container_name' was not found." >&2
  exit 1
fi

mkdir -p "$destination/exports" "$destination/epg" "$destination/providers"

# v21.4+ stores state under /app/debug-data. Copy that directory first when it
# exists so a new extracted version can inherit the currently running test data.
if docker exec "$container_name" test -d /app/debug-data; then
  docker cp "$container_name:/app/debug-data/." "$destination/"
  echo "Copied /app/debug-data"
  echo "Debug runtime state is now in $destination"
  exit 0
fi

# Legacy v21.3 and earlier kept runtime files directly under /app.
copy_if_present() {
  local source_path="$1"
  local destination_path="$2"
  if docker exec "$container_name" test -e "$source_path"; then
    docker cp "$container_name:$source_path" "$destination_path"
    echo "Copied $source_path"
  fi
}

copy_if_present /app/m3u_picker.db "$destination/m3u_picker.db"
copy_if_present /app/config.json "$destination/config.json"
copy_if_present /app/master_playlist_cache.m3u "$destination/master_playlist_cache.m3u"
copy_if_present /app/epg_cache.xml "$destination/epg_cache.xml"

if docker exec "$container_name" test -d /app/exports; then
  docker cp "$container_name:/app/exports/." "$destination/exports/"
  echo "Copied /app/exports"
fi
if docker exec "$container_name" test -d /app/epg; then
  docker cp "$container_name:/app/epg/." "$destination/epg/"
  echo "Copied /app/epg"
fi
if docker exec "$container_name" test -d /app/providers; then
  docker cp "$container_name:/app/providers/." "$destination/providers/"
  echo "Copied /app/providers"
fi

echo "Debug runtime state is now in $destination"
