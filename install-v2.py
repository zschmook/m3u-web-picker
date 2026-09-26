from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "zschmook/m3u-web-picker"
WEB_URL = "http://localhost:9999"
UPGRADE = "UP"
CLEAN = "CL"


def default_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "m3u-web-picker"
    return Path.home() / "AppData" / "Local" / "m3u-web-picker"


def choose_mode(value: str = "") -> str:
    selected = value.strip().upper()
    while selected not in {UPGRADE, CLEAN}:
        print("Choose how to install:")
        print("  UPGRADE (UP) - update the application and keep its saved setup")
        print("  CLEAN   (CL) - erase the production app data and run setup again")
        selected = input("Enter UP or CL: ").strip().upper()
    return selected


def choose_install_mode(install_dir: Path, value: str = "") -> str:
    if not install_dir.exists():
        print(f"No existing production installation was found at {install_dir}.")
        print("Starting a clean installation.\n")
        return CLEAN
    return choose_mode(value)


def confirm_clean() -> None:
    print("\nWARNING: CLEAN deletes all M3U Web Picker production app data.")
    print("This includes saved channels and settings, provider configuration, DVR schedules,")
    print("and saved sports automation selections and rules.")
    print("Existing recording files in the separate DVR folder are not deleted.")
    confirmation = input("Type CLEAN to continue: ").strip().upper()
    if confirmation != "CLEAN":
        raise SystemExit("Clean installation cancelled; nothing was changed.")


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "M3U-Web-Picker-Installer"})
    with urllib.request.urlopen(request, timeout=300) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def extract_github_archive(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as source:
        files = [item for item in source.infolist() if not item.is_dir()]
        roots = {PurePosixPath(item.filename).parts[0] for item in files if PurePosixPath(item.filename).parts}
        if len(roots) != 1:
            raise RuntimeError("Unexpected GitHub ZIP layout")
        root = next(iter(roots))
        for item in source.infolist():
            parts = PurePosixPath(item.filename).parts
            if not parts or parts[0] != root:
                continue
            relative = Path(*parts[1:])
            if not relative.parts:
                continue
            target = (destination / relative).resolve()
            resolved_destination = destination.resolve()
            if target != resolved_destination and resolved_destination not in target.parents:
                raise RuntimeError(f"Unsafe path in ZIP: {item.filename}")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output)
    return destination


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def preserve(name: str, current: Path, staging: Path) -> None:
    source = current / name
    destination = staging / name
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    elif source.is_file():
        shutil.copy2(source, destination)


def set_dotenv_value(path: Path, name: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replacement = f"{name}={value}"
    updated: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{name}="):
            updated.append(replacement)
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(replacement)
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def prepare_install_environment(staging: Path) -> None:
    env_path = staging / ".env"
    configured_dvr = ""
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("M3U_DVR_DIR=") and line.partition("=")[2].strip():
                configured_dvr = line.partition("=")[2].strip()
                break
    example = staging / ".env.example"
    if example.is_file() and not env_path.exists():
        shutil.copy2(example, env_path)
    set_dotenv_value(env_path, "M3U_HOST_PORT", "9999")
    set_dotenv_value(env_path, "M3U_EXTERNAL_PORT", "9999")
    if not configured_dvr:
        dvr_dir = Path.home() / "Videos" / "M3U-Web-Picker-DVR"
        dvr_dir.mkdir(parents=True, exist_ok=True)
        set_dotenv_value(env_path, "M3U_DVR_DIR", dvr_dir.as_posix())


def require_docker() -> None:
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker was not found. Install and start Docker Desktop, then try again.")
    result = subprocess.run(
        [docker, "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Docker Desktop is not running or is not ready.")


def production_container_status() -> str:
    docker = shutil.which("docker")
    if not docker:
        return ""
    result = subprocess.run(
        [
            docker,
            "ps",
            "--filter",
            "label=com.docker.compose.project=m3u-picker",
            "--filter",
            "label=com.docker.compose.service=m3u-picker",
            "--filter",
            "publish=9999",
            "--format",
            "{{.Names}} | {{.Status}} | {{.Ports}}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def wait_for_setup_page(seconds: int = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(WEB_URL, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("Docker started, but the setup page did not become available within two minutes.")


def install(install_dir: Path, source_ref: str, mode: str) -> None:
    require_docker()
    install_dir = install_dir.resolve()
    existing_install = install_dir.exists()
    running = production_container_status()
    if running:
        print(f"Running production M3U Web Picker detected on port 9999:\n  {running}\n")
    else:
        print("No running production M3U Web Picker was detected on port 9999.\n")
    mode = choose_install_mode(install_dir, mode)
    if mode == CLEAN and existing_install:
        confirm_clean()
    staging = install_dir.with_name(install_dir.name + ".staging")
    previous = install_dir.with_name(install_dir.name + ".previous")
    archive_url = f"https://codeload.github.com/{REPOSITORY}/zip/refs/heads/{source_ref}"

    with tempfile.TemporaryDirectory(prefix="m3u-web-picker-install-") as temporary:
        temporary_dir = Path(temporary)
        archive = temporary_dir / "source.zip"
        extracted = temporary_dir / "source"
        extracted.mkdir()
        print(f"Downloading M3U Web Picker ({source_ref})...")
        download(archive_url, archive)
        extract_github_archive(archive, extracted)

        if not (extracted / "docker-compose.yml").is_file() or not (
            extracted / "scripts" / "docker-windows.ps1"
        ).is_file():
            raise RuntimeError("The downloaded ZIP does not contain the expected M3U Web Picker files.")

        remove_tree(staging)
        shutil.move(str(extracted), str(staging))
        if install_dir.exists():
            preserve(".env", install_dir, staging)
            preserve("runtime", install_dir, staging)
            remove_tree(previous)
            install_dir.replace(previous)

        prepare_install_environment(staging)

        try:
            staging.replace(install_dir)
        except Exception:
            if previous.exists() and not install_dir.exists():
                previous.replace(install_dir)
            raise

    setup = install_dir / "scripts" / "docker-windows.ps1"
    try:
        setup_command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(setup),
        ]
        if mode == CLEAN:
            setup_command.append("-CleanVolumes")
        subprocess.run(
            setup_command,
            cwd=install_dir,
            check=True,
        )
    except Exception:
        print(f"The previous source remains available at {previous}.")
        raise
    print("Waiting for the setup guide...")
    wait_for_setup_page()
    remove_tree(previous)
    print(f"\nM3U Web Picker is ready: {WEB_URL}")
    webbrowser.open(WEB_URL)


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This installer is intended for Windows.")
    parser = argparse.ArgumentParser(description="Install the Docker edition of M3U Web Picker on Windows")
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    parser.add_argument("--source-ref", default="main", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=(UPGRADE, CLEAN), help="UP preserves saved setup; CL starts clean")
    arguments = parser.parse_args()
    install(arguments.install_dir, arguments.source_ref, arguments.mode or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
