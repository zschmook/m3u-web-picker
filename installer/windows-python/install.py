from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path, PurePosixPath


REPOSITORY = "zschmook/m3u-web-picker"
SOURCE_REF = "main"
WEB_URL = "http://localhost:9999"
FFMPEG_VERSION = "8.1.2"
FFMPEG_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/"
    "ffmpeg-8.1.2-full_build.zip"
)
FFMPEG_SHA256 = "b8cdefab5f50590a076c27c2b56b0294a0e6154faded28ba1ba05ebc4f801f57"
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(base) / "m3u-web-picker" if base else Path.home() / "AppData" / "Local" / "m3u-web-picker"


def default_dvr_dir() -> Path:
    return Path.home() / "Videos" / "M3U-Web-Picker-DVR"


def download(url: str, destination: Path, *, timeout: int = 900) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "M3U-Web-Picker-Python-Bootstrap"})
    with urllib.request.urlopen(request, timeout=timeout) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = file_sha256(path)
    if actual.lower() != expected.lower():
        raise RuntimeError(f"SHA-256 mismatch for {path.name}: got {actual}")


def safe_zip_target(base: Path, member: str) -> Path:
    target = (base / Path(*PurePosixPath(member).parts)).resolve()
    resolved_base = base.resolve()
    if target != resolved_base and resolved_base not in target.parents:
        raise RuntimeError(f"Unsafe path in ZIP: {member}")
    return target


def extract_github_archive(archive: Path, destination: Path) -> None:
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
            relative = "/".join(parts[1:])
            if not relative:
                continue
            target = safe_zip_target(destination, relative)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(item) as incoming, target.open("wb") as output:
                    shutil.copyfileobj(incoming, output)


