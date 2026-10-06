from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import webbrowser
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "zschmook/m3u-web-picker"
SOURCE_REF = "latest"
IMAGE = "ghcr.io/zschmook/m3u-web-picker"
WEB_URL = "http://localhost:9999"
UPGRADE = "UP"
CLEAN = "CL"
WINDOWS_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def host_system() -> str:
    return platform.system()


def default_install_dir() -> Path:
    system = host_system()
    if system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "m3u-web-picker"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "m3u-web-picker"
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "m3u-web-picker"


def default_dvr_dir() -> Path:
    if host_system() == "Darwin":
        return Path.home() / "Movies" / "M3U-Web-Picker-DVR"
    return Path.home() / "Videos" / "M3U-Web-Picker-DVR"


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
    if input("Type CLEAN to continue: ").strip().upper() != "CLEAN":
        raise SystemExit("Clean installation cancelled; nothing was changed.")


def safe_install_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    anchors = {Path(resolved.anchor), Path.home().resolve()}
    if resolved in anchors:
        raise RuntimeError(f"Refusing unsafe install directory: {resolved}")
    return resolved


def validate_existing_install(path: Path) -> None:
    if not path.exists():
        return
    expected = (path / "docker-compose.yml", path / "src" / "app.py")
    if not all(candidate.is_file() for candidate in expected):
        raise RuntimeError(
            f"{path} exists but is not a Docker M3U Web Picker installation. "
            "Move it, remove it, or choose a different --install-dir."
        )


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "M3U-Web-Picker-Installer"})
    with urllib.request.urlopen(request, timeout=300) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def extract_github_archive(archive: Path, destination: Path) -> None:
    resolved_destination = destination.resolve()
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
            if target != resolved_destination and resolved_destination not in target.parents:
                raise RuntimeError(f"Unsafe path in ZIP: {item.filename}")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output)


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


def dotenv_value(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    prefix = f"{name}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def set_dotenv_value(path: Path, name: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replacement = f"{name}={value}"
    updated: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{name}="):
            if not found:
                updated.append(replacement)
                found = True
        else:
            updated.append(line)
    if not found:
        updated.append(replacement)
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def detect_lan_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 80))
        address = str(probe.getsockname()[0])
        if address and not address.startswith(("127.", "169.254.")):
            return address
    except OSError:
        pass
    finally:
        probe.close()
    return ""


def prepare_install_environment(staging: Path, image_tag: str = "latest") -> None:
    env_path = staging / ".env"
    example = staging / ".env.example"
    if example.is_file() and not env_path.exists():
        shutil.copy2(example, env_path)
    set_dotenv_value(env_path, "M3U_HOST_PORT", "9999")
    set_dotenv_value(env_path, "M3U_EXTERNAL_PORT", "9999")
    set_dotenv_value(env_path, "M3U_IMAGE", f"{IMAGE}:{image_tag}")
    lan_host = detect_lan_ipv4()
    if lan_host:
        set_dotenv_value(env_path, "M3U_LAN_HOST", lan_host)
    configured_dvr = dotenv_value(env_path, "M3U_DVR_DIR")
    if not configured_dvr or configured_dvr == "./runtime/recordings":
        dvr_dir = default_dvr_dir()
        dvr_dir.mkdir(parents=True, exist_ok=True)
        set_dotenv_value(env_path, "M3U_DVR_DIR", dvr_dir.as_posix())


def command_flags(*, quiet: bool = False) -> dict[str, object]:
    flags: dict[str, object] = {}
    if quiet:
        flags.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if host_system() == "Windows" and WINDOWS_NO_WINDOW:
        flags["creationflags"] = WINDOWS_NO_WINDOW
    return flags


def docker_executable() -> str:
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError("Docker was not found. Install and start Docker Desktop or Docker Engine, then try again.")
    return executable


def require_docker() -> str:
    docker = docker_executable()
    result = subprocess.run([docker, "info"], check=False, **command_flags(quiet=True))
    if result.returncode:
        raise RuntimeError("Docker is installed but its engine is not running or ready.")
    result = subprocess.run([docker, "compose", "version"], check=False, **command_flags(quiet=True))
    if result.returncode:
        raise RuntimeError("Docker Compose v2 is unavailable. Install or update the Docker Compose plugin.")
    return docker


