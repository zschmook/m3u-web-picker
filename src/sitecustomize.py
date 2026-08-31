"""Runtime helper for both package-root and in-place script execution."""

from __future__ import annotations

from pathlib import Path
import sys


REPO_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = Path(__file__).resolve().parent

for candidate in (str(REPO_DIR), str(SRC_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)
