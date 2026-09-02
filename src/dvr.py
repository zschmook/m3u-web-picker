from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import app_config
from database import connect as initialize_database
from guide_epg import programme_schedule
from media.ffmpeg import executable as ffmpeg_executable
from settings import load_settings


SECTION = "dvr"
COMMERCIAL_LAB_TITLE_PREFIX = "Commercial Lab · "
INTERRUPTED_ERROR = "The app restarted before this recording finished."
DEFAULTS = {
    "enabled": False,
    "host_path": "",
    "plex_path": "",
    "padding_before_seconds": 60,
    "padding_after_seconds": 120,
    "max_concurrent_recordings": 2,
    "transcode_hevc": True,
    "remove_commercials": True,
    "hevc_crf": 27,
    "hevc_bitrate_kbps": 3000,
    "hevc_preset": "fast",
}
_PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow"}
_ACTIVE_LOCK = threading.RLock()
_ACTIVE: dict[int, dict[str, Any]] = {}
_PLAYING: set[int] = set()
_CONVERTING: set[int] = set()
_TICK_LOCK = threading.Lock()
_TRANSCODE_SEMAPHORE = threading.Semaphore(1)
_ENCODER_LOCK = threading.Lock()
_ENCODER_PROBED = False
_ENCODER = "libx265"
_MAINTENANCE_LOCK = threading.Lock()
_MAINTENANCE: dict[str, Any] = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "result": {},
    "error": "",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("DVR program times must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _title_key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _dedupe_key(tvg_id: str, title: str, start_at: datetime) -> str:
    raw = f"{tvg_id.strip().casefold()}|{_title_key(title)}|{_iso(start_at)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", str(value or "")).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "Recording")[:120]


def _episode_identity(item: Any) -> tuple[str, int] | None:
    searchable = " ".join(str(item[key] or "") for key in ("title", "subtitle", "description"))
    match = re.search(r"\bS(?:eason\s*)?(\d{1,2})\s*E(?:pisode\s*)?(\d{1,3})\b", searchable, re.IGNORECASE)
    if match is None:
        return None
    season = int(match.group(1))
    episode = int(match.group(2))
    return f"S{season:02d}E{episode:02d}", season


def _processed_destination(destination_root: Path, item: Any, source: Path, *, plex_enabled: bool) -> Path:
    if not plex_enabled:
        return destination_root / source.with_suffix(".mkv").name
    show = _safe_stem(str(item["title"] or "Recording"))
    episode = _episode_identity(item)
    if episode is not None:
        code, season = episode
        parent = destination_root / show / f"Season {season:02d}"
        candidate = parent / f"{show}.{code}.mkv"
    else:
        start = str(item["start_at"] or "")[:10]
        suffix = f" - {start}" if start else ""
        parent = destination_root / show
        candidate = parent / f"{show}{suffix}.mkv"
    if not candidate.exists():
        return candidate
    return candidate.with_name(f"{candidate.stem} - {int(item['id'])}{candidate.suffix}")


def recordings_dir() -> Path:
    return load_settings().dvr_dir.expanduser().resolve()


def converted_dir() -> Path:
    return recordings_dir() / "converted"


def _normalized_host_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").rstrip("/")


def plex_dir(*, plex_path: str | None = None, host_path: str | None = None) -> Path | None:
    current = settings()
    entered = _normalized_host_path(current.get("plex_path") if plex_path is None else plex_path)
    mounted_host = _normalized_host_path(current.get("host_path") if host_path is None else host_path)
    if not entered:
        return None
    prefix = f"{mounted_host}/"
    if not mounted_host or not entered.casefold().startswith(prefix.casefold()):
        return None
    relative = entered[len(prefix):]
    if not relative:
        return None
    root = recordings_dir()
    target = (root / Path(*relative.split("/"))).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def settings() -> dict[str, Any]:
    result = {**DEFAULTS, **app_config.section(SECTION)}
    result["enabled"] = bool(result.get("enabled"))
    result["transcode_hevc"] = bool(result.get("transcode_hevc"))
    result["remove_commercials"] = bool(result.get("remove_commercials"))
    result["host_path"] = _normalized_host_path(result.get("host_path"))
    result["plex_path"] = _normalized_host_path(result.get("plex_path"))
    result["padding_before_seconds"] = max(0, min(1800, int(result.get("padding_before_seconds", 60))))
    result["padding_after_seconds"] = max(0, min(3600, int(result.get("padding_after_seconds", 120))))
    result["max_concurrent_recordings"] = max(1, min(8, int(result.get("max_concurrent_recordings", 2))))
    result["hevc_crf"] = max(18, min(35, int(result.get("hevc_crf", 27))))
    result["hevc_bitrate_kbps"] = max(
        1000, min(20000, int(result.get("hevc_bitrate_kbps", 3000)))
    )
    preset = str(result.get("hevc_preset", "fast") or "fast").strip().lower()
    result["hevc_preset"] = preset if preset in _PRESETS else "fast"
    return result