def production_container_status(docker: str) -> str:
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
        **({"creationflags": WINDOWS_NO_WINDOW} if host_system() == "Windows" and WINDOWS_NO_WINDOW else {}),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def compose_command(docker: str, install_dir: Path) -> list[str]:
    command = [docker, "compose", "-f", str(install_dir / "docker-compose.release.yml")]
    if host_system() != "Darwin" and (shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe")):
        command.extend(["-f", str(install_dir / "docker-compose.gpu.yml")])
        print("NVIDIA GPU detected; requesting Docker GPU passthrough.")
    elif host_system() == "Darwin":
        print("Note: Docker GPU passthrough is not supported on macOS; FFmpeg will use CPU fallback.")
    return command


def run_compose(command: list[str], install_dir: Path, *arguments: str) -> None:
    subprocess.run([*command, *arguments], cwd=install_dir, check=True)


def prepare_bind_mount_directories(command: list[str], install_dir: Path) -> None:
    result = subprocess.run(
        [*command, "config", "--format", "json"], cwd=install_dir,
        capture_output=True, text=True, check=True, **command_flags(),
    )
    # Compose resolves quoted values, environment interpolation, and relative
    # paths. Keep its complete configuration in memory: it may contain secrets.
    config = json.loads(result.stdout)
    volumes = config.get("services", {}).get("m3u-picker", {}).get("volumes", [])
    for volume in volumes:
        if volume.get("type") != "bind":
            continue
        source = Path(volume["source"]).expanduser()
        if not source.is_absolute():
            source = install_dir / source
        try:
            source.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Could not create the host folder for {volume['target']}: {source}. "
                "Check that this location is a writable folder, then retry UPGRADE (UP)."
            ) from exc


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


def resolve_source(source_ref: str) -> tuple[str, str]:
    if source_ref == "latest":
        request = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
            headers={"User-Agent": "M3U-Web-Picker-Installer"})
        with urllib.request.urlopen(request, timeout=30) as response:
            source_ref = json.load(response)["tag_name"]
        if not re.fullmatch(r"v\d+", source_ref):
            raise RuntimeError("The latest release does not have a supported version tag.")
    is_release = bool(re.fullmatch(r"v\d+", source_ref))
    kind = "tags" if is_release else "heads"
    return (f"https://codeload.github.com/{REPOSITORY}/zip/refs/{kind}/{source_ref}",
            source_ref if is_release else "latest")


def install(install_dir: Path, source_ref: str, mode: str) -> None:
    install_dir = safe_install_dir(install_dir)
    validate_existing_install(install_dir)
    docker = require_docker()
    existing_install = install_dir.exists()
    running = production_container_status(docker)
    if running:
        print(f"Running production M3U Web Picker detected on port 9999:\n  {running}\n")
    else:
        print("No running production M3U Web Picker was detected on port 9999.\n")
    mode = choose_install_mode(install_dir, mode)
    if mode == CLEAN and existing_install:
        confirm_clean()

    transaction = uuid.uuid4().hex
    staging = install_dir.with_name(f".{install_dir.name}.staging-{transaction}")
    previous = install_dir.with_name(f".{install_dir.name}.previous-{transaction}")
    archive_url, image_tag = resolve_source(source_ref)

    with tempfile.TemporaryDirectory(prefix="m3u-web-picker-install-") as temporary:
        temporary_dir = Path(temporary)
        archive = temporary_dir / "source.zip"
        extracted = temporary_dir / "source"
        extracted.mkdir()
        print(f"Downloading M3U Web Picker ({source_ref})...")
        download(archive_url, archive)
        extract_github_archive(archive, extracted)
        if not (extracted / "docker-compose.release.yml").is_file():
            raise RuntimeError("The downloaded ZIP does not contain the expected M3U Web Picker files.")

        shutil.move(str(extracted), str(staging))
        if existing_install and mode == UPGRADE:
            preserve(".env", install_dir, staging)
            preserve("runtime", install_dir, staging)
        prepare_install_environment(staging, image_tag)
        staging_compose = compose_command(docker, staging)
        prepare_bind_mount_directories(staging_compose, staging)
        # A failed image download must leave an existing installation running.
        run_compose(staging_compose, staging, "pull")
        if existing_install:
            install_dir.replace(previous)
        try:
            staging.replace(install_dir)
        except Exception:
            if previous.exists() and not install_dir.exists():
                previous.replace(install_dir)
            raise

    compose = compose_command(docker, install_dir)
    try:
        run_compose(compose, install_dir, "down", "-v" if mode == CLEAN else "--remove-orphans")
        run_compose(compose, install_dir, "up", "-d", "--no-build")
        run_compose(compose, install_dir, "ps")
        print("Waiting for the setup guide...")
        wait_for_setup_page()
    except Exception:
        if previous.exists():
            print(f"The previous source remains available at {previous}.")
        raise

    remove_tree(previous)
    print(f"\nM3U Web Picker is ready: {WEB_URL}")
    print(f"Installed at: {install_dir}")
    print(f"DVR recordings: {dotenv_value(install_dir / '.env', 'M3U_DVR_DIR')}")
    webbrowser.open(WEB_URL)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Docker edition of M3U Web Picker")
    parser.add_argument("--install-dir", type=Path, default=default_install_dir())
    parser.add_argument("--source-ref", default=SOURCE_REF, help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=(UPGRADE, CLEAN), help="UP preserves saved setup; CL starts clean")
    arguments = parser.parse_args()
    install(arguments.install_dir, arguments.source_ref, arguments.mode or "")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("Cancelled.")
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        if getattr(sys, "frozen", False):
            try:
                input("Press Enter to close...")
            except EOFError:
                pass
        raise SystemExit(1)
