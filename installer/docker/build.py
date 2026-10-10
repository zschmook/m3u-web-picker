from __future__ import annotations

import platform
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parents[1]
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
    helper_name='M3U-Web-Picker-VPN-Helper'
    helper_command=[sys.executable,'-m','PyInstaller','--clean','--onefile','--name',helper_name,
                    '--paths',str(SOURCE/'src'),'--paths',str(SOURCE/'scripts'),
                    '--add-data',f'{notices}{separator}.']
    if platform.system()=='Windows':
        helper_command.append('--noconsole')
    if platform.system() == "Darwin":
        command.extend(["--target-arch", "universal2"])
        helper_command.extend(['--target-arch','universal2'])
    helper_command.append(str(ROOT/'vpn_helper.py'))
    run(helper_command)
    suffix='.exe' if platform.system()=='Windows' else ''
    helper=DIST/(helper_name+suffix)
    report=BUILD/'vpn-helper-self-check.json'
    run([str(helper),'--self-check','--report',str(report)])
    if not json.loads(report.read_text())['passed']:
        raise SystemExit('The packaged VPN helper failed its self-check')
    # Preserve the separately frozen helper byte-for-byte, including signatures.
    command.extend(['--add-data',f'{helper}{separator}.'])
    command.extend(['--paths',str(SOURCE/'src'),'--paths',str(SOURCE/'scripts')])
    command.append(str(ROOT / "install.py"))
    run(command)

    output = DIST / f"{name}{suffix}"
    if not output.is_file():
        raise SystemExit(f"PyInstaller did not produce {output.name}")
    run([str(output),'--verify-vpn-helper'])
    for module in ('helper_service.py','vpn_helper.py'):
        shutil.copy2(ROOT/module,DIST/module)
    print(f"Built: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