def save_settings(values: dict[str, Any]) -> dict[str, Any]:
    current = settings()
    for key in DEFAULTS:
        if key in values:
            current[key] = values[key]
    normalized = {**current}
    normalized["enabled"] = bool(normalized["enabled"])
    normalized["transcode_hevc"] = bool(normalized["transcode_hevc"])
    normalized["remove_commercials"] = bool(normalized["remove_commercials"])
    normalized["host_path"] = _normalized_host_path(normalized.get("host_path"))
    normalized["plex_path"] = _normalized_host_path(normalized.get("plex_path"))
    normalized["padding_before_seconds"] = max(0, min(1800, int(normalized["padding_before_seconds"])))
    normalized["padding_after_seconds"] = max(0, min(3600, int(normalized["padding_after_seconds"])))
    normalized["max_concurrent_recordings"] = max(1, min(8, int(normalized["max_concurrent_recordings"])))
    normalized["hevc_crf"] = max(18, min(35, int(normalized["hevc_crf"])))
    normalized["hevc_bitrate_kbps"] = max(
        1000, min(20000, int(normalized["hevc_bitrate_kbps"]))
    )
    preset = str(normalized["hevc_preset"] or "fast").strip().lower()
    if preset not in _PRESETS:
        raise ValueError("Choose a supported H.265 preset.")
    normalized["hevc_preset"] = preset
    if normalized["enabled"]:
        validation = validate_host_path(normalized["host_path"])
        if not validation.get("ok"):
            raise ValueError(str(validation.get("message") or "The DVR recording path is not available."))
        plex_validation = validate_plex_path(normalized["plex_path"], host_path=normalized["host_path"])
        if not plex_validation.get("ok"):
            raise ValueError(str(plex_validation.get("message") or "The Plex folder is not available."))
    return app_config.update_section(SECTION, normalized)


def storage_status(*, host_path: str | None = None) -> dict[str, Any]:
    target = recordings_dir()
    configured_host_path = str(os.environ.get("M3U_DVR_HOST_DIR", "") or "").strip().replace("\\", "/").rstrip("/")
    entered_host_path = (
        str(settings().get("host_path") or "")
        if host_path is None
        else str(host_path or "").strip().replace("\\", "/").rstrip("/")
    )
    try:
        target.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(target)
        writable = os.access(target, os.W_OK)
        return {
            "available": True,
            "writable": writable,
            "host_path": entered_host_path,
            "configured_host_path": configured_host_path,
            "mount_configured": bool(configured_host_path),
            "free_bytes": int(usage.free),
            "total_bytes": int(usage.total),
        }
    except OSError as exc:
        return {
            "available": False,
            "writable": False,
            "host_path": entered_host_path,
            "configured_host_path": configured_host_path,
            "mount_configured": bool(configured_host_path),
            "free_bytes": 0,
            "total_bytes": 0,
            "error": f"Recording storage is unavailable ({type(exc).__name__}).",
        }


def validate_host_path(host_path: str, *, write_probe: bool = True) -> dict[str, Any]:
    entered = str(host_path or "").strip().replace("\\", "/").rstrip("/")
    runtime = storage_status(host_path=entered)
    configured = str(runtime.get("configured_host_path") or "")
    if not entered:
        return {"ok": False, "message": "Enter the local DVR recording folder.", **runtime}
    if entered in {"", "/"} or re.fullmatch(r"[A-Za-z]:", entered):
        return {"ok": False, "message": "Choose a dedicated recording folder, not an entire drive.", **runtime}
    if not configured:
        return {
            "ok": False,
            "message": "Set M3U_DVR_DIR to this host folder and restart the container first.",
            **runtime,
        }
    if entered.casefold() != configured.casefold():
        return {
            "ok": False,
            "message": "This path does not match M3U_DVR_DIR. Update .env and restart the container with this exact folder.",
            **runtime,
        }
    if not runtime.get("available") or not runtime.get("writable"):
        return {"ok": False, "message": "The mounted DVR folder is not available and writable inside the container.", **runtime}
    if write_probe:
        probe = recordings_dir() / f".m3u-picker-dvr-write-test-{uuid.uuid4().hex}"
        try:
            probe.write_text("test", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            probe.unlink(missing_ok=True)
            return {"ok": False, "message": f"The DVR folder could not be written ({type(exc).__name__}).", **runtime}
    return {"ok": True, "message": "DVR folder is mounted and writable.", **runtime}


def validate_plex_path(
    plex_path: str,
    *,
    host_path: str | None = None,
    write_probe: bool = True,
) -> dict[str, Any]:
    entered = _normalized_host_path(plex_path)
    mounted_host = _normalized_host_path(settings().get("host_path") if host_path is None else host_path)
    if not entered:
        return {"ok": True, "message": "Converted recordings will remain in the DVR converted folder."}
    target = plex_dir(plex_path=entered, host_path=mounted_host)
    if target is None:
        return {
            "ok": False,
            "message": "For this Docker setup, the Plex folder must be inside the mounted DVR folder.",
        }
    try:
        target.mkdir(parents=True, exist_ok=True)
        if not os.access(target, os.W_OK):
            raise OSError("not writable")
        if write_probe:
            probe = target / f".m3u-picker-plex-write-test-{uuid.uuid4().hex}"
            try:
                probe.write_text("test", encoding="utf-8")
                probe.unlink()
            finally:
                probe.unlink(missing_ok=True)
    except OSError as exc:
        return {"ok": False, "message": f"The Plex folder could not be written ({type(exc).__name__})."}
    return {"ok": True, "message": "Plex folder is available and writable."}


def require_ready() -> dict[str, Any]:
    current = settings()
    if not current["enabled"]:
        raise ValueError("Enable DVR in Settings before scheduling a recording.")
    validation = validate_host_path(str(current.get("host_path") or ""), write_probe=False)
    if not validation.get("ok"):
        raise ValueError(str(validation.get("message") or "The DVR recording folder is not ready."))
    return current


def connect_database(db_path: Path | str) -> sqlite3.Connection:
    """Open the initialized DVR database without rerunning global migrations."""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str) -> None:
    initialize_database(db_path).close()


def _row_dict(row) -> dict[str, Any]:
    payload = dict(row)
    payload["playback_url"] = (
        f"/api/dvr/recordings/{payload['id']}/play"
        if payload.get("status") == "completed" and payload.get("output_name")
        else ""
    )
    return payload


