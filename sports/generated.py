from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sports as _s


def _rewrite_extinf(line: str, attrs: dict[str, str], display_name: str) -> str:
    if not line.startswith("#EXTINF"):
        line = "#EXTINF:-1,"
    left = line.rsplit(",", 1)[0] if "," in line else line
    for key, value in attrs.items():
        escaped = str(value).replace('"', "'")
        if re.search(rf'{re.escape(key)}="[^"]*"', left):
            left = re.sub(
                rf'{re.escape(key)}="[^"]*"',
                f'{key}="{escaped}"',
                left,
            )
        else:
            left += f' {key}="{escaped}"'
    return f"{left},{display_name}"


def generated_stream_path(assigned_number: int) -> str:
    """Return the app-local playback URL for one generated sports slot."""
    number = int(assigned_number)
    if number < 0:
        raise ValueError("Sports channel numbers must be non-negative.")
    return f"/sports/stream/{number}"


def _generated_raw(channel: dict, generated: dict) -> list[str]:
    playback_url = str(
        generated.get("playback_url")
        or generated_stream_path(int(generated["assigned_number"]))
    )
    raw = list(channel.get("raw", []))
    if not raw:
        raw = ["#EXTINF:-1", playback_url]
    attrs = {
        "tvg-id": generated["tvg_id"],
        "tvg-chno": str(generated["assigned_number"]),
        "tvg-name": generated["display_name"],
        "group-title": generated["group_title"],
        "x-sports-event": generated["event_key"],
        "x-sports-feed": generated["feed_type"],
        "x-sports-subtitle": generated["subtitle"],
    }
    if generated.get("tvg_logo"):
        attrs["tvg-logo"] = generated["tvg_logo"]
    raw[0] = _rewrite_extinf(raw[0], attrs, generated["display_name"])
    if raw[-1] != playback_url:
        raw[-1] = playback_url
    return raw


def _generated_tvg_id(identity: str | int) -> str:
    """Return a credential-free XMLTV id for one generated sports identity.

    New generated channels pass their logical ``channel_key`` so Jellyfin sees
    a new channel identity when a reusable numbered slot is assigned to a
    different event/feed. Integer input is retained for legacy database
    migrations that predate logical sports identities.
    """
    if isinstance(identity, int):
        number = int(identity)
        if number < 0:
            raise ValueError("Sports channel numbers must be non-negative.")
        return f"m3u-picker-sports-{number}"

    channel_key = str(identity or "").strip()
    if not channel_key:
        raise ValueError("Sports channel identity is required.")
    digest = hashlib.sha256(channel_key.encode("utf-8")).hexdigest()[:24]
    return f"m3u-picker-sports-{digest}"


def purge_stale_generated(
    db_path: Path | str,
    now: datetime | None = None,
) -> int:
    """Remove generated channels after their event end plus postgame grace."""
    _s.init_db(db_path)
    settings = _s.get_settings(db_path)
    if not settings.get("enabled"):
        return 0
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    current = (now or datetime.now().astimezone()).astimezone(timezone)
    expired_ids: list[int] = []
    with closing(_s._connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, event_end FROM sports_generated WHERE event_end IS NOT NULL"
        ).fetchall()
        for row in rows:
            end = _s._parse_iso_datetime(row["event_end"], timezone)
            if not isinstance(end, datetime):
                continue
            try:
                if current >= end.astimezone(timezone) + _s.EVENT_END_GRACE:
                    expired_ids.append(int(row["id"]))
            except Exception:
                continue
        if expired_ids:
            placeholders = ",".join("?" for _ in expired_ids)
            conn.execute(
                f"DELETE FROM sports_generated WHERE id IN ({placeholders})",
                expired_ids,
            )
            conn.commit()
    return len(expired_ids)


