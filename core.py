#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
from typing import List

from flask import Flask, Response, jsonify, request, send_file


EXPORT_DIR = APP_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = APP_DIR / "m3u_picker.db"
CONFIG_PATH = APP_DIR / "config.json"
MASTER_CACHE_PATH = APP_DIR / "master_playlist_cache.m3u"

PLAYLIST_NAME = "custom.m3u"
PLAYLIST_PATH = EXPORT_DIR / PLAYLIST_NAME
PORT = 9999

SCHEDULE_HOUR = 3
SCHEDULE_MINUTE = 0


channels: List[dict] = []
selected_ids: set[int] = set()
last_source_url = ""
last_refresh = None
source_mode = ""
scheduler_started = False


@dataclass
class Entry:
    id: int
    name: str
    group: str
    url: str
    raw: list[str]


EPG_CACHE_PATH = APP_DIR / "epg.xml"

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
    }
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


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


def download_url_text(url: str, timeout: int = 90) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "M3U-Web-Picker/1.0", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8-sig", errors="replace")


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


def start_scheduler_once() -> None:
    global scheduler_started
    if scheduler_started:
        return
    scheduler_started = True
    threading.Thread(target=scheduler_loop, daemon=True).start()


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
start_scheduler_once()
