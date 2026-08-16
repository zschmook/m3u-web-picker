from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "M3U-Web-Picker-Python-Dev-Setup.spec"
NAME = "M3U-Web-Picker-Python-Dev-Setup"


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows installer on Windows.")

    run([
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--upgrade",
        "pyinstaller",
    ])
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    SPEC.unlink(missing_ok=True)

    run([
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--name",
        NAME,
        str(ROOT / "installer.py"),
    ])

    output = DIST / f"{NAME}.exe"
    if not output.is_file():
        raise SystemExit("PyInstaller did not produce the expected EXE.")
    print(f"Built: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
