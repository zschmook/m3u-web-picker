#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

M3U_PICKER_DIR="$REPO_DIR" exec sh "$SCRIPT_DIR/docker-setup.sh"
