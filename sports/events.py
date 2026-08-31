from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import sports as _s


def _utc_instant(
    value: datetime,
    default_tz: ZoneInfo | None = None,
) -> datetime | None:
    """Normalize one timestamp to a UTC-aware instant for comparisons."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=default_tz or ZoneInfo("UTC"))
    return value.astimezone(ZoneInfo("UTC"))


def _channel_text(channel: dict) -> str:
    return " ".join(
        str(channel.get(key, "") or "")
        for key in ("name", "tvg_name", "group", "tvg_id")
    )


def _league_matches(text: str) -> list[str]:
    normalized = str(text or "").lower()
    return [
        league_id
        for league_id, patterns in _s.LEAGUE_PATTERNS.items()
        if any(re.search(pattern, normalized, re.I) for pattern in patterns)
    ]


def _college_football_match(text: str, matches: list[str]) -> str:
    lowered = str(text or "").lower()
    if "ncaaf-fbs" in matches and re.search(
        r"\bfbs\b|football bowl subdivision|division i fbs",
        lowered,
        re.I,
    ):
        return "ncaaf-fbs"
    for league_id in (
        "ncaaf-fcs",
        "ncaaf-d2",
        "ncaaf-d3",
        "naia-football",
        "njcaa-football",
        "high-school-football",
    ):
        if league_id in matches:
            return league_id
    if "ncaaf-fbs" in matches:
        return "ncaaf-fbs"
    return ""


def _detect_league(primary_text: str, fallback_text: str = "") -> str:
    """Detect a league/series while keeping shared provider groups isolated."""
    primary_matches = _league_matches(primary_text)
    if primary_matches:
        college_match = _college_football_match(primary_text, primary_matches)
        if college_match:
            return college_match
        if "milb" in primary_matches and "mlb" not in primary_matches:
            return "milb"
        if "mlb" in primary_matches and "milb" not in primary_matches:
            return "mlb"
        if len(primary_matches) == 1:
            return primary_matches[0]
        return primary_matches[0]

    fallback_matches = _league_matches(fallback_text)
    if {"mlb", "milb"}.issubset(fallback_matches):
        return ""
    college_match = _college_football_match(fallback_text, fallback_matches)
    if college_match:
        return college_match
    return fallback_matches[0] if fallback_matches else ""


def _detect_sport_tags(text: str) -> list[str]:
    normalized = str(text or "").lower()
    return [
        sport_id
        for sport_id, patterns in _s.SPORT_PATTERNS.items()
        if any(re.search(pattern, normalized, re.I) for pattern in patterns)
    ]


def _detect_sport(text: str) -> str:
    matches = _detect_sport_tags(text)
    return matches[0] if matches else ""


def _strip_provider_prefix(value: str) -> str:
    text = value.strip()
    if "|" in text:
        prefix, remainder = text.split("|", 1)
        if re.search(r"\d|MLB|NBA|NHL|FLSP|Victory|Apple|MiLB", prefix, re.I):
            text = remainder.strip()
    text = re.sub(
        r"^(?:NFL|NHL|NCAAF|NCAAB|PPV|EVENTS)\s*(?:SD\s*)?\d{1,3}\s*:\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"^(?:MiLB|MLB|NBA|NHL|NFL|NCAAF|NCAAB|NWSL)\s*:\s*",
        "",
        text,
        flags=re.I,
    )
    return text.strip(" |:-")


def _extract_event_datetime(
    text: str,
    settings: dict,
    now: datetime,
) -> tuple[str, datetime | None]:
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    match = _s.DATE_RE.search(text)
    if match:
        clean = text[: match.start()].strip()
        time_value = match.group("time") or "00:00:00"
        if len(time_value) == 5:
            time_value += ":00"
        timestamp = f"{match.group('date')}T{time_value}"
        try:
            start = datetime.fromisoformat(timestamp).replace(tzinfo=timezone)
        except (ValueError, OverflowError) as exc:
            raise _s.MalformedSportsEntry(
                f"Invalid embedded event timestamp {timestamp!r}."
            ) from exc
        return clean, start

    time_match = _s.LEADING_TIME_RE.search(text)
    if time_match:
        raw_hour = int(time_match.group("hour"))
        minute = int(time_match.group("minute") or 0)
        ampm = time_match.group("ampm").lower()
        if not 1 <= raw_hour <= 12 or not 0 <= minute <= 59:
            raise _s.MalformedSportsEntry(
                f"Invalid event time {time_match.group(0)!r}."
            )
        hour = raw_hour
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        try:
            start = datetime.combine(
                _s._sports_day(now, settings),
                dt_time(hour, minute),
                tzinfo=timezone,
            )
        except (ValueError, OverflowError) as exc:
            raise _s.MalformedSportsEntry(
                f"Invalid event time {time_match.group(0)!r}."
            ) from exc
        return text, start
    return text, None


def _team_catalog(db_path: Path | str) -> list[dict]:
    return [
        item for item in _s.catalog_payload(db_path, scope_type="team")
    ]


def _build_team_lookup(db_path: Path | str) -> dict:
    """Build one scan-local, pre-normalized team lookup."""
    teams = _team_catalog(db_path)
    aliases_by_league: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    exact_by_league: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    all_aliases: list[tuple[int, str, str, str]] = []
    exact_all: dict[str, tuple[str, str]] = {}
    for team in teams:
        league_id = str(team.get("league_id", "") or "")
        seen_aliases: set[str] = set()
        for alias in [team.get("name", ""), *team.get("aliases", [])]:
            alias_norm = _s._normalize(str(alias or ""))
            if not alias_norm or alias_norm in seen_aliases:
                continue
            seen_aliases.add(alias_norm)
            row = (
                len(alias_norm),
                alias_norm,
                str(team["id"]),
                str(team["name"]),
            )
            aliases_by_league[league_id].append(row)
            all_aliases.append(row)
            exact_by_league[league_id].setdefault(
                alias_norm,
                (str(team["id"]), str(team["name"])),
            )
            exact_all.setdefault(
                alias_norm,
                (str(team["id"]), str(team["name"])),
            )
    return {
        "teams": teams,
        "aliases_by_league": dict(aliases_by_league),
        "exact_by_league": {
            key: dict(value) for key, value in exact_by_league.items()
        },
        "all_aliases": all_aliases,
        "exact_all": exact_all,
        "resolution_cache": {},
        "cache_hits": 0,
        "cache_misses": 0,
    }


def _find_team_id(
    text: str,
    league_id: str,
    teams: list[dict],
    team_lookup: dict | None = None,
) -> tuple[str, str]:
    normalized = _s._normalize(text)
    if not normalized:
        return "", text.strip()

    if team_lookup is not None:
        cache = team_lookup.setdefault("resolution_cache", {})
        cache_key = (str(league_id or ""), normalized)
        if cache_key in cache:
            team_lookup["cache_hits"] = int(team_lookup.get("cache_hits", 0)) + 1
            return cache[cache_key]
        team_lookup["cache_misses"] = int(
            team_lookup.get("cache_misses", 0)
        ) + 1
        exact = None
        if league_id:
            exact = (
                team_lookup.get("exact_by_league", {})
                .get(str(league_id), {})
                .get(normalized)
            )
            if exact is None:
                exact = (
                    team_lookup.get("exact_by_league", {})
                    .get("", {})
                    .get(normalized)
                )
        else:
            exact = team_lookup.get("exact_all", {}).get(normalized)
        if exact is not None:
            cache[cache_key] = exact
            return exact

        if league_id:
            aliases = [
                *team_lookup.get("aliases_by_league", {}).get(
                    str(league_id), []
                ),
                *team_lookup.get("aliases_by_league", {}).get("", []),
            ]
        else:
            aliases = team_lookup.get("all_aliases", [])
        padded = f" {normalized} "
        candidates = [
            (length, team_id, name)
            for length, alias_norm, team_id, name in aliases
            if normalized == alias_norm or f" {alias_norm} " in padded
        ]
        if candidates:
            _length, team_id, name = max(candidates)
            result = (team_id, name)
        else:
            result = ("", _s._smart_team_name(text))
        cache[cache_key] = result
        return result

    candidates = []
    for team in teams:
        if (
            league_id
            and team.get("league_id")
            and team["league_id"] != league_id
        ):
            continue
        aliases = [team["name"], *team.get("aliases", [])]
        for alias in aliases:
            alias_norm = _s._normalize(alias)
            if not alias_norm:
                continue
            if normalized == alias_norm or re.search(
                rf"(?:^|\s){re.escape(alias_norm)}(?:$|\s)",
                normalized,
            ):
                candidates.append(
                    (len(alias_norm), team["id"], team["name"])
                )
    if not candidates:
        return "", _s._smart_team_name(text)
    _, team_id, name = max(candidates)
    return team_id, name


def _infer_baseball_league(
    left: str,
    right: str,
    teams: list[dict],
    team_lookup: dict | None = None,
) -> str:
    resolved = []
    for candidate in ("mlb", "milb"):
        away_id, _away_name = _find_team_id(
            left,
            candidate,
            teams,
            team_lookup,
        )
        home_id, _home_name = _find_team_id(
            right,
            candidate,
            teams,
            team_lookup,
        )
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
    forced_end: datetime | None = None,
    extra_text: str = "",
    team_lookup: dict | None = None,
) -> dict | None:
    full_text = f"{text} {extra_text} {_channel_text(channel)}".strip()
    title_text = text.strip()
    if (
        _s.PLACEHOLDER_RE.search(title_text)
        or _s.CLEAR_OFF_AIR_RE.search(title_text)
        or _s.REPLAY_RE.search(full_text)
        and not settings.get("include_replays")
    ):
        return None
    if _s.PREGAME_RE.search(full_text) and not settings.get("include_pregame"):
        return None

    league_id = _detect_league(text)
    if not league_id and extra_text:
        league_id = _detect_league(extra_text)
    if not league_id:
        league_id = _detect_league("", _channel_text(channel))
    sport_tags = _detect_sport_tags(full_text)
    mapped_sport = _s.LEAGUE_SPORTS.get(league_id, "")
    if mapped_sport and mapped_sport not in sport_tags:
        sport_tags.insert(0, mapped_sport)
    sport_id = (
        mapped_sport
        or next((tag for tag in sport_tags if tag != "olympics"), "")
        or (sport_tags[0] if sport_tags else "")
    )
    stripped_text = _strip_provider_prefix(text)
    if forced_start is not None:
        # XMLTV rows and historical generated anchors already carry an
        # authoritative timestamp. Do not reinterpret incidental title text as
        # an embedded provider time; malformed tokens such as "30pm" in an old
        # generated title must not be able to abort the next scan.
        cleaned, parsed_start = stripped_text, None
    else:
        cleaned, parsed_start = _extract_event_datetime(
            stripped_text,
            settings,
            now,
        )
    if forced_start:
        timing_source = "xmltv"
    elif parsed_start:
        timing_source = "embedded"
    else:
        timing_source = "untimed"
    time_is_explicit = timing_source != "untimed"
    start = forced_start or parsed_start
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" |:-")

    match = _s.MATCHUP_RE.search(cleaned)
    if league_id in _s.TEAM_MATCHUP_LEAGUES and not match:
        return None
    teams = (
        team_lookup.get("teams", [])
        if team_lookup is not None
        else _team_catalog(db_path)
    )
    away_id = home_id = ""
    away_name = home_name = ""
    if match:
        left = match.group("left").strip(" |:-")
        right = match.group("right").strip(" |:-")
        if not league_id:
            league_id = _infer_baseball_league(
                left,
                right,
                teams,
                team_lookup,
            )
        # A title can identify a sport without identifying a supported league
        # (for example, "Baseball • Detroit Lions at Philadelphia Eagles").
        # In that case a global team lookup would incorrectly borrow NFL team
        # identities, causing an enabled Eagles rule to generate a baseball
        # channel with football artwork.  Keep unresolved participants scoped
        # away from every catalog league whenever the sport is explicit.
        team_lookup_league = league_id or ("__unclassified__" if sport_id else "")
        away_id, away_name = _find_team_id(
            left,
            team_lookup_league,
            teams,
            team_lookup,
        )
        home_id, home_name = _find_team_id(
            right,
            team_lookup_league,
            teams,
            team_lookup,
        )
        display_name = f"{away_name} at {home_name}"
    else:
        display_name = cleaned

    meaningful = bool(match or start or sport_id)
    if not meaningful or not display_name:
        return None
    if not match and re.fullmatch(
        r"(?:\d{1,2}\s*(?:am|pm)?|[A-Z0-9 ]*NETWORK|ESPN\d?|FOX|CBS|NBC|TNT|TBS)",
        display_name,
        re.I,
    ):
        return None

    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    event_date = (
        start.astimezone(timezone).date()
        if isinstance(start, datetime)
        else _s._sports_day(now, settings)
    )
    identity = "-".join(
        filter(
            None,
            [
                league_id or sport_id or "sports",
                away_id or _s._slug(away_name),
                home_id or _s._slug(home_name),
            ],
        )
    )
    if not match:
        identity = "-".join(
            filter(
                None,
                [
                    league_id or sport_id or "sports",
                    _s._slug(display_name),
                ],
            )
        )
    variant_match = _s.EVENT_VARIANT_RE.search(cleaned)
    if variant_match:
        trailing = cleaned[variant_match.end() :]
        if not re.match(r"\s+of\s+\d+\b", trailing, re.I):
            variant = variant_match.group("number") or {
                "first": "1",
                "second": "2",
            }.get(str(variant_match.group("word") or "").lower(), "")
            if variant:
                identity = f"{identity}:game-{variant}"
    event_base_key = f"{event_date.isoformat()}:{identity}"

    return {
        "event_key": event_base_key,
        "event_base_key": event_base_key,
        "event_identity": identity,
        "event_date": event_date.isoformat(),
        "league_id": league_id,
        "sport_id": sport_id,
        "sport_tags": sport_tags,
        "display_name": display_name,
        "away_team_id": away_id,
        "away_team_name": away_name,
        "home_team_id": home_id,
        "home_team_name": home_name,
        "start": start,
        "end": forced_end,
        "time_is_explicit": time_is_explicit,
        "timing_source": timing_source,
        "source_kind": "epg" if forced_start else "m3u",
        "has_embedded_anchor": timing_source == "embedded",
        "source_channels": [channel],
        "source_text": full_text,
        "is_replay": bool(_s.REPLAY_RE.search(full_text)),
    }


def _event_has_usable_timing(event: dict) -> bool:
    return (
        str(event.get("timing_source") or "untimed") != "untimed"
        and isinstance(event.get("start"), datetime)
    )


def _primary_event_end(event: dict) -> datetime | None:
    explicit = event.get("end")
    start = event.get("start")
    if isinstance(explicit, datetime):
        if not isinstance(start, datetime) or explicit > start:
            return explicit
    if not isinstance(start, datetime):
        return None
    classification_id = str(
        event.get("league_id") or event.get("sport_id") or "sports"
    )
    return start + _s._event_duration(classification_id)


def _event_end(event: dict) -> datetime | None:
    candidates = []
    primary = _primary_event_end(event)
    if isinstance(primary, datetime):
        candidates.append(primary)
    for programme in event.get("epg_programmes", []) or []:
        if not isinstance(programme, dict):
            continue
        stop = programme.get("stop")
        if isinstance(stop, datetime):
            candidates.append(stop)
    return max(candidates) if candidates else None


def _event_overlaps_window(
    event: dict,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    start = event.get("start")
    end = _event_end(event)
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False
    try:
        local_start = start.astimezone(window_start.tzinfo)
        local_end = end.astimezone(window_start.tzinfo)
    except Exception:
        return False
    return (
        local_start < window_end
        and local_end + _s.EVENT_END_GRACE > window_start
    )


def _event_overlaps_replay_context(
    event: dict,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    start = event.get("start")
    end = _primary_event_end(event)
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False
    try:
        local_start = start.astimezone(window_start.tzinfo)
        local_end = end.astimezone(window_start.tzinfo)
    except Exception:
        return False
    return (
        local_start < window_end
        and local_end + _s.REPLAY_ATTACH_WINDOW > window_start
    )


def _event_is_stale(event: dict, scan_anchor: datetime) -> bool:
    if not _event_has_usable_timing(event):
        return True
    end = _event_end(event)
    if not end:
        return True
    try:
        current = (
            scan_anchor.astimezone(end.tzinfo)
            if end.tzinfo
            else scan_anchor.replace(tzinfo=None)
        )
    except Exception:
        current = scan_anchor
    return current >= end + _s.EVENT_END_GRACE
