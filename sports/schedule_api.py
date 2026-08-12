from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import sports as _s


def _schedule_api_secret(db_path: Path | str) -> str:
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = ?",
            (_s.SCHEDULE_API_KEY_SETTING,),
        ).fetchone()
    if not row:
        return ""
    return str(_s._json_load(row["value"], row["value"]) or "").strip()


def _schedule_api_rule_league_id(rule: dict, catalog_by_key: dict) -> str:
    scope_type = str(rule.get("scope_type") or "")
    scope_id = str(rule.get("scope_id") or "")
    if scope_type == "league":
        return scope_id
    item = catalog_by_key.get((scope_type, scope_id)) or {}
    league_id = str(item.get("league_id") or "")
    if league_id:
        return league_id
    if scope_type == "team" and ":" in scope_id:
        return scope_id.split(":", 1)[0]
    return ""


def schedule_api_request_plan(db_path: Path | str) -> dict:
    """Collapse rules into the minimum API-SPORTS schedule datasets."""
    settings = _s.get_settings(db_path)
    rules = [rule for rule in _s.get_rules(db_path) if rule.get("enabled")]
    catalog = _s.catalog_payload(db_path)
    catalog_by_key = {(item["scope_type"], item["id"]): item for item in catalog}
    dataset_ids: set[str] = set()
    api_rules: list[str] = []
    legacy_rules: list[str] = []
    mixed_rules: list[str] = []
    reference_datasets: set[str] = set()

    if settings.get("everything_mode"):
        dataset_ids.update(_s.SCHEDULE_API_DATASETS)
        mixed_rules.append("Everything Mode")

    for rule in rules:
        scope_type = str(rule.get("scope_type") or "")
        scope_id = str(rule.get("scope_id") or "")
        label = str(rule.get("display_name") or scope_id or "Sports selection")
        matched_datasets: set[str] = set()
        mixed = False

        if scope_type == "sport":
            matched_datasets.update(
                _s.SCHEDULE_API_DATASETS_BY_SPORT.get(scope_id, ())
            )
            mixed = bool(matched_datasets)
        else:
            league_id = _schedule_api_rule_league_id(rule, catalog_by_key)
            dataset_id = _s.SCHEDULE_API_DATASET_BY_LEAGUE.get(league_id)
            if dataset_id:
                matched_datasets.add(dataset_id)
                if scope_type == "conference" and dataset_id == "ncaa":
                    reference_datasets.add("ncaa_membership")

        if matched_datasets:
            dataset_ids.update(matched_datasets)
            if label not in api_rules:
                api_rules.append(label)
            if mixed and label not in mixed_rules:
                mixed_rules.append(label)
        elif label not in legacy_rules:
            legacy_rules.append(label)

    datasets = [
        dict(_s.SCHEDULE_API_DATASETS[key])
        for key in _s.SCHEDULE_API_DATASETS
        if key in dataset_ids
    ]
    return {
        "provider": _s.SCHEDULE_API_PROVIDER_NAME,
        "provider_url": _s.SCHEDULE_API_PROVIDER_URL,
        "datasets": datasets,
        "dataset_ids": [item["id"] for item in datasets],
        "api_rules": api_rules,
        "legacy_rules": legacy_rules,
        "mixed_rules": mixed_rules,
        "reference_datasets": sorted(reference_datasets),
        "uses_legacy": bool(
            legacy_rules or mixed_rules or settings.get("everything_mode")
        ),
    }


def _schedule_api_dataset_season(dataset: dict, local_now: datetime) -> int:
    if dataset.get("season_mode") == "start_year":
        return local_now.year - 1 if local_now.month <= 2 else local_now.year
    return local_now.year


