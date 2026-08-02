from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


DEFAULT_SETTINGS = {
    "enabled": False,
    "auto_update": True,
    "start_channel": 1000,
    "channels_per_event": 10,
    "group_title": "Sports Today",
    "timezone": "America/New_York",
    # Persist one canonical 24-hour value. Display formatting belongs in the UI.
    "refresh_time": "03:00",
    "event_window": "today",
    "include_replays": False,
    "include_pregame": False,
    "use_backup_feeds": True,
}

SCOPE_TYPES = {"league", "team", "conference", "sport"}
RULE_PRIORITY = {"team": 0, "conference": 1, "league": 2, "sport": 3}

LEAGUE_NAMES = {
    "nfl": "NFL",
    "mlb": "MLB",
    "milb": "MiLB",
    "nba": "NBA",
    "nhl": "NHL",
    "wnba": "WNBA",
    "ncaaf": "College Football",
    "ncaab": "College Basketball",
    "mls": "MLS",
    "nwsl": "NWSL",
}

LEAGUE_PATTERNS = {
    "nfl": [r"\bnfl\b", r"national football league"],
    "mlb": [r"\bmlb\b", r"major league baseball"],
    "milb": [r"\bmilb\b", r"minor league baseball"],
    "nba": [r"\bnba\b", r"national basketball association"],
    "nhl": [r"\bnhl\b", r"national hockey league"],
    "wnba": [r"\bwnba\b"],
    "ncaaf": [r"\bncaaf\b", r"college football", r"ncaa football"],
    "ncaab": [r"\bncaab\b", r"college basketball", r"ncaa basketball"],
    "mls": [r"\bmls\b", r"major league soccer"],
    "nwsl": [r"\bnwsl\b", r"national women'?s soccer league"],
}

SPORT_PATTERNS = {
    "cornhole": [
        r"\bcornhole\b",
        r"american cornhole",
        r"\bacl\s+(?:pro|open|teams|shootout|championship)",
        r"\baco\b",
        r"bag toss",
    ],
    "formula-1": [r"formula\s*1", r"\bf1\b", r"grand prix"],
    "ufc": [r"\bufc\b", r"ultimate fighting", r"fight night"],
    "soccer": [r"\bsoccer\b", r"\bmls\b", r"\bnwsl\b", r"premier league", r"la liga", r"champions league"],
}

REPLAY_RE = re.compile(r"\b(replay|encore|classic|rewind|repeat)\b", re.I)
PREGAME_RE = re.compile(r"\b(pre[- ]?game|post[- ]?game|pregame|postgame)\b", re.I)
PLACEHOLDER_RE = re.compile(
    r"(?:^|\s)(?:zzz|tba|placeholder)(?:$|\s)|2098-12-31|^\s*$", re.I
)
DATE_RE = re.compile(
    r"\((?P<date>\d{4}-\d{2}-\d{2})(?:\s+(?P<time>\d{2}:\d{2}(?::\d{2})?))?\)\s*$"
)
MATCHUP_RE = re.compile(
    r"(?P<left>[A-Za-z0-9À-ÿ .&'’/\-]+?)\s+(?P<op>@|at|vs\.?|versus)\s+(?P<right>[A-Za-z0-9À-ÿ .&'’/\-]+)$",
    re.I,
)
LEADING_TIME_RE = re.compile(r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)\b", re.I)

MLB_TEAMS = [
    ("arizona-diamondbacks", "Arizona Diamondbacks", ["ARI", "Diamondbacks", "D-backs", "Dbacks"]),
    ("atlanta-braves", "Atlanta Braves", ["ATL", "Braves"]),
    ("baltimore-orioles", "Baltimore Orioles", ["BAL", "Orioles", "O's"]),
    ("boston-red-sox", "Boston Red Sox", ["BOS", "Red Sox"]),
    ("chicago-cubs", "Chicago Cubs", ["CHC", "Cubs"]),
    ("chicago-white-sox", "Chicago White Sox", ["CHW", "CWS", "White Sox"]),
    ("cincinnati-reds", "Cincinnati Reds", ["CIN", "Reds"]),
    ("cleveland-guardians", "Cleveland Guardians", ["CLE", "Guardians"]),
    ("colorado-rockies", "Colorado Rockies", ["COL", "Rockies"]),
    ("detroit-tigers", "Detroit Tigers", ["DET", "Tigers"]),
    ("houston-astros", "Houston Astros", ["HOU", "Astros"]),
    ("kansas-city-royals", "Kansas City Royals", ["KC", "KCR", "Royals"]),
    ("los-angeles-angels", "Los Angeles Angels", ["LAA", "Angels"]),
    ("los-angeles-dodgers", "Los Angeles Dodgers", ["LAD", "Dodgers"]),
    ("miami-marlins", "Miami Marlins", ["MIA", "Marlins"]),
    ("milwaukee-brewers", "Milwaukee Brewers", ["MIL", "Brewers"]),
    ("minnesota-twins", "Minnesota Twins", ["MIN", "Twins"]),
    ("new-york-mets", "New York Mets", ["NYM", "Mets"]),
    ("new-york-yankees", "New York Yankees", ["NYY", "Yankees"]),
    ("oakland-athletics", "Oakland Athletics", ["ATH", "OAK", "Athletics", "A's"]),
    ("philadelphia-phillies", "Philadelphia Phillies", ["PHI", "Phillies"]),
    ("pittsburgh-pirates", "Pittsburgh Pirates", ["PIT", "Pirates"]),
    ("san-diego-padres", "San Diego Padres", ["SD", "SDP", "Padres"]),
    ("san-francisco-giants", "San Francisco Giants", ["SF", "SFG", "Giants"]),
    ("seattle-mariners", "Seattle Mariners", ["SEA", "Mariners"]),
    ("st-louis-cardinals", "St. Louis Cardinals", ["STL", "Cardinals"]),
    ("tampa-bay-rays", "Tampa Bay Rays", ["TB", "TBR", "Rays"]),
    ("texas-rangers", "Texas Rangers", ["TEX", "Rangers"]),
    ("toronto-blue-jays", "Toronto Blue Jays", ["TOR", "Blue Jays", "Jays"]),
    ("washington-nationals", "Washington Nationals", ["WSH", "WSN", "Nationals", "Nats"]),
]

MLB_ALIASES_BY_NAME = {
    _normalize_name: aliases
    for _slug_name, display_name, aliases in MLB_TEAMS
    for _normalize_name in [re.sub(r"[^a-z0-9]+", " ", display_name.lower()).strip()]
}


CONFERENCE_TEAMS = {
    "ncaaf:big-ten": [
        "Illinois", "Indiana", "Iowa", "Maryland", "Michigan", "Michigan State",
        "Minnesota", "Nebraska", "Northwestern", "Ohio State", "Oregon", "Penn State",
        "Purdue", "Rutgers", "UCLA", "USC", "Washington", "Wisconsin",
    ],
    "ncaaf:acc": [
        "Boston College", "California", "Clemson", "Duke", "Florida State",
        "Georgia Tech", "Louisville", "Miami", "North Carolina", "NC State",
        "Pittsburgh", "SMU", "Stanford", "Syracuse", "Virginia", "Virginia Tech",
        "Wake Forest",
    ],
    "ncaaf:sec": [
        "Alabama", "Arkansas", "Auburn", "Florida", "Georgia", "Kentucky", "LSU",
        "Mississippi", "Mississippi State", "Missouri", "Oklahoma", "South Carolina",
        "Tennessee", "Texas", "Texas A&M", "Vanderbilt",
    ],
}

SEED_CATALOG = [
    ("league", "nfl", "NFL", "Every NFL game", "nfl", [], "", {}),
    ("league", "mlb", "MLB", "Every MLB game", "mlb", [], "", {}),
    ("league", "milb", "MiLB", "Every Minor League Baseball game", "milb", ["Minor League Baseball"], "", {}),
    ("league", "nba", "NBA", "Every NBA game", "nba", [], "", {}),
    ("league", "nhl", "NHL", "Every NHL game", "nhl", [], "", {}),
    ("league", "wnba", "WNBA", "Every WNBA game", "wnba", [], "", {}),
    ("league", "ncaaf", "College Football", "Every college football game", "ncaaf", [], "", {}),
    ("league", "ncaab", "College Basketball", "Every college basketball game", "ncaab", [], "", {}),
    ("league", "mls", "MLS", "Every MLS match", "mls", [], "", {}),
    ("league", "nwsl", "NWSL", "Every NWSL match", "nwsl", [], "", {}),
    (
        "conference",
        "ncaaf:big-ten",
        "Big Ten Football",
        "Games with at least one Big Ten team",
        "ncaaf",
        ["Big Ten", "B1G"],
        "",
        {"teams": CONFERENCE_TEAMS["ncaaf:big-ten"]},
    ),
    (
        "conference",
        "ncaaf:acc",
        "ACC Football",
        "Games with at least one ACC team",
        "ncaaf",
        ["ACC", "Atlantic Coast Conference"],
        "",
        {"teams": CONFERENCE_TEAMS["ncaaf:acc"]},
    ),
    (
        "conference",
        "ncaaf:sec",
        "SEC Football",
        "Games with at least one SEC team",
        "ncaaf",
        ["SEC", "Southeastern Conference"],
        "",
        {"teams": CONFERENCE_TEAMS["ncaaf:sec"]},
    ),
    ("sport", "cornhole", "Cornhole", "ACL and ACO events", "", ["ACL", "ACO", "bag toss"], "", {}),
    ("sport", "formula-1", "Formula 1", "Formula 1 and Grand Prix events", "", ["F1", "Grand Prix"], "", {}),
    ("sport", "ufc", "UFC", "UFC and Fight Night events", "", ["Fight Night"], "", {}),
    ("sport", "soccer", "Soccer", "Soccer matches from any competition", "", ["football"], "", {}),
]

SEED_CATALOG.extend(
    (
        "team",
        f"mlb:{slug}",
        display_name,
        "MLB team • home and away games",
        "mlb",
        [display_name, *aliases],
        "",
        {},
    )
    for slug, display_name, aliases in MLB_TEAMS
)


