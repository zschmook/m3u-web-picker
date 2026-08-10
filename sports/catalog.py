from __future__ import annotations

import json
import re
from contextlib import closing
from pathlib import Path
from typing import Iterable

import sports as _s


def _catalog_rows(db_path: Path | str, scope_type: str = "") -> list[dict]:
    _s.init_db(db_path)
    sql = """
        SELECT scope_type, scope_id, display_name, subtitle, league_id,
               aliases_json, logo_url, metadata_json, source, updated_at
        FROM sports_catalog
    """
    params: tuple = ()
    if scope_type in _s.SCOPE_TYPES:
        sql += " WHERE scope_type = ?"
        params = (scope_type,)
    sql += " ORDER BY scope_type, display_name COLLATE NOCASE"
    with closing(_s._connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        item["id"] = item.pop("scope_id")
        item["name"] = item.pop("display_name")
        item["aliases"] = _s._json_load(item.pop("aliases_json"), [])
        item["metadata"] = _s._json_load(item.pop("metadata_json"), {})
        league_id = str(item.get("league_id", "") or "")
        sport_id = _s.LEAGUE_SPORTS.get(league_id, "")
        if sport_id:
            item["metadata"].setdefault("sport_id", sport_id)
            item["metadata"].setdefault("family", _s.SPORT_NAMES.get(sport_id, sport_id))
        if league_id in _s.LEAGUE_BLOCK_INDEX:
            item["metadata"].setdefault("block_index", _s.LEAGUE_BLOCK_INDEX[league_id])
        output.append(item)
    return output


def catalog_payload(db_path: Path | str, query: str = "", scope_type: str = "") -> list[dict]:
    query_norm = _s._normalize(query)
    output = []
    for item in _catalog_rows(db_path, scope_type):
        haystack = _s._normalize(
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
    conn,
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
            _s._now_iso(),
        ),
    )


def _team_feed_identity(channel: dict) -> tuple[str, str, str] | None:
    name = str(channel.get("name", "")).strip()
    for league_id, pattern in _s.TEAM_FEED_PATTERNS:
        match = pattern.match(name)
        if not match:
            continue
        team = _s._smart_team_name(match.group("team"))
        normalized = _s._normalize(team)
        if not normalized or any(word in normalized for word in _s.NETWORK_WORDS):
            continue
        if re.fullmatch(r"\d+|\d+\s*(am|pm)?", normalized):
            continue
        return league_id, f"{league_id}:{_s._slug(team)}", team
    return None


def _known_mlb_aliases(team_name: str) -> list[str]:
    return list(_s.MLB_ALIASES_BY_NAME.get(_s._normalize(team_name), []))


def discover_catalog_from_channels(db_path: Path | str, channels: Iterable[dict]) -> int:
    _s.init_db(db_path)
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

    with closing(_s._connect(db_path)) as conn:
        for (league_id, team_id), (name, aliases, logo) in discovered.items():
            _upsert_catalog_item(
                conn,
                scope_type="team",
                scope_id=team_id,
                display_name=name,
                subtitle=f"{_s.LEAGUE_NAMES.get(league_id, league_id.upper())} team • home and away games",
                league_id=league_id,
                aliases=aliases,
                logo_url=logo,
                metadata={
                    "sport_id": _s.LEAGUE_SPORTS.get(league_id, ""),
                    "family": _s.SPORT_NAMES.get(
                        _s.LEAGUE_SPORTS.get(league_id, ""), league_id.upper()
                    ),
                },
                source="provider",
            )
        conn.commit()
    return len(discovered)


def get_rules(db_path: Path | str) -> list[dict]:
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
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
        if scope_type not in _s.SCOPE_TYPES or not scope_id:
            raise ValueError("Choose a valid sports selection.")
        item = catalog.get((scope_type, scope_id))
        if not item:
            raise ValueError("One of the selected items is not in the cached sports catalog.")
        if preference not in {"best", "all", "favorite", "home", "away", "national"}:
            preference = "best"
        prepared.append((scope_type, scope_id, item["name"], preference))

    now = _s._now_iso()
    with closing(_s._connect(db_path)) as conn:
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
        rule
        for rule in rules
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
    values.append(_s._now_iso())
    values.append(int(rule_id))
    with closing(_s._connect(db_path)) as conn:
        conn.execute(
            f"UPDATE sports_rules SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        conn.commit()
    return next((rule for rule in get_rules(db_path) if rule["id"] == int(rule_id)), {})


def delete_rule(db_path: Path | str, rule_id: int) -> bool:
    with closing(_s._connect(db_path)) as conn:
        cursor = conn.execute("DELETE FROM sports_rules WHERE id = ?", (int(rule_id),))
        conn.commit()
        return cursor.rowcount > 0
