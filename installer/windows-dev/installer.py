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
import winreg
import zipfile
from pathlib import Path

APP_NAME = "M3U Web Picker Dev"
INSTALL_FOLDER = "M3U-Web-Picker-Dev"
SOURCE_REF = "agent/windows-python-dev-m3u"
SOURCE_ARCHIVE_URL = (
    "https://codeload.github.com/zschmook/m3u-web-picker/zip/refs/heads/"
    + SOURCE_REF
)
PORT = 9998
WEB_URL = f"http://localhost:{PORT}"
PYTHON_PACKAGE_ID = "Python.Python.3.12"

FFMPEG_VERSION = "8.1.2"
FFMPEG_URL = (
    "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/"
    "ffmpeg-8.1.2-full_build.zip"
)
FFMPEG_SHA256 = "b8cdefab5f50590a076c27c2b56b0294a0e6154faded28ba1ba05ebc4f801f57"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Uninstall\M3U Web Picker Dev"
)

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(value) if value else Path.home() / "AppData" / "Local"


ROOT = local_app_data() / INSTALL_FOLDER
APP_DIR = ROOT / "app"
VENV_DIR = ROOT / "venv"
DATA_DIR = ROOT / "data"
BACKUP_DIR = ROOT / "backups"
CAST_DIR = ROOT / "cast-hls"
FFMPEG_DIR = ROOT / "ffmpeg"
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"
HOST_ENV = ROOT / "host.env"
HOST_LOG = ROOT / "host.log"
HOST_PID = DATA_DIR / "host.pid"
INSTALLED_EXE = ROOT / "M3U-Web-Picker-Dev.exe"
STAGING_APP = ROOT / ".app-staging"


def banner(title: str) -> None:
    print(title)
    print("=" * len(title))
    print()


def pause() -> None:
    try:
        input("Press Enter to close...")
    except EOFError:
        pass


def download(url: str, destination: Path, *, timeout: int = 900) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "M3U-Web-Picker-Dev-Installer"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual.lower() != expected.lower():
        raise RuntimeError(
            f"SHA-256 mismatch for {path.name}: expected {expected}, got {actual}"
        )


def safe_target(base: Path, member: str) -> Path:
    base = base.resolve()
    target = (base / member).resolve()
    if target != base and base not in target.parents:
        raise RuntimeError(f"Unsafe path in ZIP: {member}")
    return target


