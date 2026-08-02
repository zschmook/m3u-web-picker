#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import traceback
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Iterable, List

import sports
from backup import create_database_backup


APP_DIR = Path(__file__).resolve().parent
EXPORT_DIR = APP_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = APP_DIR / "m3u_picker.db"
CONFIG_PATH = APP_DIR / "config.json"
MASTER_CACHE_PATH = APP_DIR / "master_playlist_cache.m3u"
EPG_CACHE_PATH = APP_DIR / "epg_cache.xml"
SPORTS_EPG_PATH = EXPORT_DIR / "sports.xml"
COMBINED_EPG_PATH = EXPORT_DIR / "combined.xml"

PLAYLIST_NAME = "custom.m3u"
PLAYLIST_PATH = EXPORT_DIR / PLAYLIST_NAME
PORT = int(os.environ.get("M3U_PORT", "9999"))
DEV_PORT = int(os.environ.get("M3U_DEV_PORT", "9998"))

SCHEDULE_HOUR = int(os.environ.get("MASTER_REFRESH_HOUR", "3"))
SCHEDULE_MINUTE = int(os.environ.get("MASTER_REFRESH_MINUTE", "0"))

channels: List[dict] = []
selected_ids: set[int] = set()
last_source_url = ""
last_refresh: str | None = None
source_mode = ""
scheduler_started = False

state_lock = threading.RLock()
scan_lock = threading.Lock()


class SportsScanError(RuntimeError):
    """Safe, user-facing failure for an explicit sports update."""


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
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS selections (
            key TEXT PRIMARY KEY,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL,
            sort_order INTEGER
        )
        """
    )
    try:
        conn.execute("ALTER TABLE selections ADD COLUMN sort_order INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS group_channels (
            group_id INTEGER NOT NULL,
            channel_key TEXT NOT NULL,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL,
            PRIMARY KEY (group_id, channel_key),
            FOREIGN KEY (group_id) REFERENCES custom_groups(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()
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


def channel_key(channel: dict) -> str:
    return str(channel.get("url", "")).strip()


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
                    (key, name, group_title, url, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    key,
                    channel.get("name", ""),
                    channel.get("group", ""),
                    channel.get("url", ""),
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
    saved_keys = load_selected_keys_from_db()
    selected_ids = {
        int(channel["id"])
        for channel in channels
        if channel_key(channel) in saved_keys
    }


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config() -> None:
    data = {
        "source_url": last_source_url,
        "source_mode": source_mode,
        "last_refresh": last_refresh,
        "schedule": {"hour": SCHEDULE_HOUR, "minute": SCHEDULE_MINUTE},
    }
    atomic_write_text(CONFIG_PATH, json.dumps(data, indent=2))


def restore_config() -> None:
    global last_source_url, source_mode, last_refresh
    data = load_config()
    last_source_url = str(data.get("source_url", "")).strip()
    source_mode = str(data.get("source_mode", "")).strip()
    last_refresh = data.get("last_refresh")


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

    return [asdict(entry) for entry in parsed]


def download_url_bytes(url: str, timeout: int = 90) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "M3U-Web-Picker/2.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def download_url_text(url: str, timeout: int = 90) -> str:
    return download_url_bytes(url, timeout).decode("utf-8-sig", errors="replace")


def download_m3u_text(url: str, timeout: int = 90) -> str:
    return download_url_text(url, timeout=timeout)


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
    # the Channel Manager. Generated rows retain negative IDs so the existing
    # selection endpoint never treats them as editable manual selections.
    return [*channels, *sports.generated_channel_payloads(DB_PATH)]


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
                    (group_id, channel_key, name, group_title, url)
                VALUES (?, ?, ?, ?, ?)
                """,
                (group[0], key, channel.get("name", ""), channel.get("group", ""), channel.get("url", "")),
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


def refresh_master_from_url() -> tuple[bool, str]:
    global channels, last_refresh, source_mode
    if not last_source_url:
        return False, "No source URL configured."
    try:
        text = download_m3u_text(last_source_url)
        parsed = parse_m3u_text(text)
        with state_lock:
            atomic_write_text(MASTER_CACHE_PATH, text)
            channels = parsed
            sports.discover_catalog_from_channels(DB_PATH, channels)
            apply_saved_selections_to_loaded_channels()
            last_refresh = datetime.now().astimezone().isoformat(timespec="seconds")
            source_mode = "url"
            save_config()
            write_current_playlist()
        return True, f"Refreshed {len(channels)} channels."
    except Exception as exc:
        return False, str(exc)


