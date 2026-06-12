#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import re
import ssl
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from flask import Flask, Response, jsonify, request, send_file


# All app-generated files must stay inside this folder.
APP_DIR = Path(__file__).resolve().parent
EXPORT_DIR = APP_DIR / "exports"
EXPORT_DIR.mkdir(exist_ok=True)

DB_PATH = APP_DIR / "m3u_picker.db"
CONFIG_PATH = APP_DIR / "config.json"
MASTER_CACHE_PATH = APP_DIR / "master_playlist_cache.m3u"
EPG_DIR = APP_DIR / "epg"
EPG_DIR.mkdir(exist_ok=True)

PLAYLIST_NAME = "custom.m3u"
PLAYLIST_PATH = EXPORT_DIR / PLAYLIST_NAME
PORT = 9999

SCHEDULE_HOUR = 3
SCHEDULE_MINUTE = 0
EPG_REFRESH_OFFSET_MINUTES = 15


channels: List[dict] = []
selected_ids: set[int] = set()
last_source_url = ""
last_refresh = None
source_mode = ""
epg_sources: list[dict] = []
scheduler_started = False
epg_startup_cache_started = False


@dataclass
class Entry:
    id: int
    name: str
    group: str
    url: str
    raw: list[str]


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS selections (
            key TEXT PRIMARY KEY,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL,
            sort_order INTEGER
        )
    """)
    try:
        conn.execute("ALTER TABLE selections ADD COLUMN sort_order INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_channels (
            group_id INTEGER NOT NULL,
            channel_key TEXT NOT NULL,
            name TEXT,
            group_title TEXT,
            url TEXT NOT NULL,
            PRIMARY KEY (group_id, channel_key),
            FOREIGN KEY (group_id) REFERENCES custom_groups(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    return conn


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "group"


def unique_slug(conn, name: str) -> str:
    base = slugify(name)
    slug = base
    i = 2
    while conn.execute("SELECT 1 FROM custom_groups WHERE slug = ?", (slug,)).fetchone():
        slug = f"{base}-{i}"
        i += 1
    return slug


def channel_key(channel: dict) -> str:
    return str(channel.get("url", "")).strip()


def rewrite_extinf_attr(line: str, key: str, value: str) -> str:
    if not line.startswith("#EXTINF"):
        return line

    if re.search(rf'{re.escape(key)}="[^"]*"', line):
        return re.sub(rf'{re.escape(key)}="[^"]*"', f'{key}="{value}"', line)

    if "," in line:
        left, right = line.rsplit(",", 1)
        return f'{left} {key}="{value}",{right}'

    return f'{line} {key}="{value}"'


def apply_channel_number(channel: dict, number: int) -> list[str]:
    raw = list(channel.get("raw", []))
    if not raw:
        return raw

    raw[0] = rewrite_extinf_attr(raw[0], "tvg-chno", str(number))
    return raw


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
        for ch in selected_channels:
            key = channel_key(ch)
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
                    ch.get("name", ""),
                    ch.get("group", ""),
                    ch.get("url", ""),
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
        existing = {
            row[0]
            for row in conn.execute("SELECT key FROM selections").fetchall()
        }

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
        int(ch["id"])
        for ch in channels
        if channel_key(ch) in saved_keys
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
        "epg_sources": epg_sources,
        "epg_schedule": {"offset_minutes_after_m3u": EPG_REFRESH_OFFSET_MINUTES},
    }
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def restore_config() -> None:
    global last_source_url, source_mode, last_refresh, epg_sources
    data = load_config()
    last_source_url = str(data.get("source_url", "")).strip()
    source_mode = str(data.get("source_mode", "")).strip()
    last_refresh = data.get("last_refresh")
    epg_sources = []
    for source in data.get("epg_sources", []) or []:
        name = str(source.get("name", "")).strip()
        url = str(source.get("url", "")).strip()
        if not name or not url:
            continue
        source_id = normalize_epg_id(source.get("id") or name)
        epg_sources.append({
            "id": source_id,
            "name": name,
            "url": url,
            "last_refresh": source.get("last_refresh"),
            "last_error": source.get("last_error"),
        })


def parse_m3u_text(text: str) -> list[dict]:
    lines = text.splitlines()
    parsed: list[Entry] = []
    current: list[str] = []
    name = ""
    group = ""

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("#EXTINF"):
            current = [stripped]
            name = stripped.rsplit(",", 1)[1].strip() if "," in stripped else ""
            match = re.search(r'group-title="([^"]*)"', stripped)
            group = match.group(1) if match else ""
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
                )
            )
            current = []

    return [asdict(entry) for entry in parsed]


def download_url_bytes(url: str, timeout: int = 90) -> tuple[bytes, dict]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "M3U-Web-Picker/1.0",
            "Accept": "*/*",
            # Do not let servers apply transparent HTTP compression.
            # XMLTV .gz URLs are still handled explicitly below.
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        headers = dict(response.headers.items())
    return raw, headers


def download_url_text(url: str, timeout: int = 90) -> str:
    raw, headers = download_url_bytes(url, timeout=timeout)
    return raw.decode("utf-8-sig", errors="replace")


def download_epg_url_bytes(url: str, timeout: int = 90) -> tuple[bytes, dict]:
    """Download provider EPG bytes only.

    Some XMLTV providers have broken/self-signed/expired TLS chains. Ignore SSL
    verification here only; M3U downloading and M3U serving continue to use the
    existing code paths unchanged.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "M3U-Web-Picker/1.0",
            "Accept": "application/xml,text/xml,*/*",
            "Accept-Encoding": "identity",
        },
    )
    context = ssl._create_unverified_context() if url.lower().startswith("https://") else None
    with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
        raw = response.read()
        headers = dict(response.headers.items())
    return raw, headers


