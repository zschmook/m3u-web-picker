from __future__ import annotations

import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from database import connect as connect_database


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jellyfin_cache_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    using_jellyfin INTEGER NOT NULL DEFAULT 0,
    cleanup_enabled INTEGER NOT NULL DEFAULT 0,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    host_path TEXT NOT NULL DEFAULT '',
    updated_at TEXT,
    last_cleanup_at TEXT,
    last_cleanup_status TEXT NOT NULL DEFAULT '',
    last_cleanup_message TEXT NOT NULL DEFAULT '',
    last_deleted_entries INTEGER NOT NULL DEFAULT 0
)
"""

_CONTAINER_PATH = Path("/jellyfin-cache")
_DANGEROUS_CONTAINER_PATHS = {
    "/", "/app", "/app/data", "/backups", "/bin", "/boot", "/dev",
    "/etc", "/home", "/lib", "/lib64", "/media", "/mnt", "/opt",
    "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/tmp",
    "/usr", "/var",
}
_DANGEROUS_HOST_PATHS = {
    "/", "/users", "/home", "/var", "/opt", "/srv", "/mnt",
    "/media", "/tmp",
}


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(_TABLE_SQL)
    conn.execute(
        """
        INSERT OR IGNORE INTO jellyfin_cache_settings
            (id, using_jellyfin, cleanup_enabled, acknowledged, host_path)
        VALUES (1, 0, 0, 0, '')
        """
    )
    conn.commit()
    return conn


def _normalize_host_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    text = re.sub(r"/{2,}", "/", text)
    if re.fullmatch(r"[A-Za-z]:/?", text):
        return text[:2].lower() + "/"
    if text != "/":
        text = text.rstrip("/")
    if re.match(r"^[A-Za-z]:/", text):
        text = text[0].lower() + text[1:]
    return text


def _host_path_is_absolute(value: str) -> bool:
    return bool(value.startswith("/") or re.match(r"^[A-Za-z]:/", value))


def runtime_mount_status() -> dict:
    host_path = _normalize_host_path(os.environ.get("M3U_JELLYFIN_CACHE_HOST_DIR", ""))
    target = Path(
        str(os.environ.get("M3U_JELLYFIN_CACHE_CONTAINER_DIR", str(_CONTAINER_PATH)))
        or str(_CONTAINER_PATH)
    )
    return {
        "configured_host_path": host_path,
        "container_path": str(target),
        "mount_configured": bool(host_path),
        "container_exists": target.exists(),
        "container_is_dir": target.is_dir(),
        "container_writable": os.access(target, os.W_OK) if target.exists() else False,
    }


def validate_host_path(host_path: str, *, write_probe: bool = True) -> dict:
    entered = _normalize_host_path(host_path)
    runtime = runtime_mount_status()
    configured = str(runtime.get("configured_host_path") or "")

    if not entered:
        return {"ok": False, "message": "Enter the Jellyfin cache directory.", **runtime}
    if not _host_path_is_absolute(entered):
        return {"ok": False, "message": "Use an absolute Jellyfin cache path.", **runtime}
    if entered.lower() in _DANGEROUS_HOST_PATHS or re.fullmatch(r"[a-z]:/", entered.lower()):
        return {"ok": False, "message": "That path is too broad for automatic deletion.", **runtime}
    parts = [part for part in entered.split("/") if part and not part.endswith(":")]
    if len(parts) < 3:
        return {"ok": False, "message": "That path is too broad for automatic deletion.", **runtime}
    if "jellyfin" not in entered.lower() and not entered.lower().endswith("/cache"):
        return {
            "ok": False,
            "message": "Choose the Jellyfin cache directory, not a general media or configuration directory.",
            **runtime,
        }
    if not configured:
        return {
            "ok": False,
            "message": (
                "The dev container does not have a Jellyfin cache directory mounted. "
                "Start it with M3U_JELLYFIN_CACHE_DIR set to this host path."
            ),
            **runtime,
        }
    if entered.lower() != configured.lower():
        return {
            "ok": False,
            "message": (
                "The pasted path does not match the Jellyfin cache directory mounted into the dev container. "
                "Restart with M3U_JELLYFIN_CACHE_DIR set to this exact path."
            ),
            **runtime,
        }

    target = Path(str(runtime.get("container_path") or str(_CONTAINER_PATH)))
    try:
        resolved = target.resolve(strict=True)
    except (OSError, RuntimeError):
        return {"ok": False, "message": "The Jellyfin cache mount is not available in the container.", **runtime}
    if target.is_symlink() or str(resolved).rstrip("/").lower() in _DANGEROUS_CONTAINER_PATHS:
        return {"ok": False, "message": "The mounted path is not safe for automatic deletion.", **runtime}
    if not resolved.is_dir():
        return {"ok": False, "message": "The mounted Jellyfin cache path is not a directory.", **runtime}
    if not os.access(resolved, os.W_OK):
        return {"ok": False, "message": "The Jellyfin cache directory is not writable.", **runtime}

    if write_probe:
        probe = resolved / f".m3u-picker-write-test-{uuid.uuid4().hex}"
        try:
            probe.write_text("test", encoding="utf-8")
            probe.unlink()
        except Exception as exc:
            try:
                probe.unlink(missing_ok=True)
            except Exception:
                pass
            return {
                "ok": False,
                "message": f"The Jellyfin cache directory could not be written: {type(exc).__name__}.",
                **runtime,
            }

    return {
        "ok": True,
        "message": "Jellyfin cache path is mounted and writable.",
        "host_path": entered,
        **runtime,
    }


def get_settings(db_path: Path | str) -> dict:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT using_jellyfin, cleanup_enabled, acknowledged, host_path,
                   updated_at, last_cleanup_at, last_cleanup_status,
                   last_cleanup_message, last_deleted_entries
            FROM jellyfin_cache_settings WHERE id = 1
            """
        ).fetchone()
    runtime = runtime_mount_status()
    return {
        "using_jellyfin": bool(row["using_jellyfin"]),
        "cleanup_enabled": bool(row["cleanup_enabled"]),
        "acknowledged": bool(row["acknowledged"]),
        "host_path": str(row["host_path"] or ""),
        "updated_at": row["updated_at"],
        "last_cleanup_at": row["last_cleanup_at"],
        "last_cleanup_status": str(row["last_cleanup_status"] or ""),
        "last_cleanup_message": str(row["last_cleanup_message"] or ""),
        "last_deleted_entries": int(row["last_deleted_entries"] or 0),
        "runtime": runtime,
    }