def run_sports_scan(*, trigger: str = "manual", refresh_source: bool = True) -> dict:
    settings = sports.get_settings(DB_PATH)
    if not settings.get("enabled"):
        raise SportsScanError("Turn on Sports Automation before updating sports channels.")
    if not channels:
        raise SportsScanError("Load an M3U source before updating sports channels.")
    if not scan_lock.acquire(blocking=False):
        raise SportsScanError("A sports update is already running.")
    scan_started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    failure_recorded = False
    scan_state_started = False
    try:
        sports.begin_scan_state(
            DB_PATH,
            trigger=trigger,
            started_at=scan_started_at,
            stage="Starting sports update",
        )
        scan_state_started = True
        if refresh_source and source_mode == "url" and last_source_url:
            sports.update_scan_stage(DB_PATH, "Refreshing provider playlist")
            refreshed, message = refresh_master_from_url()
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
                    "Could not refresh the provider playlist. Existing sports channels were kept."
                )

        # EPG is enrichment. A failed XMLTV refresh falls back to the cached EPG
        # and then to M3U-only matching; it does not delete yesterday's channels.
        if last_source_url:
            sports.update_scan_stage(DB_PATH, "Refreshing guide data")
            sports.refresh_epg_cache(last_source_url, EPG_CACHE_PATH)

        sports.update_scan_stage(DB_PATH, "Discovering sports catalog")
        sports.discover_catalog_from_channels(DB_PATH, channels)
        sports.update_scan_stage(DB_PATH, "Scanning and matching channels")
        result = sports.scan_channels(
            DB_PATH,
            list(channels),
            EPG_CACHE_PATH if EPG_CACHE_PATH.exists() else None,
            sports_epg_path=SPORTS_EPG_PATH,
            combined_epg_path=COMBINED_EPG_PATH,
            trigger=trigger,
            started_at=scan_started_at,
        )
        sports.update_scan_stage(DB_PATH, "Writing playlist and validating guide")
        write_current_playlist()
        guide_check = sports.validate_guide_exports(
            DB_PATH,
            playlist_path=PLAYLIST_PATH,
            sports_epg_path=SPORTS_EPG_PATH,
            combined_epg_path=COMBINED_EPG_PATH,
        )
        result["guide_check"] = guide_check
        if not guide_check["ok"]:
            print(f"Generated sports guide validation failed: {guide_check}")
        return result
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
            scan_lock.release()


def sports_number_conflicts() -> int:
    settings = sports.get_settings(DB_PATH)
    start = int(settings.get("start_channel", 1000))
    manual_count = len(selected_channels_from_selected_ids_in_order())
    return max(0, manual_count - start + 1)


def _date_from_iso(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except Exception:
        return ""


def scheduler_loop() -> None:
    last_backup_date = ""
    while True:
        try:
            now = datetime.now().astimezone()
            today = now.date().isoformat()

            if sports.purge_expired_disabled_cache(DB_PATH, now):
                sports.rebuild_epg_exports(
                    DB_PATH,
                    base_epg_path=EPG_CACHE_PATH if EPG_CACHE_PATH.exists() else None,
                    sports_epg_path=SPORTS_EPG_PATH,
                    combined_epg_path=COMBINED_EPG_PATH,
                )
                write_current_playlist()

            master_refreshed = False
            if (
                source_mode == "url"
                and last_source_url
                and (now.hour, now.minute) == (SCHEDULE_HOUR, SCHEDULE_MINUTE)
                and _date_from_iso(last_refresh) != today
            ):
                master_refreshed, _message = refresh_master_from_url()

            if sports.should_run_scheduled(DB_PATH, now):
                try:
                    # Avoid downloading the provider playlist twice when the master
                    # refresh and sports refresh share the same minute.
                    run_sports_scan(
                        trigger="scheduled",
                        refresh_source=not master_refreshed,
                    )
                except Exception as exc:
                    print(f"Scheduled sports scan failed: {exc}")

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


restore_config()


def load_cached_master_playlist_on_startup() -> None:
    global channels
    db_connect().close()
    if sports.recover_interrupted_scan(DB_PATH):
        print("Recovered an interrupted sports scan state from the previous app process.")
    if not MASTER_CACHE_PATH.exists():
        write_current_playlist()
        try:
            sports.rebuild_epg_exports(
                DB_PATH,
                base_epg_path=EPG_CACHE_PATH if EPG_CACHE_PATH.exists() else None,
                sports_epg_path=SPORTS_EPG_PATH,
                combined_epg_path=COMBINED_EPG_PATH,
            )
        except Exception as exc:
            print(f"Startup sports guide rebuild failed: {exc}")
        return
    try:
        text = MASTER_CACHE_PATH.read_text(encoding="utf-8-sig", errors="replace")
        channels = parse_m3u_text(text)
        sports.discover_catalog_from_channels(DB_PATH, channels)
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
        sports.rebuild_epg_exports(
            DB_PATH,
            base_epg_path=EPG_CACHE_PATH if EPG_CACHE_PATH.exists() else None,
            sports_epg_path=SPORTS_EPG_PATH,
            combined_epg_path=COMBINED_EPG_PATH,
        )
    except Exception as exc:
        print(f"Startup cache load failed: {exc}")


load_cached_master_playlist_on_startup()
start_scheduler_once()