def _schedule_api_cache_summary(db_path: Path | str, dataset: dict) -> dict:
    with closing(_s._connect(db_path)) as conn:
        last = conn.execute(
            """
            SELECT schedule_date, fetched_at, result_count, remaining_quota
            FROM sports_schedule_api_cache
            WHERE source = ? AND league_id = ?
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (dataset["source"], dataset["league_id"]),
        ).fetchone()
        cached_event_count = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM sports_schedule_events
                WHERE source = ? AND league_id = ?
                """,
                (dataset["source"], dataset["league_id"]),
            ).fetchone()[0]
        )
        cached_dates = [
            row["schedule_date"]
            for row in conn.execute(
                """
                SELECT schedule_date FROM sports_schedule_api_cache
                WHERE source = ? AND league_id = ?
                ORDER BY schedule_date
                """,
                (dataset["source"], dataset["league_id"]),
            ).fetchall()
        ]
    return {
        "last_fetch_at": last["fetched_at"] if last else None,
        "last_fetch_date": last["schedule_date"] if last else None,
        "last_result_count": int(last["result_count"] or 0) if last else 0,
        "cached_event_count": cached_event_count,
        "remaining_quota": last["remaining_quota"] if last else None,
        "cached_dates": cached_dates,
    }


def schedule_api_status(db_path: Path | str) -> dict:
    """Return credential-free API-SPORTS state and the rule-derived plan."""
    settings = _s.get_settings(db_path)
    api_key = _schedule_api_secret(db_path)
    enabled = bool(settings.get("schedule_api_enabled"))
    configured = bool(api_key)
    effective = bool(enabled and configured)
    plan = schedule_api_request_plan(db_path)
    entries = []
    all_fetches = []
    total_cached_events = 0
    remaining_values = []
    cached_dates: set[str] = set()
    for dataset in plan["datasets"]:
        summary = _schedule_api_cache_summary(db_path, dataset)
        total_cached_events += summary["cached_event_count"]
        if summary["last_fetch_at"]:
            all_fetches.append(summary["last_fetch_at"])
        if summary["remaining_quota"] is not None:
            remaining_values.append(int(summary["remaining_quota"]))
        cached_dates.update(summary["cached_dates"])
        entries.append(
            {
                "id": dataset["id"],
                "provider": _s.SCHEDULE_API_PROVIDER_NAME,
                "product": dataset["product"],
                "scope": dataset["label"],
                "url": dataset["base_url"],
                "enabled": enabled,
                "configured": configured,
                "effective": effective,
                "key_configured": bool(api_key),
                **summary,
            }
        )

    return {
        "enabled": enabled,
        "configured": configured,
        "effective": effective,
        "provider": _s.SCHEDULE_API_PROVIDER_NAME,
        "provider_url": _s.SCHEDULE_API_PROVIDER_URL,
        "key_configured": bool(api_key),
        "last_fetch_at": max(all_fetches) if all_fetches else None,
        "last_fetch_date": None,
        "last_result_count": sum(
            item.get("last_result_count", 0) for item in entries
        ),
        "cached_event_count": total_cached_events,
        "remaining_quota": min(remaining_values) if remaining_values else None,
        "cached_dates": sorted(cached_dates),
        "fallback_mode": not effective,
        "plan": plan,
        "apis": entries,
    }


def update_schedule_api_config(
    db_path: Path | str,
    *,
    enabled: bool | None = None,
    url: str | None = None,
    api_key: str | None = None,
    clear_key: bool = False,
) -> dict:
    """Persist API-SPORTS enable/key state without exposing the secret."""
    _s.init_db(db_path)
    changes = {}
    if enabled is not None:
        changes["schedule_api_enabled"] = bool(enabled)
    if url is not None:
        cleaned_url = str(url or "").strip().rstrip("/")
        if cleaned_url and not re.match(r"^https?://", cleaned_url, re.I):
            raise ValueError(
                "Schedule API URL must start with http:// or https://."
            )
        changes["schedule_api_url"] = cleaned_url
    if changes:
        _s.update_settings(db_path, changes)
    with closing(_s._connect(db_path)) as conn:
        if clear_key:
            conn.execute(
                "DELETE FROM sports_settings WHERE key = ?",
                (_s.SCHEDULE_API_KEY_SETTING,),
            )
        elif api_key is not None and str(api_key).strip():
            conn.execute(
                "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
                (
                    _s.SCHEDULE_API_KEY_SETTING,
                    json.dumps(str(api_key).strip()),
                ),
            )
        conn.commit()
    return schedule_api_status(db_path)