def extract_ffmpeg_tools(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    wanted = {"ffmpeg.exe", "ffprobe.exe"}
    found: set[str] = set()
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            name = PurePosixPath(item.filename).name.lower()
            if name not in wanted or item.is_dir():
                continue
            with source.open(item) as incoming, (destination / name).open("wb") as output:
                shutil.copyfileobj(incoming, output)
            found.add(name)
    missing = wanted - found
    if missing:
        raise RuntimeError(f"FFmpeg archive is missing: {', '.join(sorted(missing))}")


def detect_lan_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        address = str(sock.getsockname()[0])
        if address and not address.startswith(("127.", "169.254.")):
            return address
    except OSError:
        pass
    finally:
        sock.close()
    return ""


def app_reachable() -> bool:
    try:
        with urllib.request.urlopen(WEB_URL, timeout=2) as response:
            return response.status < 500
    except Exception:
        return False


def wait_for_app(seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if app_reachable():
            return
        time.sleep(0.75)
    raise RuntimeError("The Python host did not become reachable on port 9999. Check host.log.")


def stop_existing(data_dir: Path) -> None:
    pid_file = data_dir / "host.pid"
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pid = 0
    if pid > 0 and app_reachable():
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and app_reachable():
            time.sleep(0.25)
    pid_file.unlink(missing_ok=True)
    if app_reachable():
        raise RuntimeError("Port 9999 is already in use by a process this script did not start.")


def write_host_env(root: Path, app_dir: Path, dvr_dir: Path) -> Path:
    data_dir = root / "data"
    values = {
        "PYTHONUNBUFFERED": "1",
        "M3U_ONBOARDING_ENABLED": "true",
        "M3U_BACKUP_ENABLED": "true",
        "M3U_DATA_DIR": str(data_dir),
        "M3U_CAST_HLS_DIR": str(root / "cast-hls"),
        "M3U_BACKUP_CONTAINER_DIR": str(root / "backups"),
        "M3U_DVR_CONTAINER_DIR": str(dvr_dir),
        "M3U_DVR_HOST_DIR": str(dvr_dir),
        "M3U_FFMPEG": str(root / "ffmpeg" / "ffmpeg.exe"),
        "M3U_PORT": "9999",
        "M3U_EXTERNAL_PORT": "9999",
        "M3U_LAN_HOST": detect_lan_ipv4(),
        "BACKUP_RETENTION_DAYS": "30",
        "MASTER_REFRESH_HOUR": "3",
        "MASTER_REFRESH_MINUTE": "0",
    }
    path = root / "host.env"
    path.write_text("\n".join(f"{name}={value}" for name, value in values.items()) + "\n", encoding="utf-8")
    return path


LAUNCHER = r'''from __future__ import annotations
import argparse, os, subprocess, time, urllib.request, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
PYTHONW = ROOT / "venv" / "Scripts" / "pythonw.exe"
LOG = ROOT / "host.log"
URL = "http://localhost:9999"

def reachable():
    try:
        with urllib.request.urlopen(URL, timeout=2) as response:
            return response.status < 500
    except Exception:
        return False

def environment():
    env = os.environ.copy()
    for raw in (ROOT / "host.env").read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.lstrip().startswith("#") and "=" in raw:
            name, value = raw.split("=", 1)
            env[name.strip()] = value.strip()
    env["M3U_HOST_ENV"] = str(ROOT / "host.env")
    env["PATH"] = str(ROOT / "ffmpeg") + os.pathsep + env.get("PATH", "")
    # Modules in app/src still use top-level imports, while api/media packages
    # live at the application root. Make both locations explicit.
    env["PYTHONPATH"] = os.pathsep.join((str(APP / "src"), str(APP)))
    return env

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if not reachable():
        log = LOG.open("a", encoding="utf-8", errors="replace")
        subprocess.Popen(
            [str(PYTHONW), "-m", "host_runtime"], cwd=str(APP), env=environment(),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=0x00000008 | 0x00000200, close_fds=True,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not reachable():
            time.sleep(0.75)
    if args.open:
        webbrowser.open(URL)

if __name__ == "__main__":
    main()
'''


def write_launcher(root: Path) -> Path:
    launcher = root / "run.py"
    launcher.write_text(LAUNCHER, encoding="utf-8")
    return launcher


def desktop_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def write_desktop_launcher(root: Path, launcher: Path) -> Path | None:
    desktop = desktop_dir()
    if not desktop.is_dir():
        return None
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    shortcut = desktop / "M3U Web Picker.cmd"
    shortcut.write_text(
        "@echo off\r\n"
        f'start "" "{pythonw}" "{launcher}" --open\r\n',
        encoding="utf-8",
    )
    return shortcut


def install(root: Path, *, source_ref: str) -> None:
    if os.name != "nt":
        raise RuntimeError("This fallback is intended for Windows.")
    if sys.version_info < (3, 12):
        raise RuntimeError("Python 3.12 or newer is required.")

    root = root.resolve()
    app_dir = root / "app"
    data_dir = root / "data"
    dvr_dir = default_dvr_dir()
    for directory in (root, data_dir, root / "backups", root / "cast-hls", dvr_dir):
        directory.mkdir(parents=True, exist_ok=True)

    stop_existing(data_dir)
    source_url = f"https://codeload.github.com/{REPOSITORY}/zip/refs/heads/{source_ref}"
    with tempfile.TemporaryDirectory(prefix="m3u-web-picker-python-") as temporary:
        temporary_dir = Path(temporary)
        source_zip = temporary_dir / "source.zip"
        staged_app = root / ".app-staging"
        old_app = root / ".app-previous"
        shutil.rmtree(staged_app, ignore_errors=True)
        staged_app.mkdir()
        print(f"Downloading M3U Web Picker ({source_ref})...")
        download(source_url, source_zip, timeout=300)
        extract_github_archive(source_zip, staged_app)
        for required in ("src/app.py", "src/host_runtime.py", "requirements.txt"):
            if not (staged_app / required).is_file():
                raise RuntimeError(f"Downloaded source is missing {required}")

        ffmpeg_dir = root / "ffmpeg"
        if not (ffmpeg_dir / "ffmpeg.exe").is_file() or not (ffmpeg_dir / "ffprobe.exe").is_file():
            ffmpeg_zip = temporary_dir / "ffmpeg.zip"
            print(f"Downloading FFmpeg {FFMPEG_VERSION}...")
            download(FFMPEG_URL, ffmpeg_zip)
            verify_sha256(ffmpeg_zip, FFMPEG_SHA256)
            shutil.rmtree(ffmpeg_dir, ignore_errors=True)
            extract_ffmpeg_tools(ffmpeg_zip, ffmpeg_dir)

        shutil.rmtree(old_app, ignore_errors=True)
        if app_dir.exists():
            app_dir.replace(old_app)
        try:
            staged_app.replace(app_dir)
        except Exception:
            if old_app.exists() and not app_dir.exists():
                old_app.replace(app_dir)
            raise

    venv_dir = root / "venv"
    venv_python = venv_dir / "Scripts" / "python.exe"
    if not venv_python.is_file():
        print("Creating private Python environment...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    print("Installing Python dependencies...")
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(app_dir / "requirements.txt")],
        cwd=app_dir,
        check=True,
    )
    write_host_env(root, app_dir, dvr_dir)
    launcher = write_launcher(root)
    shortcut = write_desktop_launcher(root, launcher)
    shutil.rmtree(root / ".app-previous", ignore_errors=True)

    print("Starting M3U Web Picker on port 9999...")
    subprocess.Popen([sys.executable, str(launcher)], cwd=root, creationflags=CREATE_NO_WINDOW, close_fds=True)
    wait_for_app()
    webbrowser.open(WEB_URL)
    print(f"Ready: {WEB_URL}")
    print(f"Installed at: {root}")
    print(f"DVR recordings: {dvr_dir}")
    if shortcut:
        print(f"Desktop launcher: {shortcut}")
    print("If Windows asks, allow Python access on private networks so phones and Roku can connect.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run M3U Web Picker on Windows without Docker")
    parser.add_argument("--root", type=Path, default=default_root(), help=argparse.SUPPRESS)
    parser.add_argument("--source-ref", default=SOURCE_REF, help=argparse.SUPPRESS)
    args = parser.parse_args()
    install(args.root, source_ref=args.source_ref)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("Cancelled.")
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