def list_recordings(db_path: Path | str, *, limit: int = 250) -> list[dict[str, Any]]:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM dvr_recordings
            ORDER BY
                CASE status
                    WHEN 'recording' THEN 0 WHEN 'processing' THEN 1
                    WHEN 'scheduled' THEN 2 WHEN 'failed' THEN 3
                    WHEN 'completed' THEN 4 ELSE 5
                END,
                CASE WHEN status = 'scheduled' THEN start_at END ASC,
                COALESCE(completed_at, updated_at) DESC
            LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        ).fetchall()
        return [_row_dict(row) for row in rows]
    finally:
        conn.close()


def list_series_rules(db_path: Path | str) -> list[dict[str, Any]]:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM dvr_series_rules ORDER BY title COLLATE NOCASE, channel_name COLLATE NOCASE"
        ).fetchall()]
    finally:
        conn.close()


def has_enabled_series_rules(db_path: Path | str) -> bool:
    conn = connect_database(db_path)
    try:
        return conn.execute(
            "SELECT 1 FROM dvr_series_rules WHERE enabled = 1 LIMIT 1"
        ).fetchone() is not None
    finally:
        conn.close()


def state(db_path: Path | str) -> dict[str, Any]:
    items = [
        item for item in list_recordings(db_path)
        if not (
            str(item.get("title") or "").startswith(COMMERCIAL_LAB_TITLE_PREFIX)
            and str(item.get("status") or "") in {"analyzed", "discarded"}
        )
    ]
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    with _ACTIVE_LOCK:
        active_ids = sorted(_ACTIVE)
    return {
        "settings": settings(),
        "storage": storage_status(),
        "counts": counts,
        "active_ids": active_ids,
        "maintenance": maintenance_state(),
        "recordings": items,
        "series_rules": list_series_rules(db_path),
    }


def maintenance_state() -> dict[str, Any]:
    with _MAINTENANCE_LOCK:
        return {
            "running": bool(_MAINTENANCE["running"]),
            "started_at": str(_MAINTENANCE["started_at"] or ""),
            "finished_at": str(_MAINTENANCE["finished_at"] or ""),
            "result": dict(_MAINTENANCE["result"] or {}),
            "error": str(_MAINTENANCE["error"] or ""),
        }


def _manual_maintenance_worker(db_path: Path | str) -> None:
    try:
        result = nightly_maintenance(db_path)
        error = str(result.get("error") or "")
    except Exception as exc:
        result = {}
        error = f"DVR processing failed ({type(exc).__name__})."
    with _MAINTENANCE_LOCK:
        _MAINTENANCE.update({
            "running": False,
            "finished_at": _iso(_now()),
            "result": result,
            "error": error,
        })


def start_manual_maintenance(db_path: Path | str) -> bool:
    with _MAINTENANCE_LOCK:
        if _MAINTENANCE["running"]:
            return False
        _MAINTENANCE.update({
            "running": True,
            "started_at": _iso(_now()),
            "finished_at": "",
            "result": {},
            "error": "",
        })
    threading.Thread(
        target=_manual_maintenance_worker,
        args=(db_path,),
        daemon=True,
        name="dvr-manual-maintenance",
    ).start()
    return True