def _schedule_api_required_dates(
    scan_anchor: datetime,
    settings: dict,
) -> list[date]:
    window_start, window_end, _sports_date = _s._target_window(
        scan_anchor,
        settings,
    )
    last_instant = window_end - timedelta(microseconds=1)
    current = window_start.date()
    end_date = last_instant.date()
    values = []
    while current <= end_date:
        values.append(current)
        current += timedelta(days=1)
    return values


def _schedule_api_request_key(
    dataset: dict,
    *,
    schedule_date: date,
    season: int,
    timezone: str,
) -> str:
    parameters = {
        "date": schedule_date.isoformat(),
        "timezone": str(timezone),
    }
    if dataset.get("request_mode") == "american_football":
        parameters["league"] = str(dataset["remote_league_id"])
        parameters["season"] = str(season)
    payload = {
        "provider": "api_sports",
        "product": str(dataset.get("product") or ""),
        "endpoint": "games",
        "parameters": parameters,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _schedule_api_dataset_games_url(
    dataset: dict,
    *,
    schedule_date: date,
    season: int,
    timezone: str,
) -> str:
    base_url = str(dataset.get("base_url") or "").strip()
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Schedule API adapter URL is invalid.")
    path = parsed.path.rstrip("/")
    path = f"{path}/games" if path else "/games"
    query = {"date": schedule_date.isoformat(), "timezone": timezone}
    if dataset.get("request_mode") == "american_football":
        query["league"] = str(dataset["remote_league_id"])
        query["season"] = str(season)
    return urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            "",
            urllib.parse.urlencode(query),
            "",
        )
    )


def _schedule_api_games_url(
    base_url: str,
    *,
    schedule_date: date,
    season: int,
    timezone: str,
) -> str:
    dataset = dict(_s.SCHEDULE_API_DATASETS["mlb"])
    dataset["base_url"] = str(base_url or dataset["base_url"]).rstrip("/")
    return _schedule_api_dataset_games_url(
        dataset,
        schedule_date=schedule_date,
        season=season,
        timezone=timezone,
    )


def _schedule_api_scheduled_start(
    dataset: dict,
    game: dict,
    timezone_name: str,
) -> str:
    if dataset.get("request_mode") == "american_football":
        game_info = game.get("game") or {}
        date_info = game_info.get("date") or {}
        timestamp = date_info.get("timestamp")
        try:
            if timestamp is not None:
                return datetime.fromtimestamp(
                    int(timestamp),
                    ZoneInfo(timezone_name),
                ).isoformat()
        except (TypeError, ValueError, OSError):
            pass
        day = str(date_info.get("date") or "").strip()
        clock = str(date_info.get("time") or "").strip() or "00:00"
        if day:
            try:
                return datetime.fromisoformat(f"{day}T{clock}").replace(
                    tzinfo=ZoneInfo(timezone_name)
                ).isoformat()
            except (ValueError, TypeError):
                return ""
        return ""
    return str(game.get("date") or "").strip()


