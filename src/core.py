#!/usr/bin/env python3
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Iterable, List

import sports
import app_config
import dvr
import commercial_lab_rotation
from backup import create_database_backup
from database import connect as connect_database
from settings import SETTINGS
from runtime_state import RUNTIME_STATE


APP_DIR = Path(__file__).resolve().parent
# Runtime state can live outside the source tree. Docker Compose points this at
# a persistent volume so rebuilding the container does not erase the database,
# cached playlist, generated guide, or EPG source configuration.
DATA_DIR = SETTINGS.data_dir
DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
EPG_DIR = DATA_DIR / "epg"
EPG_DIR.mkdir(parents=True, exist_ok=True)
PROVIDER_DIR = DATA_DIR / "providers"
PROVIDER_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_EPG_DIR = DATA_DIR / "public_epg"
PUBLIC_EPG_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "m3u_picker.db"
CONFIG_PATH = app_config.CONFIG_PATH
MASTER_CACHE_PATH = DATA_DIR / "master_playlist_cache.m3u"
EPG_CACHE_PATH = DATA_DIR / "epg_cache.xml"
SPORTS_EPG_PATH = EXPORT_DIR / "sports.xml"
COMBINED_EPG_PATH = EXPORT_DIR / "epg.xml"

PLAYLIST_NAME = "channels.m3u"
PLAYLIST_PATH = EXPORT_DIR / PLAYLIST_NAME
PORT = SETTINGS.port
DEV_PORT = SETTINGS.dev_port

SCHEDULE_HOUR = SETTINGS.schedule_hour
SCHEDULE_MINUTE = SETTINGS.schedule_minute
MAX_PROVIDER_CHANNELS = SETTINGS.max_provider_channels
PROVIDER_CHANNEL_WARNING = SETTINGS.provider_channel_warning
MAX_PROVIDER_PLAYLIST_BYTES = SETTINGS.max_provider_playlist_bytes
MAX_PROVIDER_JSON_BYTES = SETTINGS.max_provider_json_bytes

channels: List[dict] = []
selected_ids: set[int] = set()
last_source_url = ""
last_refresh: str | None = None
source_mode = ""
epg_sources: list[dict] = []
provider_sources: list[dict] = []

# One application-wide daily update clock.  It intentionally lives outside
# Sports Automation so provider/EPG refreshes still happen when sports is off.
master_auto_update = True
master_refresh_time = "03:00"
last_master_update: str | None = None
last_master_duration_seconds: float | None = None
last_master_trigger: str | None = None
master_update_runtime = RUNTIME_STATE.master_update

# IPTV-EPG country feeds are optional system-wide fallback/enrichment sources.
# They apply to every selected manual channel in Combined XMLTV and are also
# available to Sports Automation for corroboration. Fresh installs enable the
# U.S. guide only; all URLs are owned internally and use the provider's gzip
# form to avoid downloading hundreds of megabytes of XML.
PUBLIC_EPG_REGISTRY = [
    ("AL", "Albania"), ("AR", "Argentina"), ("AM", "Armenia"),
    ("AU", "Australia"), ("AT", "Austria"), ("BS", "Bahamas"),
    ("BY", "Belarus"), ("BE", "Belgium"), ("BO", "Bolivia"),
    ("BA", "Bosnia & Herzegovina"), ("BR", "Brazil"), ("BG", "Bulgaria"),
    ("CA", "Canada"), ("CL", "Chile"), ("CO", "Colombia"),
    ("CR", "Costa Rica"), ("HR", "Croatia"), ("CW", "Curacao"),
    ("CZ", "Czech Republic"), ("DK", "Denmark"), ("DO", "Dominican Republic"),
    ("EG", "Egypt"), ("SV", "El Salvador"), ("FI", "Finland"),
    ("FR", "France"), ("GE", "Georgia"), ("DE", "Germany"),
    ("GH", "Ghana"), ("GR", "Greece"), ("GT", "Guatemala"),
    ("HN", "Honduras"), ("HK", "Hong Kong"), ("HU", "Hungary"),
    ("IS", "Iceland"), ("IN", "India"), ("ID", "Indonesia"),
    ("IL", "Israel"), ("IT", "Italy"), ("JM", "Jamaica"),
    ("LV", "Latvia"), ("LB", "Lebanon"), ("LT", "Lithuania"),
    ("LU", "Luxembourg"), ("MK", "Macedonia"), ("MY", "Malaysia"),
    ("MT", "Malta"), ("MX", "Mexico"), ("ME", "Montenegro"),
    ("NL", "Netherlands"), ("NZ", "New Zealand"), ("NI", "Nicaragua"),
    ("NG", "Nigeria"), ("NO", "Norway"), ("PA", "Panama"),
    ("PY", "Paraguay"), ("PE", "Peru"), ("PH", "Philippines"),
    ("PL", "Poland"), ("PT", "Portugal"), ("RO", "Romania"),
    ("RU", "Russia"), ("RS", "Serbia"), ("SG", "Singapore"),
    ("SI", "Slovenia"), ("ZA", "South Africa"), ("KR", "South Korea"),
    ("ES", "Spain"), ("SE", "Sweden"), ("CH", "Switzerland"),
    ("TW", "Taiwan"), ("TH", "Thailand"), ("TT", "Trinidad & Tobago"),
    ("TR", "Turkey"), ("UG", "Uganda"), ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"), ("GB", "United Kingdom"),
    ("US", "United States"), ("UY", "Uruguay"), ("VE", "Venezuela"),
    ("ZW", "Zimbabwe"),
]
PUBLIC_EPG_CODES = {code for code, _name in PUBLIC_EPG_REGISTRY}
public_epg_enabled_codes: set[str] = {"US"}
public_epg_state: dict[str, dict] = {}
MAX_PUBLIC_EPG_COMPRESSED_BYTES = SETTINGS.max_public_epg_compressed_bytes
PUBLIC_EPG_DOWNLOAD_TIMEOUT_SECONDS = 180
PUBLIC_EPG_SOCKET_TIMEOUT_SECONDS = 30

scheduler_started = False

# Compatibility aliases keep the public core module surface stable while the
# process-local synchronization state lives in one explicit object.
state_lock = RUNTIME_STATE.state_lock
scan_lock = RUNTIME_STATE.scan_lock
scan_cancel_event = RUNTIME_STATE.scan_cancel_event
provider_progress_lock = RUNTIME_STATE.provider_progress_lock
provider_progress = RUNTIME_STATE.provider_progress


class SportsScanError(RuntimeError):
    """Safe, user-facing failure for an explicit sports update."""


class SportsScanCancelled(SportsScanError):
    """A manual sports update was cancelled without changing published outputs."""


def _provider_progress_update(
    stage: str,
    *,
    detail: str = "",
    channel_count: int | None = None,
    active: bool = True,
    status: str = "running",
) -> None:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with provider_progress_lock:
        if active and not provider_progress.get("active"):
            provider_progress["started_at"] = now
        provider_progress.update(
            {
                "active": active,
                "stage": str(stage or "Working"),
                "detail": str(detail or ""),
                "channel_count": channel_count,
                "updated_at": now,
                "status": status,
            }
        )


def provider_progress_payload() -> dict:
    with provider_progress_lock:
        return dict(provider_progress)


def fail_provider_progress(message: str) -> None:
    _provider_progress_update(
        "Provider load failed",
        detail=redact_url_credentials(str(message or "Unknown provider error")),
        active=False,
        status="failed",
    )


def _provider_progress_complete(channel_count: int, detail: str = "") -> None:
    _provider_progress_update(
        "Provider ready",
        detail=detail,
        channel_count=channel_count,
        active=False,
        status="complete",
    )


@dataclass
class Entry:
    id: int
    name: str
    group: str
    url: str
    raw: list[str]
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    tvg_chno: str = ""
    attrs: dict[str, str] | None = None
    is_sports_generated: bool = False


def db_connect() -> sqlite3.Connection:
    conn = connect_database(DB_PATH)
    sports.init_db(DB_PATH)
    return conn


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "group"


