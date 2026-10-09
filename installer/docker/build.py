from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def artifact_name() -> str:
    system = platform.system()
    if system == "Windows":
        return "M3U-Web-Picker-Windows-Setup"
    if system == "Darwin":
        return "M3U-Web-Picker-macOS-Setup"
    if system == "Linux":
        return "M3U-Web-Picker-Linux-Setup"
    raise SystemExit(f"Unsupported build platform: {system}")


def run(arguments: list[str]) -> None:
    subprocess.run(arguments, cwd=ROOT, check=True)


def main() -> int:
    from notices import write_notices
    name = artifact_name()
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            "pyinstaller>=6.16,<7",
        ]
    )
    shutil.rmtree(DIST, ignore_errors=True)
    shutil.rmtree(BUILD, ignore_errors=True)
    for spec in ROOT.glob("M3U-Web-Picker-*-Setup.spec"):
        spec.unlink()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--name",
        name,
    ]
    BUILD.mkdir(parents=True, exist_ok=True)
    notices = BUILD / 'third-party-notices.txt'
    write_notices(notices)
    separator = ';' if platform.system() == 'Windows' else ':'
    command.extend(['--add-data', f'{notices}{separator}.'])
    if platform.system() == "Darwin":
        command.extend(["--target-arch", "universal2"])
    command.append(str(ROOT / "install.py"))
    run(command)

    suffix = ".exe" if platform.system() == "Windows" else ""
    output = DIST / f"{name}{suffix}"
    if not output.is_file():
        raise SystemExit(f"PyInstaller did not produce {output.name}")
    print(f"Built: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