LEGACY_DEMO_RULES = {
    ("league", "nfl"),
    ("team", "mlb:philadelphia-phillies"),
    ("conference", "ncaaf:big-ten"),
    ("sport", "cornhole"),
}

TEAM_FEED_PATTERNS = [
    ("mlb", re.compile(r"^MLB\s+(?!NETWORK\b|STRIKE\b)(?P<team>.+?)\s*$", re.I)),
    ("nfl", re.compile(r"^NFL\s*\|\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
    ("nba", re.compile(r"^NBA\s*\|\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
    ("nhl", re.compile(r"^NHL\s*:\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
    ("wnba", re.compile(r"^WNBA\s*\|\s*(?P<team>.+?)(?:\s+(?:HD|SD|FHD))?\s*$", re.I)),
]

NETWORK_WORDS = {
    "espn", "espn2", "espnu", "fox", "fs1", "fs2", "cbs", "cbssn", "nbc",
    "tnt", "tbs", "abc", "apple", "prime", "network", "redzone", "strike zone",
}

MAX_MALFORMED_SAMPLES = 10

XMLTV_GENERATOR_NAME = "M3U Web Picker Sports Automation"
GUIDE_PREGAME_HOURS = 24
GUIDE_POSTGAME_HOURS = 2
ESTIMATED_EVENT_HOURS = {
    "mlb": 4,
    "milb": 4,
    "nfl": 4,
    "ncaaf": 4,
    "nba": 3,
    "wnba": 3,
    "nhl": 3,
    "ncaab": 3,
    "mls": 3,
    "nwsl": 3,
}


class MalformedSportsEntry(ValueError):
    """A provider entry contains bad event data and may be skipped safely."""


def _new_scan_diagnostics() -> dict:
    return {
        "malformed_m3u": 0,
        "malformed_epg": 0,
        "samples": [],
    }


def _record_malformed_entry(
    diagnostics: dict,
    *,
    source: str,
    label: str,
    exc: Exception,
) -> None:
    key = f"malformed_{source}"
    diagnostics[key] = int(diagnostics.get(key, 0)) + 1
    samples = diagnostics.setdefault("samples", [])
    if len(samples) < MAX_MALFORMED_SAMPLES:
        clean_label = re.sub(r"\s+", " ", str(label or "unnamed entry")).strip()
        samples.append(
            {
                "source": source.upper(),
                "label": clean_label[:180],
                "error": f"{type(exc).__name__}: {exc}"[:240],
            }
        )


def _malformed_count(diagnostics: dict) -> int:
    return int(diagnostics.get("malformed_m3u", 0)) + int(
        diagnostics.get("malformed_epg", 0)
    )


def _log_malformed_summary(diagnostics: dict) -> None:
    count = _malformed_count(diagnostics)
    if not count:
        return
    print(f"Sports scan skipped {count} malformed provider entr{'y' if count == 1 else 'ies'}.")
    for sample in diagnostics.get("samples", []):
        print(
            "  - "
            f"{sample.get('source', 'SOURCE')} entry "
            f"{sample.get('label', 'unnamed entry')!r}: "
            f"{sample.get('error', 'invalid data')}"
        )


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _normalize(value: str) -> str:
    value = value.replace("&", " and ").replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def _smart_team_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip(" |:-"))
    if not value:
        return value
    if value.isupper():
        value = value.title()
    replacements = {
        "76Ers": "76ers",
        "Fc": "FC",
        "Sc": "SC",
        "Ucla": "UCLA",
        "Usc": "USC",
        "Lsu": "LSU",
        "Smu": "SMU",
    }
    for before, after in replacements.items():
        value = re.sub(rf"\b{re.escape(before)}\b", after, value)
    return value


def _json_load(value: str, fallback):
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _canonical_refresh_time(value, *, fallback: str | None = None) -> str:
    """Return HH:MM or raise a user-facing configuration error.

    The AM/PM parser exists only to safely migrate values produced by an older
    build. New writes are always stored as 24-hour HH:MM strings.
    """
    text = str(value or "").strip().upper()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            parsed = datetime.strptime(text, fmt)
            return f"{parsed.hour:02d}:{parsed.minute:02d}"
        except ValueError:
            pass
    if fallback is not None:
        return fallback
    raise ValueError("Refresh time must be a valid time such as 03:00.")


def _refresh_time_parts(settings: dict) -> tuple[int, int]:
    value = settings.get("refresh_time")
    if value not in (None, ""):
        canonical = _canonical_refresh_time(value, fallback="03:00")
        hour, minute = canonical.split(":", 1)
        return int(hour), int(minute)

    # Backward-compatible read of databases created before refresh_time existed.
    try:
        hour = int(settings.get("refresh_hour", 3))
        minute = int(settings.get("refresh_minute", 0))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (TypeError, ValueError):
        pass
    return 3, 0


def init_db(db_path: Path | str) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_catalog (
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                subtitle TEXT NOT NULL DEFAULT '',
                league_id TEXT NOT NULL DEFAULT '',
                aliases_json TEXT NOT NULL DEFAULT '[]',
                logo_url TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                source TEXT NOT NULL DEFAULT 'seed',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (scope_type, scope_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                feed_preference TEXT NOT NULL DEFAULT 'best',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(scope_type, scope_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_generated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_key TEXT NOT NULL UNIQUE,
                source_channel_key TEXT NOT NULL,
                event_key TEXT NOT NULL,
                league_id TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL,
                subtitle TEXT NOT NULL DEFAULT '',
                feed_type TEXT NOT NULL DEFAULT 'event',
                assigned_number INTEGER NOT NULL,
                group_title TEXT NOT NULL,
                url TEXT NOT NULL,
                tvg_id TEXT NOT NULL DEFAULT '',
                source_tvg_id TEXT NOT NULL DEFAULT '',
                tvg_logo TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL,
                event_title TEXT NOT NULL DEFAULT '',
                event_start TEXT,
                event_end TEXT,
                is_replay INTEGER NOT NULL DEFAULT 0,
                generated_at TEXT NOT NULL
            )
            """
        )
        # Add guide-related columns when upgrading an existing sports database.
        for column_name, column_sql in (
            ("source_tvg_id", "TEXT NOT NULL DEFAULT ''"),
            ("event_title", "TEXT NOT NULL DEFAULT ''"),
            ("event_end", "TEXT"),
            ("is_replay", "INTEGER NOT NULL DEFAULT 0"),
        ):
            try:
                conn.execute(
                    f"ALTER TABLE sports_generated ADD COLUMN {column_name} {column_sql}"
                )
            except sqlite3.OperationalError:
                pass

        # Existing v20.3 rows inherited provider tvg-id values. Upgrade them
        # once so every generated feed has its own stable XMLTV identity.
        guide_migration_key = "migration_generated_xmltv_ids_v20_4"
        guide_migrated = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?",
            (guide_migration_key,),
        ).fetchone()
        if not guide_migrated:
            legacy_rows = conn.execute(
                """
                SELECT id, event_key, feed_type, source_channel_key, display_name,
                       assigned_number, tvg_id, source_tvg_id, raw_json, league_id,
                       event_start, event_title, event_end
                FROM sports_generated
                """
            ).fetchall()
            for legacy_row in legacy_rows:
                old_tvg_id = str(legacy_row["tvg_id"] or "")
                new_tvg_id = old_tvg_id
                source_tvg_id = str(legacy_row["source_tvg_id"] or "")
                if not old_tvg_id.startswith("m3u-picker.sports."):
                    source_tvg_id = source_tvg_id or old_tvg_id
                    new_tvg_id = _generated_tvg_id(int(legacy_row["assigned_number"]))
                raw = _json_load(legacy_row["raw_json"], [])
                if raw and new_tvg_id != old_tvg_id:
                    raw[0] = _rewrite_extinf(
                        raw[0],
                        {"tvg-id": new_tvg_id},
                        str(legacy_row["display_name"] or "Sports event"),
                    )
                event_title = str(legacy_row["event_title"] or "").strip()
                if not event_title:
                    event_title = re.sub(
                        r"\s+—\s+[^—]+$",
                        "",
                        re.sub(
                            r"^[^•]+•\s*",
                            "",
                            str(legacy_row["display_name"] or "Sports event"),
                        ),
                    ).strip()
                event_end = legacy_row["event_end"]
                if not event_end and legacy_row["event_start"]:
                    try:
                        event_end = (
                            datetime.fromisoformat(legacy_row["event_start"])
                            + _event_duration(str(legacy_row["league_id"] or ""))
                        ).isoformat()
                    except (TypeError, ValueError, OverflowError):
                        event_end = None
                conn.execute(
                    """
                    UPDATE sports_generated
                    SET tvg_id = ?, source_tvg_id = ?, raw_json = ?,
                        event_title = ?, event_end = ?
                    WHERE id = ?
                    """,
                    (
                        new_tvg_id,
                        source_tvg_id,
                        json.dumps(raw),
                        event_title,
                        event_end,
                        legacy_row["id"],
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (guide_migration_key, json.dumps(True)),
            )

        # Temporary sports channels reuse fixed numbered slots. Keep the XMLTV
        # identity tied to that slot rather than to a changing event or stream
        # URL so Jellyfin does not lose its guide mapping after each daily scan.
        slot_guide_migration_key = "migration_generated_xmltv_slot_ids_v20_7"
        slot_guide_migrated = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?",
            (slot_guide_migration_key,),
        ).fetchone()
        if not slot_guide_migrated:
            slot_rows = conn.execute(
                """
                SELECT id, assigned_number, display_name, tvg_id, raw_json
                FROM sports_generated
                """
            ).fetchall()
            for slot_row in slot_rows:
                new_tvg_id = _generated_tvg_id(int(slot_row["assigned_number"]))
                raw = _json_load(slot_row["raw_json"], [])
                if raw:
                    raw[0] = _rewrite_extinf(
                        raw[0],
                        {"tvg-id": new_tvg_id},
                        str(slot_row["display_name"] or "Sports event"),
                    )
                conn.execute(
                    """
                    UPDATE sports_generated
                    SET tvg_id = ?, raw_json = ?
                    WHERE id = ?
                    """,
                    (new_tvg_id, json.dumps(raw), slot_row["id"]),
                )
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (slot_guide_migration_key, json.dumps(True)),
            )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sports_scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                event_count INTEGER NOT NULL DEFAULT 0,
                channel_count INTEGER NOT NULL DEFAULT 0,
                target_date TEXT,
                trigger TEXT NOT NULL DEFAULT 'manual'
            )
            """
        )

        # Migrate the old split hour/minute settings before inserting defaults.
        refresh_row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = 'refresh_time'"
        ).fetchone()
        if refresh_row is None:
            legacy = {
                row["key"]: _json_load(row["value"], row["value"])
                for row in conn.execute(
                    "SELECT key, value FROM sports_settings "
                    "WHERE key IN ('refresh_hour', 'refresh_minute')"
                )
            }
            hour, minute = _refresh_time_parts(legacy)
            conn.execute(
                "INSERT INTO sports_settings(key, value) VALUES ('refresh_time', ?)",
                (json.dumps(f"{hour:02d}:{minute:02d}"),),
            )

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO sports_settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

        now = _now_iso()
        for scope_type, scope_id, name, subtitle, league_id, aliases, logo, metadata in SEED_CATALOG:
            conn.execute(
                """
                INSERT INTO sports_catalog
                    (scope_type, scope_id, display_name, subtitle, league_id,
                     aliases_json, logo_url, metadata_json, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seed', ?)
                ON CONFLICT(scope_type, scope_id) DO NOTHING
                """,
                (
                    scope_type,
                    scope_id,
                    name,
                    subtitle,
                    league_id,
                    json.dumps(aliases),
                    logo,
                    json.dumps(metadata),
                    now,
                ),
            )

        # v20.1 inserted four demonstration rules. Remove that exact untouched
        # set once during migration, while preserving any customized or expanded
        # rule list. Fresh installs intentionally start with zero selections.
        migration_key = "migration_removed_v20_1_demo_rules"
        migrated = conn.execute(
            "SELECT 1 FROM sports_settings WHERE key = ?", (migration_key,)
        ).fetchone()
        if not migrated:
            rows = conn.execute(
                """
                SELECT scope_type, scope_id, created_at, updated_at
                FROM sports_rules
                """
            ).fetchall()
            signature = {(row["scope_type"], row["scope_id"]) for row in rows}
            untouched = all(row["created_at"] == row["updated_at"] for row in rows)
            if len(rows) == len(LEGACY_DEMO_RULES) and signature == LEGACY_DEMO_RULES and untouched:
                conn.execute("DELETE FROM sports_rules")
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (migration_key, json.dumps(True)),
            )

        # Catalog entries are choices, not assumptions about what the user follows.
        conn.commit()


def get_settings(db_path: Path | str) -> dict:
    init_db(db_path)
    output = dict(DEFAULT_SETTINGS)
    with closing(_connect(db_path)) as conn:
        for row in conn.execute("SELECT key, value FROM sports_settings"):
            output[row["key"]] = _json_load(row["value"], row["value"])

    hour, minute = _refresh_time_parts(output)
    output["refresh_time"] = f"{hour:02d}:{minute:02d}"
    # Do not expose obsolete keys back to the browser.
    output.pop("refresh_hour", None)
    output.pop("refresh_minute", None)
    return output


def update_settings(db_path: Path | str, changes: dict) -> dict:
    allowed = set(DEFAULT_SETTINGS)
    clean = {key: value for key, value in changes.items() if key in allowed}

    for key in ("enabled", "auto_update", "include_replays", "include_pregame", "use_backup_feeds"):
        if key in clean:
            clean[key] = bool(clean[key])
    if "start_channel" in clean:
        try:
            clean["start_channel"] = max(1, int(clean["start_channel"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Starting channel must be a positive whole number.") from exc
    if "channels_per_event" in clean:
        try:
            clean["channels_per_event"] = min(50, max(1, int(clean["channels_per_event"])))
        except (TypeError, ValueError) as exc:
            raise ValueError("Channels per event must be a whole number from 1 to 50.") from exc
    if "refresh_time" in clean:
        clean["refresh_time"] = _canonical_refresh_time(clean["refresh_time"])

    # Accept the old split API fields for one release so an older open browser
    # tab cannot corrupt the scheduler. They are immediately converted to HH:MM.
    if "refresh_time" not in clean and ("refresh_hour" in changes or "refresh_minute" in changes):
        current_hour, current_minute = _refresh_time_parts(get_settings(db_path))
        try:
            hour = int(changes.get("refresh_hour", current_hour))
            minute = int(changes.get("refresh_minute", current_minute))
        except (TypeError, ValueError) as exc:
            raise ValueError("Refresh time must be a valid time such as 03:00.") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Refresh time must be a valid time such as 03:00.")
        clean["refresh_time"] = f"{hour:02d}:{minute:02d}"

    if "group_title" in clean:
        clean["group_title"] = str(clean["group_title"]).strip()[:120] or "Sports Today"
    if "timezone" in clean:
        clean["timezone"] = str(clean["timezone"]).strip()
        try:
            ZoneInfo(clean["timezone"])
        except Exception as exc:
            raise ValueError("Choose a valid timezone.") from exc
    if "event_window" in clean and clean["event_window"] not in {"today", "today_tomorrow", "next_24_hours"}:
        clean["event_window"] = "today"

    with closing(_connect(db_path)) as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
        # Retire the legacy split values after any successful write.
        conn.execute("DELETE FROM sports_settings WHERE key IN ('refresh_hour', 'refresh_minute')")
        conn.commit()
    return get_settings(db_path)


def _catalog_rows(db_path: Path | str, scope_type: str = "") -> list[dict]:
    init_db(db_path)
    sql = """
        SELECT scope_type, scope_id, display_name, subtitle, league_id,
               aliases_json, logo_url, metadata_json, source, updated_at
        FROM sports_catalog
    """
    params: tuple = ()
    if scope_type in SCOPE_TYPES:
        sql += " WHERE scope_type = ?"
        params = (scope_type,)
    sql += " ORDER BY scope_type, display_name COLLATE NOCASE"
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["id"] = item.pop("scope_id")
        item["name"] = item.pop("display_name")
        item["aliases"] = _json_load(item.pop("aliases_json"), [])
        item["metadata"] = _json_load(item.pop("metadata_json"), {})
        output.append(item)
    return output


def catalog_payload(db_path: Path | str, query: str = "", scope_type: str = "") -> list[dict]:
    query_norm = _normalize(query)
    output = []
    for item in _catalog_rows(db_path, scope_type):
        haystack = _normalize(
            " ".join(
                [
                    item["name"],
                    item["subtitle"],
                    item["id"],
                    " ".join(item["aliases"]),
                ]
            )
        )
        if not query_norm or query_norm in haystack:
            output.append(item)
    return output


def _upsert_catalog_item(
    conn: sqlite3.Connection,
    *,
    scope_type: str,
    scope_id: str,
    display_name: str,
    subtitle: str,
    league_id: str,
    aliases: list[str],
    logo_url: str,
    metadata: dict,
    source: str,
) -> None:
    conn.execute(
        """
        INSERT INTO sports_catalog
            (scope_type, scope_id, display_name, subtitle, league_id,
             aliases_json, logo_url, metadata_json, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope_type, scope_id) DO UPDATE SET
            display_name = excluded.display_name,
            subtitle = excluded.subtitle,
            league_id = excluded.league_id,
            aliases_json = excluded.aliases_json,
            logo_url = CASE
                WHEN excluded.logo_url <> '' THEN excluded.logo_url
                ELSE sports_catalog.logo_url
            END,
            metadata_json = excluded.metadata_json,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (
            scope_type,
            scope_id,
            display_name,
            subtitle,
            league_id,
            json.dumps(sorted(set(alias for alias in aliases if alias))),
            logo_url,
            json.dumps(metadata),
            source,
            _now_iso(),
        ),
    )


def _team_feed_identity(channel: dict) -> tuple[str, str, str] | None:
    name = str(channel.get("name", "")).strip()
    for league_id, pattern in TEAM_FEED_PATTERNS:
        match = pattern.match(name)
        if not match:
            continue
        team = _smart_team_name(match.group("team"))
        normalized = _normalize(team)
        if not normalized or any(word in normalized for word in NETWORK_WORDS):
            continue
        if re.fullmatch(r"\d+|\d+\s*(am|pm)?", normalized):
            continue
        return league_id, f"{league_id}:{_slug(team)}", team
    return None


def _known_mlb_aliases(team_name: str) -> list[str]:
    return list(MLB_ALIASES_BY_NAME.get(_normalize(team_name), []))


def discover_catalog_from_channels(db_path: Path | str, channels: Iterable[dict]) -> int:
    """Cache provider-discovered team names and logos in SQLite."""
    init_db(db_path)
    discovered: dict[tuple[str, str], tuple[str, list[str], str]] = {}
    for channel in channels:
        identity = _team_feed_identity(channel)
        if not identity:
            continue
        league_id, team_id, team_name = identity
        aliases = [team_name]
        if league_id == "mlb":
            aliases.extend(_known_mlb_aliases(team_name))
        words = team_name.split()
        if len(words) >= 2:
            aliases.append(words[-1])
            aliases.append(" ".join(words[-2:]))
        logo = str(channel.get("tvg_logo", "") or "")
        discovered[(league_id, team_id)] = (team_name, aliases, logo)

    with closing(_connect(db_path)) as conn:
        for (league_id, team_id), (name, aliases, logo) in discovered.items():
            _upsert_catalog_item(
                conn,
                scope_type="team",
                scope_id=team_id,
                display_name=name,
                subtitle=f"{LEAGUE_NAMES.get(league_id, league_id.upper())} team • home and away games",
                league_id=league_id,
                aliases=aliases,
                logo_url=logo,
                metadata={},
                source="provider",
            )
        conn.commit()
    return len(discovered)


def get_rules(db_path: Path | str) -> list[dict]:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, scope_type, scope_id, display_name,
                   feed_preference, enabled, created_at, updated_at
            FROM sports_rules
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) | {"enabled": bool(row["enabled"])} for row in rows]


def add_rules(db_path: Path | str, payloads: list[dict]) -> list[dict]:
    if not isinstance(payloads, list) or not payloads:
        raise ValueError("Choose at least one sports selection.")
    if len(payloads) > 100:
        raise ValueError("Add no more than 100 sports selections at once.")

    catalog = {
        (item["scope_type"], item["id"]): item
        for item in catalog_payload(db_path)
    }
    prepared = []
    for payload in payloads:
        scope_type = str(payload.get("scope_type", "")).strip().lower()
        scope_id = str(payload.get("scope_id", "")).strip().lower()
        preference = str(payload.get("feed_preference", "best")).strip().lower() or "best"
        if scope_type not in SCOPE_TYPES or not scope_id:
            raise ValueError("Choose a valid sports selection.")
        item = catalog.get((scope_type, scope_id))
        if not item:
            raise ValueError("One of the selected items is not in the cached sports catalog.")
        if preference not in {"best", "all", "favorite", "home", "away", "national"}:
            preference = "best"
        prepared.append((scope_type, scope_id, item["name"], preference))

    now = _now_iso()
    with closing(_connect(db_path)) as conn:
        for scope_type, scope_id, display_name, preference in prepared:
            conn.execute(
                """
                INSERT INTO sports_rules
                    (scope_type, scope_id, display_name, feed_preference,
                     enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(scope_type, scope_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    feed_preference = excluded.feed_preference,
                    enabled = 1,
                    updated_at = excluded.updated_at
                """,
                (scope_type, scope_id, display_name, preference, now, now),
            )
        conn.commit()
    return get_rules(db_path)


def add_rule(db_path: Path | str, payload: dict) -> dict:
    rules = add_rules(db_path, [payload])
    scope_type = str(payload.get("scope_type", "")).strip().lower()
    scope_id = str(payload.get("scope_id", "")).strip().lower()
    return next(
        rule for rule in rules
        if rule["scope_type"] == scope_type and rule["scope_id"] == scope_id
    )


def update_rule(db_path: Path | str, rule_id: int, payload: dict) -> dict:
    fields = []
    values = []
    if "feed_preference" in payload:
        preference = str(payload["feed_preference"]).strip().lower()
        if preference not in {"best", "all", "favorite", "home", "away", "national"}:
            preference = "best"
        fields.append("feed_preference = ?")
        values.append(preference)
    if "enabled" in payload:
        fields.append("enabled = ?")
        values.append(1 if payload["enabled"] else 0)
    if not fields:
        raise ValueError("Nothing to update.")
    fields.append("updated_at = ?")
    values.append(_now_iso())
    values.append(int(rule_id))
    with closing(_connect(db_path)) as conn:
        conn.execute(
            f"UPDATE sports_rules SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()
    return next((rule for rule in get_rules(db_path) if rule["id"] == int(rule_id)), {})


def delete_rule(db_path: Path | str, rule_id: int) -> bool:
    with closing(_connect(db_path)) as conn:
        cursor = conn.execute("DELETE FROM sports_rules WHERE id = ?", (int(rule_id),))
        conn.commit()
        return cursor.rowcount > 0


def _sports_day(now: datetime, settings: dict) -> date:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = now.astimezone(timezone)
    refresh_hour, refresh_minute = _refresh_time_parts(settings)
    refresh = dt_time(refresh_hour, refresh_minute)
    if local_now.time().replace(tzinfo=None) < refresh:
        return local_now.date() - timedelta(days=1)
    return local_now.date()


def _target_window(now: datetime, settings: dict) -> tuple[datetime, datetime, date]:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = now.astimezone(timezone)
    sports_day = _sports_day(now, settings)
    boundary = datetime.combine(
        sports_day,
        dt_time(*_refresh_time_parts(settings)),
        tzinfo=timezone,
    )
    mode = settings.get("event_window", "today")
    if mode == "next_24_hours":
        return local_now, local_now + timedelta(hours=24), sports_day
    if mode == "today_tomorrow":
        return boundary, boundary + timedelta(days=2), sports_day
    return boundary, boundary + timedelta(days=1), sports_day


def next_update_at(db_path: Path | str, now: datetime | None = None) -> datetime:
    settings = get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (now or datetime.now().astimezone()).astimezone(timezone)
    refresh_hour, refresh_minute = _refresh_time_parts(settings)
    target = local_now.replace(
        hour=refresh_hour,
        minute=refresh_minute,
        second=0,
        microsecond=0,
    )
    if target <= local_now:
        target += timedelta(days=1)
    return target


def should_run_scheduled(db_path: Path | str, now: datetime | None = None) -> bool:
    settings = get_settings(db_path)
    if not settings.get("enabled") or not settings.get("auto_update"):
        return False
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (now or datetime.now().astimezone()).astimezone(timezone)
    refresh_hour, refresh_minute = _refresh_time_parts(settings)
    if (local_now.hour, local_now.minute) != (refresh_hour, refresh_minute):
        return False

    last = last_scan(db_path)
    if not last:
        return True

    # A successful manual scan before the refresh boundary can target yesterday's
    # sports day, so compare target dates rather than calendar dates.
    target_date = _sports_day(local_now, settings).isoformat()
    if last.get("status") == "success" and last.get("target_date") == target_date:
        return False

    # The scheduler wakes every 30 seconds. Do not retry a failed scheduled scan
    # twice during the same configured minute and hammer the provider.
    if last.get("trigger") == "scheduled":
        try:
            attempted = datetime.fromisoformat(last["started_at"]).astimezone(timezone)
            if (
                attempted.date() == local_now.date()
                and (attempted.hour, attempted.minute) == (refresh_hour, refresh_minute)
            ):
                return False
        except Exception:
            pass
    return True


def derive_xmltv_url(source_url: str) -> str:
    """Derive a conventional Xtream XMLTV URL without exposing credentials."""
    if not source_url:
        return ""
    parsed = urllib.parse.urlparse(source_url)
    query = urllib.parse.parse_qs(parsed.query)
    username = query.get("username", [""])[-1]
    password = query.get("password", [""])[-1]
    if not parsed.scheme or not parsed.netloc or not username or not password:
        return ""
    encoded = urllib.parse.urlencode({"username": username, "password": password})
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "/xmltv.php", "", encoded, "")
    )


def refresh_epg_cache(source_url: str, cache_path: Path, timeout: int = 120) -> tuple[bool, str]:
    xmltv_url = derive_xmltv_url(source_url)
    if not xmltv_url:
        return False, "No Xtream XMLTV URL could be derived."

    request = urllib.request.Request(
        xmltv_url,
        headers={
            "User-Agent": "M3U-Web-Picker/2.0",
            "Accept": "application/xml,text/xml,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_encoding = str(response.headers.get("Content-Encoding", "")).lower()
    except Exception as exc:
        return False, f"EPG refresh failed: {exc}"

    try:
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
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp_path.write_bytes(raw)
        temp_path.replace(cache_path)
        return True, f"Cached {len(raw)} bytes of XMLTV data."
    except Exception as exc:
        return False, f"EPG cache validation failed: {exc}"


def _parse_xmltv_time(value: str, default_tz: ZoneInfo) -> datetime | None:
    value = str(value or "").strip()
    match = re.match(r"^(\d{14})(?:\s+([+-]\d{4}|Z))?", value)
    if not match:
        return None
    try:
        base = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
        offset = match.group(2)
        if not offset:
            return base.replace(tzinfo=default_tz)
        if offset == "Z":
            return base.replace(tzinfo=ZoneInfo("UTC"))
        sign = 1 if offset.startswith("+") else -1
        hours = int(offset[1:3])
        minutes = int(offset[3:5])
        if hours > 23 or minutes > 59:
            raise ValueError("invalid XMLTV UTC offset")
        from datetime import timezone

        return base.replace(
            tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes))
        )
    except (ValueError, OverflowError) as exc:
        raise MalformedSportsEntry(f"Invalid XMLTV timestamp {value!r}.") from exc


def _channel_text(channel: dict) -> str:
    return " ".join(
        str(channel.get(key, "") or "")
        for key in ("name", "tvg_name", "group", "tvg_id")
    )


def _league_matches(text: str) -> list[str]:
    normalized = str(text or "").lower()
    return [
        league_id
        for league_id, patterns in LEAGUE_PATTERNS.items()
        if any(re.search(pattern, normalized, re.I) for pattern in patterns)
    ]


def _detect_league(primary_text: str, fallback_text: str = "") -> str:
    """Detect a league without letting shared provider groups blur MLB and MiLB.

    Event titles and XMLTV categories are authoritative. Provider group/title
    metadata is only a fallback, and an ambiguous ``MLB / MiLB`` fallback is
    deliberately left unclassified rather than guessed.
    """
    primary_matches = _league_matches(primary_text)
    if primary_matches:
        # A title should normally contain only one league. If both baseball
        # tokens appear, the more specific MiLB token wins only when MLB is not
        # also explicitly present in the same title/category text.
        if "milb" in primary_matches and "mlb" not in primary_matches:
            return "milb"
        if "mlb" in primary_matches and "milb" not in primary_matches:
            return "mlb"
        if len(primary_matches) == 1:
            return primary_matches[0]
        for league_id in LEAGUE_PATTERNS:
            if league_id in primary_matches and league_id not in {"mlb", "milb"}:
                return league_id
        return ""

    fallback_matches = _league_matches(fallback_text)
    if {"mlb", "milb"}.issubset(fallback_matches):
        return ""
    return fallback_matches[0] if len(fallback_matches) == 1 else ""


def _detect_sport(text: str) -> str:
    normalized = text.lower()
    for sport_id, patterns in SPORT_PATTERNS.items():
        if any(re.search(pattern, normalized, re.I) for pattern in patterns):
            return sport_id
    return ""


def _strip_provider_prefix(value: str) -> str:
    text = value.strip()
    if "|" in text:
        prefix, remainder = text.split("|", 1)
        if re.search(r"\d|MLB|NBA|NHL|FLSP|Victory|Apple|MiLB", prefix, re.I):
            text = remainder.strip()
    text = re.sub(r"^(?:NFL|NHL|NCAAF|NCAAB|PPV|EVENTS)\s*(?:SD\s*)?\d{1,3}\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"^(?:MiLB|MLB|NBA|NHL|NFL|NCAAF|NCAAB|NWSL)\s*:\s*", "", text, flags=re.I)
    return text.strip(" |:-")


def _extract_event_datetime(text: str, settings: dict, now: datetime) -> tuple[str, datetime | None]:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    match = DATE_RE.search(text)
    if match:
        clean = text[: match.start()].strip()
        time_value = match.group("time") or "00:00:00"
        if len(time_value) == 5:
            time_value += ":00"
        timestamp = f"{match.group('date')}T{time_value}"
        try:
            start = datetime.fromisoformat(timestamp).replace(tzinfo=timezone)
        except (ValueError, OverflowError) as exc:
            raise MalformedSportsEntry(
                f"Invalid embedded event timestamp {timestamp!r}."
            ) from exc
        return clean, start

    time_match = LEADING_TIME_RE.search(text)
    if time_match:
        raw_hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute") or 0)
        ampm = time_match.group("ampm").lower()
        if not 1 <= raw_hour <= 12 or not 0 <= minute <= 59:
            raise MalformedSportsEntry(
                f"Invalid event time {time_match.group(0)!r}."
            )
        hour = raw_hour
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        try:
            start = datetime.combine(
                _sports_day(now, settings),
                dt_time(hour, minute),
                tzinfo=timezone,
            )
        except (ValueError, OverflowError) as exc:
            raise MalformedSportsEntry(
                f"Invalid event time {time_match.group(0)!r}."
            ) from exc
        return text, start
    return text, None


def _team_catalog(db_path: Path | str) -> list[dict]:
    return [item for item in catalog_payload(db_path, scope_type="team")]


def _find_team_id(text: str, league_id: str, teams: list[dict]) -> tuple[str, str]:
    normalized = _normalize(text)
    if not normalized:
        return "", text.strip()
    candidates = []
    for team in teams:
        if league_id and team.get("league_id") and team["league_id"] != league_id:
            continue
        aliases = [team["name"], *team.get("aliases", [])]
        for alias in aliases:
            alias_norm = _normalize(alias)
            if not alias_norm:
                continue
            if normalized == alias_norm or re.search(rf"(?:^|\s){re.escape(alias_norm)}(?:$|\s)", normalized):
                candidates.append((len(alias_norm), team["id"], team["name"]))
    if not candidates:
        return "", _smart_team_name(text)
    _, team_id, name = max(candidates)
    return team_id, name


def _infer_baseball_league(
    left: str,
    right: str,
    teams: list[dict],
) -> str:
    """Resolve an ambiguous shared baseball group from both participants.

    Provider groups commonly say ``MLB / MiLB``. A matchup is promoted to a
    league only when both sides resolve inside the same catalog, preventing an
    MLB nickname embedded in a minor-league team name from leaking across the
    boundary.
    """
    resolved = []
    for candidate in ("mlb", "milb"):
        away_id, _away_name = _find_team_id(left, candidate, teams)
        home_id, _home_name = _find_team_id(right, candidate, teams)
        if away_id and home_id:
            resolved.append(candidate)
    return resolved[0] if len(resolved) == 1 else ""


def _event_from_text(
    db_path: Path | str,
    channel: dict,
    text: str,
    settings: dict,
    now: datetime,
    *,
    forced_start: datetime | None = None,
    extra_text: str = "",
) -> dict | None:
    full_text = f"{text} {extra_text} {_channel_text(channel)}".strip()
    if PLACEHOLDER_RE.search(text.strip()) or REPLAY_RE.search(full_text) and not settings.get("include_replays"):
        return None
    if PREGAME_RE.search(full_text) and not settings.get("include_pregame"):
        return None

    league_id = _detect_league(text)
    if not league_id and extra_text:
        league_id = _detect_league(extra_text)
    if not league_id:
        league_id = _detect_league("", _channel_text(channel))
    sport_id = _detect_sport(full_text)
    cleaned, parsed_start = _extract_event_datetime(_strip_provider_prefix(text), settings, now)
    start = forced_start or parsed_start
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" |:-")

    match = MATCHUP_RE.search(cleaned)
    # Team-sport league rules are intended to add games, not static networks,
    # studio shows, RedZone channels, or numbered empty event slots.
    if league_id in {"nfl", "mlb", "milb", "nba", "nhl", "wnba", "ncaaf", "ncaab", "mls", "nwsl"} and not match:
        return None
    teams = _team_catalog(db_path)
    away_id = home_id = ""
    away_name = home_name = ""
    if match:
        left = match.group("left").strip(" |:-")
        right = match.group("right").strip(" |:-")
        if not league_id:
            league_id = _infer_baseball_league(left, right, teams)
        away_id, away_name = _find_team_id(left, league_id, teams)
        home_id, home_name = _find_team_id(right, league_id, teams)
        display_name = f"{away_name} at {home_name}"
    else:
        display_name = cleaned

    meaningful = bool(match or start or sport_id)
    if not meaningful or not display_name:
        return None
    if not match and re.fullmatch(r"(?:\d{1,2}\s*(?:am|pm)?|[A-Z0-9 ]*NETWORK|ESPN\d?|FOX|CBS|NBC|TNT|TBS)", display_name, re.I):
        return None

    if not start:
        # Provider event/PPV slots without explicit dates are treated as belonging
        # to the current sports day. Permanent team feeds are removed earlier.
        timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
        start = datetime.combine(_sports_day(now, settings), dt_time(12, 0), tzinfo=timezone)

    event_date = start.astimezone(ZoneInfo(str(settings.get("timezone", "America/New_York")))).date()
    identity = "-".join(filter(None, [league_id or sport_id or "sports", away_id or _slug(away_name), home_id or _slug(home_name)]))
    if not match:
        identity = "-".join(filter(None, [league_id or sport_id or "sports", _slug(display_name)]))
    event_key = f"{event_date.isoformat()}:{identity}"

    return {
        "event_key": event_key,
        "league_id": league_id,
        "sport_id": sport_id,
        "display_name": display_name,
        "away_team_id": away_id,
        "away_team_name": away_name,
        "home_team_id": home_id,
        "home_team_name": home_name,
        "start": start,
        "source_channels": [channel],
        "source_text": full_text,
        "is_replay": bool(REPLAY_RE.search(full_text)),
    }


def _within_window(start: datetime, window_start: datetime, window_end: datetime) -> bool:
    try:
        local = start.astimezone(window_start.tzinfo)
    except Exception:
        return False
    return window_start <= local < window_end


def _m3u_events(
    db_path: Path | str,
    channels: Iterable[dict],
    settings: dict,
    now: datetime,
    diagnostics: dict,
) -> list[dict]:
    window_start, window_end, _ = _target_window(now, settings)
    events = []
    for channel in channels:
        if _team_feed_identity(channel):
            continue
        text = str(channel.get("name", "") or "")
        try:
            event = _event_from_text(db_path, channel, text, settings, now)
        except MalformedSportsEntry as exc:
            _record_malformed_entry(
                diagnostics,
                source="m3u",
                label=text or str(channel.get("tvg_name", "") or ""),
                exc=exc,
            )
            continue
        if event and _within_window(event["start"], window_start, window_end):
            events.append(event)
    return events


def _epg_events(
    db_path: Path | str,
    epg_path: Path | None,
    channels: list[dict],
    settings: dict,
    now: datetime,
    diagnostics: dict,
) -> list[dict]:
    if not epg_path or not epg_path.exists() or epg_path.stat().st_size == 0:
        return []

    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    window_start, window_end, _ = _target_window(now, settings)
    by_tvg_id: dict[str, list[dict]] = defaultdict(list)
    by_name: dict[str, list[dict]] = defaultdict(list)
    for channel in channels:
        tvg_id = str(channel.get("tvg_id", "") or "").strip()
        if tvg_id:
            by_tvg_id[tvg_id].append(channel)
        for value in (channel.get("tvg_name", ""), channel.get("name", "")):
            normalized = _normalize(str(value or ""))
            if normalized:
                by_name[normalized].append(channel)

    xml_names: dict[str, list[str]] = defaultdict(list)
    output: list[dict] = []
    try:
        for _event, element in ElementTree.iterparse(epg_path, events=("end",)):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "channel":
                channel_id = element.attrib.get("id", "")
                xml_names[channel_id] = [
                    child.text.strip()
                    for child in element
                    if child.tag.rsplit("}", 1)[-1] == "display-name" and child.text
                ]
                element.clear()
                continue
            if tag != "programme":
                continue

            channel_id = element.attrib.get("channel", "")
            raw_start = element.attrib.get("start", "")
            try:
                start = _parse_xmltv_time(raw_start, timezone)
            except MalformedSportsEntry as exc:
                _record_malformed_entry(
                    diagnostics,
                    source="epg",
                    label=f"programme channel={channel_id or 'unknown'} start={raw_start or 'missing'}",
                    exc=exc,
                )
                element.clear()
                continue
            if not start or not _within_window(start, window_start, window_end):
                element.clear()
                continue

            source_channels = list(by_tvg_id.get(channel_id, []))
            if not source_channels:
                for display_name in xml_names.get(channel_id, []):
                    source_channels.extend(by_name.get(_normalize(display_name), []))
            if not source_channels:
                element.clear()
                continue

            fields: dict[str, list[str]] = defaultdict(list)
            for child in element:
                child_tag = child.tag.rsplit("}", 1)[-1]
                if child.text and child_tag in {"title", "sub-title", "desc", "category"}:
                    fields[child_tag].append(child.text.strip())
            title = fields["title"][0] if fields["title"] else ""
            extra = " ".join(fields["sub-title"] + fields["desc"] + fields["category"])
            if not title:
                element.clear()
                continue

            try:
                parsed = _event_from_text(
                    db_path,
                    source_channels[0],
                    title,
                    settings,
                    now,
                    forced_start=start,
                    extra_text=extra,
                )
            except MalformedSportsEntry as exc:
                _record_malformed_entry(
                    diagnostics,
                    source="epg",
                    label=title,
                    exc=exc,
                )
                element.clear()
                continue
            if parsed:
                parsed["source_channels"] = source_channels
                output.append(parsed)
            element.clear()
    except (ElementTree.ParseError, OSError):
        return []
    return output


def _merge_events(events: Iterable[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for event in events:
        existing = merged.get(event["event_key"])
        if not existing:
            merged[event["event_key"]] = event
            continue
        seen = {str(ch.get("url", "")) for ch in existing["source_channels"]}
        for channel in event["source_channels"]:
            if str(channel.get("url", "")) not in seen:
                existing["source_channels"].append(channel)
        if event.get("start") and (not existing.get("start") or event["start"] < existing["start"]):
            existing["start"] = event["start"]
    return list(merged.values())


def _conference_matches(event: dict, conference_id: str) -> bool:
    if event.get("league_id") != "ncaaf":
        return False
    team_names = CONFERENCE_TEAMS.get(conference_id, [])
    participant_text = _normalize(
        f"{event.get('away_team_name', '')} {event.get('home_team_name', '')}"
    )
    return any(_normalize(team) in participant_text for team in team_names)


def _matching_rules(event: dict, rules: list[dict]) -> list[dict]:
    matched = []
    for rule in rules:
        scope_type = rule["scope_type"]
        scope_id = rule["scope_id"]
        if scope_type == "league" and event.get("league_id") == scope_id:
            matched.append(rule)
        elif scope_type == "team" and scope_id in {
            event.get("away_team_id"),
            event.get("home_team_id"),
        }:
            matched.append(rule)
        elif scope_type == "conference" and _conference_matches(event, scope_id):
            matched.append(rule)
        elif scope_type == "sport":
            if event.get("sport_id") == scope_id:
                matched.append(rule)
            elif any(re.search(pattern, event.get("source_text", ""), re.I) for pattern in SPORT_PATTERNS.get(scope_id, [])):
                matched.append(rule)
    return sorted(matched, key=lambda rule: (RULE_PRIORITY.get(rule["scope_type"], 99), rule["id"]))


def _team_feeds(channels: Iterable[dict]) -> dict[str, list[dict]]:
    output: dict[str, list[dict]] = defaultdict(list)
    for channel in channels:
        identity = _team_feed_identity(channel)
        if identity:
            _league_id, team_id, _team_name = identity
            output[team_id].append(channel)
    return output


def _feed_type(channel: dict, event: dict, team_id: str = "") -> str:
    text = _channel_text(channel).lower()
    if "backup" in text:
        return "backup"
    if re.search(r"espa[nñ]ol|spanish|\bes\b", text, re.I):
        return "spanish"
    if team_id and team_id == event.get("away_team_id"):
        return "away"
    if team_id and team_id == event.get("home_team_id"):
        return "home"
    if any(word in text for word in NETWORK_WORDS):
        return "national"
    return "event"


def _feed_label(feed_type: str, event: dict, team_id: str) -> tuple[str, str]:
    if feed_type == "away":
        team = event.get("away_team_name") or "Away"
        return f"{team.split()[-1]} Feed", f"Away broadcast • {team}"
    if feed_type == "home":
        team = event.get("home_team_name") or "Home"
        return f"{team.split()[-1]} Feed", f"Home broadcast • {team}"
    if feed_type == "national":
        return "National Feed", "National broadcast"
    if feed_type == "spanish":
        return "Spanish Feed", "Spanish-language broadcast"
    if feed_type == "backup":
        return "Backup Feed", "Backup stream"
    return "Event Feed", "Provider event stream"


def _build_feeds(event: dict, channels: list[dict], rule: dict, settings: dict) -> list[dict]:
    team_feed_map = _team_feeds(channels)
    candidates = []
    seen_urls = set()

    def add(channel: dict, team_id: str = "") -> None:
        url = str(channel.get("url", "") or "").strip()
        if not url or url in seen_urls:
            return
        seen_urls.add(url)
        kind = _feed_type(channel, event, team_id)
        candidates.append({"channel": channel, "feed_type": kind, "team_id": team_id})

    for source in event.get("source_channels", []):
        add(source)
    for team_id in (event.get("away_team_id"), event.get("home_team_id")):
        if team_id:
            for channel in team_feed_map.get(team_id, []):
                add(channel, team_id)

    if not settings.get("use_backup_feeds"):
        candidates = [candidate for candidate in candidates if candidate["feed_type"] != "backup"]
    elif any(candidate["feed_type"] != "backup" for candidate in candidates):
        # Backups stay hidden unless the user explicitly asks for all feeds.
        if rule.get("feed_preference") != "all":
            candidates = [candidate for candidate in candidates if candidate["feed_type"] != "backup"]

    preference = rule.get("feed_preference", "best")
    favorite_team_id = rule.get("scope_id") if rule.get("scope_type") == "team" else ""

    rank = {
        "national": 20,
        "event": 25,
        "home": 30,
        "away": 31,
        "spanish": 50,
        "backup": 90,
    }
    if preference == "favorite" and favorite_team_id:
        for candidate in candidates:
            if candidate["team_id"] == favorite_team_id:
                rank[candidate["feed_type"]] = -10
    elif preference == "home":
        rank["home"] = -10
    elif preference == "away":
        rank["away"] = -10
    elif preference == "national":
        rank["national"] = -10
        rank["event"] = 0

    candidates.sort(
        key=lambda candidate: (
            -10 if favorite_team_id and candidate["team_id"] == favorite_team_id else rank.get(candidate["feed_type"], 60),
            str(candidate["channel"].get("name", "")).lower(),
        )
    )

    if rule.get("scope_type") in {"league", "conference", "sport"} and preference == "best":
        return candidates[:1]
    return candidates


def _rewrite_extinf(line: str, attrs: dict[str, str], display_name: str) -> str:
    if not line.startswith("#EXTINF"):
        line = "#EXTINF:-1,"
    left = line.rsplit(",", 1)[0] if "," in line else line
    for key, value in attrs.items():
        escaped = str(value).replace('"', "'")
        if re.search(rf'{re.escape(key)}="[^"]*"', left):
            left = re.sub(rf'{re.escape(key)}="[^"]*"', f'{key}="{escaped}"', left)
        else:
            left += f' {key}="{escaped}"'
    return f"{left},{display_name}"


def _generated_raw(channel: dict, generated: dict) -> list[str]:
    raw = list(channel.get("raw", []))
    if not raw:
        raw = ["#EXTINF:-1", generated["url"]]
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
    if raw[-1] != generated["url"]:
        raw[-1] = generated["url"]
    return raw



def _generated_tvg_id(assigned_number: int) -> str:
    """Return a credential-free XMLTV id stable for one numbered sports slot.

    Event names and provider URLs change every day. Jellyfin can retain a tuner
    channel between refreshes, so tying the guide id to either value can leave
    the retained channel mapped to yesterday's XMLTV id. The assigned sports
    channel number is the durable identity the user configured.
    """
    number = int(assigned_number)
    if number < 0:
        raise ValueError("Sports channel numbers must be non-negative.")
    return f"m3u-picker-sports-{number}"


def _xmltv_time(value: datetime) -> str:
    local = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
    return local.strftime("%Y%m%d%H%M%S %z")


def _parse_iso_datetime(value: str | None, fallback_tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=fallback_tz)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _event_duration(league_id: str) -> timedelta:
    return timedelta(hours=ESTIMATED_EVENT_HOURS.get(league_id, 3))


def _clean_feed_subtitle(value: str) -> str:
    # The M3U subtitle already carries the start time. XMLTV has explicit times,
    # so omit the duplicate trailing time from programme subtitles.
    return re.sub(r"\s*•\s*\d{1,2}:\d{2}\s+(?:AM|PM)\s*$", "", value or "", flags=re.I).strip()


def _add_text(parent: ElementTree.Element, tag: str, text: str, **attrs) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, tag, {key: str(value) for key, value in attrs.items()})
    element.text = str(text)
    return element


def _add_programme(
    root: ElementTree.Element,
    *,
    channel_id: str,
    start: datetime,
    stop: datetime,
    title: str,
    subtitle: str,
    description: str,
    categories: Iterable[str],
    is_live: bool = False,
    is_replay: bool = False,
) -> bool:
    if stop <= start:
        return False
    programme = ElementTree.SubElement(
        root,
        "programme",
        {
            "start": _xmltv_time(start),
            "stop": _xmltv_time(stop),
            "channel": channel_id,
        },
    )
    _add_text(programme, "title", title, lang="en")
    if subtitle:
        _add_text(programme, "sub-title", subtitle, lang="en")
    if description:
        _add_text(programme, "desc", description, lang="en")
    for category in categories:
        if category:
            _add_text(programme, "category", category, lang="en")
    if is_live:
        ElementTree.SubElement(programme, "live")
    if is_replay:
        ElementTree.SubElement(programme, "previously-shown")
    return True


def _guide_coverage_window(anchor: datetime, settings: dict) -> tuple[datetime, datetime]:
    """Return a continuous guide window around the current sports lineup.

    Generated channels may survive a container restart with a provider timestamp
    that has already gone stale. Keep those channels populated in Jellyfin while
    the next scan corrects or replaces them instead of exporting a blank guide.
    """
    target_start, target_end, _ = _target_window(anchor, settings)
    return (
        min(target_start, anchor - timedelta(hours=6)),
        max(target_end, anchor + timedelta(hours=30)),
    )


def build_sports_xmltv(
    generated: list[dict],
    settings: dict,
    *,
    generated_at: datetime | None = None,
) -> bytes:
    """Build a standalone XMLTV guide for generated sports channels."""
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    anchor = (generated_at or datetime.now().astimezone()).astimezone(timezone)
    root = ElementTree.Element(
        "tv",
        {
            "generator-info-name": XMLTV_GENERATOR_NAME,
            "source-info-name": "Generated sports guide",
        },
    )

    for item in generated:
        channel_id = str(item.get("tvg_id", "") or "").strip()
        if not channel_id:
            continue
        channel = ElementTree.SubElement(root, "channel", {"id": channel_id})
        _add_text(channel, "display-name", item.get("display_name", "Sports"), lang="en")
        # Jellyfin can map guide channels by channel number as well as id/name.
        # Keep a purely numeric alias in addition to the friendly display name.
        assigned_number = str(item.get("assigned_number", "") or "").strip()
        if assigned_number:
            _add_text(channel, "display-name", assigned_number, lang="en")
            _add_text(channel, "display-name", f"CH {assigned_number}", lang="en")
        logo = str(item.get("tvg_logo", "") or "").strip()
        if logo:
            ElementTree.SubElement(channel, "icon", {"src": logo})

    for item in generated:
        channel_id = str(item.get("tvg_id", "") or "").strip()
        if not channel_id:
            continue
        league_id = str(item.get("league_id", "") or "")
        league_label = LEAGUE_NAMES.get(league_id, "Sports")
        event_title = str(item.get("event_title", "") or "").strip()
        if not event_title:
            # Migration fallback for rows generated before event_title existed.
            display = str(item.get("display_name", "Sports event") or "Sports event")
            event_title = re.sub(r"^[^•]+•\s*", "", display)
            event_title = re.sub(r"\s+—\s+[^—]+$", "", event_title).strip()
        feed_subtitle = _clean_feed_subtitle(str(item.get("subtitle", "") or ""))
        start = _parse_iso_datetime(item.get("event_start"), timezone)
        end = _parse_iso_datetime(item.get("event_end"), timezone)
        is_replay = bool(item.get("is_replay"))
        categories = ["Sports", league_label]
        if is_replay:
            categories.append("Replay")

        coverage_start, coverage_end = _guide_coverage_window(anchor, settings)
        if start:
            local_start = start.astimezone(timezone)
            live_end = (end or (start + _event_duration(league_id))).astimezone(timezone)
            if live_end <= local_start:
                live_end = local_start + _event_duration(league_id)
            scheduled = local_start.strftime("%A, %B %-d at %-I:%M %p %Z")

            # A provider timestamp can lag behind the actual event slot. When
            # the entire scheduled interval is already outside the active guide
            # window, export one continuous fallback programme instead of a
            # blank channel. The next successful scan can replace it with exact
            # upcoming/live/postgame segments.
            schedule_is_stale = live_end + timedelta(hours=GUIDE_POSTGAME_HOURS) <= coverage_start
            if schedule_is_stale:
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=coverage_start,
                    stop=coverage_end,
                    title=f"{league_label} • {event_title}",
                    subtitle=feed_subtitle,
                    description=(
                        "Generated sports event channel. Provider schedule data was stale or unavailable; "
                        "guide coverage is being held until the next refresh."
                    ),
                    categories=categories,
                    is_replay=is_replay,
                )
                continue

            # Cover the entire active channel window with non-overlapping XMLTV
            # programmes. This prevents Jellyfin gaps before first pitch, during
            # the event, or after a container restart.
            upcoming_stop = min(local_start, coverage_end)
            if coverage_start < upcoming_stop:
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=coverage_start,
                    stop=upcoming_stop,
                    title=f"Upcoming: {event_title}",
                    subtitle=feed_subtitle,
                    description=f"{league_label} event scheduled for {scheduled}. {feed_subtitle}.",
                    categories=categories,
                )

            live_start = max(local_start, coverage_start)
            live_stop = min(live_end, coverage_end)
            live_prefix = "Replay" if is_replay else league_label
            _add_programme(
                root,
                channel_id=channel_id,
                start=live_start,
                stop=live_stop,
                title=f"{live_prefix} • {event_title}",
                subtitle=feed_subtitle,
                description=f"{event_title}. {feed_subtitle}.",
                categories=categories,
                is_live=not is_replay,
                is_replay=is_replay,
            )

            post_start = max(live_end, coverage_start)
            if post_start < coverage_end:
                _add_programme(
                    root,
                    channel_id=channel_id,
                    start=post_start,
                    stop=coverage_end,
                    title=f"{event_title} — Event window",
                    subtitle=feed_subtitle,
                    description="The generated event channel remains available until the next sports refresh.",
                    categories=categories,
                )
        else:
            # Exact provider schedule is unavailable. Cover the active lineup
            # continuously so Jellyfin never presents an empty generated channel.
            _add_programme(
                root,
                channel_id=channel_id,
                start=coverage_start,
                stop=coverage_end,
                title=f"{league_label} • {event_title}",
                subtitle=feed_subtitle,
                description="Provider sports event or replay; exact schedule data was unavailable.",
                categories=categories,
                is_replay=is_replay,
            )

    if hasattr(ElementTree, "indent"):
        ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _xmltv_fragments(elements: Iterable[ElementTree.Element]) -> bytes:
    """Serialize XMLTV children without introducing a second XML declaration."""
    return b"\n".join(
        ElementTree.tostring(child, encoding="unicode", short_empty_elements=True).encode(
            "ascii", errors="xmlcharrefreplace"
        )
        for child in elements
    )


def build_combined_xmltv(base_epg_path: Path | None, sports_xmltv: bytes) -> bytes:
    """Merge generated guide data while preserving XMLTV element ordering.

    XMLTV requires all ``channel`` elements to precede ``programme`` elements.
    Older builds appended the complete sports document just before ``</tv>``,
    which placed generated channels after the provider's programmes. Lenient XML
    parsers accepted the file, but Jellyfin could ignore those late channel
    definitions and therefore show blank guide rows.
    """
    if not base_epg_path or not base_epg_path.exists() or base_epg_path.stat().st_size == 0:
        return sports_xmltv

    base = base_epg_path.read_bytes()
    close_matches = list(re.finditer(rb"</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?tv\s*>", base, flags=re.I))
    if not close_matches:
        # A broken provider guide must not prevent generated sports guide data.
        return sports_xmltv

    overlay_root = ElementTree.fromstring(sports_xmltv)
    channels = [child for child in overlay_root if child.tag.rsplit("}", 1)[-1] == "channel"]
    programmes = [child for child in overlay_root if child.tag.rsplit("}", 1)[-1] == "programme"]
    channel_fragment = _xmltv_fragments(channels)
    programme_fragment = _xmltv_fragments(programmes)

    close_match = close_matches[-1]
    close_start = close_match.start()
    programme_match = re.search(
        rb"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?programme(?:\s|>)",
        base[:close_start],
        flags=re.I,
    )
    channel_insert = programme_match.start() if programme_match else close_start

    pieces = [base[:channel_insert]]
    if channel_fragment:
        pieces.extend([b"\n", channel_fragment, b"\n"])
    pieces.append(base[channel_insert:close_start])
    if programme_fragment:
        pieces.extend([b"\n", programme_fragment, b"\n"])
    pieces.append(base[close_start:])
    return b"".join(pieces)

def _write_prepared_epg_files(
    generated: list[dict],
    settings: dict,
    *,
    base_epg_path: Path | None,
    sports_epg_path: Path | None,
    combined_epg_path: Path | None,
    generated_at: datetime,
) -> list[tuple[Path, Path]]:
    """Write validated temporary XMLTV files and return (temp, final) pairs."""
    prepared: list[tuple[Path, Path]] = []
    sports_bytes = build_sports_xmltv(generated, settings, generated_at=generated_at)
    # Validate the standalone guide before touching a live export.
    ElementTree.fromstring(sports_bytes)

    for destination, payload in (
        (sports_epg_path, sports_bytes),
        (combined_epg_path, build_combined_xmltv(base_epg_path, sports_bytes)),
    ):
        if not destination:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + ".tmp")
        temp.write_bytes(payload)
        prepared.append((temp, destination))
    return prepared


def rebuild_epg_exports(
    db_path: Path | str,
    *,
    base_epg_path: Path | None,
    sports_epg_path: Path,
    combined_epg_path: Path,
) -> None:
    """Recreate guide exports from persisted generated rows, such as at startup."""
    settings = get_settings(db_path)
    rows = generated_rows(db_path)
    generated_at = datetime.now().astimezone()
    prepared = _write_prepared_epg_files(
        rows,
        settings,
        base_epg_path=base_epg_path,
        sports_epg_path=sports_epg_path,
        combined_epg_path=combined_epg_path,
        generated_at=generated_at,
    )
    for temp, destination in prepared:
        temp.replace(destination)



def _local_xml_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _xmltv_index(path: Path | None, fallback_tz: ZoneInfo) -> dict:
    result = {
        "exists": bool(path and path.exists()),
        "channels": set(),
        "programmes": defaultdict(list),
        "error": "",
        "size": 0,
        "modified": "",
    }
    if not path or not path.exists():
        return result
    try:
        result["size"] = path.stat().st_size
        result["modified"] = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        root = ElementTree.parse(path).getroot()
        for child in root:
            tag = _local_xml_name(child.tag)
            if tag == "channel":
                channel_id = str(child.attrib.get("id", "") or "").strip()
                if channel_id:
                    result["channels"].add(channel_id)
            elif tag == "programme":
                channel_id = str(child.attrib.get("channel", "") or "").strip()
                if not channel_id:
                    continue
                start = _parse_xmltv_time(str(child.attrib.get("start", "") or ""), fallback_tz)
                stop = _parse_xmltv_time(str(child.attrib.get("stop", "") or ""), fallback_tz)
                if start and stop and stop > start:
                    result["programmes"][channel_id].append((start, stop))
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def _playlist_tvg_ids(path: Path | None) -> tuple[set[str], str]:
    if not path or not path.exists():
        return set(), "playlist file is missing"
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return {
            match.group(1).strip()
            for match in re.finditer(r'\btvg-id="([^"]+)"', text, flags=re.I)
            if match.group(1).strip()
        }, ""
    except Exception as exc:
        return set(), f"{type(exc).__name__}: {exc}"


def validate_guide_exports(
    db_path: Path | str,
    *,
    playlist_path: Path | None,
    sports_epg_path: Path | None,
    combined_epg_path: Path | None,
) -> dict:
    """Validate the exact files served to Jellyfin without exposing stream URLs."""
    settings = get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    rows = generated_rows(db_path)
    expected_ids = {str(row.get("tvg_id", "") or "").strip() for row in rows}
    expected_ids.discard("")

    playlist_ids, playlist_error = _playlist_tvg_ids(playlist_path)
    sports_index = _xmltv_index(sports_epg_path, timezone)
    combined_index = _xmltv_index(combined_epg_path, timezone)

    missing_playlist = sorted(expected_ids - playlist_ids)
    missing_sports_channels = sorted(expected_ids - sports_index["channels"])
    missing_sports_programmes = sorted(
        channel_id for channel_id in expected_ids if not sports_index["programmes"].get(channel_id)
    )
    missing_combined_channels = sorted(expected_ids - combined_index["channels"])
    missing_combined_programmes = sorted(
        channel_id for channel_id in expected_ids if not combined_index["programmes"].get(channel_id)
    )

    uncovered_event_starts = []
    for row in rows:
        channel_id = str(row.get("tvg_id", "") or "").strip()
        event_start = _parse_iso_datetime(row.get("event_start"), timezone)
        if not channel_id or not event_start:
            continue
        event_start = event_start.astimezone(timezone)
        intervals = sports_index["programmes"].get(channel_id, [])
        if not any(start <= event_start < stop for start, stop in intervals):
            uncovered_event_starts.append(channel_id)

    errors = [value for value in (playlist_error, sports_index["error"], combined_index["error"]) if value]
    ok = not any(
        (
            errors,
            missing_playlist,
            missing_sports_channels,
            missing_sports_programmes,
            missing_combined_channels,
            missing_combined_programmes,
            uncovered_event_starts,
        )
    )
    return {
        "ok": ok,
        "generated_channels": len(expected_ids),
        "playlist_sports_ids": len(expected_ids & playlist_ids),
        "sports_xml_channels": len(expected_ids & sports_index["channels"]),
        "sports_xml_programme_channels": sum(
            1 for channel_id in expected_ids if sports_index["programmes"].get(channel_id)
        ),
        "combined_xml_channels": len(expected_ids & combined_index["channels"]),
        "combined_xml_programme_channels": sum(
            1 for channel_id in expected_ids if combined_index["programmes"].get(channel_id)
        ),
        "missing_playlist_ids": missing_playlist,
        "missing_sports_channels": missing_sports_channels,
        "missing_sports_programmes": missing_sports_programmes,
        "missing_combined_channels": missing_combined_channels,
        "missing_combined_programmes": missing_combined_programmes,
        "uncovered_event_starts": sorted(uncovered_event_starts),
        "errors": errors,
        "sports_xml_size": sports_index["size"],
        "sports_xml_modified": sports_index["modified"],
        "combined_xml_size": combined_index["size"],
        "combined_xml_modified": combined_index["modified"],
    }


def _record_scan(
    db_path: Path | str,
    *,
    started_at: str,
    status: str,
    message: str,
    event_count: int,
    channel_count: int,
    target_date: str,
    trigger: str,
) -> None:
    with closing(_connect(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO sports_scan_runs
                (started_at, finished_at, status, message,
                 event_count, channel_count, target_date, trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                started_at,
                _now_iso(),
                status,
                message,
                event_count,
                channel_count,
                target_date,
                trigger,
            ),
        )
        conn.commit()


def record_scan_failure(db_path: Path | str, message: str, trigger: str = "scheduled") -> None:
    settings = get_settings(db_path)
    now = datetime.now().astimezone()
    _record_scan(
        db_path,
        started_at=_now_iso(),
        status="failed",
        message=message,
        event_count=0,
        channel_count=0,
        target_date=_sports_day(now, settings).isoformat(),
        trigger=trigger,
    )


def scan_channels(
    db_path: Path | str,
    channels: list[dict],
    epg_path: Path | None = None,
    *,
    sports_epg_path: Path | None = None,
    combined_epg_path: Path | None = None,
    trigger: str = "manual",
    now: datetime | None = None,
) -> dict:
    init_db(db_path)
    started_at = _now_iso()
    settings = get_settings(db_path)
    current = now or datetime.now().astimezone()
    target_date = _sports_day(current, settings).isoformat()

    if not settings.get("enabled"):
        result = {
            "ok": True,
            "count": len(generated_rows(db_path)),
            "events": 0,
            "message": "Sports automation is disabled; existing generated channels were left unchanged.",
            "target_date": target_date,
        }
        _record_scan(
            db_path,
            started_at=started_at,
            status="skipped",
            message=result["message"],
            event_count=0,
            channel_count=result["count"],
            target_date=target_date,
            trigger=trigger,
        )
        return result

    rules = [rule for rule in get_rules(db_path) if rule["enabled"]]
    diagnostics = _new_scan_diagnostics()
    events = _merge_events(
        [
            *_m3u_events(db_path, channels, settings, current, diagnostics),
            *_epg_events(
                db_path,
                epg_path,
                channels,
                settings,
                current,
                diagnostics,
            ),
        ]
    )

    selected_events = []
    for event in events:
        matched = _matching_rules(event, rules)
        if not matched:
            continue
        event["matched_rule"] = matched[0]
        selected_events.append(event)

    selected_events.sort(
        key=lambda event: (
            event.get("start") or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            event.get("league_id", ""),
            event.get("display_name", "").lower(),
        )
    )

    start_number = int(settings.get("start_channel", 1000))
    block_size = int(settings.get("channels_per_event", 10))
    group_title = str(settings.get("group_title", "Sports Today"))
    generated = []

    team_catalog = {item["id"]: item for item in _team_catalog(db_path)}
    for event_index, event in enumerate(selected_events):
        rule = event["matched_rule"]
        feeds = _build_feeds(event, channels, rule, settings)[:block_size]
        for feed_index, feed in enumerate(feeds):
            channel = feed["channel"]
            feed_type = feed["feed_type"]
            feed_label, subtitle = _feed_label(feed_type, event, feed.get("team_id", ""))
            start_text = ""
            if event.get("start"):
                start_text = event["start"].astimezone(
                    ZoneInfo(str(settings.get("timezone", "America/New_York")))
                ).strftime("%-I:%M %p")
            if start_text:
                subtitle = f"{subtitle} • {start_text}"
            league_label = LEAGUE_NAMES.get(event.get("league_id", ""), "Sports")
            display_name = f"{league_label} • {event['display_name']} — {feed_label}"
            assigned = start_number + event_index * block_size + feed_index
            logo = str(channel.get("tvg_logo", "") or "")
            if not logo:
                preferred_team = team_catalog.get(feed.get("team_id", ""))
                if preferred_team:
                    logo = preferred_team.get("logo_url", "")
            source_channel_key = str(channel.get("url", "") or "")
            event_start = event.get("start")
            event_end = (
                event_start + _event_duration(event.get("league_id", ""))
                if event_start
                else None
            )
            item = {
                "channel_key": f"sports:{event['event_key']}:{feed_type}:{source_channel_key}",
                "source_channel_key": source_channel_key,
                "event_key": event["event_key"],
                "league_id": event.get("league_id", ""),
                "display_name": display_name,
                "subtitle": subtitle,
                "feed_type": feed_type,
                "assigned_number": assigned,
                "group_title": group_title,
                "url": source_channel_key,
                "tvg_id": _generated_tvg_id(assigned),
                "source_tvg_id": str(channel.get("tvg_id", "") or ""),
                "tvg_logo": logo,
                "event_title": event.get("display_name", ""),
                "event_start": event_start.isoformat() if event_start else None,
                "event_end": event_end.isoformat() if event_end else None,
                "is_replay": bool(event.get("is_replay")),
            }
            item["raw"] = _generated_raw(channel, item)
            generated.append(item)

    generated_at_dt = current.astimezone()
    generated_at = generated_at_dt.isoformat(timespec="seconds")
    prepared_epg = _write_prepared_epg_files(
        generated,
        settings,
        base_epg_path=epg_path,
        sports_epg_path=sports_epg_path,
        combined_epg_path=combined_epg_path,
        generated_at=generated_at_dt,
    )
    installed_epg: list[tuple[Path, Path | None]] = []
    with closing(_connect(db_path)) as conn:
        # Database rows and guide exports are replaced as one operation. If an
        # export cannot be installed, SQLite rolls back and the old guide files
        # are restored, preserving yesterday's working lineup.
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
                         event_title, event_start, event_end, is_replay, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    malformed_count = _malformed_count(diagnostics)
    message = f"Generated {len(generated)} channels for {len(selected_events)} matching events."
    if malformed_count:
        message += (
            f" Skipped {malformed_count} malformed provider "
            f"entr{'y' if malformed_count == 1 else 'ies'}."
        )
        _log_malformed_summary(diagnostics)
    _record_scan(
        db_path,
        started_at=started_at,
        status="success",
        message=message,
        event_count=len(selected_events),
        channel_count=len(generated),
        target_date=target_date,
        trigger=trigger,
    )
    return {
        "ok": True,
        "count": len(generated),
        "events": len(selected_events),
        "generated_at": generated_at,
        "target_date": target_date,
        "message": message,
        "skipped_entries": malformed_count,
        "malformed_m3u": diagnostics.get("malformed_m3u", 0),
        "malformed_epg": diagnostics.get("malformed_epg", 0),
        "guide_channels": len(generated),
    }


def generated_rows(db_path: Path | str) -> list[dict]:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT id, channel_key, source_channel_key, event_key, league_id,
                   display_name, subtitle, feed_type, assigned_number,
                   group_title, url, tvg_id, source_tvg_id, tvg_logo, raw_json,
                   event_title, event_start, event_end, is_replay, generated_at
            FROM sports_generated
            ORDER BY assigned_number
            """
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["raw"] = _json_load(item.pop("raw_json"), [])
        output.append(item)
    return output


def generated_channel_payloads(db_path: Path | str) -> list[dict]:
    output = []
    for index, row in enumerate(generated_rows(db_path), start=1):
        output.append(
            {
                "id": -index,
                "name": row["display_name"],
                "group": row["group_title"],
                "url": row["url"],
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


def last_scan(db_path: Path | str) -> dict | None:
    init_db(db_path)
    with closing(_connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT id, started_at, finished_at, status, message,
                   event_count, channel_count, target_date, trigger
            FROM sports_scan_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def status_payload(db_path: Path | str, now: datetime | None = None) -> dict:
    settings = get_settings(db_path)
    generated = generated_rows(db_path)
    next_run = next_update_at(db_path, now)
    return {
        "settings": settings,
        "rules": get_rules(db_path),
        "catalog": catalog_payload(db_path),
        "generated": [
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "subtitle": row["subtitle"],
                "feed_type": row["feed_type"],
                "assigned_number": row["assigned_number"],
                "event_start": row["event_start"],
                "generated_at": row["generated_at"],
            }
            for row in generated
        ],
        "last_scan": last_scan(db_path),
        "next_update": next_run.isoformat(),
    }