def unique_slug(conn: sqlite3.Connection, name: str) -> str:
    base = slugify(name)
    slug = base
    index = 2
    while conn.execute("SELECT 1 FROM custom_groups WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{index}"
        index += 1
    return slug


def legacy_channel_key(channel: dict) -> str:
    """Return the pre-v21.9 manual identity used by existing databases."""
    return str(channel.get("url", "") or "").strip()


def channel_key(channel: dict) -> str:
    """Return a stable manual-channel identity that is independent of sports rows.

    Older releases keyed manual selections only by stream URL. That collapses
    distinct provider rows which happen to share a stream and makes a saved
    manual channel vulnerable to being resolved as another row after refresh.
    Manual rows now have their own namespace and include provider metadata in
    the identity. Generated sports rows are never passed through this key.
    """
    existing = str(channel.get("key", "") or "").strip()
    if existing.startswith("manual:"):
        return existing
    parts = [
        legacy_channel_key(channel),
        str(channel.get("tvg_id", "") or "").strip(),
        str(channel.get("tvg_chno", "") or "").strip(),
        str(channel.get("name", "") or "").strip(),
        str(channel.get("group", "") or "").strip(),
    ]
    if not any(parts):
        return ""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"manual:{digest}"


def parse_extinf_attributes(line: str) -> dict[str, str]:
    return {
        key: value
        for key, value in re.findall(r'([A-Za-z0-9_-]+)="([^"]*)"', line)
    }


def rewrite_extinf_attr(line: str, key: str, value: str) -> str:
    if not line.startswith("#EXTINF"):
        return line
    escaped = str(value).replace('"', "'")
    if re.search(rf'{re.escape(key)}="[^"]*"', line):
        return re.sub(rf'{re.escape(key)}="[^"]*"', f'{key}="{escaped}"', line)
    if "," in line:
        left, right = line.rsplit(",", 1)
        return f'{left} {key}="{escaped}",{right}'
    return f'{line} {key}="{escaped}"'


def apply_channel_number(channel: dict, number: int) -> list[str]:
    raw = list(channel.get("raw", []))
    if not raw:
        return raw
    raw[0] = rewrite_extinf_attr(raw[0], "tvg-chno", str(number))
    return raw


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _selection_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT key, name, group_title, url, tvg_id, sort_order
        FROM selections
        ORDER BY sort_order IS NULL, sort_order, name
        """
    ).fetchall()


def _match_saved_manual_row(row: sqlite3.Row, claimed: set[int]) -> dict | None:
    """Resolve a saved v21.8-or-earlier URL key to one current provider row."""
    exact_key = str(row["key"] or "")
    for channel in channels:
        channel_id = int(channel.get("id", -1))
        if channel_id not in claimed and channel_key(channel) == exact_key:
            return channel

    saved_url = str(row["url"] or exact_key or "").strip()
    candidates = [
        channel
        for channel in channels
        if int(channel.get("id", -1)) not in claimed
        and legacy_channel_key(channel) == saved_url
    ]
    if not candidates:
        return None

    saved_name = str(row["name"] or "").strip().casefold()
    saved_group = str(row["group_title"] or "").strip().casefold()
    saved_tvg_id = str(row["tvg_id"] or "").strip().casefold()

    def score(channel: dict) -> tuple[int, int]:
        matches = 0
        if saved_tvg_id and str(channel.get("tvg_id", "") or "").strip().casefold() == saved_tvg_id:
            matches += 8
        if saved_name and str(channel.get("name", "") or "").strip().casefold() == saved_name:
            matches += 4
        if saved_group and str(channel.get("group", "") or "").strip().casefold() == saved_group:
            matches += 2
        # Stable tie-breaker follows provider playlist order.
        return (matches, -int(channel.get("id", 0)))

    return max(candidates, key=score)


def migrate_saved_manual_keys() -> int:
    """Upgrade URL-only selection/group keys without dropping manual channels."""
    if not channels:
        return 0
    conn = db_connect()
    migrated = 0
    try:
        rows = _selection_rows(conn)
        claimed: set[int] = set()
        rebuilt: list[tuple[str, str, str, str, str, int | None]] = []
        changed = False
        for row in rows:
            channel = _match_saved_manual_row(row, claimed)
            if channel is None:
                rebuilt.append(
                    (
                        str(row["key"] or ""),
                        str(row["name"] or ""),
                        str(row["group_title"] or ""),
                        str(row["url"] or ""),
                        str(row["tvg_id"] or ""),
                        row["sort_order"],
                    )
                )
                continue
            claimed.add(int(channel["id"]))
            new_key = channel_key(channel)
            rebuilt.append(
                (
                    new_key,
                    str(channel.get("name", "") or ""),
                    str(channel.get("group", "") or ""),
                    legacy_channel_key(channel),
                    str(channel.get("tvg_id", "") or ""),
                    row["sort_order"],
                )
            )
            if new_key != str(row["key"] or ""):
                changed = True
                migrated += 1

        if changed:
            conn.execute("DELETE FROM selections")
            conn.executemany(
                """
                INSERT OR REPLACE INTO selections
                    (key, name, group_title, url, tvg_id, sort_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rebuilt,
            )

        group_rows = conn.execute(
            """
            SELECT group_id, channel_key, name, group_title, url, tvg_id
            FROM group_channels
            """
        ).fetchall()
        group_rebuilt = []
        group_changed = False
        for group_row in group_rows:
            row_dict = {
                "key": group_row[1],
                "name": group_row[2],
                "group_title": group_row[3],
                "url": group_row[4],
                "tvg_id": group_row[5],
            }
            # sqlite.Row-like adapter for the shared resolver.
            class SavedRow(dict):
                def __getitem__(self, key):
                    return self.get(key)
            channel = _match_saved_manual_row(SavedRow(row_dict), set())
            if channel is None:
                group_rebuilt.append(tuple(group_row))
                continue
            new_key = channel_key(channel)
            group_rebuilt.append(
                (
                    group_row[0],
                    new_key,
                    str(channel.get("name", "") or ""),
                    str(channel.get("group", "") or ""),
                    legacy_channel_key(channel),
                    str(channel.get("tvg_id", "") or ""),
                )
            )
            if new_key != str(group_row[1] or ""):
                group_changed = True
                migrated += 1

        if group_changed:
            conn.execute("DELETE FROM group_channels")
            conn.executemany(
                """
                INSERT OR REPLACE INTO group_channels
                    (group_id, channel_key, name, group_title, url, tvg_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                group_rebuilt,
            )
        conn.commit()
        return migrated
    finally:
        conn.close()


def load_selected_keys_from_db() -> set[str]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT key FROM selections").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def save_selected_channels_to_db(selected_channels: list[dict]) -> None:
    conn = db_connect()
    try:
        existing_order = {
            row[0]: row[1]
            for row in conn.execute("SELECT key, sort_order FROM selections").fetchall()
        }
        next_order = max(
            [order for order in existing_order.values() if order is not None] or [-1]
        ) + 1

        conn.execute("DELETE FROM selections")
        for channel in selected_channels:
            key = channel_key(channel)
            if not key:
                continue
            sort_order = existing_order.get(key)
            if sort_order is None:
                sort_order = next_order
                next_order += 1
            conn.execute(
                """
                INSERT OR REPLACE INTO selections
                    (key, name, group_title, url, tvg_id, sort_order)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    channel.get("name", ""),
                    channel.get("group", ""),
                    channel.get("url", ""),
                    channel.get("tvg_id", ""),
                    sort_order,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def selected_channels_in_order() -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT key FROM selections ORDER BY sort_order IS NULL, sort_order, name"
        ).fetchall()
        ordered_keys = [row[0] for row in rows]
    finally:
        conn.close()
    current = channel_by_key_map()
    return [current[key] for key in ordered_keys if key in current]


def selected_xmltv_ids() -> set[str]:
    """Return exact XMLTV ids for manual channels currently in custom.m3u."""
    return {
        str(channel.get("tvg_id", "") or "").strip()
        for channel in selected_channels_from_selected_ids_in_order()
        if str(channel.get("tvg_id", "") or "").strip()
    }


def selected_channel_order_payload() -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(
            """
            SELECT key, name, group_title, url, sort_order
            FROM selections
            ORDER BY sort_order IS NULL, sort_order, name
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "key": row[0],
            "name": row[1],
            "group": row[2],
            "url": row[3],
            "sort_order": row[4],
        }
        for row in rows
    ]


def save_channel_order(keys: list[str]) -> int:
    conn = db_connect()
    try:
        existing = {row[0] for row in conn.execute("SELECT key FROM selections")}
        order = 0
        for key in keys:
            if key in existing:
                conn.execute(
                    "UPDATE selections SET sort_order = ? WHERE key = ?",
                    (order, key),
                )
                order += 1
        conn.commit()
        return order
    finally:
        conn.close()


def apply_saved_selections_to_loaded_channels() -> None:
    global selected_ids
    migrate_saved_manual_keys()
    saved_keys = load_selected_keys_from_db()
    selected_ids = {
        int(channel["id"])
        for channel in channels
        if channel_key(channel) in saved_keys
    }


def normalize_provider_id(value: str) -> str:
    return slugify(str(value or "").strip())


def provider_cache_path(source_id: str) -> Path:
    source_id = normalize_provider_id(source_id)
    if source_id == "primary":
        return MASTER_CACHE_PATH
    return PROVIDER_DIR / f"{source_id}.m3u"


def provider_epg_cache_path(source_id: str) -> Path:
    source_id = normalize_provider_id(source_id)
    if source_id == "primary":
        return EPG_CACHE_PATH
    return PROVIDER_DIR / f"{source_id}.xml"


def normalize_provider_base_url(value: str) -> str:
    """Normalize an Xtream server/base URL without embedding credentials."""
    raw = str(value or "").strip()
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Provider URL must start with http:// or https://")
    path = parsed.path or ""
    known_endpoints = {"get.php", "player_api.php", "xmltv.php"}
    segments = [segment for segment in path.split("/") if segment]
    if segments and (
        segments[-1].lower() in known_endpoints
        or segments[-1].lower().endswith((".m3u", ".m3u8"))
    ):
        segments.pop()
    normalized_path = "/" + "/".join(segments) if segments else ""
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, normalized_path.rstrip("/"), "", "", "")
    ).rstrip("/")


def provider_endpoint_url(source: dict, endpoint: str, **query_values: str) -> str:
    base = normalize_provider_base_url(str(source.get("url", "")))
    path = f"{base}/{endpoint.lstrip('/')}"
    query = urllib.parse.urlencode(
        {key: str(value) for key, value in query_values.items() if value is not None}
    )
    return f"{path}?{query}" if query else path


def provider_playlist_url(source: dict) -> str:
    kind = str(source.get("kind", "m3u") or "m3u").strip().lower()
    if kind != "xtream":
        return str(source.get("url", "") or "").strip()
    username = str(source.get("username", "") or "")
    password = str(source.get("password", "") or "")
    return provider_endpoint_url(
        source,
        "get.php",
        username=username,
        password=password,
        type="m3u_plus",
        output=str(source.get("output", "ts") or "ts"),
    )


def provider_xmltv_url(source: dict) -> str:
    if str(source.get("kind", "") or "").strip().lower() != "xtream":
        return sports.derive_xmltv_url(str(source.get("url", "") or ""))
    return provider_endpoint_url(
        source,
        "xmltv.php",
        username=str(source.get("username", "") or ""),
        password=str(source.get("password", "") or ""),
    )


def primary_provider_source() -> dict | None:
    return next(
        (source for source in provider_sources if source.get("role") == "primary"),
        None,
    )


def find_provider_source(source_id: str) -> dict | None:
    wanted = normalize_provider_id(source_id)
    return next(
        (
            source
            for source in provider_sources
            if normalize_provider_id(source.get("id", "")) == wanted
        ),
        None,
    )


def unique_provider_id(name: str) -> str:
    base = normalize_provider_id(name) or "provider"
    if base == "primary":
        base = "provider"
    existing = {normalize_provider_id(source.get("id", "")) for source in provider_sources}
    source_id = base
    index = 2
    while source_id in existing:
        source_id = f"{base}-{index}"
        index += 1
    return source_id


def load_config() -> dict:
    return app_config.load(CONFIG_PATH)


def _canonical_master_time(value) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError("Daily update time must be a valid time such as 03:00.")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Daily update time must be a valid time such as 03:00.")
    return f"{hour:02d}:{minute:02d}"


def master_timezone_name() -> str:
    # Reuse the Sports Automation timezone as the application's local clock.
    # It already exists on fresh installs and remains meaningful when sports is
    # temporarily disabled.
    try:
        return str(sports.get_settings(DB_PATH).get("timezone") or "America/New_York")
    except Exception:
        return "America/New_York"


def master_update_payload(now: datetime | None = None) -> dict:
    timezone = ZoneInfo(master_timezone_name())
    local_now = (now or datetime.now().astimezone()).astimezone(timezone)
    hour, minute = [int(part) for part in master_refresh_time.split(":", 1)]
    target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local_now:
        target += timedelta(days=1)
    elapsed_seconds = None
    if master_update_runtime.get("running") and master_update_runtime.get("started_monotonic") is not None:
        elapsed_seconds = max(0, int(time.monotonic() - float(master_update_runtime["started_monotonic"])))
    return {
        "enabled": bool(master_auto_update),
        "time": master_refresh_time,
        "timezone": str(timezone),
        "next_update": target.isoformat(timespec="seconds") if master_auto_update else None,
        "last_update": last_master_update,
        "last_duration_seconds": last_master_duration_seconds,
        "last_trigger": last_master_trigger,
        "running": bool(master_update_runtime.get("running")),
        "started_at": master_update_runtime.get("started_at"),
        "trigger": master_update_runtime.get("trigger"),
        "elapsed_seconds": elapsed_seconds,
    }


def update_master_settings(*, enabled=None, refresh_time=None) -> dict:
    global master_auto_update, master_refresh_time
    if enabled is not None:
        master_auto_update = bool(enabled)
    if refresh_time is not None:
        master_refresh_time = _canonical_master_time(refresh_time)
        # Keep the Sports "day" boundary aligned with the one application-wide
        # update clock.  There is no independent sports scheduler anymore.
        sports.update_settings(DB_PATH, {"refresh_time": master_refresh_time})
    save_config()
    return master_update_payload()


def save_config() -> None:
    primary = primary_provider_source()
    data = {
        # Keep the legacy field for downgrade/migration support, but never put
        # separately entered Xtream credentials back into a URL.
        "source_url": (
            str(primary.get("url", "") or "")
            if primary
            else last_source_url
        ),
        "source_mode": source_mode,
        "last_refresh": last_refresh,
        "schedule": {"hour": SCHEDULE_HOUR, "minute": SCHEDULE_MINUTE},
        "master_update": {
            "enabled": bool(master_auto_update),
            "time": master_refresh_time,
            "last_update": last_master_update,
            "last_duration_seconds": last_master_duration_seconds,
            "last_trigger": last_master_trigger,
        },
        "public_epg": {
            "enabled_countries": sorted(public_epg_enabled_codes),
            "state": public_epg_state,
        },
        "epg_sources": epg_sources,
        "provider_sources": provider_sources,
    }
    app_config.update(data, path=CONFIG_PATH)


def restore_config() -> None:
    global last_source_url, source_mode, last_refresh, epg_sources, provider_sources
    global master_auto_update, master_refresh_time, last_master_update
    global last_master_duration_seconds, last_master_trigger
    global public_epg_enabled_codes, public_epg_state
    data = load_config()
    source_mode = str(data.get("source_mode", "")).strip()
    last_refresh = data.get("last_refresh")
    master = data.get("master_update", {}) if isinstance(data.get("master_update"), dict) else {}
    master_auto_update = bool(master.get("enabled", True))
    try:
        master_refresh_time = _canonical_master_time(master.get("time", "03:00"))
    except ValueError:
        master_refresh_time = "03:00"
    last_master_update = master.get("last_update") or None
    try:
        last_master_duration_seconds = float(master.get("last_duration_seconds")) if master.get("last_duration_seconds") is not None else None
    except (TypeError, ValueError):
        last_master_duration_seconds = None
    last_master_trigger = str(master.get("last_trigger") or "").strip() or None

    public = data.get("public_epg", {}) if isinstance(data.get("public_epg"), dict) else {}
    enabled_codes = public.get("enabled_countries", ["US"])
    if not isinstance(enabled_codes, list):
        enabled_codes = ["US"]
    public_epg_enabled_codes = {
        str(code).upper() for code in enabled_codes if str(code).upper() in PUBLIC_EPG_CODES
    }
    if "public_epg" not in data:
        public_epg_enabled_codes = {"US"}
    restored_public_state = public.get("state", {})
    public_epg_state = {
        str(code).upper(): dict(value)
        for code, value in restored_public_state.items()
        if str(code).upper() in PUBLIC_EPG_CODES and isinstance(value, dict)
    } if isinstance(restored_public_state, dict) else {}

    restored_sources = data.get("epg_sources", [])
    epg_sources = [dict(item) for item in restored_sources if isinstance(item, dict)]
    restored_providers = data.get("provider_sources", [])
    provider_sources = [
        dict(item)
        for item in restored_providers
        if isinstance(item, dict) and str(item.get("url", "") or "").strip()
    ]

    primary = primary_provider_source()
    legacy_url = str(data.get("source_url", "") or "").strip()
    if not primary and legacy_url and source_mode == "url":
        primary = {
            "id": "primary",
            "name": "Primary",
            "role": "primary",
            "priority": 0,
            "kind": "m3u",
            "url": legacy_url,
            "username": "",
            "password": "",
            "output": "ts",
            "last_refresh": last_refresh,
            "last_error": None,
            "channel_count": 0,
        }
        provider_sources.insert(0, primary)

    # Repair old/partial provider records. A provider list may intentionally
    # contain only inactive fallbacks after the primary has been removed.
    if provider_sources:
        primary_index = next(
            (index for index, item in enumerate(provider_sources) if item.get("role") == "primary"),
            None,
        )
        # Older configurations sometimes omitted role metadata. Preserve their
        # prior behavior only when the saved source mode still says URL.
        if primary_index is None and source_mode == "url":
            primary_index = 0
        for index, item in enumerate(provider_sources):
            is_primary = primary_index is not None and index == primary_index
            item["id"] = "primary" if is_primary else normalize_provider_id(
                item.get("id", "") or item.get("name", "") or f"fallback-{index}"
            )
            item["role"] = "primary" if is_primary else "fallback"
            item.setdefault("name", "Primary" if is_primary else f"Fallback {index + 1}")
            item.setdefault("kind", "m3u")
            item.setdefault("username", "")
            item.setdefault("password", "")
            item.setdefault("output", "ts")
            item.setdefault("last_refresh", None)
            item.setdefault("last_error", None)
            item.setdefault("channel_count", 0)
            item.setdefault("deferred", item.get("role") == "fallback" and not provider_cache_path(str(item.get("id", ""))).exists())
            item.setdefault("warning", None)
        _renumber_provider_priorities()
        primary = primary_provider_source()

    last_source_url = provider_playlist_url(primary) if primary else (legacy_url if source_mode == "url" else "")


def parse_m3u_text(text: str) -> list[dict]:
    lines = text.splitlines()
    parsed: list[Entry] = []
    current: list[str] = []
    name = ""
    group = ""
    attrs: dict[str, str] = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#EXTINF"):
            current = [stripped]
            name = stripped.rsplit(",", 1)[1].strip() if "," in stripped else ""
            attrs = parse_extinf_attributes(stripped)
            group = attrs.get("group-title", "")
            continue
        if current and stripped.startswith("#"):
            current.append(stripped)
            continue
        if current and not stripped.startswith("#"):
            current.append(stripped)
            parsed.append(
                Entry(
                    id=len(parsed),
                    name=name or stripped,
                    group=group,
                    url=stripped,
                    raw=current.copy(),
                    tvg_id=attrs.get("tvg-id", ""),
                    tvg_name=attrs.get("tvg-name", ""),
                    tvg_logo=attrs.get("tvg-logo", ""),
                    tvg_chno=attrs.get("tvg-chno", ""),
                    attrs=dict(attrs),
                )
            )
            current = []
            attrs = {}

    output = [asdict(entry) for entry in parsed]
    for channel in output:
        channel["key"] = channel_key(channel)
    return output


def download_url_bytes(
    url: str,
    timeout: int = 90,
    cancel_check=None,
    max_bytes: int | None = None,
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "M3U-Web-Picker/2.0", "Accept": "*/*"},
    )
    chunks = []
    total = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        while True:
            if cancel_check and cancel_check():
                raise sports.ScanCancelled("Sports update cancelled. Existing sports channels were kept.")
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(
                    f"Provider response exceeded the {max_bytes // (1024 * 1024)} MB safety limit."
                )
            chunks.append(chunk)
    return b"".join(chunks)


def download_url_text(
    url: str,
    timeout: int = 90,
    cancel_check=None,
    max_bytes: int | None = None,
) -> str:
    return download_url_bytes(
        url, timeout, cancel_check, max_bytes=max_bytes
    ).decode("utf-8-sig", errors="replace")


def download_m3u_text(
    url: str,
    timeout: int = 90,
    cancel_check=None,
    max_bytes: int | None = MAX_PROVIDER_PLAYLIST_BYTES,
) -> str:
    return download_url_text(
        url, timeout=timeout, cancel_check=cancel_check, max_bytes=max_bytes
    )


def validate_m3u_text(text: str, *, max_channels: int | None = MAX_PROVIDER_CHANNELS) -> list[dict]:
    if not str(text or "").lstrip("\ufeff\r\n\t ").startswith("#EXTM3U"):
        raise ValueError("The provider response did not look like an M3U playlist.")
    entry_count = str(text).count("#EXTINF")
    if max_channels is not None and entry_count > max_channels:
        raise ValueError(
            f"Provider playlist contains about {entry_count:,} entries, above the "
            f"{max_channels:,}-channel safety limit. For Xtream providers, use "
            "separate credentials so the app can request live streams only."
        )
    parsed = parse_m3u_text(text)
    if not parsed:
        raise ValueError("The provider playlist did not contain any channels.")
    if max_channels is not None and len(parsed) > max_channels:
        raise ValueError(
            f"Provider playlist contains {len(parsed):,} channels, above the "
            f"{max_channels:,}-channel safety limit."
        )
    return parsed


def _probe_m3u_header(url: str, timeout: int = 20, limit: int = 256 * 1024) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "M3U-Web-Picker/2.0",
            "Accept": "audio/x-mpegurl,application/vnd.apple.mpegurl,text/plain,*/*",
            "Range": f"bytes=0-{limit - 1}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = urllib.parse.urlparse(response.geturl())
        initial_url = urllib.parse.urlparse(url)
        if final_url.hostname and initial_url.hostname and final_url.hostname.lower() != initial_url.hostname.lower():
            raise ValueError("Provider redirected credentials to a different host.")
        raw = response.read(limit)
    text = raw.decode("utf-8-sig", errors="replace")
    if not text.lstrip("\ufeff\r\n\t ").startswith("#EXTM3U"):
        raise ValueError("The provider response did not look like an M3U playlist.")


def _download_probe_bytes(url: str, *, accept: str, timeout: int = 20, limit: int = 2 * 1024 * 1024) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "M3U-Web-Picker/2.0", "Accept": accept},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final_url = urllib.parse.urlparse(response.geturl())
        initial_url = urllib.parse.urlparse(url)
        if final_url.hostname and initial_url.hostname and final_url.hostname.lower() != initial_url.hostname.lower():
            raise ValueError("Provider redirected credentials to a different host.")
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Provider probe response was unexpectedly large.")
    return raw


def _xtream_auth_is_valid(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    user_info = payload.get("user_info")
    if not isinstance(user_info, dict):
        return False
    auth = user_info.get("auth")
    if auth in {1, "1", True, "true", "True"}:
        return True
    return str(user_info.get("status", "") or "").strip().lower() in {
        "active",
        "enabled",
    }


def _xtream_account_metadata(payload: object) -> dict[str, str | None]:
    """Return safe, provider-reported account details from player_api.php.

    Xtream panels are not billing systems of record, so these values are kept
    deliberately small and are presented as provider-reported status only.
    Credentials and the raw API response never enter browser payloads.
    """
    if not isinstance(payload, dict):
        return {"account_status": None, "expires_at": None}
    user_info = payload.get("user_info")
    if not isinstance(user_info, dict):
        return {"account_status": None, "expires_at": None}

    raw_status = str(user_info.get("status", "") or "").strip()
    if raw_status:
        account_status = raw_status[:64]
    elif _xtream_auth_is_valid(payload):
        account_status = "Active"
    else:
        account_status = None

    expires_at = None
    raw_expiry = user_info.get("exp_date")
    try:
        timestamp = int(str(raw_expiry or "0").strip())
        # A few panels return milliseconds even though Xtream convention is
        # seconds. Normalize both forms and ignore zero/unlimited accounts.
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        if timestamp > 0:
            expires_at = datetime.fromtimestamp(
                timestamp,
                tz=ZoneInfo("UTC"),
            ).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        expires_at = None

    return {
        "account_status": account_status,
        "expires_at": expires_at,
    }


def _apply_xtream_account_metadata(source: dict, payload: object) -> None:
    source.update(_xtream_account_metadata(payload))


def refresh_xtream_account_metadata(source: dict, cancel_check=None) -> None:
    """Refresh safe Xtream account status without failing playlist refreshes."""
    if not source.get("xtream_api"):
        return
    try:
        payload = _download_json(
            _xtream_api_url(source),
            timeout=20,
            max_bytes=1024 * 1024,
            cancel_check=cancel_check,
        )
        if _xtream_auth_is_valid(payload):
            _apply_xtream_account_metadata(source, payload)
    except sports.ScanCancelled:
        raise
    except Exception:
        # Account metadata is informational. A panel that temporarily omits it
        # must not block channel or sports refreshes.
        return


def _xtream_api_url(source: dict, action: str | None = None) -> str:
    values = {
        "username": str(source.get("username", "") or ""),
        "password": str(source.get("password", "") or ""),
    }
    if action:
        values["action"] = action
    return provider_endpoint_url(source, "player_api.php", **values)


def _download_json(url: str, *, timeout: int = 90, max_bytes: int = MAX_PROVIDER_JSON_BYTES, cancel_check=None):
    raw = download_url_bytes(
        url, timeout=timeout, cancel_check=cancel_check, max_bytes=max_bytes
    )
    return json.loads(raw.decode("utf-8-sig", errors="replace"))


def _m3u_attribute(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace('"', "&quot;")


def _xtream_stream_url(source: dict, stream_id: object) -> str:
    base = normalize_provider_base_url(str(source.get("url", "")))
    username = urllib.parse.quote(str(source.get("username", "") or ""), safe="")
    password = urllib.parse.quote(str(source.get("password", "") or ""), safe="")
    output = str(source.get("output", "ts") or "ts").lstrip(".")
    return f"{base}/live/{username}/{password}/{stream_id}.{output}"


def xtream_live_playlist(source: dict, cancel_check=None) -> tuple[str, list[dict]]:
    _provider_progress_update("Downloading Xtream live-stream list")
    payload = _download_json(
        _xtream_api_url(source, "get_live_streams"),
        cancel_check=cancel_check,
    )
    if not isinstance(payload, list):
        raise ValueError("Xtream live-stream endpoint did not return a channel list.")
    if len(payload) > MAX_PROVIDER_CHANNELS:
        raise ValueError(
            f"Xtream returned {len(payload):,} live streams, above the "
            f"{MAX_PROVIDER_CHANNELS:,}-channel safety limit."
        )
    _provider_progress_update(
        "Loading live categories",
        channel_count=len(payload),
        detail=f"{len(payload):,} live streams reported",
    )
    categories: dict[str, str] = {}
    try:
        category_payload = _download_json(
            _xtream_api_url(source, "get_live_categories"),
            timeout=30,
            max_bytes=8 * 1024 * 1024,
            cancel_check=cancel_check,
        )
        if isinstance(category_payload, list):
            categories = {
                str(item.get("category_id", "")): str(item.get("category_name", "") or "Live TV")
                for item in category_payload
                if isinstance(item, dict)
            }
    except Exception:
        categories = {}

    _provider_progress_update(
        "Building live-only playlist",
        channel_count=len(payload),
    )
    lines = ["#EXTM3U"]
    parsed: list[dict] = []
    for item in payload:
        if cancel_check and cancel_check():
            raise sports.ScanCancelled("Sports update cancelled. Existing sports channels were kept.")
        if not isinstance(item, dict):
            continue
        stream_type = str(item.get("stream_type", "live") or "live").strip().lower()
        if stream_type and stream_type not in {"live", "created_live"}:
            continue
        stream_id = item.get("stream_id")
        name = str(item.get("name", "") or "").strip()
        if stream_id in {None, ""} or not name:
            continue
        category_id = str(item.get("category_id", "") or "")
        group = categories.get(category_id) or str(item.get("category_name", "") or "Live TV")
        tvg_id = str(item.get("epg_channel_id", "") or item.get("tvg_id", "") or "")
        tvg_logo = str(item.get("stream_icon", "") or "")
        tvg_chno = str(item.get("num", "") or "")
        stream_url = _xtream_stream_url(source, stream_id)
        attrs = {
            "tvg-id": tvg_id,
            "tvg-name": name,
            "tvg-logo": tvg_logo,
            "group-title": group,
        }
        if tvg_chno:
            attrs["tvg-chno"] = tvg_chno
        extinf = (
            '#EXTINF:-1 '
            f'tvg-id="{_m3u_attribute(tvg_id)}" '
            f'tvg-name="{_m3u_attribute(name)}" '
            f'tvg-logo="{_m3u_attribute(tvg_logo)}" '
            f'group-title="{_m3u_attribute(group)}"'
        )
        if tvg_chno:
            extinf += f' tvg-chno="{_m3u_attribute(tvg_chno)}"'
        extinf += f",{name}"
        raw = [extinf, stream_url]
        channel = {
            "id": len(parsed),
            "name": name,
            "group": group,
            "url": stream_url,
            "raw": raw,
            "tvg_id": tvg_id,
            "tvg_name": name,
            "tvg_logo": tvg_logo,
            "tvg_chno": tvg_chno,
            "attrs": attrs,
            "is_sports_generated": False,
        }
        channel["key"] = channel_key(channel)
        parsed.append(channel)
        lines.extend(raw)
    if not parsed:
        raise ValueError("Xtream live-stream endpoint returned no usable live channels.")
    if len(parsed) > MAX_PROVIDER_CHANNELS:
        raise ValueError(
            f"Xtream returned {len(parsed):,} usable live channels, above the "
            f"{MAX_PROVIDER_CHANNELS:,}-channel safety limit."
        )
    return "\n".join(lines) + "\n", parsed


def load_provider_playlist(source: dict, cancel_check=None) -> tuple[str, list[dict]]:
    if str(source.get("kind", "m3u") or "m3u").lower() == "xtream" and source.get("xtream_api"):
        try:
            return xtream_live_playlist(source, cancel_check=cancel_check)
        except sports.ScanCancelled:
            raise
        except Exception as exc:
            if "safety limit" in str(exc).lower():
                raise
            source["live_api_error"] = redact_url_credentials(str(exc))
            _provider_progress_update(
                "Live API unavailable; trying capped M3U fallback",
                detail=source["live_api_error"],
            )
    _provider_progress_update("Downloading provider playlist")
    text = download_m3u_text(
        provider_playlist_url(source),
        timeout=90,
        cancel_check=cancel_check,
        max_bytes=MAX_PROVIDER_PLAYLIST_BYTES,
    )
    _provider_progress_update("Checking playlist size")
    parsed = validate_m3u_text(text)
    return text, parsed


def detect_provider_source(
    name: str,
    url: str,
    *,
    username: str = "",
    password: str = "",
    role: str = "fallback",
    source_id: str | None = None,
    load_channels: bool = True,
) -> tuple[dict, str, list[dict]]:
    """Validate a direct M3U or separate-field Xtream provider login.

    Primary providers are loaded immediately. Fallbacks can be registered in
    deferred mode and are not downloaded until Master Update needs them.
    Authenticated Xtream providers use get_live_streams first so VOD and series
    entries never enter the sports channel set.
    """
    clean_name = str(name or "").strip() or ("Primary" if role == "primary" else "Fallback")
    clean_url = str(url or "").strip()
    clean_username = str(username or "")
    clean_password = str(password or "")
    if bool(clean_username) != bool(clean_password):
        raise ValueError("Enter both the Xtream username and password, or leave both blank.")
    if not clean_url.startswith(("http://", "https://")):
        raise ValueError("Provider URL must start with http:// or https://")

    _provider_progress_update(
        "Preparing provider validation",
        detail="Fallback channels will load only during Master Update." if not load_channels else "",
    )
    resolved_id = "primary" if role == "primary" else (source_id or unique_provider_id(clean_name))
    source = {
        "id": normalize_provider_id(resolved_id),
        "name": clean_name,
        "role": "primary" if role == "primary" else "fallback",
        "priority": 0,
        "kind": "m3u",
        "url": clean_url,
        "username": "",
        "password": "",
        "output": "ts",
        "xtream_api": False,
        "deferred": not load_channels,
        "last_refresh": None,
        "last_error": None,
        "channel_count": 0,
        "account_status": None,
        "expires_at": None,
    }

    try:
        if clean_username and clean_password:
            source.update(
                {
                    "kind": "xtream",
                    "url": normalize_provider_base_url(clean_url),
                    "username": clean_username,
                    "password": clean_password,
                }
            )
            _provider_progress_update("Probing Xtream authentication")
            api_url = _xtream_api_url(source)
            api_error: Exception | None = None
            try:
                raw = _download_probe_bytes(api_url, accept="application/json,*/*")
                payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
                if not _xtream_auth_is_valid(payload):
                    user_info = payload.get("user_info") if isinstance(payload, dict) else None
                    if isinstance(user_info, dict) and str(user_info.get("auth", "")) in {"0", "False", "false"}:
                        raise ValueError("Xtream authentication was rejected by the provider.")
                    raise ValueError("The provider did not return a valid Xtream authentication response.")
                source["xtream_api"] = True
                _apply_xtream_account_metadata(source, payload)
            except ValueError as exc:
                if "rejected" in str(exc).lower():
                    raise
                api_error = exc
            except Exception as exc:
                api_error = exc

            if not load_channels:
                if not source["xtream_api"]:
                    _provider_progress_update("Probing Xtream-compatible M3U endpoint")
                    for output in ("ts", "m3u8"):
                        source["output"] = output
                        try:
                            _probe_m3u_header(provider_playlist_url(source))
                            break
                        except Exception as exc:
                            api_error = exc
                    else:
                        safe = redact_url_credentials(str(api_error or "Unknown provider error"))
                        raise ValueError(f"Xtream login could not be validated: {safe}")
                _provider_progress_complete(0, "Saved for live-only loading during Master Update.")
                return source, "", []

            text, parsed = load_provider_playlist(source)
            source["channel_count"] = len(parsed)
            if len(parsed) >= PROVIDER_CHANNEL_WARNING:
                source["warning"] = f"Large live channel set: {len(parsed):,} channels."
            _provider_progress_complete(len(parsed), "Live-only channels loaded." if source.get("xtream_api") else "Capped M3U loaded.")
            return source, text, parsed

        if not load_channels:
            _provider_progress_update("Probing direct M3U source")
            _probe_m3u_header(clean_url)
            _provider_progress_complete(0, "Saved for loading during Master Update.")
            return source, "", []

        text, parsed = load_provider_playlist(source)
        source["channel_count"] = len(parsed)
        if len(parsed) >= PROVIDER_CHANNEL_WARNING:
            source["warning"] = f"Large channel set: {len(parsed):,} channels."
        _provider_progress_complete(len(parsed), "Provider playlist loaded.")
        return source, text, parsed
    except Exception as exc:
        fail_provider_progress(str(exc))
        raise


def selected_channels_from_selected_ids_in_order() -> list[dict]:
    selected_channels = [
        channel for channel in channels if int(channel["id"]) in selected_ids
    ]
    conn = db_connect()
    try:
        existing_order = {
            row[0]: row[1]
            for row in conn.execute("SELECT key, sort_order FROM selections").fetchall()
        }
    finally:
        conn.close()

    def sort_key(channel: dict):
        key = channel_key(channel)
        order = existing_order.get(key)
        if order is None:
            return (1, channel.get("name", "").lower(), key)
        return (0, order, channel.get("name", "").lower())

    return sorted(selected_channels, key=sort_key)


def write_current_playlist() -> int:
    with state_lock:
        manual_channels = selected_channels_from_selected_ids_in_order()
        generated = sports.generated_rows(DB_PATH)
        # Manual/static and generated sports channels intentionally coexist.
        # Never deduplicate across these namespaces, even when their stream URL
        # or provider tvg-id is identical. Jellyfin distinguishes the generated
        # row by its unique m3u-picker-sports tvg-id and channel number.
        lines = ["#EXTM3U"]
        for number, channel in enumerate(manual_channels, start=1):
            lines.extend(apply_channel_number(channel, number))
        for row in generated:
            lines.extend(row.get("raw", []))
        atomic_write_text(PLAYLIST_PATH, "\n".join(lines) + "\n")
        save_selected_channels_to_db(manual_channels)
        return len(manual_channels) + len(generated)


def channel_by_key_map() -> dict[str, dict]:
    return {channel_key(channel): channel for channel in channels if channel_key(channel)}


def combined_channels_for_api() -> list[dict]:
    # Keep provider/manual channels above generated sports and event channels in
    # the Channel Manager. These are separate namespaces: matching URLs or tvg-id
    # values never suppress either row. Generated rows retain negative IDs so the
    # selection endpoint never treats them as editable manual selections.
    manual_payload = []
    for channel in channels:
        item = dict(channel)
        item["key"] = channel_key(item)
        manual_payload.append(item)
    return [*manual_payload, *sports.generated_channel_payloads(DB_PATH)]


def manual_stream_target(token: str) -> str:
    """Resolve an opaque curated manual-channel token to its current provider URL."""
    expected_key = f"manual:{str(token or '').strip()}"
    if expected_key == "manual:":
        return ""
    for channel in selected_channels_from_selected_ids_in_order():
        if channel_key(channel) != expected_key:
            continue
        return str(channel.get("url", "") or "").strip()
    return ""


def curated_channels_for_guide() -> list[dict]:
    """Return the exact currently served curated lineup without exposing provider URLs."""
    output: list[dict] = []

    for number, channel in enumerate(selected_channels_from_selected_ids_in_order(), start=1):
        key = channel_key(channel)
        token = key.split(":", 1)[1] if key.startswith("manual:") else ""
        if not token:
            continue
        output.append({
            "number": number,
            "name": str(channel.get("name", "") or ""),
            "group": str(channel.get("group", "") or ""),
            "logo": str(channel.get("tvg_logo", "") or ""),
            "tvg_id": str(channel.get("tvg_id", "") or ""),
            "subtitle": "",
            "generated": False,
            "play_url": f"/guide/play/manual/{token}",
        })

    for row in sports.generated_rows(DB_PATH):
        assigned = int(row.get("assigned_number") or 0)
        if assigned <= 0:
            continue
        output.append({
            "number": assigned,
            "name": str(row.get("display_name", "") or ""),
            "group": str(row.get("group_title", "") or ""),
            "logo": str(row.get("tvg_logo", "") or ""),
            "tvg_id": str(row.get("tvg_id", "") or ""),
            "subtitle": str(row.get("subtitle", "") or ""),
            "generated": True,
            "play_url": f"/guide/play/sports/{assigned}",
        })

    return output


def selected_ids_payload() -> list[int]:
    generated_ids = [channel["id"] for channel in sports.generated_channel_payloads(DB_PATH)]
    return sorted([*selected_ids, *generated_ids])


def group_channels_for_slug(slug: str) -> tuple[str, list[dict]]:
    conn = db_connect()
    try:
        group = conn.execute(
            "SELECT id, name FROM custom_groups WHERE slug = ?", (slug,)
        ).fetchone()
        if not group:
            return "", []
        group_id, group_name = group
        keys = [
            row[0]
            for row in conn.execute(
                "SELECT channel_key FROM group_channels WHERE group_id = ? ORDER BY name",
                (group_id,),
            ).fetchall()
        ]
    finally:
        conn.close()
    current = channel_by_key_map()
    return group_name, [current[key] for key in keys if key in current]


def all_grouped_channels() -> list[dict]:
    conn = db_connect()
    try:
        keys = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT channel_key FROM group_channels ORDER BY name"
            ).fetchall()
        ]
    finally:
        conn.close()
    current = channel_by_key_map()
    return [current[key] for key in keys if key in current]


def list_custom_groups() -> list[dict]:
    conn = db_connect()
    try:
        rows = conn.execute(
            """
            SELECT g.id, g.name, g.slug, g.created_at, COUNT(gc.channel_key)
            FROM custom_groups g
            LEFT JOIN group_channels gc ON gc.group_id = g.id
            GROUP BY g.id
            ORDER BY g.name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row[0],
            "name": row[1],
            "slug": row[2],
            "created_at": row[3],
            "channel_count": row[4],
        }
        for row in rows
    ]