def download_epg_bytes(url: str, timeout: int = 90) -> bytes:
    """Download an XMLTV EPG and return XML bytes suitable to serve directly.

    Preserve provider bytes instead of decoding/re-encoding so XML declarations
    and non-UTF-8 encodings remain intact for clients like Jellyfin. If the
    provider URL/headers indicate gzip, decompress once and cache the XML bytes.
    """
    raw, headers = download_epg_url_bytes(url, timeout=timeout)
    content_encoding = str(headers.get("Content-Encoding", "")).lower()
    content_type = str(headers.get("Content-Type", "")).lower()
    is_gzip = (
        "gzip" in content_encoding
        or "gzip" in content_type
        or url.lower().endswith((".gz", ".gzip"))
        or raw.startswith(b"\x1f\x8b")
    )
    if is_gzip:
        raw = gzip.decompress(raw)
    return raw


def download_epg_text(url: str, timeout: int = 90) -> str:
    return download_epg_bytes(url, timeout=timeout).decode("utf-8-sig", errors="replace")


def download_m3u_text(url: str, timeout: int = 60) -> str:
    return download_url_text(url, timeout=timeout)



def selected_channels_from_selected_ids_in_order() -> list[dict]:
    selected_channels = [ch for ch in channels if int(ch["id"]) in selected_ids]

    conn = db_connect()
    try:
        existing_order = {
            row[0]: row[1]
            for row in conn.execute("SELECT key, sort_order FROM selections").fetchall()
        }
    finally:
        conn.close()

    def sort_key(ch: dict):
        key = channel_key(ch)
        order = existing_order.get(key)
        if order is None:
            return (1, ch.get("name", "").lower(), key)
        return (0, order, ch.get("name", "").lower())

    return sorted(selected_channels, key=sort_key)


def write_current_playlist() -> int:
    selected_channels = selected_channels_from_selected_ids_in_order()

    lines = ["#EXTM3U"]
    for number, ch in enumerate(selected_channels, start=1):
        lines.extend(apply_channel_number(ch, number))

    PLAYLIST_PATH.write_text("\n".join(lines), encoding="utf-8")
    save_selected_channels_to_db(selected_channels)
    return len(selected_channels)


def channel_by_key_map() -> dict[str, dict]:
    return {channel_key(ch): ch for ch in channels if channel_key(ch)}