def extract_source_archive(archive: Path, destination: Path) -> None:
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        files = [name for name in zf.namelist() if name and not name.endswith("/")]
        roots = {name.split("/", 1)[0] for name in files if "/" in name}
        if len(roots) != 1:
            raise RuntimeError("Unexpected GitHub source archive layout")
        root = next(iter(roots)) + "/"
        for info in zf.infolist():
            if not info.filename.startswith(root):
                continue
            relative = info.filename[len(root):]
            if not relative:
                continue
            target = safe_target(destination, relative)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def extract_named_file(archive: Path, filename: str, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        matches = [
            info
            for info in zf.infolist()
            if Path(info.filename).name.lower() == filename.lower()
        ]
        if not matches:
            raise RuntimeError(f"{filename} was not found in {archive.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(matches[0]) as src, destination.open("wb") as dst:
            shutil.copyfileobj(src, dst)


def run(args: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
    )


def python_is_312(executable: Path) -> bool:
    try:
        result = subprocess.run(
            [
                str(executable),
                "-c",
                "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
        return result.returncode == 0 and result.stdout.strip() == "3.12"
    except (OSError, subprocess.SubprocessError):
        return False


def find_python312() -> Path | None:
    candidates = [
        local_app_data() / "Programs" / "Python" / "Python312" / "python.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Python312"
        / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file() and python_is_312(candidate):
            return candidate

    py = shutil.which("py.exe")
    if py:
        try:
            result = subprocess.run(
                [py, "-3.12", "-c", "import sys;print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            candidate = Path(result.stdout.strip())
            if (
                result.returncode == 0
                and candidate.is_file()
                and python_is_312(candidate)
            ):
                return candidate
        except (OSError, subprocess.SubprocessError):
            pass

    python = shutil.which("python.exe")
    if python:
        candidate = Path(python)
        if python_is_312(candidate):
            return candidate
    return None


def ensure_python312() -> Path:
    found = find_python312()
    if found:
        return found

    winget = shutil.which("winget.exe")
    if not winget:
        raise RuntimeError(
            "Python 3.12 is not installed and winget.exe is unavailable."
        )

    print("Python 3.12 was not found. Installing it for the current user...")
    run(
        [
            winget,
            "install",
            "-e",
            "--id",
            PYTHON_PACKAGE_ID,
            "--scope",
            "user",
            "--silent",
            "--accept-source-agreements",
            "--accept-package-agreements",
        ]
    )
    found = find_python312()
    if not found:
        raise RuntimeError(
            "Python installation finished but Python 3.12 could not be found. "
            "Restart Windows and rerun the installer."
        )
    return found


def ensure_ffmpeg() -> None:
    if FFMPEG_EXE.is_file():
        return
    print(f"Downloading private FFmpeg {FFMPEG_VERSION}...")
    temp_zip = Path(tempfile.gettempdir()) / "m3u-web-picker-dev-ffmpeg.zip"
    try:
        download(FFMPEG_URL, temp_zip)
        verify_sha256(temp_zip, FFMPEG_SHA256)
        shutil.rmtree(FFMPEG_DIR, ignore_errors=True)
        extract_named_file(temp_zip, "ffmpeg.exe", FFMPEG_EXE)
    finally:
        temp_zip.unlink(missing_ok=True)


def install_source() -> None:
    print(f"Downloading dev source ({SOURCE_REF})...")
    temp_zip = Path(tempfile.gettempdir()) / "m3u-web-picker-dev-source.zip"
    try:
        download(SOURCE_ARCHIVE_URL, temp_zip, timeout=300)
        extract_source_archive(temp_zip, STAGING_APP)
    finally:
        temp_zip.unlink(missing_ok=True)

    for required in ("app.py", "host_runtime.py", "requirements.txt"):
        if not (STAGING_APP / required).is_file():
            raise RuntimeError(f"Downloaded source is missing {required}")

    old = ROOT / ".app-old"
    shutil.rmtree(old, ignore_errors=True)
    if APP_DIR.exists():
        APP_DIR.replace(old)
    try:
        STAGING_APP.replace(APP_DIR)
    except Exception:
        if old.exists() and not APP_DIR.exists():
            old.replace(APP_DIR)
        raise
    shutil.rmtree(old, ignore_errors=True)


def install_requirements(python: Path) -> None:
    print("Installing Python dependencies...")
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            "pip",
        ],
        cwd=APP_DIR,
    )
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(APP_DIR / "requirements.txt"),
        ],
        cwd=APP_DIR,
    )


def prepare_venv(base_python: Path) -> Path:
    python = VENV_DIR / "Scripts" / "python.exe"
    if not python.is_file():
        print("Creating private Python environment...")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        run([str(base_python), "-m", "venv", str(VENV_DIR)])
    install_requirements(python)
    return python


def detect_lan_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        value = sock.getsockname()[0]
        if value and not value.startswith("127.") and value != "0.0.0.0":
            return value
    except OSError:
        pass
    finally:
        sock.close()

    try:
        for value in socket.gethostbyname_ex(socket.gethostname())[2]:
            if value and not value.startswith("127.") and value != "0.0.0.0":
                return value
    except OSError:
        pass
    return ""


def write_host_env() -> None:
    lan = detect_lan_ipv4()
    print(f"LAN address: {lan}" if lan else "LAN address not detected.")
    values = {
        "PYTHONUNBUFFERED": "1",
        "M3U_ONBOARDING_ENABLED": "true",
        "M3U_BACKUP_ENABLED": "true",
        "M3U_DEBUG_TOOLS": "true",
        "M3U_DATA_DIR": str(DATA_DIR),
        "M3U_CAST_HLS_DIR": str(CAST_DIR),
        "M3U_BACKUP_CONTAINER_DIR": str(BACKUP_DIR),
        "M3U_FFMPEG": str(FFMPEG_EXE),
        "M3U_PORT": str(PORT),
        "M3U_EXTERNAL_PORT": str(PORT),
        "M3U_LAN_HOST": lan,
        "BACKUP_RETENTION_DAYS": "30",
        "MASTER_REFRESH_HOUR": "3",
        "MASTER_REFRESH_MINUTE": "0",
    }
    HOST_ENV.write_text(
        "# Managed by the M3U Web Picker Python dev installer\n"
        + "\n".join(f"{key}={value}" for key, value in values.items())
        + "\n",
        encoding="utf-8",
    )


def load_host_env() -> dict[str, str]:
    env = os.environ.copy()
    if HOST_ENV.is_file():
        for raw in HOST_ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    env["M3U_HOST_ENV"] = str(HOST_ENV)
    env["PATH"] = str(FFMPEG_DIR) + os.pathsep + env.get("PATH", "")
    return env


def copy_self() -> None:
    current = Path(sys.executable).resolve()
    if current == INSTALLED_EXE.resolve():
        return
    if not getattr(sys, "frozen", False):
        raise RuntimeError(
            "The installer must be run from the packaged Windows EXE."
        )
    shutil.copy2(current, INSTALLED_EXE)


def desktop_folder() -> Path:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Desktop")
            return Path(os.path.expandvars(value))
    except OSError:
        return Path.home() / "Desktop"


def install_shell_integration() -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(
            key,
            APP_NAME,
            0,
            winreg.REG_SZ,
            f'"{INSTALLED_EXE}" --run',
        )

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "dev")
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "M3U Web Picker")
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(ROOT))
        winreg.SetValueEx(
            key,
            "UninstallString",
            0,
            winreg.REG_SZ,
            f'"{INSTALLED_EXE}" --uninstall',
        )
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)

    desktop = desktop_folder()
    if desktop.is_dir():
        (desktop / "M3U Web Picker Dev.url").write_text(
            f"[InternetShortcut]\r\nURL={WEB_URL}\r\n",
            encoding="ascii",
        )