def generated_rows(
    db_path: Path | str,
    *,
    include_cached: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """Return generated rows visible to clients, or the disabled cache."""
    _s.init_db(db_path)
    _s.purge_expired_disabled_cache(db_path, now)
    if not include_cached and not _s.get_settings(db_path).get("enabled"):
        return []
    with closing(_s._connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, channel_key, source_channel_key, event_key, league_id,
                   display_name, subtitle, feed_type, assigned_number,
                   group_title, url, tvg_id, source_tvg_id, tvg_logo, raw_json,
                   event_title, event_start, event_end, is_replay,
                   epg_programme_json, generated_at
            FROM sports_generated
            ORDER BY assigned_number
            """
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["raw"] = _s._json_load(item.pop("raw_json"), [])
        if item["raw"]:
            item["raw"][-1] = generated_stream_path(int(item["assigned_number"]))
        item["epg_programme"] = _s._json_load(
            item.pop("epg_programme_json", "{}"), {}
        )
        output.append(item)
    return output


def generated_stream_target(db_path: Path | str, assigned_number: int) -> str:
    """Resolve a generated slot to its current provider playback URL."""
    _s.init_db(db_path)
    if not _s.get_settings(db_path).get("enabled"):
        return ""
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            "SELECT url FROM sports_generated WHERE assigned_number = ?",
            (int(assigned_number),),
        ).fetchone()
    return str(row["url"] or "").strip() if row else ""


def generated_channel_payloads(db_path: Path | str) -> list[dict]:
    output = []
    for index, row in enumerate(generated_rows(db_path), start=1):
        output.append(
            {
                "id": -index,
                "key": row["channel_key"],
                "name": row["display_name"],
                "group": row["group_title"],
                "url": generated_stream_path(int(row["assigned_number"])),
                "raw": row["raw"],
                "tvg_id": row["tvg_id"],
                "tvg_name": row["display_name"],
                "tvg_logo": row["tvg_logo"],
                "tvg_chno": str(row["assigned_number"]),
                "sports_subtitle": row["subtitle"],
                "sports_feed_type": row["feed_type"],
                "sports_event_key": row["event_key"],
                "is_sports_generated": True,
            }
        )
    return output


def publish_generated(
    db_path: Path | str,
    generated: list[dict],
    prepared_epg: list[tuple[Path, Path]],
    generated_at: str,
) -> None:
    """Atomically replace generated rows and prepared guide exports."""
    installed_epg: list[tuple[Path, Path | None]] = []
    with closing(_s._connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM sports_generated")
            for item in generated:
                conn.execute(
                    """
                    INSERT INTO sports_generated
                        (channel_key, source_channel_key, event_key, league_id,
                         display_name, subtitle, feed_type, assigned_number,
                         group_title, url, tvg_id, source_tvg_id, tvg_logo, raw_json,
                         event_title, event_start, event_end, is_replay,
                         epg_programme_json, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["channel_key"],
                        item["source_channel_key"],
                        item["event_key"],
                        item["league_id"],
                        item["display_name"],
                        item["subtitle"],
                        item["feed_type"],
                        item["assigned_number"],
                        item["group_title"],
                        item["url"],
                        item["tvg_id"],
                        item["source_tvg_id"],
                        item["tvg_logo"],
                        json.dumps(item["raw"]),
                        item["event_title"],
                        item["event_start"],
                        item["event_end"],
                        1 if item["is_replay"] else 0,
                        json.dumps(item.get("epg_programme") or {}),
                        generated_at,
                    ),
                )

            for temp_path, destination in prepared_epg:
                backup_path = None
                if destination.exists():
                    backup_path = destination.with_name(destination.name + ".previous")
                    backup_path.unlink(missing_ok=True)
                    destination.replace(backup_path)
                try:
                    temp_path.replace(destination)
                except Exception:
                    if backup_path and backup_path.exists():
                        backup_path.replace(destination)
                    raise
                installed_epg.append((destination, backup_path))
            conn.commit()
        except Exception:
            conn.rollback()
            for destination, backup_path in reversed(installed_epg):
                destination.unlink(missing_ok=True)
                if backup_path and backup_path.exists():
                    backup_path.replace(destination)
            raise
        finally:
            for temp_path, _destination in prepared_epg:
                temp_path.unlink(missing_ok=True)
            for _destination, backup_path in installed_epg:
                if backup_path:
                    backup_path.unlink(missing_ok=True)