def schedule_recording(
    db_path: Path | str,
    *,
    play_url: str,
    tvg_id: str,
    channel_name: str,
    title: str,
    subtitle: str = "",
    description: str = "",
    start_at: Any,
    stop_at: Any,
    rule_id: int | None = None,
) -> dict[str, Any]:
    start = _parse_datetime(start_at)
    stop = _parse_datetime(stop_at)
    if stop <= start:
        raise ValueError("The program end time must be after its start time.")
    if stop <= _now():
        raise ValueError("That program has already ended.")
    title_value = str(title or "").strip()
    tvg_value = str(tvg_id or "").strip()
    play_value = str(play_url or "").strip()
    if not title_value or not tvg_value or not play_value:
        raise ValueError("The program is missing its title, channel identity, or play target.")
    now_text = _iso(_now())
    key = _dedupe_key(tvg_value, title_value, start)
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO dvr_recordings (
                rule_id, dedupe_key, play_url, tvg_id, channel_name, title,
                subtitle, description, start_at, stop_at, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?)
            """,
            (
                rule_id, key, play_value, tvg_value, str(channel_name or "").strip(),
                title_value, str(subtitle or "").strip(), str(description or "").strip(),
                _iso(start), _iso(stop), now_text, now_text,
            ),
        )
        if rule_id is not None:
            conn.execute(
                "UPDATE dvr_recordings SET rule_id = COALESCE(rule_id, ?) WHERE dedupe_key = ?",
                (rule_id, key),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM dvr_recordings WHERE dedupe_key = ?", (key,)).fetchone()
        return _row_dict(row)
    finally:
        conn.close()


def create_series_rule(
    db_path: Path | str,
    *,
    title: str,
    tvg_id: str,
    channel_name: str,
) -> dict[str, Any]:
    title_value = str(title or "").strip()
    tvg_value = str(tvg_id or "").strip()
    if not title_value or not tvg_value:
        raise ValueError("A series rule needs a show title and channel identity.")
    now_text = _iso(_now())
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """
            INSERT INTO dvr_series_rules (title, title_key, tvg_id, channel_name, enabled, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(title_key, tvg_id) DO UPDATE SET
                title = excluded.title,
                channel_name = excluded.channel_name,
                enabled = 1
            """,
            (title_value, _title_key(title_value), tvg_value, str(channel_name or "").strip(), now_text),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM dvr_series_rules WHERE title_key = ? AND tvg_id = ?",
            (_title_key(title_value), tvg_value),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def sync_series_rules(
    db_path: Path | str,
    *,
    channels: list[dict],
    epg_path: Path,
    timezone_name: str,
) -> int:
    rules = [rule for rule in list_series_rules(db_path) if rule.get("enabled")]
    if not rules or not settings()["enabled"]:
        return 0
    by_tvg = {
        str(channel.get("tvg_id") or "").strip(): channel
        for channel in channels
        if str(channel.get("tvg_id") or "").strip()
    }
    wanted_ids = {str(rule["tvg_id"]) for rule in rules}
    schedules = programme_schedule(epg_path, timezone_name=timezone_name, channel_ids=wanted_ids)
    conn = connect_database(db_path)
    try:
        known_keys = {str(row[0]) for row in conn.execute("SELECT dedupe_key FROM dvr_recordings").fetchall()}
    finally:
        conn.close()
    created = 0
    for rule in rules:
        channel = by_tvg.get(str(rule["tvg_id"]))
        if not channel:
            continue
        for programme in schedules.get(str(rule["tvg_id"]), []):
            if _title_key(programme.get("title")) != str(rule["title_key"]):
                continue
            try:
                programme_start = _parse_datetime(programme.get("start"))
                key = _dedupe_key(str(rule["tvg_id"]), str(programme.get("title") or rule["title"]), programme_start)
                if key in known_keys:
                    continue
                schedule_recording(
                    db_path,
                    rule_id=int(rule["id"]),
                    play_url=str(channel.get("play_url") or ""),
                    tvg_id=str(rule["tvg_id"]),
                    channel_name=str(channel.get("name") or rule.get("channel_name") or ""),
                    title=str(programme.get("title") or rule["title"]),
                    subtitle=str(programme.get("subtitle") or ""),
                    description=str(programme.get("description") or ""),
                    start_at=programme_start,
                    stop_at=programme.get("stop"),
                )
                known_keys.add(key)
                created += 1
            except ValueError:
                continue
    return created


def remove_series_rule(db_path: Path | str, rule_id: int) -> bool:
    conn = connect_database(db_path)
    try:
        conn.execute(
            "DELETE FROM dvr_recordings WHERE rule_id = ? AND status = 'scheduled'",
            (int(rule_id),),
        )
        cursor = conn.execute("DELETE FROM dvr_series_rules WHERE id = ?", (int(rule_id),))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def _resolve_target(play_url: str) -> str:
    import core
    import sports

    value = str(play_url or "").split("?", 1)[0].strip()
    manual = re.fullmatch(r"/guide/play/manual/([^/]+)", value)
    if manual:
        return core.manual_stream_target(manual.group(1))
    generated = re.fullmatch(r"/guide/play/sports/(\d+)", value)
    if generated:
        return sports.generated_stream_target(core.DB_PATH, int(generated.group(1)))
    return ""


def _update_recording(db_path: Path | str, recording_id: int, **values: Any) -> None:
    if not values:
        return
    values["updated_at"] = _iso(_now())
    columns = ", ".join(f"{key} = ?" for key in values)
    conn = connect_database(db_path)
    try:
        conn.execute(
            f"UPDATE dvr_recordings SET {columns} WHERE id = ?",
            (*values.values(), int(recording_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _recording_status(db_path: Path | str, recording_id: int) -> str:
    conn = connect_database(db_path)
    try:
        row = conn.execute("SELECT status FROM dvr_recordings WHERE id = ?", (int(recording_id),)).fetchone()
        return str(row[0]) if row else ""
    finally:
        conn.close()


def begin_playback(recording_id: int) -> bool:
    with _ACTIVE_LOCK:
        value = int(recording_id)
        if value in _CONVERTING:
            return False
        _PLAYING.add(value)
        return True


def end_playback(recording_id: int) -> None:
    with _ACTIVE_LOCK:
        _PLAYING.discard(int(recording_id))


def _capture_command(target: str, destination: Path, duration_seconds: int) -> list[str]:
    return [
        ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
        "-fflags", "+genpts", "-i", target,
        "-map", "0:v:0?", "-map", "0:a?", "-map", "0:s?",
        "-t", str(max(1, duration_seconds)), "-c", "copy",
        "-f", "mpegts", str(destination),
    ]


def _preferred_hevc_encoder() -> str:
    global _ENCODER_PROBED, _ENCODER
    requested = str(os.environ.get("M3U_DVR_HEVC_ENCODER", "auto") or "auto").strip().lower()
    if requested in {"cpu", "libx265"}:
        return "libx265"
    with _ENCODER_LOCK:
        if _ENCODER_PROBED:
            return _ENCODER
        _ENCODER_PROBED = True
        try:
            result = subprocess.run(
                [
                    ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=size=256x256:rate=1", "-frames:v", "1",
                    "-c:v", "hevc_nvenc", "-preset", "p4", "-f", "null", "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            if result.returncode == 0:
                _ENCODER = "hevc_nvenc"
        except (OSError, subprocess.SubprocessError):
            _ENCODER = "libx265"
        return _ENCODER


def _video_encode_args(current: dict[str, Any], *, force_cpu: bool = False) -> list[str]:
    encoder = "libx265" if force_cpu else _preferred_hevc_encoder()
    if encoder == "hevc_nvenc":
        bitrate_kbps = int(current["hevc_bitrate_kbps"])
        return [
            "-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq",
            "-rc", "vbr", "-cq", str(current["hevc_crf"]),
            "-b:v", f"{bitrate_kbps}k",
            "-maxrate", f"{round(bitrate_kbps * 1.5)}k",
            "-bufsize", f"{bitrate_kbps * 2}k",
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx265", "-preset", str(current["hevc_preset"]),
        "-crf", str(current["hevc_crf"]), "-pix_fmt", "yuv420p",
    ]


def _uses_nvenc(command: list[str]) -> bool:
    return "hevc_nvenc" in command


def _transcode_command(
    source: Path,
    destination: Path,
    current: dict[str, Any],
    *,
    force_cpu: bool = False,
) -> list[str]:
    return [
        ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(source),
        "-map", "0:v:0?", "-map", "0:a?", "-map", "0:s?",
        *_video_encode_args(current, force_cpu=force_cpu),
        "-vf", "bwdif=mode=send_frame:parity=auto:deint=interlaced",
        "-c:a", "aac", "-b:a", "160k", "-c:s", "copy",
        "-map_metadata", "0", "-f", "matroska", str(destination),
    ]


def _media_details(path: Path) -> tuple[float, int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe is unavailable")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type",
            "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("FFprobe could not inspect the recording")
    try:
        payload = json.loads(result.stdout or "{}")
        duration = float(payload.get("format", {}).get("duration", 0))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("FFprobe returned an invalid recording duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError("The recording duration is unavailable")
    audio_streams = sum(
        1 for stream in payload.get("streams", [])
        if str(stream.get("codec_type") or "") == "audio"
    )
    return duration, audio_streams


def _parse_comskip_edl(path: Path, duration: float) -> list[tuple[float, float]]:
    cuts: list[tuple[float, float]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = raw_line.split()
        if len(fields) < 2:
            continue
        try:
            start = max(0.0, float(fields[0]))
            stop = min(float(duration), float(fields[1]))
            action = int(float(fields[2])) if len(fields) > 2 else 0
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(stop) or action != 0 or stop - start < 2.0:
            continue
        cuts.append((start, stop))

    merged: list[tuple[float, float]] = []
    for start, stop in sorted(cuts):
        if merged and start <= merged[-1][1] + 0.25:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((start, stop))
    return merged


def _kept_intervals(duration: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    kept: list[tuple[float, float]] = []
    cursor = 0.0
    for start, stop in cuts:
        if start - cursor >= 0.25:
            kept.append((cursor, start))
        cursor = max(cursor, stop)
    if duration - cursor >= 0.25:
        kept.append((cursor, duration))
    return kept


def _validated_commercial_plan(edl_path: Path, duration: float) -> list[tuple[float, float]]:
    cuts = _parse_comskip_edl(edl_path, duration)
    if not cuts:
        return []
    removed = sum(stop - start for start, stop in cuts)
    # A detector result that removes nearly half a programme is too risky to
    # apply automatically. The uncut conversion remains the safe fallback.
    if removed > duration * 0.45:
        raise RuntimeError("Comskip marked an implausibly large part of the recording")
    kept = _kept_intervals(duration, cuts)
    if not kept or sum(stop - start for start, stop in kept) < max(60.0, duration * 0.50):
        raise RuntimeError("Comskip did not leave enough program content")
    return cuts


def _comskip_command(source: Path) -> list[str]:
    executable = str(os.environ.get("M3U_COMSKIP", "") or "").strip() or shutil.which("comskip")
    if not executable:
        raise RuntimeError("Comskip is unavailable")
    ini_path = Path(__file__).resolve().parents[1] / "resources" / "comskip.ini"
    if not ini_path.is_file():
        raise RuntimeError("Comskip configuration is unavailable")
    return [executable, f"--ini={ini_path}", str(source)]


def _detect_commercials(source: Path, duration: float, log_handle) -> list[tuple[float, float]]:
    edl_path = source.with_suffix(".edl")
    edl_path.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            _comskip_command(source),
            cwd=source.parent,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            timeout=max(1800, min(21600, int(duration * 4))),
            check=False,
        )
        if result.returncode != 0 or not edl_path.is_file():
            raise RuntimeError(f"Comskip exited with code {result.returncode}")
        return _validated_commercial_plan(edl_path, duration)
    finally:
        edl_path.unlink(missing_ok=True)


def _commercial_transcode_command(
    source: Path,
    destination: Path,
    current: dict[str, Any],
    *,
    duration: float,
    audio_streams: int,
    cuts: list[tuple[float, float]],
    force_cpu: bool = False,
) -> list[str]:
    kept = _kept_intervals(duration, cuts)
    filters: list[str] = []
    for index, (start, stop) in enumerate(kept):
        filters.append(
            f"[0:v:0]trim=start={start:.3f}:end={stop:.3f},setpts=PTS-STARTPTS[v{index}]"
        )
    video_inputs = "".join(f"[v{index}]" for index in range(len(kept)))
    filters.append(
        f"{video_inputs}concat=n={len(kept)}:v=1:a=0,"
        "bwdif=mode=send_frame:parity=auto:deint=interlaced[vout]"
    )
    for audio_index in range(audio_streams):
        for index, (start, stop) in enumerate(kept):
            filters.append(
                f"[0:a:{audio_index}]atrim=start={start:.3f}:end={stop:.3f},"
                f"asetpts=PTS-STARTPTS[a{audio_index}_{index}]"
            )
        audio_inputs = "".join(f"[a{audio_index}_{index}]" for index in range(len(kept)))
        filters.append(
            f"{audio_inputs}concat=n={len(kept)}:v=0:a=1[aout{audio_index}]"
        )

    command = [
        ffmpeg_executable(), "-nostdin", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(source), "-filter_complex", ";".join(filters), "-map", "[vout]",
    ]
    for audio_index in range(audio_streams):
        command.extend(["-map", f"[aout{audio_index}]"])
    command.extend([
        *_video_encode_args(current, force_cpu=force_cpu),
        "-c:a", "aac", "-b:a", "160k", "-map_metadata", "0",
        "-f", "matroska", str(destination),
    ])
    return command


def _valid_media(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return True
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _finish_capture(
    db_path: Path | str,
    recording_id: int,
    process: subprocess.Popen,
    capture_path: Path,
    final_path: Path,
    log_path: Path,
    log_handle,
) -> None:
    return_code = process.wait()
    try:
        log_handle.close()
    except OSError:
        pass
    with _ACTIVE_LOCK:
        _ACTIVE.pop(recording_id, None)
    if _recording_status(db_path, recording_id) == "cancelled":
        capture_path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)
        return
    if return_code != 0 or not _valid_media(capture_path):
        _update_recording(
            db_path,
            recording_id,
            status="failed",
            error=f"Stream capture failed (FFmpeg exit {return_code}). The partial capture was kept.",
        )
        return

    ts_path = final_path.with_suffix(".ts")
    os.replace(capture_path, ts_path)
    conversion_status = "pending" if settings()["transcode_hevc"] else ""
    _update_recording(
        db_path,
        recording_id,
        status="completed",
        output_name=ts_path.name,
        completed_at=_iso(_now()),
        error="",
        conversion_status=conversion_status,
        conversion_error="",
        commercial_status="pending" if conversion_status and settings()["remove_commercials"] else "",
        commercial_error="",
        commercial_count=0,
        commercial_seconds=0,
    )
    try:
        log_path.unlink(missing_ok=True)
    except OSError:
        pass


def _idle_recording_file(path: Path) -> bool:
    try:
        first = path.stat()
        with path.open("rb") as handle:
            handle.seek(max(0, first.st_size - 4096))
            handle.read(4096)
        time.sleep(0.2)
        second = path.stat()
    except OSError:
        return False
    return first.st_size == second.st_size and first.st_mtime_ns == second.st_mtime_ns


def nightly_maintenance(db_path: Path | str) -> dict[str, Any]:
    current = settings()
    summary = {"checked": 0, "converted": 0, "moved": 0, "commercials_removed": 0, "skipped": 0, "failed": 0}
    if not current["enabled"] or not current["transcode_hevc"]:
        return summary
    validation = validate_host_path(str(current.get("host_path") or ""), write_probe=False)
    if not validation.get("ok"):
        summary["error"] = str(validation.get("message") or "DVR storage is unavailable.")
        return summary

    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, output_name, title, subtitle, description, start_at FROM dvr_recordings
            WHERE status = 'completed'
              AND lower(output_name) LIKE '%.ts'
              AND conversion_status IN ('', 'pending')
              AND title NOT LIKE ?
            ORDER BY completed_at ASC, id ASC
            """,
            (f"{COMMERCIAL_LAB_TITLE_PREFIX}%",),
        ).fetchall()
    finally:
        conn.close()

    root = recordings_dir()
    destination_root = plex_dir() or converted_dir()
    destination_root.mkdir(parents=True, exist_ok=True)
    for row in rows:
        recording_id = int(row["id"])
        summary["checked"] += 1
        with _ACTIVE_LOCK:
            active = recording_id in _ACTIVE or recording_id in _PLAYING
        source = (root / str(row["output_name"] or "")).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            active = True
        if active or source.suffix.lower() != ".ts" or not _idle_recording_file(source) or not _valid_media(source):
            summary["skipped"] += 1
            continue

        with _ACTIVE_LOCK:
            if recording_id in _ACTIVE or recording_id in _PLAYING or recording_id in _CONVERTING:
                summary["skipped"] += 1
                continue
            _CONVERTING.add(recording_id)

        destination = _processed_destination(destination_root, row, source, plex_enabled=bool(current["plex_path"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        working = destination.parent / f".{destination.stem}.working.mkv"
        conversion_log = root / f".{source.stem}.convert.log"
        try:
            _update_recording(
                db_path,
                recording_id,
                conversion_status="processing",
                conversion_error="",
                commercial_status="processing" if current["remove_commercials"] else "disabled",
                commercial_error="",
                commercial_count=0,
                commercial_seconds=0,
            )
            try:
                with _TRANSCODE_SEMAPHORE:
                    with conversion_log.open("ab") as log_handle:
                        cuts: list[tuple[float, float]] = []
                        duration = 0.0
                        audio_streams = 0
                        commercial_error = ""
                        force_cpu = False
                        if current["remove_commercials"]:
                            try:
                                duration, audio_streams = _media_details(source)
                                cuts = _detect_commercials(source, duration, log_handle)
                            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                                commercial_error = (
                                    f"Commercial detection failed ({type(exc).__name__}); "
                                    "the recording was converted without cuts."
                                )

                        if cuts:
                            command = _commercial_transcode_command(
                                source,
                                working,
                                current,
                                duration=duration,
                                audio_streams=audio_streams,
                                cuts=cuts,
                                force_cpu=force_cpu,
                            )
                        else:
                            command = _transcode_command(source, working, current, force_cpu=force_cpu)
                        result = subprocess.run(
                            command,
                            stdout=subprocess.DEVNULL,
                            stderr=log_handle,
                            check=False,
                        )
                        if _uses_nvenc(command) and (result.returncode != 0 or not _valid_media(working)):
                            working.unlink(missing_ok=True)
                            force_cpu = True
                            if cuts:
                                command = _commercial_transcode_command(
                                    source,
                                    working,
                                    current,
                                    duration=duration,
                                    audio_streams=audio_streams,
                                    cuts=cuts,
                                    force_cpu=True,
                                )
                            else:
                                command = _transcode_command(source, working, current, force_cpu=True)
                            result = subprocess.run(
                                command,
                                stdout=subprocess.DEVNULL,
                                stderr=log_handle,
                                check=False,
                            )
                        if cuts and (result.returncode != 0 or not _valid_media(working)):
                            working.unlink(missing_ok=True)
                            commercial_error = (
                                "Commercial removal failed during FFmpeg processing; "
                                "the recording was converted without cuts."
                            )
                            cuts = []
                            result = subprocess.run(
                                _transcode_command(source, working, current, force_cpu=force_cpu),
                                stdout=subprocess.DEVNULL,
                                stderr=log_handle,
                                check=False,
                            )
                if result.returncode != 0 or not _valid_media(working):
                    raise RuntimeError(f"FFmpeg exited with code {result.returncode}")
                os.replace(working, destination)
                removed_seconds = sum(stop - start for start, stop in cuts)
                commercial_status = (
                    "removed" if cuts else
                    "failed" if commercial_error else
                    "none" if current["remove_commercials"] else
                    "disabled"
                )
                _update_recording(
                    db_path,
                    recording_id,
                    output_name=destination.relative_to(root).as_posix(),
                    conversion_status="completed",
                    conversion_error="",
                    commercial_status=commercial_status,
                    commercial_error=commercial_error,
                    commercial_count=len(cuts),
                    commercial_seconds=round(removed_seconds, 3),
                )
                try:
                    source.unlink(missing_ok=True)
                except OSError:
                    _update_recording(
                        db_path,
                        recording_id,
                        conversion_error="H.265 conversion finished, but the original .ts could not be removed.",
                    )
                try:
                    conversion_log.unlink(missing_ok=True)
                except OSError:
                    pass
                summary["converted"] += 1
                if current["plex_path"]:
                    summary["moved"] += 1
                if cuts:
                    summary["commercials_removed"] += len(cuts)
            except (OSError, RuntimeError) as exc:
                try:
                    working.unlink(missing_ok=True)
                except OSError:
                    pass
                _update_recording(
                    db_path,
                    recording_id,
                    conversion_status="failed",
                    conversion_error=f"H.265 conversion failed ({type(exc).__name__}); the original .ts was kept.",
                    commercial_status="failed" if current["remove_commercials"] else "disabled",
                )
                summary["failed"] += 1
        finally:
            with _ACTIVE_LOCK:
                _CONVERTING.discard(recording_id)
    return summary


def _start_recording(db_path: Path | str, item: dict[str, Any], now: datetime) -> None:
    target = _resolve_target(str(item.get("play_url") or ""))
    if not target:
        _update_recording(db_path, int(item["id"]), status="failed", error="The curated channel is no longer available.")
        return
    current = settings()
    stop = _parse_datetime(item["stop_at"])
    padding_after = (
        0
        if str(item.get("title") or "").startswith(COMMERCIAL_LAB_TITLE_PREFIX)
        else int(current["padding_after_seconds"])
    )
    duration = int((stop - now).total_seconds()) + padding_after
    if duration <= 0:
        _update_recording(db_path, int(item["id"]), status="missed", error="The recording window ended before capture could start.")
        return
    root = recordings_dir()
    root.mkdir(parents=True, exist_ok=True)
    start = _parse_datetime(item["start_at"])
    stamp = start.strftime("%Y-%m-%d %H-%M")
    stem = f"{_safe_stem(str(item['title']))} - {stamp} - {int(item['id'])}"
    capture = root / f".{stem}.capture.ts"
    final = root / f"{stem}.mkv"
    log_path = root / f".{stem}.ffmpeg.log"
    log_handle = log_path.open("ab")
    try:
        process = subprocess.Popen(
            _capture_command(target, capture, duration),
            stdout=subprocess.DEVNULL,
            stderr=log_handle,
        )
    except (OSError, RuntimeError) as exc:
        log_handle.close()
        _update_recording(db_path, int(item["id"]), status="failed", error=f"Could not start FFmpeg ({type(exc).__name__}).")
        return
    recording_id = int(item["id"])
    with _ACTIVE_LOCK:
        _ACTIVE[recording_id] = {"process": process, "capture": capture, "final": final}
    _update_recording(
        db_path,
        recording_id,
        status="recording",
        output_name=final.name,
        started_at=_iso(now),
        error="",
    )
    threading.Thread(
        target=_finish_capture,
        args=(db_path, recording_id, process, capture, final, log_path, log_handle),
        daemon=True,
        name=f"dvr-recording-{recording_id}",
    ).start()


def _tick(db_path: Path | str) -> None:
    current = settings()
    if not current["enabled"]:
        return
    if not validate_host_path(str(current.get("host_path") or ""), write_probe=False).get("ok"):
        return
    now = _now()
    padding_before = int(current["padding_before_seconds"])
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM dvr_recordings WHERE status = 'scheduled' ORDER BY start_at ASC"
        ).fetchall()
    finally:
        conn.close()
    with _ACTIVE_LOCK:
        capacity = max(0, int(current["max_concurrent_recordings"]) - len(_ACTIVE))
    for row in rows:
        item = dict(row)
        start = _parse_datetime(item["start_at"])
        stop = _parse_datetime(item["stop_at"])
        if stop.timestamp() + int(current["padding_after_seconds"]) <= now.timestamp():
            _update_recording(db_path, int(item["id"]), status="missed", error="The recording window was missed.")
            continue
        if start.timestamp() - padding_before > now.timestamp():
            continue
        if capacity <= 0:
            continue
        _start_recording(db_path, item, now)
        capacity -= 1


def tick(db_path: Path | str) -> None:
    with _TICK_LOCK:
        _tick(db_path)


def cancel_recording(db_path: Path | str, recording_id: int) -> bool:
    conn = connect_database(db_path)
    try:
        cursor = conn.execute(
            "UPDATE dvr_recordings SET status = 'cancelled', updated_at = ? WHERE id = ? AND status IN ('scheduled', 'recording', 'processing')",
            (_iso(_now()), int(recording_id)),
        )
        conn.commit()
        cancelled = cursor.rowcount > 0
    finally:
        conn.close()
    if cancelled:
        with _ACTIVE_LOCK:
            runtime = _ACTIVE.get(int(recording_id))
        if runtime:
            try:
                runtime["process"].terminate()
            except OSError:
                pass
    return cancelled


def recording_file(db_path: Path | str, recording_id: int) -> Path | None:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT status, output_name FROM dvr_recordings WHERE id = ?", (int(recording_id),)).fetchone()
    finally:
        conn.close()
    if not row or row["status"] != "completed" or not row["output_name"]:
        return None
    root = recordings_dir()
    candidate = (root / str(row["output_name"])).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _remove_recording_artifacts(output_name: str) -> bool:
    if not output_name:
        return True
    root = recordings_dir()
    candidate = (root / str(output_name)).resolve()
    try:
        candidate.relative_to(root)
        stem = candidate.stem
        for artifact in (
            candidate,
            root / f"{stem}.ts",
            root / f".{stem}.capture.ts",
            root / f".{stem}.ffmpeg.log",
            root / f".{stem}.convert.log",
            root / f"{stem}.edl",
            converted_dir() / f"{stem}.mkv",
            converted_dir() / f".{stem}.working.mkv",
        ):
            artifact.unlink(missing_ok=True)
    except (ValueError, OSError):
        return False
    return True


def delete_recording(db_path: Path | str, recording_id: int) -> bool:
    with _ACTIVE_LOCK:
        if int(recording_id) in _ACTIVE or int(recording_id) in _CONVERTING:
            raise ValueError("The recording is still active. Try deleting it again in a moment.")
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT status, output_name FROM dvr_recordings WHERE id = ?", (int(recording_id),)).fetchone()
        if not row:
            return False
        if str(row["status"]) in {"scheduled", "recording", "processing"}:
            raise ValueError("Cancel this recording before deleting it.")
        output_name = str(row["output_name"] or "")
        _remove_recording_artifacts(output_name)
        conn.execute("DELETE FROM dvr_recordings WHERE id = ?", (int(recording_id),))
        conn.commit()
        return True
    finally:
        conn.close()


def _discard_interrupted_lab_rows(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> int:
    discarded = 0
    now = _iso(_now())
    for row in rows:
        if _remove_recording_artifacts(str(row["output_name"] or "")):
            conn.execute(
                """
                UPDATE dvr_recordings
                SET status = 'discarded', output_name = '',
                    error = 'Interrupted commercial-learning sample discarded after restart.',
                    conversion_status = '', commercial_status = 'excluded', updated_at = ?
                WHERE id = ?
                """,
                (now, int(row["id"])),
            )
            discarded += 1
        else:
            conn.execute(
                """
                UPDATE dvr_recordings
                SET status = 'failed',
                    error = 'Unable to remove the interrupted commercial-learning sample.',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, int(row["id"])),
            )
    return discarded


def discard_interrupted_lab_failures(db_path: Path | str) -> int:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, output_name FROM dvr_recordings
            WHERE title LIKE ? AND status = 'failed' AND error = ?
            """,
            (f"{COMMERCIAL_LAB_TITLE_PREFIX}%", INTERRUPTED_ERROR),
        ).fetchall()
        discarded = _discard_interrupted_lab_rows(conn, rows)
        conn.commit()
        return discarded
    finally:
        conn.close()


def recover_interrupted(db_path: Path | str) -> int:
    conn = connect_database(db_path)
    conn.row_factory = sqlite3.Row
    try:
        interrupted_lab = conn.execute(
            """
            SELECT id, output_name FROM dvr_recordings
            WHERE title LIKE ? AND (
                status IN ('recording', 'processing')
                OR (status = 'failed' AND error = ?)
            )
            """,
            (f"{COMMERCIAL_LAB_TITLE_PREFIX}%", INTERRUPTED_ERROR),
        ).fetchall()
        discarded_lab = _discard_interrupted_lab_rows(conn, interrupted_lab)
        cursor = conn.execute(
            """
            UPDATE dvr_recordings
            SET status = 'failed', error = ?, updated_at = ?
            WHERE status IN ('recording', 'processing') AND title NOT LIKE ?
            """,
            (INTERRUPTED_ERROR, _iso(_now()), f"{COMMERCIAL_LAB_TITLE_PREFIX}%"),
        )
        conversion_cursor = conn.execute(
            """
            UPDATE dvr_recordings
            SET conversion_status = 'pending',
                conversion_error = 'The app restarted during conversion; it will retry on the next nightly update.',
                updated_at = ?
            WHERE status = 'completed' AND conversion_status = 'processing'
            """,
            (_iso(_now()),),
        )
        conn.execute(
            """
            UPDATE dvr_recordings
            SET commercial_status = 'pending',
                commercial_error = 'The app restarted during commercial detection; it will retry on the next nightly update.',
                updated_at = ?
            WHERE status = 'completed' AND commercial_status = 'processing'
            """,
            (_iso(_now()),),
        )
        conn.commit()
        return discarded_lab + cursor.rowcount + conversion_cursor.rowcount
    finally:
        conn.close()