def create_custom_group(name: str) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Group name is required.")
    conn = db_connect()
    try:
        slug = unique_slug(conn, clean_name)
        cursor = conn.execute(
            "INSERT INTO custom_groups(name, slug, created_at) VALUES (?, ?, ?)",
            (clean_name, slug, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return {"id": cursor.lastrowid, "name": clean_name, "slug": slug}
    finally:
        conn.close()


def group_member_keys(slug: str) -> list[str]:
    conn = db_connect()
    try:
        group = conn.execute("SELECT id FROM custom_groups WHERE slug = ?", (slug,)).fetchone()
        if not group:
            return []
        rows = conn.execute(
            "SELECT channel_key FROM group_channels WHERE group_id = ?",
            (group[0],),
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def add_channels_to_group(slug: str, keys: Iterable[str]) -> int:
    current = channel_by_key_map()
    conn = db_connect()
    try:
        group = conn.execute("SELECT id FROM custom_groups WHERE slug = ?", (slug,)).fetchone()
        if not group:
            raise ValueError("Group not found.")
        added = 0
        for key in keys:
            channel = current.get(key)
            if not channel:
                continue
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO group_channels
                    (group_id, channel_key, name, group_title, url, tvg_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    group[0],
                    key,
                    channel.get("name", ""),
                    channel.get("group", ""),
                    channel.get("url", ""),
                    channel.get("tvg_id", ""),
                ),
            )
            added += cursor.rowcount
        conn.commit()
        return added
    finally:
        conn.close()


def remove_channels_from_group(slug: str, keys: Iterable[str]) -> int:
    conn = db_connect()
    try:
        group = conn.execute("SELECT id FROM custom_groups WHERE slug = ?", (slug,)).fetchone()
        if not group:
            raise ValueError("Group not found.")
        removed = 0
        for key in keys:
            cursor = conn.execute(
                "DELETE FROM group_channels WHERE group_id = ? AND channel_key = ?",
                (group[0], key),
            )
            removed += cursor.rowcount
        conn.commit()
        return removed
    finally:
        conn.close()


def m3u_from_channels(items: list[dict]) -> str:
    lines = ["#EXTM3U"]
    seen = set()
    for channel in items:
        key = channel_key(channel)
        if not key or key in seen:
            continue
        seen.add(key)
        lines.extend(channel["raw"])
    return "\n".join(lines) + "\n"


def normalize_epg_id(source_id: str) -> str:
    return slugify(str(source_id or "").removesuffix(".xml"))


def epg_cache_path(source_id: str) -> Path:
    return EPG_DIR / f"{normalize_epg_id(source_id)}.xml"


def epg_public_url(source_id: str) -> str:
    return f"/epg/{normalize_epg_id(source_id)}.xml"


def redact_url_credentials(value: str) -> str:
    text = str(value or "")
    # Collapse the entire path/query of any http(s) URL to a fixed placeholder
    # before errors reach the browser or logs. Xtream panels embed credentials
    # either in query parameters (get.php?username=...&password=...) or
    # directly in the path (.../live/<user>/<pass>/<id>.ts), so redacting a
    # fixed number of path segments or a fixed set of query keys reliably
    # misses one shape or the other; collapsing everything after the host
    # does not.
    text = re.sub(r"(https?://[^/\s?#]+)[^\s]*", r"\1/REDACTED", text, flags=re.I)
    return text


def friendly_source_label(url: str) -> str:
    try:
        hostname = urllib.parse.urlparse(str(url or "")).hostname or ""
    except Exception:
        hostname = ""
    hostname = hostname.lower().removeprefix("www.")
    if not hostname or re.fullmatch(r"[\d.:]+", hostname):
        return "Provider"
    if re.search(r"(^|\.)astranettv\.", hostname, flags=re.I) or re.search(
        r"(^|\.)astranet\.", hostname, flags=re.I
    ):
        return "AstraNet"
    parts = [part for part in hostname.split(".") if part]
    common = {"api", "cdn", "edge", "live", "media", "stream", "streams", "tv"}
    while len(parts) > 2 and parts[0] in common:
        parts.pop(0)
    candidate = parts[-2] if len(parts) >= 2 else parts[0]
    label = re.sub(r"[-_]+", " ", candidate).strip().title()
    return label or "Provider"


def provider_sources_payload() -> list[dict]:
    payload = []
    for source in sorted(provider_sources, key=lambda item: int(item.get("priority", 999))):
        source_id = normalize_provider_id(source.get("id", ""))
        cache = provider_cache_path(source_id)
        payload.append(
            {
                "id": source_id,
                "name": str(source.get("name", "") or source_id),
                "role": str(source.get("role", "fallback") or "fallback"),
                "priority": int(source.get("priority", 999)),
                "source_label": friendly_source_label(str(source.get("url", ""))),
                "kind": str(source.get("kind", "m3u") or "m3u"),
                "xtream_api": bool(source.get("xtream_api")),
                "credentials_saved": bool(source.get("username") and source.get("password")),
                "cached": cache.exists(),
                "channel_count": int(source.get("channel_count", 0) or 0),
                "deferred": bool(source.get("deferred")),
                "warning": source.get("warning"),
                "last_refresh": source.get("last_refresh"),
                "last_error": source.get("last_error"),
                "account_status": source.get("account_status"),
                "expires_at": source.get("expires_at"),
            }
        )
    return payload


def _renumber_provider_priorities() -> None:
    primary = primary_provider_source()
    provider_sources.sort(
        key=lambda item: (
            0 if item.get("role") == "primary" else 1,
            int(item.get("priority", 999)),
        )
    )
    fallback_priority = 1
    for source in provider_sources:
        if primary is not None and source is primary:
            source["priority"] = 0
            source["role"] = "primary"
            source["id"] = "primary"
            continue
        source["priority"] = fallback_priority
        source["role"] = "fallback"
        fallback_priority += 1


def install_primary_provider(source: dict, text: str, parsed: list[dict]) -> None:
    global channels, last_source_url, source_mode, last_refresh, provider_sources
    with state_lock:
        fallback_sources = [item for item in provider_sources if item.get("role") != "primary"]
        source = dict(source)
        source["id"] = "primary"
        source["role"] = "primary"
        source["priority"] = 0
        source["last_refresh"] = datetime.now().astimezone().isoformat(timespec="seconds")
        source["last_error"] = None
        source["channel_count"] = len(parsed)
        provider_sources = [source, *fallback_sources]
        _renumber_provider_priorities()
        atomic_write_text(MASTER_CACHE_PATH, text)
        channels = parsed
        last_source_url = provider_playlist_url(source)
        source_mode = "url"
        last_refresh = source["last_refresh"]
        sports.discover_catalog_from_channels(DB_PATH, channels)
        apply_saved_selections_to_loaded_channels()
        save_config()
        write_current_playlist()


def add_fallback_provider(
    name: str,
    url: str,
    *,
    username: str = "",
    password: str = "",
) -> dict:
    if not primary_provider_source():
        raise ValueError("Load a URL primary provider before adding fallbacks.")
    source, text, parsed = detect_provider_source(
        name,
        url,
        username=username,
        password=password,
        role="fallback",
        load_channels=False,
    )
    source["last_refresh"] = None
    source["channel_count"] = 0
    source["priority"] = len(provider_sources)
    provider_sources.append(source)
    _renumber_provider_priorities()
    save_config()
    return source


def delete_fallback_provider(source_id: str) -> bool:
    global provider_sources
    wanted = normalize_provider_id(source_id)
    source = find_provider_source(wanted)
    if not source or source.get("role") == "primary":
        return False
    provider_sources = [
        item
        for item in provider_sources
        if normalize_provider_id(item.get("id", "")) != wanted
    ]
    provider_cache_path(wanted).unlink(missing_ok=True)
    provider_epg_cache_path(wanted).unlink(missing_ok=True)
    _renumber_provider_priorities()
    save_config()
    return True


def remove_primary_source() -> bool:
    """Remove the active primary while preserving inactive fallback settings."""
    global channels, selected_ids, last_source_url, last_refresh, source_mode, provider_sources
    with state_lock:
        primary = primary_provider_source()
        has_file_primary = source_mode == "file"
        if not primary and not has_file_primary:
            return False

        provider_sources = [
            item for item in provider_sources if item.get("role") != "primary"
        ]
        _renumber_provider_priorities()
        channels = []
        selected_ids = set()
        last_source_url = ""
        last_refresh = None
        source_mode = ""

        MASTER_CACHE_PATH.unlink(missing_ok=True)
        EPG_CACHE_PATH.unlink(missing_ok=True)
        sports.clear_generated_channels(DB_PATH)
        save_config()
        write_current_playlist()
        try:
            ensure_epg_exports_current(force=True)
        except Exception as exc:
            print(f"Could not rebuild guides after primary removal: {exc}")
    return True


def refresh_provider_source(source: dict, cancel_check=None) -> tuple[bool, str, list[dict]]:
    source_id = normalize_provider_id(source.get("id", ""))
    try:
        refresh_xtream_account_metadata(source, cancel_check=cancel_check)
        text, parsed = load_provider_playlist(source, cancel_check=cancel_check)
        atomic_write_text(provider_cache_path(source_id), text)
        source["last_refresh"] = datetime.now().astimezone().isoformat(timespec="seconds")
        source["last_error"] = None
        source["deferred"] = False
        source["channel_count"] = len(parsed)
        source["warning"] = (
            f"Large channel set: {len(parsed):,} channels."
            if len(parsed) >= PROVIDER_CHANNEL_WARNING
            else None
        )
        save_config()
        _provider_progress_complete(len(parsed), f"Refreshed {len(parsed):,} live channels.")
        return True, f"Refreshed {len(parsed)} live channels.", parsed
    except sports.ScanCancelled:
        raise
    except Exception as exc:
        safe_error = redact_url_credentials(str(exc))
        source["last_error"] = safe_error
        fail_provider_progress(safe_error)
        save_config()
        return False, safe_error, []


def refresh_provider_epg(source: dict, cancel_check=None) -> tuple[bool, str]:
    url = provider_xmltv_url(source)
    if not url:
        return False, "No Xtream XMLTV URL could be derived."
    try:
        raw = sports.download_xmltv_bytes(url, cancel_check=cancel_check)
        atomic_write_bytes(provider_epg_cache_path(str(source.get("id", ""))), raw)
        return True, f"Cached {len(raw)} bytes of XMLTV data."
    except sports.ScanCancelled:
        raise
    except Exception as exc:
        return False, redact_url_credentials(str(exc))


def _load_provider_cache(source: dict) -> list[dict]:
    path = provider_cache_path(str(source.get("id", "")))
    if not path.exists():
        return []
    try:
        return validate_m3u_text(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return []


def sports_provider_channel_sets() -> list[tuple[dict, list[dict]]]:
    """Return primary/fallback channel sets annotated for sports precedence."""
    primary = primary_provider_source()
    if primary is None:
        if not channels:
            return []
        # File uploads and pre-migration state remain a valid single primary.
        annotated = []
        for channel in channels:
            item = dict(channel)
            item["_provider_source_id"] = "primary"
            item["_provider_priority"] = 0
            item["_provider_role"] = "primary"
            annotated.append(item)
        return [({"id": "primary", "role": "primary", "priority": 0}, annotated)]

    sets: list[tuple[dict, list[dict]]] = []
    for source in sorted(provider_sources, key=lambda item: int(item.get("priority", 999))):
        source_channels = channels if source.get("role") == "primary" else _load_provider_cache(source)
        if not source_channels:
            continue
        annotated: list[dict] = []
        for channel in source_channels:
            item = dict(channel)
            item["_provider_source_id"] = normalize_provider_id(source.get("id", ""))
            item["_provider_priority"] = int(source.get("priority", 999))
            item["_provider_role"] = str(source.get("role", "fallback"))
            annotated.append(item)
        sets.append((source, annotated))
    return sets


def find_epg_source(source_id: str) -> dict | None:
    wanted = normalize_epg_id(source_id)
    return next(
        (
            item
            for item in epg_sources
            if normalize_epg_id(item.get("id", "")) == wanted
        ),
        None,
    )


def epg_sources_payload() -> list[dict]:
    payload = []
    for source in epg_sources:
        source_id = normalize_epg_id(source.get("id", ""))
        item = {
            "id": source_id,
            "name": str(source.get("name", "") or source_id),
            "source_label": friendly_source_label(str(source.get("url", ""))),
            "url_path": epg_public_url(source_id),
            "cached": epg_cache_path(source_id).exists(),
            "last_refresh": source.get("last_refresh"),
            "last_error": source.get("last_error"),
        }
        payload.append(item)
    return payload


def epg_builtin_payload() -> list[dict]:
    # EPG is the single user-facing XMLTV output. sports.xml remains an
    # internal/diagnostic endpoint for troubleshooting generated sports only.
    guides = (
        ("epg", "EPG", COMBINED_EPG_PATH, "/epg/epg.xml"),
    )
    payload: list[dict] = []
    for guide_id, name, path, url_path in guides:
        cached = path.exists() and path.is_file()
        last_refresh = None
        size_bytes = 0
        if cached:
            try:
                stat = path.stat()
                last_refresh = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
                size_bytes = int(stat.st_size)
            except OSError:
                cached = False
        payload.append(
            {
                "id": guide_id,
                "name": name,
                "source_label": "Built-in",
                "url_path": url_path,
                "cached": cached,
                "last_refresh": last_refresh,
                "size_bytes": size_bytes,
            }
        )
    return payload


def unique_epg_id(name: str) -> str:
    base = normalize_epg_id(name)
    existing = {normalize_epg_id(source.get("id", "")) for source in epg_sources}
    source_id = base
    index = 2
    while source_id in existing:
        source_id = f"{base}-{index}"
        index += 1
    return source_id


def add_epg_source(name: str, url: str) -> dict:
    clean_name = str(name or "").strip()
    clean_url = str(url or "").strip()
    if not clean_name:
        raise ValueError("EPG name is required.")
    if not clean_url.startswith(("http://", "https://")):
        raise ValueError("EPG URL must start with http:// or https://")
    source = {
        "id": unique_epg_id(clean_name),
        "name": clean_name,
        "url": clean_url,
        "last_refresh": None,
        "last_error": None,
    }
    epg_sources.append(source)
    save_config()
    return source


def delete_epg_source(source_id: str) -> bool:
    global epg_sources
    wanted = normalize_epg_id(source_id)
    before = len(epg_sources)
    epg_sources = [
        source
        for source in epg_sources
        if normalize_epg_id(source.get("id", "")) != wanted
    ]
    epg_cache_path(wanted).unlink(missing_ok=True)
    save_config()
    return len(epg_sources) != before


def download_epg_bytes(url: str, timeout: int = 90) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "M3U-Web-Picker/2.0",
            "Accept": "application/xml,text/xml,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        content_encoding = str(response.headers.get("Content-Encoding", "")).lower()
    if content_encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/")]
            if not members:
                raise ValueError("The EPG archive was empty.")
            raw = archive.read(members[0])
    if b"<tv" not in raw[:10000]:
        raise ValueError("The provider response did not look like XMLTV data.")
    return raw


def refresh_epg_source(source_id: str) -> tuple[bool, str]:
    source = find_epg_source(source_id)
    if not source:
        return False, "EPG source not found."
    try:
        raw = download_epg_bytes(str(source.get("url", "")))
        atomic_write_bytes(epg_cache_path(str(source.get("id", ""))), raw)
        source["last_refresh"] = datetime.now().astimezone().isoformat(timespec="seconds")
        source["last_error"] = None
        save_config()
        return True, f"Refreshed {source.get('name', 'EPG source')}."
    except Exception as exc:
        safe_error = redact_url_credentials(str(exc))
        source["last_error"] = safe_error
        save_config()
        return False, safe_error


def refresh_all_epg_sources() -> dict:
    results = []
    ok_count = 0
    for source in list(epg_sources):
        ok, message = refresh_epg_source(str(source.get("id", "")))
        ok_count += 1 if ok else 0
        results.append(
            {
                "id": source.get("id"),
                "name": source.get("name"),
                "ok": ok,
                "message": message,
            }
        )
    return {"count": len(results), "ok_count": ok_count, "results": results}


def public_epg_url(country_code: str) -> str:
    code = str(country_code or "").strip().lower()
    return f"https://iptv-epg.org/files/epg-{code}.xml.gz"


def public_epg_cache_path(country_code: str) -> Path:
    code = str(country_code or "").strip().upper()
    return PUBLIC_EPG_DIR / f"epg-{code.lower()}.xml.gz"


def public_epg_filtered_path(country_code: str) -> Path:
    code = str(country_code or "").strip().upper()
    return PUBLIC_EPG_DIR / f"epg-{code.lower()}.filtered.xml.gz"


def _public_epg_relevant_matchers() -> tuple[set[str], set[str]]:
    """Return the small subset of IPTV channels worth keeping from public guides.

    Every selected manual channel is eligible for public-guide fallback, not
    only sports channels. Sports Automation additionally keeps fixed team/network
    candidates for canonical-event corroboration. Event channels with blank
    tvg-id remain discoverable from their M3U names and do not require the giant
    public guide.
    """
    wanted_ids = set(selected_xmltv_ids())
    wanted_names: set[str] = set()
    for channel in channels:
        channel_id = str(channel.get("tvg_id", "") or "").strip()
        name_values = [str(channel.get("tvg_name", "") or "").strip(), str(channel.get("name", "") or "").strip()]
        provider_channel_id = channel.get("id")
        is_manual = provider_channel_id is not None and int(provider_channel_id) in selected_ids
        text = " ".join([str(channel.get("group", "") or ""), *name_values])
        is_sports_candidate = bool(
            sports._team_feed_identity(channel)
            or sports._detect_sport(text)
            or re.search(r"\b(?:espn|fox sports|fs1|fs2|tnt sports|cbs sports|nbc sports|mlb network|nfl network|nba tv|nhl network)\b", text, re.I)
        )
        if not (is_manual or is_sports_candidate):
            continue
        if channel_id:
            wanted_ids.add(channel_id)
        for value in name_values:
            normalized = sports._normalize(value)
            if normalized:
                wanted_names.add(normalized)
    return wanted_ids, wanted_names


def _public_epg_subset_signature(country_code: str, wanted_ids: set[str], wanted_names: set[str]) -> str:
    source = public_epg_cache_path(country_code)
    source_marker = f"{source.stat().st_mtime_ns}:{source.stat().st_size}" if source.exists() else "missing"
    digest = hashlib.sha256()
    digest.update(source_marker.encode("utf-8"))
    for value in sorted(wanted_ids):
        digest.update(b"\0i:")
        digest.update(value.encode("utf-8", errors="replace"))
    for value in sorted(wanted_names):
        digest.update(b"\0n:")
        digest.update(value.encode("utf-8", errors="replace"))
    return digest.hexdigest()


def _filter_public_epg_cache(country_code: str, *, cancel_check=None) -> tuple[bool, str]:
    """Create a compact gzip XMLTV subset from a large IPTV-EPG country cache.

    IPTV-EPG publishes pretty-printed XMLTV with channel/programme blocks on
    separate lines. A line-oriented first pass is dramatically faster than
    constructing ElementTree objects for ~1M unrelated programmes. Only the
    resulting compact gzip is handed to the normal XMLTV parser.
    """
    code = str(country_code or "").strip().upper()
    source = public_epg_cache_path(code)
    destination = public_epg_filtered_path(code)
    state = public_epg_state.setdefault(code, {})
    if not _gzip_xmltv_looks_valid(source):
        destination.unlink(missing_ok=True)
        return False, "Raw public EPG cache is unavailable."

    wanted_ids, wanted_names = _public_epg_relevant_matchers()
    signature = _public_epg_subset_signature(code, wanted_ids, wanted_names)
    if (
        destination.exists()
        and destination.stat().st_size > 0
        and state.get("filter_signature") == signature
        and _gzip_xmltv_looks_valid(destination)
    ):
        return True, f"{code} public EPG filtered cache is fresh."

    temp = destination.with_name(destination.name + ".tmp")
    temp.unlink(missing_ok=True)
    effective_ids = set(wanted_ids)
    id_re = re.compile(r'\b(?:id|channel)="([^"]+)"')
    kept_channels = 0
    kept_programmes = 0
    scanned_lines = 0

    def block_id(block: str) -> str:
        match = id_re.search(block)
        return match.group(1) if match else ""

    try:
        with gzip.open(source, "rt", encoding="utf-8", errors="replace") as input_handle, gzip.open(
            temp, "wt", encoding="utf-8", compresslevel=1
        ) as output_handle:
            output_handle.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            output_handle.write('<tv source-info-name="IPTV-EPG.org filtered" source-info-url="https://iptv-epg.org">\n')
            block_lines: list[str] = []
            block_kind = ""
            for line in input_handle:
                scanned_lines += 1
                if scanned_lines % 50000 == 0 and cancel_check and cancel_check():
                    raise sports.ScanCancelled()
                stripped = line.lstrip()
                if not block_kind:
                    if stripped.startswith("<channel "):
                        block_kind = "channel"
                        block_lines = [line]
                    elif stripped.startswith("<programme "):
                        block_kind = "programme"
                        block_lines = [line]
                    else:
                        continue
                else:
                    block_lines.append(line)

                closing = f"</{block_kind}>"
                if closing not in line:
                    continue

                block = "".join(block_lines)
                cid = block_id(block)
                keep = cid in effective_ids
                if block_kind == "channel" and not keep and wanted_names:
                    try:
                        element = sports.ElementTree.fromstring(block)
                        display_names = [
                            child.text.strip()
                            for child in element
                            if child.tag.rsplit("}", 1)[-1] == "display-name" and child.text
                        ]
                        keep = any(sports._normalize(name) in wanted_names for name in display_names)
                    except Exception:
                        keep = False
                    if keep and cid:
                        effective_ids.add(cid)
                if keep:
                    output_handle.write(block)
                    if block_kind == "channel":
                        kept_channels += 1
                    else:
                        kept_programmes += 1
                block_kind = ""
                block_lines = []
            output_handle.write("</tv>\n")
        if not _gzip_xmltv_looks_valid(temp):
            raise ValueError("Filtered public EPG was not valid gzip XMLTV.")
        temp.replace(destination)
        state["filter_signature"] = signature
        state["filtered_at"] = datetime.now().astimezone(ZoneInfo(master_timezone_name())).isoformat(timespec="seconds")
        state["filtered_channels"] = kept_channels
        state["filtered_programmes"] = kept_programmes
        state["filtered_bytes"] = destination.stat().st_size
        save_config()
        return True, f"Filtered {code} public EPG to {kept_channels:,} channels / {kept_programmes:,} programmes."
    except sports.ScanCancelled:
        temp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        state["filter_error"] = str(exc)
        save_config()
        return False, str(exc)


def public_epg_payload() -> dict:
    countries = []
    for code, name in PUBLIC_EPG_REGISTRY:
        path = public_epg_cache_path(code)
        state = dict(public_epg_state.get(code) or {})
        filtered = public_epg_filtered_path(code)
        countries.append({
            "code": code,
            "name": name,
            "enabled": code in public_epg_enabled_codes,
            "cached": path.exists() and path.stat().st_size > 0,
            "compressed_bytes": path.stat().st_size if path.exists() else 0,
            "filtered_bytes": filtered.stat().st_size if filtered.exists() else 0,
            "filtered_channels": int(state.get("filtered_channels") or 0),
            "filtered_programmes": int(state.get("filtered_programmes") or 0),
            "last_refresh": state.get("last_refresh"),
            "last_error": state.get("last_error") or state.get("filter_error"),
        })
    enabled_names = [item["name"] for item in countries if item["enabled"]]
    return {
        "provider": "IPTV-EPG",
        "compressed": True,
        "enabled_codes": sorted(public_epg_enabled_codes),
        "enabled_count": len(public_epg_enabled_codes),
        "summary": enabled_names[0] if len(enabled_names) == 1 else f"{len(enabled_names)} countries enabled",
        "countries": countries,
    }


def update_public_epg_countries(enabled_codes: Iterable[str]) -> dict:
    global public_epg_enabled_codes
    requested = {str(code).strip().upper() for code in enabled_codes if str(code).strip()}
    invalid = sorted(requested - PUBLIC_EPG_CODES)
    if invalid:
        raise ValueError(f"Unknown public EPG country code: {invalid[0]}")
    disabled = public_epg_enabled_codes - requested
    public_epg_enabled_codes = requested
    for code in disabled:
        public_epg_cache_path(code).unlink(missing_ok=True)
        public_epg_filtered_path(code).unlink(missing_ok=True)
        public_epg_state.pop(code, None)
    save_config()
    return public_epg_payload()


def _gzip_xmltv_looks_valid(path: Path) -> bool:
    try:
        with gzip.open(path, "rb") as handle:
            return b"<tv" in handle.read(65536)
    except Exception:
        return False


def refresh_public_epg_source(country_code: str, *, force: bool = False, cancel_check=None) -> tuple[bool, str]:
    code = str(country_code or "").strip().upper()
    if code not in PUBLIC_EPG_CODES:
        return False, "Unknown public EPG country."
    if code not in public_epg_enabled_codes:
        return False, "Public EPG country is disabled."

    timezone = ZoneInfo(master_timezone_name())
    local_now = datetime.now().astimezone(timezone)
    state = public_epg_state.setdefault(code, {})
    path = public_epg_cache_path(code)
    if not force and path.exists() and _date_from_iso(state.get("last_refresh")) == local_now.date().isoformat():
        return True, f"{code} public EPG cache is fresh."

    temp = path.with_name(path.name + ".tmp")
    temp.unlink(missing_ok=True)
    request = urllib.request.Request(
        public_epg_url(code),
        headers={
            "User-Agent": "M3U-Web-Picker/22.1",
            "Accept": "application/gzip,application/octet-stream,*/*",
            "Accept-Encoding": "identity",
        },
    )
    total = 0
    deadline = time.monotonic() + PUBLIC_EPG_DOWNLOAD_TIMEOUT_SECONDS
    try:
        with urllib.request.urlopen(
            request,
            timeout=min(PUBLIC_EPG_SOCKET_TIMEOUT_SECONDS, PUBLIC_EPG_DOWNLOAD_TIMEOUT_SECONDS),
        ) as response, temp.open("wb") as output:
            while True:
                if cancel_check and cancel_check():
                    raise sports.ScanCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Public EPG download exceeded the 180-second time limit.")
                try:
                    response.fp.raw._sock.settimeout(
                        max(0.1, min(PUBLIC_EPG_SOCKET_TIMEOUT_SECONDS, remaining))
                    )
                except (AttributeError, OSError):
                    # urllib wrappers used by tests/proxies may not expose the
                    # socket; urlopen's bounded socket timeout still applies.
                    pass
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PUBLIC_EPG_COMPRESSED_BYTES:
                    raise ValueError("Compressed public EPG exceeded the configured safety limit.")
                output.write(chunk)
        if total == 0 or not _gzip_xmltv_looks_valid(temp):
            raise ValueError("Public EPG response was not a valid gzip XMLTV guide.")
        temp.replace(path)
        state["last_refresh"] = local_now.isoformat(timespec="seconds")
        state["last_error"] = None
        state.pop("filter_error", None)
        state["compressed_bytes"] = total
        save_config()
        return True, f"Refreshed {code} public EPG ({total:,} compressed bytes)."
    except sports.ScanCancelled:
        temp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        temp.unlink(missing_ok=True)
        state["last_error"] = str(exc)
        save_config()
        return False, str(exc)


def refresh_public_epg_sources(*, force: bool = False, cancel_check=None) -> dict:
    results = []
    ok_count = 0
    registry_order = [code for code, _name in PUBLIC_EPG_REGISTRY]
    for code in registry_order:
        if code not in public_epg_enabled_codes:
            continue
        ok, message = refresh_public_epg_source(code, force=force, cancel_check=cancel_check)
        if ok:
            filtered_ok, filtered_message = _filter_public_epg_cache(code, cancel_check=cancel_check)
            ok = filtered_ok
            message = f"{message} {filtered_message}"
        ok_count += 1 if ok else 0
        results.append({"code": code, "ok": ok, "message": message})
    return {"count": len(results), "ok_count": ok_count, "results": results}


def active_public_epg_paths() -> list[Path]:
    paths = []
    for code, _name in PUBLIC_EPG_REGISTRY:
        if code not in public_epg_enabled_codes:
            continue
        filtered = public_epg_filtered_path(code)
        if _valid_xmltv_file(filtered):
            paths.append(filtered)
    return paths


def configured_epg_fallback_paths(base_path: Path | None = None) -> list[Path]:
    output: list[Path] = []
    seen = {str(base_path.resolve())} if base_path and base_path.exists() else set()
    for source in epg_sources:
        candidate = epg_cache_path(str(source.get("id", "")))
        if not _valid_xmltv_file(candidate):
            continue
        resolved = str(candidate.resolve())
        if resolved not in seen:
            seen.add(resolved)
            output.append(candidate)
    for candidate in active_public_epg_paths():
        resolved = str(candidate.resolve())
        if resolved not in seen:
            seen.add(resolved)
            output.append(candidate)
    return output


def _valid_xmltv_file(path: Path | None) -> bool:
    if not path or not path.exists() or path.stat().st_size == 0:
        return False
    try:
        if path.suffix.lower() == ".gz":
            return _gzip_xmltv_looks_valid(path)
        return b"<tv" in path.read_bytes()[:10000]
    except Exception:
        return False


def active_base_epg_path() -> Path | None:
    # Prefer the automatically derived Xtream guide. A manually configured EPG
    # source is a fallback for providers whose playlist URL does not expose the
    # conventional xmltv.php endpoint.
    if _valid_xmltv_file(EPG_CACHE_PATH):
        return EPG_CACHE_PATH
    for source in epg_sources:
        candidate = epg_cache_path(str(source.get("id", "")))
        if _valid_xmltv_file(candidate):
            return candidate
    return None


def _latest_generated_timestamp() -> float:
    rows = sports.generated_rows(DB_PATH, include_cached=True)
    values = []
    for row in rows:
        try:
            values.append(datetime.fromisoformat(str(row.get("generated_at", ""))).timestamp())
        except Exception:
            pass
    return max(values, default=0.0)


def guide_export_needs_rebuild(path: Path, *, combined: bool = False) -> bool:
    rows = sports.generated_rows(DB_PATH)
    if not path.exists():
        return True
    try:
        payload = path.read_bytes()
    except Exception:
        return True
    marker_present = b"m3u-picker-sports-" in payload
    if rows and not marker_present:
        return True
    if not rows and marker_present:
        return True
    newest_input = _latest_generated_timestamp()
    base_path = active_base_epg_path() if combined else None
    if base_path and base_path.exists():
        newest_input = max(newest_input, base_path.stat().st_mtime)
    if combined:
        for fallback_path in configured_epg_fallback_paths(base_path):
            if fallback_path.exists():
                newest_input = max(newest_input, fallback_path.stat().st_mtime)
    if combined and PLAYLIST_PATH.exists():
        newest_input = max(newest_input, PLAYLIST_PATH.stat().st_mtime)
    return bool(newest_input and path.stat().st_mtime + 0.001 < newest_input)


def ensure_epg_exports_current(*, force: bool = False) -> None:
    if not force and not (
        guide_export_needs_rebuild(SPORTS_EPG_PATH)
        or guide_export_needs_rebuild(COMBINED_EPG_PATH, combined=True)
    ):
        return
    sports.rebuild_epg_exports(
        DB_PATH,
        base_epg_path=active_base_epg_path(),
        base_channel_ids=selected_xmltv_ids(),
        fallback_epg_paths=configured_epg_fallback_paths(active_base_epg_path()),
        sports_epg_path=SPORTS_EPG_PATH,
        combined_epg_path=COMBINED_EPG_PATH,
    )


def refresh_master_from_url(cancel_check=None) -> tuple[bool, str]:
    global channels, last_source_url, last_refresh, source_mode
    primary = primary_provider_source()
    if not primary:
        return False, "No source URL configured."
    ok, message, parsed = refresh_provider_source(primary, cancel_check=cancel_check)
    if not ok:
        return False, message
    with state_lock:
        channels = parsed
        last_source_url = provider_playlist_url(primary)
        last_refresh = primary.get("last_refresh")
        source_mode = "url"
        sports.discover_catalog_from_channels(DB_PATH, channels)
        apply_saved_selections_to_loaded_channels()
        save_config()
        write_current_playlist()
    return True, f"Refreshed {len(channels)} channels."


def request_sports_scan_cancel() -> tuple[bool, str]:
    state = sports.scan_state(DB_PATH)
    if not state.get("running"):
        return False, "No sports update is currently running."
    if str(state.get("trigger") or "manual") != "manual":
        return False, "Scheduled sports updates cannot be cancelled from the browser."
    scan_cancel_event.set()
    sports.update_scan_stage(DB_PATH, "Cancellation requested")
    return True, "Cancellation requested. The scan will stop at the next safe checkpoint."


SPORTS_AUTOMATION_CYCLE_ORDER = [
    "schedule_api",
    "provider_refresh",
    "epg_refresh",
    "sports_scan_match",
    "channel_build",
    "epg_publish",
    "m3u_publish",
]


def validate_sports_cycle_trace(actual: list[str]) -> dict:
    expected = list(SPORTS_AUTOMATION_CYCLE_ORDER)
    actual = list(actual or [])
    return {
        "ok": actual == expected,
        "expected_order": expected,
        "actual_order": actual,
    }


def run_sports_scan(*, trigger: str = "manual", refresh_source: bool = True) -> dict:
    settings = sports.get_settings(DB_PATH)
    if not settings.get("enabled"):
        raise SportsScanError("Turn on Sports Automation before updating sports channels.")
    if not channels:
        raise SportsScanError("Load an M3U source before updating sports channels.")
    if not scan_lock.acquire(blocking=False):
        raise SportsScanError("A sports update is already running.")
    scan_cancel_event.clear()
    scan_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    failure_recorded = False
    scan_state_started = False
    provider_warnings: list[str] = []
    cycle_trace: list[str] = []
    cancel_check = scan_cancel_event.is_set if trigger == "manual" else None
    try:
        sports.begin_scan_state(
            DB_PATH,
            trigger=trigger,
            started_at=scan_started_at,
            stage="Starting sports update",
        )
        scan_state_started = True
        if scan_cancel_event.is_set():
            raise sports.ScanCancelled()

        # One Sports Automation cycle owns the complete dependency chain. The
        # optional canonical schedule refresh runs first; when disabled or not
        # configured this stage is a no-op and the legacy provider-derived
        # discovery path remains in use.
        sports.update_scan_stage(DB_PATH, "1/6 Checking schedule API cache")
        schedule_api_result = sports.refresh_schedule_api_if_due(
            DB_PATH,
            cancel_check=cancel_check,
        )
        cycle_trace.append("schedule_api")

        if source_mode == "url" and primary_provider_source():
            if refresh_source:
                sports.update_scan_stage(DB_PATH, "2/6 Refreshing provider channels")
                refreshed, message = refresh_master_from_url(cancel_check)
                if not refreshed:
                    sports.record_scan_failure(
                        DB_PATH,
                        "Provider playlist refresh failed.",
                        trigger,
                        started_at=scan_started_at,
                    )
                    failure_recorded = True
                    print("Provider refresh failed during sports update.")
                    raise SportsScanError(
                        "Could not refresh the primary provider playlist. Existing sports channels were kept."
                    )

            # Even when the scheduler already refreshed the primary at the
            # master-playlist boundary, fallback caches still need their own
            # refresh before this sports scan.
            for source in [item for item in provider_sources if item.get("role") == "fallback"]:
                sports.update_scan_stage(DB_PATH, f"Refreshing fallback: {source.get('name', 'Provider')}")
                ok, fallback_message, _parsed = refresh_provider_source(source, cancel_check=cancel_check)
                if not ok:
                    provider_warnings.append(
                        f"{source.get('name', 'Fallback provider')}: {fallback_message}"
                    )

        cycle_trace.append("provider_refresh")

        # EPG is enrichment. A failed XMLTV refresh falls back to the cached EPG
        # and then to M3U-only matching; it does not delete yesterday's channels.
        if provider_sources:
            sports.update_scan_stage(DB_PATH, "3/6 Refreshing provider guide data")
            for source in provider_sources:
                if not provider_xmltv_url(source):
                    continue
                ok, epg_message = refresh_provider_epg(source, cancel_check=cancel_check)
                if not ok and source.get("role") == "fallback":
                    provider_warnings.append(
                        f"{source.get('name', 'Fallback provider')} guide: {epg_message}"
                    )
        elif last_source_url:
            sports.update_scan_stage(DB_PATH, "3/6 Refreshing guide data")
            sports.refresh_epg_cache(last_source_url, EPG_CACHE_PATH, cancel_check=cancel_check)

        # When Sports Automation owns the cycle, user-configured guide sources
        # refresh in the same dependency chain instead of on a separate clock.
        if epg_sources:
            sports.update_scan_stage(DB_PATH, "Refreshing configured guide sources")
            epg_result = refresh_all_epg_sources()
            failed_epg = [item for item in epg_result.get("results", []) if not item.get("ok")]
            for item in failed_epg:
                provider_warnings.append(f"{item.get('name', 'EPG source')}: {item.get('message', 'refresh failed')}")

        if public_epg_enabled_codes:
            sports.update_scan_stage(DB_PATH, "Refreshing selected public EPG countries")
            public_result = refresh_public_epg_sources(cancel_check=cancel_check)
            for item in public_result.get("results", []):
                if not item.get("ok"):
                    provider_warnings.append(
                        f"Public EPG {item.get('code', '')}: {item.get('message', 'refresh failed')}"
                    )
        if scan_cancel_event.is_set():
            raise sports.ScanCancelled()
        cycle_trace.append("epg_refresh")

        provider_sets = sports_provider_channel_sets()
        sports_channels = [channel for _source, source_channels in provider_sets for channel in source_channels]
        provider_epg_sources: list[tuple[Path, list[dict]]] = []
        for source, source_channels in provider_sets:
            candidate = provider_epg_cache_path(str(source.get("id", "")))
            if source.get("role") == "primary" and not _valid_xmltv_file(candidate):
                candidate = active_base_epg_path() or candidate
            if _valid_xmltv_file(candidate):
                provider_epg_sources.append((candidate, source_channels))

        # Public country guides are system-wide Combined-XMLTV fallbacks and
        # also last-resort sports corroboration. Match their XMLTV IDs/names
        # against the primary IPTV catalog, but never let them replace provider
        # guide metadata when both exist.
        primary_channels = provider_sets[0][1] if provider_sets else list(channels)
        existing_paths = {str(path.resolve()) for path, _channels in provider_epg_sources if path.exists()}
        for configured in epg_sources:
            configured_path = epg_cache_path(str(configured.get("id", "")))
            if _valid_xmltv_file(configured_path) and str(configured_path.resolve()) not in existing_paths:
                provider_epg_sources.append((configured_path, primary_channels))
                existing_paths.add(str(configured_path.resolve()))
        for public_path in active_public_epg_paths():
            if str(public_path.resolve()) not in existing_paths:
                provider_epg_sources.append((public_path, primary_channels))
                existing_paths.add(str(public_path.resolve()))

        sports.update_scan_stage(DB_PATH, "4/6 Preparing sports match indexes")
        sports.discover_catalog_from_channels(DB_PATH, sports_channels)
        if scan_cancel_event.is_set():
            raise sports.ScanCancelled()
        sports.update_scan_stage(DB_PATH, "4/6 Matching events and building channels")
        result = sports.scan_channels(
            DB_PATH,
            sports_channels,
            active_base_epg_path(),
            provider_epg_sources=provider_epg_sources,
            sports_epg_path=SPORTS_EPG_PATH,
            combined_epg_path=COMBINED_EPG_PATH,
            trigger=trigger,
            started_at=scan_started_at,
            base_channel_ids=selected_xmltv_ids(),
            fallback_epg_paths=configured_epg_fallback_paths(active_base_epg_path()),
            manual_channel_count=len(selected_channels_from_selected_ids_in_order()),
            cancel_check=cancel_check,
        )
        cycle_trace.extend(result.get("pipeline_trace") or [])
        result["provider_warnings"] = provider_warnings
        result["schedule_api"] = schedule_api_result
        if provider_warnings:
            result["message"] += f" {len(provider_warnings)} fallback provider warning{'s' if len(provider_warnings) != 1 else ''}."
        sports.update_scan_stage(DB_PATH, "5/6 Publishing M3U and XMLTV")
        write_current_playlist()
        cycle_trace.append("m3u_publish")
        guide_check = sports.validate_guide_exports(
            DB_PATH,
            playlist_path=PLAYLIST_PATH,
            sports_epg_path=SPORTS_EPG_PATH,
            combined_epg_path=COMBINED_EPG_PATH,
        )
        result["guide_check"] = guide_check
        sports.update_scan_stage(DB_PATH, "6/6 Validating automation cycle")
        order_check = validate_sports_cycle_trace(cycle_trace)
        expected_cycle = order_check["expected_order"]
        order_ok = order_check["ok"]
        cycle_steps = [
            {
                "name": "schedule_api",
                "ok": True,
                "mode": "api" if schedule_api_result.get("used") else "legacy",
                "warning": schedule_api_result.get("warning", ""),
            },
            {"name": "provider_refresh", "ok": "provider_refresh" in cycle_trace},
            {"name": "epg_refresh", "ok": "epg_refresh" in cycle_trace},
            {"name": "sports_scan_match", "ok": "sports_scan_match" in cycle_trace},
            {"name": "channel_build", "ok": "channel_build" in cycle_trace},
            {"name": "epg_publish", "ok": "epg_publish" in cycle_trace},
            {"name": "m3u_publish", "ok": bool(guide_check.get("ok")) and "m3u_publish" in cycle_trace},
        ]
        result["cycle_check"] = {
            "ok": order_ok and all(step["ok"] for step in cycle_steps),
            "order_ok": order_ok,
            "expected_order": expected_cycle,
            "actual_order": cycle_trace,
            "steps": cycle_steps,
        }
        if schedule_api_result.get("warning"):
            result["message"] += " Schedule API refresh failed; cached schedule or legacy matching was used."
        if not guide_check["ok"]:
            print(f"Generated sports guide validation failed: {guide_check}")
        return result
    except sports.ScanCancelled as exc:
        sports.record_scan_cancelled(
            DB_PATH,
            trigger=trigger,
            started_at=scan_started_at,
        )
        raise SportsScanCancelled(str(exc) or "Sports update cancelled.") from exc
    except SportsScanError:
        raise
    except Exception as exc:
        # Keep credentials and Python internals out of the browser. In debug mode,
        # print the complete traceback to Docker logs so parser failures are
        # actionable. Production logs keep the message compact.
        debug_enabled = str(os.environ.get("FLASK_DEBUG", "")).lower() in {
            "1",
            "true",
            "yes",
            "on",
        } or str(os.environ.get("M3U_DEBUG", "")).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if debug_enabled:
            print("Unexpected sports update traceback:")
            traceback.print_exc()
        else:
            print(f"Sports update failed ({type(exc).__name__}).")
        if not failure_recorded:
            sports.record_scan_failure(
                DB_PATH,
                "Sports update failed. Existing sports channels were kept.",
                trigger,
                started_at=scan_started_at,
            )
            failure_recorded = True
        raise SportsScanError(
            "Sports update failed. Existing sports channels were kept."
        ) from exc
    finally:
        try:
            if scan_state_started:
                sports.finish_scan_state(DB_PATH)
        except Exception as exc:
            print(f"Could not finalize persistent sports scan status: {type(exc).__name__}.")
        finally:
            scan_cancel_event.clear()
            scan_lock.release()


def sports_numbering_adjustment() -> dict:
    settings = sports.get_settings(DB_PATH)
    configured_start = int(settings.get("start_channel", 1000))
    manual_count = len(selected_channels_from_selected_ids_in_order())
    effective_start = sports.effective_start_channel(configured_start, manual_count)
    return {
        "configured_start_channel": configured_start,
        "effective_start_channel": effective_start,
        "manual_channel_count": manual_count,
        "overlap_count": max(0, manual_count - configured_start + 1),
        "auto_shifted": effective_start != configured_start,
    }


def sports_number_conflicts() -> int:
    return int(sports_numbering_adjustment()["overlap_count"])


def enrich_sports_status(payload: dict) -> dict:
    adjustment = sports_numbering_adjustment()
    payload["number_conflicts"] = adjustment["overlap_count"]
    payload["numbering_adjustment"] = adjustment
    settings = dict(payload.get("settings") or sports.get_settings(DB_PATH))
    settings["start_channel"] = adjustment["effective_start_channel"]
    payload["numbering"] = sports.numbering_plan(settings)
    payload["numbering"]["configured_start_channel"] = adjustment["configured_start_channel"]
    payload["numbering"]["effective_start_channel"] = adjustment["effective_start_channel"]
    payload["numbering"]["manual_channel_count"] = adjustment["manual_channel_count"]
    payload["numbering"]["auto_shifted"] = adjustment["auto_shifted"]
    return payload


def _date_from_iso(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except Exception:
        return ""


def _run_dvr_maintenance() -> dict:
    if dvr.settings().get("processing_policy") != "scheduled":
        return {
            "checked": 0,
            "converted": 0,
            "moved": 0,
            "commercials_removed": 0,
            "skipped": 0,
            "failed": 0,
            "skipped_policy": True,
        }
    try:
        maintenance = dvr.nightly_maintenance(DB_PATH)
        if maintenance.get("checked") or maintenance.get("error"):
            print(f"DVR update maintenance: {maintenance}")
        return maintenance
    except Exception as exc:
        print(f"DVR update maintenance failed: {type(exc).__name__}.")
        return {
            "checked": 0,
            "converted": 0,
            "commercials_removed": 0,
            "skipped": 0,
            "failed": 0,
            "error": f"DVR maintenance could not run ({type(exc).__name__}).",
        }


def run_master_update(*, trigger: str = "manual") -> dict:
    """Run the one dependency-ordered application update cycle.

    Sports Automation is an optional stage. Provider/EPG/public-guide refreshes
    still run when sports is disabled, so Jellyfin/Plex can rely on one daily
    application schedule and one Combined XMLTV URL.
    """
    global last_master_update, last_master_duration_seconds, last_master_trigger

    trigger = str(trigger or "manual").strip() or "manual"
    timezone = ZoneInfo(master_timezone_name())
    started_at = datetime.now().astimezone(timezone)
    started_monotonic = time.monotonic()
    master_update_runtime.update({
        "running": True,
        "started_at": started_at.isoformat(timespec="seconds"),
        "trigger": trigger,
        "started_monotonic": started_monotonic,
    })
    dvr_maintenance_ran = False

    try:
        sports_settings = sports.get_settings(DB_PATH)
        if sports_settings.get("enabled"):
            result = run_sports_scan(trigger=trigger, refresh_source=True)
        else:
            warnings: list[str] = []
            trace = ["schedule_api"]  # Disabled sports/API is an intentional no-op stage.
            if source_mode == "url" and primary_provider_source():
                ok, message = refresh_master_from_url()
                if not ok:
                    raise SportsScanError("Could not refresh the primary provider playlist. Existing outputs were kept.")
            trace.append("provider_refresh")

            if provider_sources:
                for source in provider_sources:
                    if source.get("role") != "primary" or not provider_xmltv_url(source):
                        continue
                    ok, message = refresh_provider_epg(source)
                    if not ok:
                        warnings.append(f"{source.get('name', 'Primary provider')} guide: {message}")
            elif last_source_url:
                try:
                    sports.refresh_epg_cache(last_source_url, EPG_CACHE_PATH)
                except Exception as exc:
                    warnings.append(f"Provider guide: {redact_url_credentials(str(exc))}")

            if epg_sources:
                epg_result = refresh_all_epg_sources()
                for item in epg_result.get("results", []):
                    if not item.get("ok"):
                        warnings.append(f"{item.get('name', 'EPG source')}: {item.get('message', 'refresh failed')}")
            public_result = refresh_public_epg_sources()
            for item in public_result.get("results", []):
                if not item.get("ok"):
                    warnings.append(f"Public EPG {item.get('code', '')}: {item.get('message', 'refresh failed')}")
            trace.append("epg_refresh")

            base_path = active_base_epg_path()
            sports.rebuild_epg_exports(
                DB_PATH,
                base_epg_path=base_path,
                base_channel_ids=selected_xmltv_ids(),
                fallback_epg_paths=configured_epg_fallback_paths(base_path),
                sports_epg_path=SPORTS_EPG_PATH,
                combined_epg_path=COMBINED_EPG_PATH,
            )
            trace.extend(["sports_scan_match", "channel_build", "epg_publish"])
            write_current_playlist()
            trace.append("m3u_publish")
            order_check = validate_sports_cycle_trace(trace)
            result = {
                "ok": True,
                "message": "Master update completed; Sports Automation is disabled.",
                "provider_warnings": warnings,
                "cycle_check": {
                    "ok": bool(order_check.get("ok")),
                    "order_ok": bool(order_check.get("ok")),
                    "expected_order": order_check.get("expected_order"),
                    "actual_order": trace,
                },
            }

        result["dvr_maintenance"] = _run_dvr_maintenance()
        dvr_maintenance_ran = True
        finished_at = datetime.now().astimezone(timezone)
        last_master_update = finished_at.isoformat(timespec="seconds")
        last_master_duration_seconds = round(max(0.0, time.monotonic() - started_monotonic), 1)
        last_master_trigger = trigger
        save_config()
        result["master_update"] = master_update_payload()
        return result
    finally:
        if not dvr_maintenance_ran:
            _run_dvr_maintenance()
        master_update_runtime.update({
            "running": False,
            "started_at": None,
            "trigger": None,
            "started_monotonic": None,
        })


def _master_update_due(now: datetime) -> bool:
    if not master_auto_update:
        return False
    timezone = ZoneInfo(master_timezone_name())
    local_now = now.astimezone(timezone)
    hour, minute = [int(part) for part in master_refresh_time.split(":", 1)]
    if (local_now.hour, local_now.minute) != (hour, minute):
        return False
    if last_master_update:
        try:
            last = datetime.fromisoformat(last_master_update)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone)
            if last.astimezone(timezone).date() == local_now.date():
                return False
        except Exception:
            pass
    return True


def scheduler_loop() -> None:
    last_backup_date = ""
    last_master_attempt_key = ""
    while True:
        try:
            now = datetime.now().astimezone()

            if sports.purge_expired_disabled_cache(DB_PATH, now):
                base_path = active_base_epg_path()
                sports.rebuild_epg_exports(
                    DB_PATH,
                    base_epg_path=base_path,
                    base_channel_ids=selected_xmltv_ids(),
                    fallback_epg_paths=configured_epg_fallback_paths(base_path),
                    sports_epg_path=SPORTS_EPG_PATH,
                    combined_epg_path=COMBINED_EPG_PATH,
                )
                write_current_playlist()

            # Full provider/API refresh remains on the master schedule. This is
            # only a cheap lifecycle cleanup so completed sports channels do not
            # sit blank in Jellyfin for the rest of the day.
            if not sports.scan_state(DB_PATH, now).get("running"):
                stale_removed = sports.purge_stale_generated(DB_PATH, now)
                if stale_removed:
                    base_path = active_base_epg_path()
                    sports.rebuild_epg_exports(
                        DB_PATH,
                        base_epg_path=base_path,
                        base_channel_ids=selected_xmltv_ids(),
                        fallback_epg_paths=configured_epg_fallback_paths(base_path),
                        sports_epg_path=SPORTS_EPG_PATH,
                        combined_epg_path=COMBINED_EPG_PATH,
                    )
                    write_current_playlist()

            if _master_update_due(now):
                local_now = now.astimezone(ZoneInfo(master_timezone_name()))
                attempt_key = local_now.strftime("%Y-%m-%dT%H:%M")
                if attempt_key != last_master_attempt_key:
                    last_master_attempt_key = attempt_key
                    try:
                        run_master_update(trigger="scheduled")
                    except Exception as exc:
                        print(f"Scheduled master update failed: {exc}")

            backup_enabled = os.environ.get("M3U_BACKUP_ENABLED", "false").lower() in {
                "1", "true", "yes", "on"
            }
            backup_time = os.environ.get("BACKUP_TIME", "03:15")
            backup_timezone_name = os.environ.get("BACKUP_TIMEZONE", "America/New_York")
            try:
                backup_hour, backup_minute = [int(part) for part in backup_time.split(":", 1)]
                backup_now = now.astimezone(ZoneInfo(backup_timezone_name))
            except Exception:
                backup_hour, backup_minute = 3, 15
                backup_now = now.astimezone(ZoneInfo("America/New_York"))
            backup_date = backup_now.date().isoformat()
            if (
                backup_enabled
                and (backup_now.hour, backup_now.minute) == (backup_hour, backup_minute)
                and last_backup_date != backup_date
            ):
                backup_dir = Path(os.environ.get("M3U_BACKUP_CONTAINER_DIR", "/backups"))
                retention = int(os.environ.get("BACKUP_RETENTION_DAYS", "30"))
                create_database_backup(DB_PATH, backup_dir, retention)
                last_backup_date = backup_date
        except Exception as exc:
            print(f"Scheduler error: {exc}")
        time.sleep(30)


def dvr_scheduler_loop() -> None:
    last_series_sync = 0.0
    while True:
        try:
            now_monotonic = time.monotonic()
            if now_monotonic - last_series_sync >= 60:
                last_series_sync = now_monotonic
                if dvr.has_enabled_series_rules(DB_PATH):
                    dvr_timezone = str(
                        sports.get_settings(DB_PATH).get("timezone")
                        or "America/New_York"
                    )
                    dvr.sync_series_rules(
                        DB_PATH,
                        channels=curated_channels_for_guide(),
                        epg_path=COMBINED_EPG_PATH,
                        timezone_name=dvr_timezone,
                    )
            dvr.tick(DB_PATH)
            lab_control = commercial_lab_rotation.control(DB_PATH)
            if lab_control["enabled"]:
                commercial_lab_rotation.ensure_capacity(
                    DB_PATH,
                    curated_channels_for_guide(),
                    dvr.schedule_recording,
                    current=lab_control,
                )
                dvr.tick(DB_PATH)
        except Exception as exc:
            print(f"DVR scheduler failed: {exc}")
        time.sleep(5)


def commercial_lab_processing_loop() -> None:
    """Immediately drain completed lab captures without using DVR maintenance."""
    retry_after: dict[int, float] = {}
    script_path = APP_DIR.parent / "scripts" / "commercial_lab.py"
    recordings_root = Path(os.environ.get("M3U_DVR_CONTAINER_DIR", "/recordings"))
    while True:
        try:
            now_monotonic = time.monotonic()
            retry_after = {
                recording_id: deadline
                for recording_id, deadline in retry_after.items()
                if deadline > now_monotonic
            }
            lab_control = commercial_lab_rotation.control(DB_PATH)
            if not lab_control["enabled"]:
                time.sleep(2)
                continue
            recording_id = commercial_lab_rotation.next_completed_recording(
                DB_PATH,
                excluded_ids=set(retry_after),
            )
            if recording_id is None:
                time.sleep(2)
                continue
            result = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "process",
                    "--db",
                    str(DB_PATH),
                    "--recordings-root",
                    str(recordings_root),
                    "--recording-id",
                    str(recording_id),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                print(f"Commercial lab processed DVR recording {recording_id}: {result.stdout.strip()}")
                retry_after.pop(recording_id, None)
            else:
                retry_after[recording_id] = time.monotonic() + 300
                detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
                print(f"Commercial lab processing failed for DVR recording {recording_id}: {detail}")
        except Exception as exc:
            print(f"Commercial lab worker failed: {type(exc).__name__}: {exc}")
            time.sleep(2)

def start_scheduler_once() -> None:
    global scheduler_started
    if scheduler_started or os.environ.get("M3U_DISABLE_SCHEDULER", "").lower() in {"1", "true", "yes"}:
        return
    # Flask's debug reloader imports the app twice. Skip the parent process and
    # start the scheduler only inside the serving child.
    if os.environ.get("FLASK_DEBUG") == "1" and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return
    scheduler_started = True
    threading.Thread(target=scheduler_loop, daemon=True, name="m3u-scheduler").start()
    threading.Thread(target=dvr_scheduler_loop, daemon=True, name="m3u-dvr-scheduler").start()
    threading.Thread(
        target=commercial_lab_processing_loop,
        daemon=True,
        name="m3u-commercial-lab-worker",
    ).start()


restore_config()


def load_cached_master_playlist_on_startup() -> None:
    global channels
    db_connect().close()
    dvr.init_db(DB_PATH)
    recovered_dvr = dvr.recover_interrupted(DB_PATH)
    if recovered_dvr:
        print(f"Recovered {recovered_dvr} interrupted DVR recording(s).")
    if sports.recover_interrupted_scan(DB_PATH):
        print("Recovered an interrupted sports scan state from the previous app process.")
    # Do the same cheap lifecycle cleanup used by the scheduler before serving
    # cached outputs after a restart. Finished games should not reappear as
    # blank channels for the first scheduler interval.
    sports.purge_stale_generated(DB_PATH)
    if not MASTER_CACHE_PATH.exists():
        write_current_playlist()
        try:
            sports.rebuild_epg_exports(
                DB_PATH,
                base_epg_path=active_base_epg_path(),
                base_channel_ids=selected_xmltv_ids(),
                fallback_epg_paths=configured_epg_fallback_paths(active_base_epg_path()),
                sports_epg_path=SPORTS_EPG_PATH,
                combined_epg_path=COMBINED_EPG_PATH,
            )
        except Exception as exc:
            print(f"Startup sports guide rebuild failed: {exc}")
        return
    try:
        text = MASTER_CACHE_PATH.read_text(encoding="utf-8-sig", errors="replace")
        channels = parse_m3u_text(text)
        primary = primary_provider_source()
        if primary:
            primary["channel_count"] = len(channels)
            if not primary.get("last_refresh"):
                primary["last_refresh"] = last_refresh
            save_config()
        sports.discover_catalog_from_channels(DB_PATH, channels)
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
        sports.rebuild_epg_exports(
            DB_PATH,
            base_epg_path=active_base_epg_path(),
            base_channel_ids=selected_xmltv_ids(),
            fallback_epg_paths=configured_epg_fallback_paths(active_base_epg_path()),
            sports_epg_path=SPORTS_EPG_PATH,
            combined_epg_path=COMBINED_EPG_PATH,
        )
    except Exception as exc:
        print(f"Startup cache load failed: {exc}")


load_cached_master_playlist_on_startup()
start_scheduler_once()
