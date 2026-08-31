from __future__ import annotations

import atexit
import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent


def _load_env_file() -> None:
    raw = os.environ.get("M3U_HOST_ENV", "").strip()
    env_path = Path(raw).expanduser() if raw else APP_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_env_file()

# Host-friendly defaults. The Windows installer writes explicit absolute paths,
# while source checkouts can run directly with local data/backups directories.
os.environ.setdefault("M3U_DATA_DIR", str(APP_DIR / "data"))
os.environ.setdefault("M3U_CAST_HLS_DIR", str(APP_DIR / "data" / "cast-hls"))
os.environ.setdefault("M3U_BACKUP_CONTAINER_DIR", str(APP_DIR / "runtime" / "backups"))
os.environ.setdefault("M3U_EXTERNAL_PORT", os.environ.get("M3U_PORT", "9999"))
os.environ.setdefault("M3U_ONBOARDING_ENABLED", "true")

from waitress import serve  # noqa: E402
import hdhr_config  # noqa: E402
from api.hdhr_discovery import start_hdhr_discovery, stop_hdhr_discovery  # noqa: E402
from app import app  # noqa: E402
from settings import SETTINGS  # noqa: E402


def _pid_path() -> Path:
    data_dir = Path(os.environ["M3U_DATA_DIR"]).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "host.pid"


def _clear_pid(path: Path) -> None:
    try:
        if path.exists() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> None:
    pid_path = _pid_path()
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    atexit.register(_clear_pid, pid_path)

    if hdhr_config.is_enabled() and start_hdhr_discovery():
        atexit.register(stop_hdhr_discovery)

    serve(
        app,
        host="0.0.0.0",
        port=SETTINGS.port,
        threads=8,
        clear_untrusted_proxy_headers=True,
    )


if __name__ == "__main__":
    main()