def remove_shell_integration() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass
    try:
        (desktop_folder() / "M3U Web Picker Dev.url").unlink(missing_ok=True)
    except OSError:
        pass


def app_reachable() -> bool:
    try:
        with urllib.request.urlopen(
            WEB_URL + "/api/guide/ping",
            timeout=2,
        ) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_app(seconds: int = 60) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if app_reachable():
            return True
        time.sleep(0.75)
    return False


def read_pid() -> int | None:
    try:
        value = int(HOST_PID.read_text(encoding="ascii").strip())
        return value if value > 0 else None
    except (OSError, ValueError):
        return None


def stop_host() -> None:
    pid = read_pid()
    if pid:
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=CREATE_NO_WINDOW,
        )
    HOST_PID.unlink(missing_ok=True)

    deadline = time.time() + 12
    while time.time() < deadline and app_reachable():
        time.sleep(0.25)


def launch_installed() -> None:
    subprocess.Popen(
        [str(INSTALLED_EXE), "--run"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
    )


def run_host() -> int:
    if app_reachable():
        return 0
    python = VENV_DIR / "Scripts" / "python.exe"
    if not python.is_file() or not APP_DIR.is_dir():
        return 2

    ROOT.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    env = load_host_env()
    with HOST_LOG.open("a", encoding="utf-8", errors="replace") as log:
        log.write(f"\n--- starting {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        log.flush()
        process = subprocess.Popen(
            [str(python), str(APP_DIR / "host_runtime.py")],
            cwd=str(APP_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )
        try:
            return process.wait()
        finally:
            try:
                if read_pid() == process.pid:
                    HOST_PID.unlink(missing_ok=True)
            except OSError:
                pass


def open_browser() -> None:
    os.startfile(WEB_URL)  # type: ignore[attr-defined]


def install() -> None:
    banner("M3U Web Picker Python Dev Installer")
    print("Python host only. No Docker. No WSL. Dev port 9998.\n")
    for directory in (ROOT, DATA_DIR, BACKUP_DIR, CAST_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    python = ensure_python312()
    print(f"Python: {python}")
    ensure_ffmpeg()
    print(f"FFmpeg: {FFMPEG_EXE}")
    install_source()
    prepare_venv(python)
    write_host_env()
    copy_self()
    install_shell_integration()

    stop_host()
    launch_installed()
    if not wait_for_app():
        raise RuntimeError(
            "The Python dev host did not become reachable on port 9998; "
            f"check {HOST_LOG}."
        )

    print(f"\nM3U Web Picker Dev is running at {WEB_URL}")
    print(f"Installed under: {ROOT}")
    print("Guide debug M3U/TS links are enabled.")
    open_browser()
    print("\nSetup complete.")
    pause()


def update_install() -> None:
    banner("M3U Web Picker Dev Update")
    if not INSTALLED_EXE.is_file():
        raise RuntimeError("M3U Web Picker Dev is not installed.")

    python = VENV_DIR / "Scripts" / "python.exe"
    if not python.is_file():
        raise RuntimeError(
            "Managed Python environment is missing; rerun the installer."
        )

    stop_host()
    install_source()
    install_requirements(python)
    write_host_env()
    launch_installed()
    if not wait_for_app():
        raise RuntimeError(
            "Updated dev host did not become reachable; check host.log."
        )
    print("Update complete.")
    open_browser()
    pause()


def schedule_cleanup(*, keep_data: bool) -> None:
    python = find_python312()
    if not python:
        raise RuntimeError(
            "Could not locate Python 3.12 for final uninstall cleanup."
        )

    script = r'''import os, shutil, sys, time
root = sys.argv[1]
keep = sys.argv[2] == "1"
time.sleep(2)
if keep:
    for name in os.listdir(root):
        if name in {"data", "backups"}:
            continue
        path = os.path.join(root, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
else:
    shutil.rmtree(root, ignore_errors=True)
'''
    subprocess.Popen(
        [
            str(python),
            "-c",
            script,
            str(ROOT),
            "1" if keep_data else "0",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
    )


def uninstall() -> None:
    banner("M3U Web Picker Dev Uninstaller")
    answer = input("Keep dev database and backups? [Y/n]: ").strip().lower()
    keep_data = answer not in {"n", "no"}

    stop_host()
    remove_shell_integration()
    schedule_cleanup(keep_data=keep_data)

    if keep_data:
        print(f"Dev app removed. Data/backups remain under {ROOT}.")
    else:
        print("Dev app, data, and backups will be removed.")
    print("Uninstall complete.")


def main() -> int:
    if os.name != "nt":
        print("ERROR: This installer is intended for Windows.")
        return 1

    parser = argparse.ArgumentParser(
        description="M3U Web Picker Python dev Windows installer"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--update", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    try:
        if args.run:
            return run_host()
        if args.update:
            update_install()
        elif args.uninstall:
            uninstall()
        else:
            install()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc}\n")
        pause()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
