"""Runtime helper for the src-layout package structure."""

from __future__ import annotations

from pathlib import Path
import sys


SRC_DIR = Path(__file__).resolve().parent / "src"
if SRC_DIR.is_dir():
    src_path = str(SRC_DIR)
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