def _schedule_api_game_fields(
    dataset: dict,
    game: dict,
    timezone_name: str,
) -> dict | None:
    if not isinstance(game, dict):
        return None
    league = game.get("league") or {}
    try:
        if int(league.get("id") or 0) != int(dataset["remote_league_id"]):
            return None
    except (TypeError, ValueError):
        return None
    if dataset.get("request_mode") == "american_football":
        game_info = game.get("game") or {}
        event_id = str(game_info.get("id") or "").strip()
        status = game_info.get("status") or {}
    else:
        event_id = str(game.get("id") or "").strip()
        status = game.get("status") or {}
    scheduled_start = _schedule_api_scheduled_start(
        dataset,
        game,
        timezone_name,
    )
    if not event_id or not scheduled_start:
        return None
    teams = game.get("teams") or {}
    return {
        "event_id": event_id,
        "scheduled_start": scheduled_start,
        "status_short": str(status.get("short") or ""),
        "status_long": str(status.get("long") or ""),
        "home": teams.get("home") or {},
        "away": teams.get("away") or {},
        "raw": game,
    }


def _upsert_schedule_api_team(
    conn: sqlite3.Connection,
    *,
    dataset: dict,
    team: dict,
    conference: str = "",
) -> None:
    name = str(team.get("name") or "").strip()
    if not name:
        return
    league_id = str(dataset["league_id"])
    scope_id = f"{league_id}:{_s._slug(name)}"
    metadata = {
        "sport_id": dataset["sport_id"],
        "family": _s.SPORT_NAMES.get(dataset["sport_id"], dataset["sport_id"]),
        "api_provider": _s.SCHEDULE_API_PROVIDER_NAME,
        "api_product": dataset["product"],
        "api_team_id": str(team.get("id") or ""),
    }
    if conference:
        metadata["conference"] = conference
    _s._upsert_catalog_item(
        conn,
        scope_type="team",
        scope_id=scope_id,
        display_name=name,
        subtitle=(
            f"{_s.LEAGUE_NAMES.get(league_id, league_id.upper())} team • "
            "home and away games"
        ),
        league_id=league_id,
        aliases=[name],
        logo_url=str(team.get("logo") or ""),
        metadata=metadata,
        source="api-sports",
    )