def update_settings(
    db_path: Path | str,
    *,
    using_jellyfin: bool | None = None,
    cleanup_enabled: bool | None = None,
    acknowledged: bool | None = None,
    host_path: str | None = None,
) -> dict:
    previous = get_settings(db_path)
    using = previous["using_jellyfin"] if using_jellyfin is None else bool(using_jellyfin)
    ack = previous["acknowledged"] if acknowledged is None else bool(acknowledged)
    path = previous["host_path"] if host_path is None else _normalize_host_path(host_path)
    cleanup = previous["cleanup_enabled"] if cleanup_enabled is None else bool(cleanup_enabled)

    if not using:
        cleanup = False
        ack = False
    if cleanup and not ack:
        raise ValueError("Turn on ‘I understand the risks’ before enabling Jellyfin cache cleanup.")
    if cleanup:
        validation = validate_host_path(path)
        if not validation.get("ok"):
            raise ValueError(str(validation.get("message") or "Jellyfin cache path is not valid."))

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE jellyfin_cache_settings
            SET using_jellyfin = ?, cleanup_enabled = ?, acknowledged = ?,
                host_path = ?, updated_at = ?
            WHERE id = 1
            """,
            (1 if using else 0, 1 if cleanup else 0, 1 if ack else 0, path, now),
        )
        conn.commit()
    return get_settings(db_path)


def _record_cleanup(
    db_path: Path | str,
    *,
    status: str,
    message: str,
    deleted_entries: int,
) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE jellyfin_cache_settings
            SET last_cleanup_at = ?, last_cleanup_status = ?,
                last_cleanup_message = ?, last_deleted_entries = ?
            WHERE id = 1
            """,
            (now, str(status), str(message), int(deleted_entries)),
        )
        conn.commit()


def clear_configured_cache(db_path: Path | str) -> dict:
    settings = get_settings(db_path)
    if not settings.get("using_jellyfin") or not settings.get("cleanup_enabled"):
        return {"status": "disabled", "deleted_entries": 0, "message": "Jellyfin cache cleanup is disabled."}
    if not settings.get("acknowledged"):
        message = "Jellyfin cache cleanup was skipped because the risk acknowledgement is off."
        _record_cleanup(db_path, status="skipped", message=message, deleted_entries=0)
        return {"status": "skipped", "deleted_entries": 0, "message": message}

    validation = validate_host_path(str(settings.get("host_path") or ""), write_probe=False)
    if not validation.get("ok"):
        message = str(validation.get("message") or "Jellyfin cache path is not valid.")
        _record_cleanup(db_path, status="failed", message=message, deleted_entries=0)
        return {"status": "failed", "deleted_entries": 0, "message": message}

    target = Path(str(validation.get("container_path") or str(_CONTAINER_PATH))).resolve(strict=True)
    deleted = 0
    errors: list[str] = []
    for child in list(target.iterdir()):
        try:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
            deleted += 1
        except Exception as exc:
            errors.append(f"{child.name}: {type(exc).__name__}")

    if errors:
        message = f"Deleted {deleted} cache entries; {len(errors)} could not be removed ({'; '.join(errors[:3])})."
        status = "failed"
    else:
        message = f"Deleted {deleted} Jellyfin cache entries after the successful update."
        status = "success"
    _record_cleanup(db_path, status=status, message=message, deleted_entries=deleted)
    return {"status": status, "deleted_entries": deleted, "message": message}


def install(core_module) -> None:
    current = getattr(core_module, "run_master_update", None)
    if not callable(current) or getattr(current, "_jellyfin_cache_wrapped", False):
        return
    original = current
    db_path = core_module.DB_PATH

    def run_master_update_with_jellyfin_cache(*, trigger: str = "manual"):
        result = original(trigger=trigger)
        cleanup = clear_configured_cache(db_path)
        result["jellyfin_cache_cleanup"] = cleanup
        if cleanup.get("status") == "failed":
            warnings = list(result.get("provider_warnings") or [])
            warnings.append(f"Jellyfin cache cleanup: {cleanup.get('message') or 'failed.'}")
            result["provider_warnings"] = warnings
        return result

    run_master_update_with_jellyfin_cache._jellyfin_cache_wrapped = True
    run_master_update_with_jellyfin_cache._jellyfin_cache_original = original
    core_module.run_master_update = run_master_update_with_jellyfin_cache
