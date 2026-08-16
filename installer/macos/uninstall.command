#!/bin/bash
set -euo pipefail

ROOT="$HOME/Library/Application Support/M3U-Web-Picker"
PLIST="$HOME/Library/LaunchAgents/com.m3uwebpicker.host.plist"

launchctl bootout "gui/$UID/com.m3uwebpicker.host" >/dev/null 2>&1 || true
rm -f "$PLIST"

answer=""
read -r -p "Keep database and backups? [Y/n]: " answer || true
case "${answer:-y}" in
    n|N|no|NO|No)
        rm -rf "$ROOT"
        echo "M3U Web Picker, data, and backups removed."
        ;;
    *)
        if [ -d "$ROOT" ]; then
            find "$ROOT" -mindepth 1 -maxdepth 1 \
                ! -name data ! -name backups -exec rm -rf {} +
        fi
        echo "Application removed. Data and backups remain under: $ROOT"
        ;;
esac
