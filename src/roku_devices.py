from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from database import connect as connect_database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def device_key(device: dict) -> str:
    device_id = str(device.get("device_id", "") or "").strip()
    if device_id:
        return f"device:{device_id}"
    serial = str(device.get("serial_number", "") or "").strip()
    if serial:
        return f"serial:{serial}"
    return ""


def _row_to_dict(row) -> dict:
    return {
        "device_key": row[0],
        "device_id": row[1],
        "serial_number": row[2],
        "name": row[3],
        "model": row[4],
        "model_number": row[5],
        "software_version": row[6],
        "host": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "last_seen_at": row[10],
        "saved": True,
    }


def list_saved(db_path: Path | str) -> list[dict]:
    with closing(connect_database(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT device_key, device_id, serial_number, name, model,
                   model_number, software_version, host, created_at,
                   updated_at, last_seen_at
              FROM roku_devices
             ORDER BY name COLLATE NOCASE, device_key
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_saved(db_path: Path | str, key: str) -> dict | None:
    normalized = str(key or "").strip()
    if not normalized:
        return None
    with closing(connect_database(db_path)) as conn:
        row = conn.execute(
            """
            SELECT device_key, device_id, serial_number, name, model,
                   model_number, software_version, host, created_at,
                   updated_at, last_seen_at
              FROM roku_devices
             WHERE device_key = ?
            """,
            (normalized,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def save_device(db_path: Path | str, host: str, info: dict) -> dict:
    key = device_key(info)
    if not key:
        raise ValueError("Roku device identity is missing; cannot save this device.")

    now = _now()
    values = {
        "device_key": key,
        "device_id": str(info.get("device_id", "") or "").strip(),
        "serial_number": str(info.get("serial_number", "") or "").strip(),
        "name": str(info.get("name", "") or "Roku TV").strip() or "Roku TV",
        "model": str(info.get("model", "") or "").strip(),
        "model_number": str(info.get("model_number", "") or "").strip(),
        "software_version": str(info.get("software_version", "") or "").strip(),
        "host": str(host or "").strip(),
    }
    if not values["host"]:
        raise ValueError("Roku device address is missing.")

    with closing(connect_database(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO roku_devices (
                device_key, device_id, serial_number, name, model,
                model_number, software_version, host, created_at,
                updated_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_key) DO UPDATE SET
                device_id = excluded.device_id,
                serial_number = excluded.serial_number,
                name = excluded.name,
                model = excluded.model,
                model_number = excluded.model_number,
                software_version = excluded.software_version,
                host = excluded.host,
                updated_at = excluded.updated_at,
                last_seen_at = excluded.last_seen_at
            """,
            (
                values["device_key"], values["device_id"], values["serial_number"],
                values["name"], values["model"], values["model_number"],
                values["software_version"], values["host"], now, now, now,
            ),
        )
        conn.commit()
    saved = get_saved(db_path, key)
    if saved is None:
        raise RuntimeError("Saved Roku device could not be reloaded.")
    return saved


def remove_device(db_path: Path | str, key: str) -> bool:
    normalized = str(key or "").strip()
    if not normalized:
        return False
    with closing(connect_database(db_path)) as conn:
        cursor = conn.execute("DELETE FROM roku_devices WHERE device_key = ?", (normalized,))
        conn.commit()
        return cursor.rowcount > 0


def reconcile_discovered(db_path: Path | str, devices: list[dict]) -> list[dict]:
    """Annotate discovered Rokus and refresh metadata only for devices already saved."""
    saved_by_key = {item["device_key"]: item for item in list_saved(db_path)}
    annotated: list[dict] = []
    for original in devices:
        device = dict(original)
        key = device_key(device)
        device["device_key"] = key
        existing = saved_by_key.get(key) if key else None
        device["saved"] = bool(existing)
        if existing:
            refreshed = save_device(db_path, device.get("host", existing["host"]), device)
            device.update({
                "host": refreshed["host"],
                "saved": True,
                "created_at": refreshed["created_at"],
                "updated_at": refreshed["updated_at"],
                "last_seen_at": refreshed["last_seen_at"],
            })
        annotated.append(device)
    annotated.sort(key=lambda item: (not bool(item.get("saved")), str(item.get("name", "")).lower(), str(item.get("host", ""))))
    return annotated