def _fetch_schedule_api_dataset_date(
    db_path: Path | str,
    *,
    dataset: dict,
    api_key: str,
    schedule_date: date,
    season: int,
    timezone: str,
    fetched_on: str,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    _s._raise_if_cancelled(cancel_check)
    url = _schedule_api_dataset_games_url(
        dataset,
        schedule_date=schedule_date,
        season=season,
        timezone=timezone,
    )
    request_key = _schedule_api_request_key(
        dataset,
        schedule_date=schedule_date,
        season=season,
        timezone=timezone,
    )
    request = urllib.request.Request(
        url,
        headers={"x-apisports-key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError(
                    "Schedule API response exceeded the 8 MB safety limit."
                )
            remaining_header = response.headers.get(
                "x-ratelimit-requests-remaining"
            )
            minute_remaining_header = response.headers.get(
                "X-RateLimit-Remaining"
            )
    except Exception as exc:
        raise ValueError(
            f"Could not fetch {dataset['label']} schedule for "
            f"{schedule_date.isoformat()}."
        ) from exc
    _s._raise_if_cancelled(cancel_check)
    try:
        payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except Exception as exc:
        raise ValueError("Schedule API returned invalid JSON.") from exc
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        raise ValueError("Schedule API reported an error.")
    games = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(games, list):
        raise ValueError("Schedule API did not return a games list.")
    fetched_at = _s._now_iso()
    remaining = None
    minute_remaining = None
    try:
        if remaining_header is not None:
            remaining = int(remaining_header)
    except (TypeError, ValueError):
        pass
    try:
        if minute_remaining_header is not None:
            minute_remaining = int(minute_remaining_header)
    except (TypeError, ValueError):
        pass

    with closing(_s._connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            DELETE FROM sports_schedule_events
            WHERE source = ? AND league_id = ? AND season = ? AND schedule_date = ?
            """,
            (
                dataset["source"],
                dataset["league_id"],
                season,
                schedule_date.isoformat(),
            ),
        )
        stored = 0
        for game in games:
            fields = _schedule_api_game_fields(dataset, game, timezone)
            if not fields:
                continue
            home = fields["home"]
            away = fields["away"]
            _upsert_schedule_api_team(conn, dataset=dataset, team=home)
            _upsert_schedule_api_team(conn, dataset=dataset, team=away)
            conn.execute(
                """
                INSERT OR REPLACE INTO sports_schedule_events
                    (source, api_event_id, league_id, season, schedule_date,
                     scheduled_start, status_short, status_long,
                     home_api_id, home_name, home_logo,
                     away_api_id, away_name, away_logo,
                     raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset["source"],
                    fields["event_id"],
                    dataset["league_id"],
                    season,
                    schedule_date.isoformat(),
                    fields["scheduled_start"],
                    fields["status_short"],
                    fields["status_long"],
                    str(home.get("id") or ""),
                    str(home.get("name") or ""),
                    str(home.get("logo") or ""),
                    str(away.get("id") or ""),
                    str(away.get("name") or ""),
                    str(away.get("logo") or ""),
                    json.dumps(fields["raw"], separators=(",", ":")),
                    fetched_at,
                ),
            )
            stored += 1
        conn.execute(
            """
            INSERT OR REPLACE INTO sports_schedule_api_cache
                (source, league_id, season, schedule_date, request_key, fetched_on,
                 fetched_at, result_count, remaining_quota)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset["source"],
                dataset["league_id"],
                season,
                schedule_date.isoformat(),
                request_key,
                fetched_on,
                fetched_at,
                stored,
                remaining,
            ),
        )
        conn.commit()
    return {
        "dataset": dataset["id"],
        "scope": dataset["label"],
        "date": schedule_date.isoformat(),
        "games": stored,
        "remaining_quota": remaining,
        "minute_remaining": minute_remaining,
        "fetched_at": fetched_at,
        "url": url,
        "request_key": request_key,
    }


def _conference_catalog_map(db_path: Path | str) -> dict[str, dict]:
    return {
        item["id"]: item
        for item in _s.catalog_payload(db_path, scope_type="conference")
        if item.get("league_id") == "ncaaf-fbs"
    }


def _match_ncaa_conference_id(
    conference_name: str,
    conferences: dict[str, dict],
) -> str:
    normalized = _s._normalize(conference_name)
    if not normalized:
        return ""
    for conference_id, item in conferences.items():
        candidates = [item.get("name", ""), *item.get("aliases", [])]
        for candidate in candidates:
            value = _s._normalize(str(candidate or ""))
            if value and (
                value == normalized
                or value in normalized
                or normalized in value
            ):
                return conference_id
    return ""


def _refresh_ncaa_reference_metadata_if_needed(
    db_path: Path | str,
    *,
    api_key: str,
    season: int,
    force: bool = False,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    """Cache NCAA conference membership once per season."""
    dataset = _s.SCHEDULE_API_DATASETS["ncaa"]
    cache_key = "ncaa-standings-membership"
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT fetched_at, remaining_quota, raw_json
            FROM sports_schedule_reference_cache
            WHERE source = ? AND cache_key = ? AND season = ?
            """,
            (dataset["source"], cache_key, season),
        ).fetchone()
    if row and not force:
        return {
            "cached": True,
            "fetched_at": row["fetched_at"],
            "remaining_quota": row["remaining_quota"],
        }

    _s._raise_if_cancelled(cancel_check)
    query = urllib.parse.urlencode(
        {"league": dataset["remote_league_id"], "season": season}
    )
    url = f"{dataset['base_url']}/standings?{query}"
    request = urllib.request.Request(
        url,
        headers={"x-apisports-key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError(
                    "Schedule API reference response exceeded the 8 MB safety limit."
                )
            remaining_header = response.headers.get(
                "x-ratelimit-requests-remaining"
            )
    except Exception as exc:
        raise ValueError(
            "Could not refresh NCAA conference membership."
        ) from exc
    try:
        payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except Exception as exc:
        raise ValueError(
            "Schedule API returned invalid NCAA standings JSON."
        ) from exc
    if payload.get("errors") if isinstance(payload, dict) else True:
        raise ValueError("Schedule API reported an NCAA standings error.")
    standings = payload.get("response")
    if not isinstance(standings, list):
        raise ValueError("Schedule API did not return NCAA standings.")

    conferences = _conference_catalog_map(db_path)
    memberships: dict[str, list[str]] = defaultdict(list)
    remaining = None
    try:
        if remaining_header is not None:
            remaining = int(remaining_header)
    except (TypeError, ValueError):
        pass
    fetched_at = _s._now_iso()
    with closing(_s._connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in standings:
            if not isinstance(item, dict):
                continue
            conference_name = str(item.get("conference") or "").strip()
            conference_id = _match_ncaa_conference_id(
                conference_name,
                conferences,
            )
            team = item.get("team") or {}
            _upsert_schedule_api_team(
                conn,
                dataset=dataset,
                team=team,
                conference=conference_name,
            )
            team_name = str(team.get("name") or "").strip()
            if conference_id and team_name:
                memberships[conference_id].append(team_name)

        for conference_id, team_names in memberships.items():
            item = conferences.get(conference_id) or {}
            metadata = dict(item.get("metadata") or {})
            metadata["teams"] = sorted(set(team_names), key=str.casefold)
            metadata["sport_id"] = "football"
            metadata["family"] = "Football"
            metadata["api_provider"] = _s.SCHEDULE_API_PROVIDER_NAME
            metadata["api_product"] = dataset["product"]
            _s._upsert_catalog_item(
                conn,
                scope_type="conference",
                scope_id=conference_id,
                display_name=str(item.get("name") or conference_id),
                subtitle=str(item.get("subtitle") or "FBS conference games"),
                league_id="ncaaf-fbs",
                aliases=list(item.get("aliases") or []),
                logo_url=str(item.get("logo_url") or ""),
                metadata=metadata,
                source="api-sports",
            )

        conn.execute(
            """
            INSERT OR REPLACE INTO sports_schedule_reference_cache
                (source, cache_key, season, fetched_at, remaining_quota, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                dataset["source"],
                cache_key,
                season,
                fetched_at,
                remaining,
                json.dumps(
                    {"memberships": memberships},
                    separators=(",", ":"),
                ),
            ),
        )
        conn.commit()
    return {
        "cached": False,
        "fetched_at": fetched_at,
        "remaining_quota": remaining,
        "conference_count": len(memberships),
        "team_count": sum(len(values) for values in memberships.values()),
    }


def _schedule_api_authoritative_leagues(
    db_path: Path | str,
    scan_anchor: datetime | None = None,
) -> set[str]:
    settings = _s.get_settings(db_path)
    state = schedule_api_status(db_path)
    if not state.get("effective"):
        return set()
    datasets = (state.get("plan") or {}).get("datasets") or []
    if not datasets:
        return set()

    timezone_name = str(settings.get("timezone", "America/New_York"))
    timezone = ZoneInfo(timezone_name)
    local_now = (scan_anchor or datetime.now().astimezone()).astimezone(timezone)
    fetched_on = local_now.date().isoformat()
    required_dates = _schedule_api_required_dates(local_now, settings)
    if not required_dates:
        return set()

    authoritative: set[str] = set()
    with closing(_s._connect(db_path)) as conn:
        for planned_dataset in datasets:
            dataset = _s.SCHEDULE_API_DATASETS.get(
                str(planned_dataset.get("id") or ""),
                planned_dataset,
            )
            if not dataset.get("source") or not dataset.get("league_id"):
                continue
            season = _schedule_api_dataset_season(dataset, local_now)
            complete = True
            for schedule_date in required_dates:
                row = conn.execute(
                    """
                    SELECT fetched_on, request_key
                    FROM sports_schedule_api_cache
                    WHERE source = ? AND league_id = ? AND season = ? AND schedule_date = ?
                    """,
                    (
                        dataset["source"],
                        dataset["league_id"],
                        season,
                        schedule_date.isoformat(),
                    ),
                ).fetchone()
                expected_key = _schedule_api_request_key(
                    dataset,
                    schedule_date=schedule_date,
                    season=season,
                    timezone=timezone_name,
                )
                if (
                    not row
                    or str(row["fetched_on"] or "") != fetched_on
                    or str(row["request_key"] or "") != expected_key
                ):
                    complete = False
                    break
            if complete:
                authoritative.add(str(dataset["league_id"]))
    return authoritative


def _filter_provider_events_by_authoritative_schedule(
    provider_events: list[dict],
    authoritative_leagues: set[str],
    *,
    include_replays: bool,
) -> list[dict]:
    if include_replays or not authoritative_leagues:
        return provider_events
    return [
        event
        for event in provider_events
        if str(event.get("league_id") or "") not in authoritative_leagues
        or bool(event.get("has_schedule_api_identity"))
    ]


def schedule_api_events_for_window(
    db_path: Path | str,
    scan_anchor: datetime | None = None,
) -> list[dict]:
    settings = _s.get_settings(db_path)
    state = schedule_api_status(db_path)
    if not state.get("effective"):
        return []
    datasets = (state.get("plan") or {}).get("datasets") or []
    if not datasets:
        return []
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (scan_anchor or datetime.now().astimezone()).astimezone(timezone)
    window_start, window_end, _ = _s._target_window(local_now, settings)
    required_dates = [
        value.isoformat()
        for value in _schedule_api_required_dates(local_now, settings)
    ]
    if not required_dates:
        return []
    placeholders = ",".join("?" for _ in required_dates)
    rows = []
    with closing(_s._connect(db_path)) as conn:
        for dataset in datasets:
            season = _schedule_api_dataset_season(dataset, local_now)
            rows.extend(
                conn.execute(
                    f"""
                    SELECT e.*
                    FROM sports_schedule_events e
                    INNER JOIN sports_schedule_api_cache c
                      ON c.source = e.source
                     AND c.league_id = e.league_id
                     AND c.season = e.season
                     AND c.schedule_date = e.schedule_date
                    WHERE e.source = ? AND e.league_id = ?
                      AND e.season IN (?, ?)
                      AND e.schedule_date IN ({placeholders})
                    ORDER BY e.scheduled_start
                    """,
                    (
                        dataset["source"],
                        dataset["league_id"],
                        season - 1,
                        season,
                        *required_dates,
                    ),
                ).fetchall()
            )
    output = []
    for row in rows:
        try:
            start = datetime.fromisoformat(str(row["scheduled_start"]))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone)
            start = start.astimezone(timezone)
        except Exception:
            continue
        if not (
            window_start - timedelta(hours=18)
            <= start
            < window_end + timedelta(hours=18)
        ):
            continue
        dataset_id = _s.SCHEDULE_API_DATASET_BY_LEAGUE.get(
            str(row["league_id"]),
            "",
        )
        dataset = _s.SCHEDULE_API_DATASETS.get(dataset_id, {})
        output.append(
            {
                "api_source": str(row["source"]),
                "api_event_id": str(row["api_event_id"]),
                "api_dataset": dataset_id,
                "league_id": str(row["league_id"]),
                "sport_id": str(
                    dataset.get("sport_id")
                    or _s.LEAGUE_SPORTS.get(str(row["league_id"]), "")
                ),
                "season": int(row["season"]),
                "scheduled_start": start,
                "status_short": str(row["status_short"] or ""),
                "status_long": str(row["status_long"] or ""),
                "home_api_id": str(row["home_api_id"] or ""),
                "home_name": str(row["home_name"] or ""),
                "home_logo": str(row["home_logo"] or ""),
                "away_api_id": str(row["away_api_id"] or ""),
                "away_name": str(row["away_name"] or ""),
                "away_logo": str(row["away_logo"] or ""),
            }
        )
    return output