def group_channels_for_slug(slug: str) -> tuple[str, list[dict]]:
    conn = db_connect()
    try:
        group = conn.execute(
            "SELECT id, name FROM custom_groups WHERE slug = ?",
            (slug,),
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
    output = []
    for key in keys:
        if key in current:
            output.append(current[key])
    return group_name, output


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


def m3u_from_channels(items: list[dict]) -> str:
    lines = ["#EXTM3U"]
    seen = set()
    for ch in items:
        key = channel_key(ch)
        if not key or key in seen:
            continue
        seen.add(key)
        lines.extend(ch["raw"])
    return "\n".join(lines) + "\n"



def normalize_epg_id(source_id: str) -> str:
    return slugify(str(source_id or "").removesuffix(".xml"))


def epg_cache_path(source_id: str) -> Path:
    return EPG_DIR / f"{normalize_epg_id(source_id)}.xml"


def epg_public_url(source_id: str) -> str:
    return f"/epg/{normalize_epg_id(source_id)}.xml"


def find_epg_source(source_id: str) -> dict | None:
    wanted = normalize_epg_id(source_id)
    return next((item for item in epg_sources if normalize_epg_id(item.get("id")) == wanted), None)


def epg_sources_payload() -> list[dict]:
    payload = []
    for source in epg_sources:
        source_id = normalize_epg_id(source.get("id"))
        item = dict(source)
        item["id"] = source_id
        item["url_path"] = epg_public_url(source_id)
        item["cached"] = epg_cache_path(source_id).exists()
        payload.append(item)
    return payload


def add_epg_source(name: str, url: str) -> dict:
    global epg_sources
    name = name.strip()
    url = url.strip()
    if not name:
        raise ValueError("EPG name is required.")
    if not url.startswith(("http://", "https://")):
        raise ValueError("EPG URL must start with http:// or https://")

    source_id = unique_epg_id(name)
    source = {
        "id": source_id,
        "name": name,
        "url": url,
        "last_refresh": None,
        "last_error": None,
    }
    epg_sources.append(source)
    save_config()
    return source


def unique_epg_id(name: str) -> str:
    base = normalize_epg_id(name)
    existing = {normalize_epg_id(source.get("id", "")) for source in epg_sources}
    source_id = base
    i = 2
    while source_id in existing:
        source_id = f"{base}-{i}"
        i += 1
    return source_id


def delete_epg_source(source_id: str) -> bool:
    global epg_sources
    wanted = normalize_epg_id(source_id)
    before = len(epg_sources)
    epg_sources = [source for source in epg_sources if normalize_epg_id(source.get("id")) != wanted]
    try:
        epg_cache_path(wanted).unlink(missing_ok=True)
    except Exception:
        pass
    save_config()
    return len(epg_sources) != before

def refresh_epg_source(source_id: str) -> tuple[bool, str]:
    source_id = normalize_epg_id(source_id)
    source = find_epg_source(source_id)
    if not source:
        return False, "EPG source not found."

    try:
        raw = download_epg_bytes(source["url"], timeout=90)
        epg_cache_path(source_id).write_bytes(raw)
        source["last_refresh"] = datetime.now().isoformat(timespec="seconds")
        source["last_error"] = None
        save_config()
        return True, f"Refreshed {source['name']}."
    except Exception as exc:
        source["last_error"] = str(exc)
        save_config()
        return False, str(exc)


def refresh_all_epg_sources() -> dict:
    results = []
    ok_count = 0
    for source in list(epg_sources):
        ok, message = refresh_epg_source(source["id"])
        ok_count += 1 if ok else 0
        results.append({"id": source["id"], "name": source["name"], "ok": ok, "message": message})
    return {"count": len(results), "ok_count": ok_count, "results": results}


def refresh_master_from_url() -> tuple[bool, str]:
    global channels, last_refresh, source_mode
    if not last_source_url:
        return False, "No source URL configured."
    try:
        text = download_m3u_text(last_source_url)
        MASTER_CACHE_PATH.write_text(text, encoding="utf-8")
        channels = parse_m3u_text(text)
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
        last_refresh = datetime.now().isoformat(timespec="seconds")
        source_mode = "url"
        save_config()
        return True, f"Refreshed {len(channels)} channels."
    except Exception as exc:
        return False, str(exc)



def seconds_until_next_run(hour: int, minute: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def scheduler_loop() -> None:
    while True:
        time.sleep(seconds_until_next_run(SCHEDULE_HOUR, SCHEDULE_MINUTE))
        refresh_master_from_url()
        time.sleep(EPG_REFRESH_OFFSET_MINUTES * 60)
        refresh_all_epg_sources()


def start_scheduler_once() -> None:
    global scheduler_started
    if scheduler_started:
        return
    scheduler_started = True
    threading.Thread(target=scheduler_loop, daemon=True).start()


def refresh_missing_epg_caches() -> None:
    """Generate any missing EPG cache files after startup.

    EPG source configuration and EPG cache files are separate. On a fresh
    install/rebuild the config can exist while the cache directory is empty,
    so generate missing caches immediately instead of waiting for the 3:15 AM
    scheduled refresh.
    """
    for source in list(epg_sources):
        source_id = normalize_epg_id(source.get("id"))
        if source_id and not epg_cache_path(source_id).exists():
            refresh_epg_source(source_id)


def start_epg_startup_cache_once() -> None:
    global epg_startup_cache_started
    if epg_startup_cache_started:
        return
    epg_startup_cache_started = True
    threading.Thread(target=refresh_missing_epg_caches, daemon=True).start()


restore_config()

def load_cached_master_playlist_on_startup() -> None:
    global channels

    if not MASTER_CACHE_PATH.exists():
        return

    try:
        text = MASTER_CACHE_PATH.read_text(encoding="utf-8-sig", errors="replace")
        channels = parse_m3u_text(text)
        apply_saved_selections_to_loaded_channels()
        write_current_playlist()
    except Exception as exc:
        print(f"Startup cache load failed: {exc}")

load_cached_master_playlist_on_startup()
start_epg_startup_cache_once()
start_scheduler_once()
