from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import sports as _s



ESPN_SCOREBOARDS = {
    "nfl": ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard", ()),
    "ncaaf": (
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        (80, 81, 35, 17),
    ),
}
MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
_SOURCE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "M3U-Web-Picker/31 remote-sports",
}


def _json_get(url: str, *, timeout: float = 8.0) -> dict:
    source_request = urllib.request.Request(url, headers=_SOURCE_HEADERS, method="GET")
    with urllib.request.urlopen(source_request, timeout=timeout) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise RuntimeError("Live-score provider returned an unexpected response.")
    return data


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _row_start(row: dict) -> datetime:
    value = str(row.get("event_start") or "").strip()
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.astimezone()
        except (TypeError, ValueError, OverflowError):
            pass
    return datetime.now().astimezone()


def _football_scoreboard_events(group: str, anchor: datetime) -> list[dict]:
    definition = ESPN_SCOREBOARDS.get(str(group or "").strip().lower())
    if not definition:
        return []
    url, configured_groups = definition
    output: list[dict] = []
    seen: set[str] = set()
    for espn_group in configured_groups or (None,):
        query = {"dates": anchor.strftime("%Y%m%d"), "limit": "1000"}
        if espn_group is not None:
            query["groups"] = str(espn_group)
        data = _json_get(f"{url}?{urllib.parse.urlencode(query)}")
        events = data.get("events") if isinstance(data.get("events"), list) else []
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "").strip()
            if event_id and event_id not in seen:
                seen.add(event_id)
                output.append(event)
    return output


def _football_competitors(competition: dict) -> dict[str, dict]:
    output: dict[str, dict] = {}
    competitors = competition.get("competitors")
    for item in competitors if isinstance(competitors, list) else []:
        if not isinstance(item, dict):
            continue
        side = str(item.get("homeAway") or "").lower()
        if side in {"home", "away"}:
            output[side] = item
    return output


def _football_team_payload(competitor: dict) -> dict:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    records = competitor.get("records") if isinstance(competitor.get("records"), list) else []
    record = str(records[0].get("summary") or "") if records and isinstance(records[0], dict) else ""
    logo = str(team.get("logo") or "").strip()
    if not logo:
        logos = team.get("logos") if isinstance(team.get("logos"), list) else []
        logo = next(
            (
                str(item.get("href") or "").strip()
                for item in logos
                if isinstance(item, dict) and str(item.get("href") or "").strip()
            ),
            "",
        )
    return {
        "id": str(team.get("id") or "").strip(),
        "name": str(team.get("displayName") or team.get("name") or "Team"),
        "abbr": str(team.get("abbreviation") or "").upper(),
        "score": str(competitor.get("score", "0") or "0"),
        "record": record,
        "logo": logo,
        "ap_rank": None,
    }


def _football_team_aliases(competitor: dict) -> set[str]:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    values = {
        team.get("displayName"),
        team.get("shortDisplayName"),
        team.get("name"),
        team.get("location"),
        team.get("abbreviation"),
    }
    return {_norm(value) for value in values if _norm(value)}


def _football_event_match_score(row: dict, event: dict) -> int:
    haystack = _norm(" ".join(str(row.get(key) or "") for key in ("event_title", "display_name", "subtitle")))
    competition = _competition(event)
    competitors = competition.get("competitors")
    if not isinstance(competitors, list) or len(competitors) < 2:
        return -1
    matched_teams = 0
    score = 0
    for competitor in competitors[:2]:
        aliases = _football_team_aliases(competitor if isinstance(competitor, dict) else {})
        best = 0
        for alias in aliases:
            if alias in haystack:
                best = max(best, 8 + min(4, len(alias) // 5))
            else:
                words = [word for word in alias.split() if len(word) >= 4]
                best = max(best, sum(1 for word in words if word in haystack) * 2)
        if best >= 4:
            matched_teams += 1
        score += best
    return score if matched_teams >= 2 else -1


def _time_bonus(row: dict, candidate_date: object) -> int:
    value = str(candidate_date or "").strip()
    if not value:
        return 0
    try:
        scheduled = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if scheduled.tzinfo is None:
            scheduled = scheduled.astimezone()
        hours = abs((scheduled - _row_start(row)).total_seconds()) / 3600.0
    except (TypeError, ValueError, OverflowError):
        return 0
    if hours <= 0.5:
        return 6
    if hours <= 1.5:
        return 4
    if hours <= 3.0:
        return 2
    return 0


def _football_time_bonus(row: dict, event: dict) -> int:
    return _time_bonus(row, event.get("date"))


def _mlb_team_aliases(team: dict) -> set[str]:
    values = {
        team.get("name"),
        team.get("teamName"),
        team.get("clubName"),
        team.get("shortName"),
        team.get("locationName"),
        team.get("abbreviation"),
        team.get("fileCode"),
    }
    return {_norm(value) for value in values if _norm(value)}


def _mlb_event_match_score(row: dict, game: dict) -> int:
    haystack = _norm(" ".join(str(row.get(key) or "") for key in ("event_title", "display_name", "subtitle")))
    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    matched_teams = 0
    score = 0
    for side in ("away", "home"):
        entry = teams.get(side) if isinstance(teams.get(side), dict) else {}
        team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
        best = 0
        for alias in _mlb_team_aliases(team):
            if alias in haystack:
                best = max(best, 8 + min(4, len(alias) // 5))
            else:
                words = [word for word in alias.split() if len(word) >= 4]
                best = max(best, sum(1 for word in words if word in haystack) * 2)
        if best >= 4:
            matched_teams += 1
        score += best
    return score if matched_teams == 2 else -1


def _mlb_schedule_games(date_value: datetime, sport_ids: tuple[int, ...] = (1,)) -> list[dict]:
    query = urllib.parse.urlencode({
        "sportId": ",".join(str(value) for value in sport_ids),
        "date": date_value.strftime("%Y-%m-%d"),
    })
    payload = _json_get(f"{MLB_SCHEDULE_URL}?{query}")
    output: list[dict] = []
    dates = payload.get("dates") if isinstance(payload.get("dates"), list) else []
    for date_item in dates:
        if not isinstance(date_item, dict):
            continue
        output.extend(game for game in date_item.get("games", []) or [] if isinstance(game, dict))
    return output


def _mlb_time_bonus(row: dict, game: dict) -> int:
    return _time_bonus(row, game.get("gameDate"))


REFRESH_SECONDS = 10.0
POSTGAME_GRACE = timedelta(minutes=90)
SCORE_HISTORY_RETENTION = timedelta(days=1)
UPCOMING_REPLACES_HISTORY = timedelta(hours=3)

_LOCK = threading.RLock()
_CACHE_SIGNATURE: tuple = ()
_CACHE: dict[str, dict] = {}
_CACHE_AT = 0.0
_REFRESHING = False


def _datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _local_now(timezone_name: str, now: datetime | None = None) -> datetime:
    try:
        timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    except (ZoneInfoNotFoundError, ValueError):
        timezone = datetime.now().astimezone().tzinfo
    current = now or datetime.now(timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone)
    return current.astimezone(timezone)


def _group(row: dict) -> str:
    explicit = str(row.get("stats_carousel_group") or "").strip().lower()
    if explicit:
        return explicit
    league_id = str(row.get("league_id") or "").strip().lower()
    if league_id == "mlb":
        return "mlb"
    if league_id == "nfl":
        return "nfl"
    if league_id in {"ncaaf-fbs", "ncaaf-fcs", "ncaaf-d2", "ncaaf-d3"}:
        return "ncaaf"
    return league_id or "sports"


def _event_key(row: dict) -> str:
    return str(row.get("event_key") or row.get("channel_key") or "").strip()


def primary_rows(rows: list[dict]) -> list[dict]:
    """Choose one playable provider feed for each logical sporting event."""
    chosen: dict[str, dict] = {}
    for raw in rows:
        row = dict(raw)
        if bool(row.get("is_replay")):
            continue
        key = _event_key(row)
        number = int(row.get("assigned_number") or 0)
        target = str(row.get("url") or "").strip()
        if not key or number <= 0 or not target:
            continue
        current = chosen.get(key)
        if current is None or number < int(current.get("assigned_number") or 0):
            chosen[key] = row
    return sorted(
        chosen.values(),
        key=lambda row: (
            str(row.get("event_start") or ""),
            int(row.get("assigned_number") or 0),
        ),
    )


def _team_names(title: str) -> tuple[str, str]:
    parts = re.split(r"\s+(?:@|at|vs\.?|versus)\s+", str(title or ""), maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return str(title or "Away").strip(), "Home"


def _base_phase(row: dict, now: datetime) -> str:
    start = _datetime(row.get("event_start"))
    stop = _datetime(row.get("event_end"))
    if start is not None and now.astimezone(start.tzinfo) < start:
        return "upcoming"
    if start is not None and stop is None:
        stop = start + timedelta(hours=5)
    if stop is None or now.astimezone(stop.tzinfo) <= stop + POSTGAME_GRACE:
        return "live"
    return "final"


def _base_card(row: dict, now: datetime) -> dict:
    title = str(row.get("event_title") or row.get("display_name") or "Sports event").strip()
    away_name, home_name = _team_names(title)
    number = int(row.get("assigned_number") or 0)
    group = _group(row)
    labels = {"mlb": "MLB", "nfl": "NFL", "ncaaf": "NCAA Football"}
    phase = _base_phase(row, now)
    return {
        "id": f"/guide/play/sports/{number}",
        "number": str(number),
        "name": title,
        "group": str(row.get("group_title") or "Sports Today"),
        "logo": str(row.get("tvg_logo") or ""),
        "event_key": _event_key(row),
        "sport": group,
        "sport_label": labels.get(group, group.replace("-", " ").upper()),
        "title": title,
        "feed": str(row.get("subtitle") or row.get("feed_type") or "").strip(),
        "start": str(row.get("event_start") or ""),
        "stop": str(row.get("event_end") or ""),
        "phase": phase,
        "selectable": phase == "live",
        "away": {"name": away_name, "abbr": "", "score": "-", "record": "", "logo": "", "ap_rank": None},
        "home": {"name": home_name, "abbr": "", "score": "-", "record": "", "logo": "", "ap_rank": None},
        "status": "Upcoming" if phase == "upcoming" else ("Final" if phase == "final" else "Live"),
        "last_play": "",
        "network": "",
        "top_25": False,
        "score_margin": None,
    }


def _competition(event: dict) -> dict:
    competitions = event.get("competitions") if isinstance(event.get("competitions"), list) else []
    return competitions[0] if competitions and isinstance(competitions[0], dict) else {}


def _ap_rank(competitor: dict, group: str) -> int | None:
    if group != "ncaaf":
        return None
    curated = competitor.get("curatedRank") if isinstance(competitor.get("curatedRank"), dict) else {}
    try:
        rank = int(curated.get("current") or 0)
    except (TypeError, ValueError):
        return None
    return rank if 1 <= rank <= 25 else None


def _network(competition: dict) -> str:
    for broadcast in competition.get("broadcasts", []) or []:
        if not isinstance(broadcast, dict):
            continue
        names = broadcast.get("names") if isinstance(broadcast.get("names"), list) else []
        value = ", ".join(str(name).strip() for name in names if str(name).strip())
        if value:
            return value
    return ""


def _integer_score(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _phase_from_espn(competition: dict) -> str:
    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    state = str(status_type.get("state") or "").strip().lower()
    if bool(status_type.get("completed")) or state == "post":
        return "final"
    if state == "pre":
        return "upcoming"
    return "live" if state in {"in", "live"} else ""


def _football_snapshot(row: dict, event: dict, group: str) -> dict:
    competition = _competition(event)
    competitors = _football_competitors(competition)
    away_competitor = competitors.get("away", {})
    home_competitor = competitors.get("home", {})
    away = _football_team_payload(away_competitor)
    home = _football_team_payload(home_competitor)
    away["ap_rank"] = _ap_rank(away_competitor, group)
    home["ap_rank"] = _ap_rank(home_competitor, group)
    phase = _phase_from_espn(competition) or "live"
    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    situation = competition.get("situation") if isinstance(competition.get("situation"), dict) else {}
    last_play = situation.get("lastPlay") if isinstance(situation.get("lastPlay"), dict) else {}
    away_score = _integer_score(away.get("score"))
    home_score = _integer_score(home.get("score"))
    return {
        "away": away,
        "home": home,
        "phase": phase,
        "selectable": phase == "live",
        "status": str(status_type.get("shortDetail") or status_type.get("detail") or phase.title()),
        "last_play": str(last_play.get("text") or "").strip(),
        "network": _network(competition),
        "top_25": bool(away.get("ap_rank") or home.get("ap_rank")),
        "score_margin": abs(away_score - home_score) if away_score is not None and home_score is not None else None,
    }


def _record(entry: dict) -> str:
    record = entry.get("leagueRecord") if isinstance(entry.get("leagueRecord"), dict) else {}
    wins = record.get("wins")
    losses = record.get("losses")
    return f"{wins}-{losses}" if wins is not None and losses is not None else ""


def _mlb_team(entry: dict) -> dict:
    team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
    return {
        "name": str(team.get("name") or team.get("teamName") or "Team"),
        "abbr": str(team.get("abbreviation") or team.get("fileCode") or "").upper(),
        "score": str(entry.get("score", "-") if entry.get("score") is not None else "-"),
        "record": _record(entry),
        "logo": "",
        "ap_rank": None,
    }


def _mlb_snapshot(row: dict, game: dict) -> dict:
    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    away = _mlb_team(teams.get("away") if isinstance(teams.get("away"), dict) else {})
    home = _mlb_team(teams.get("home") if isinstance(teams.get("home"), dict) else {})
    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    abstract = str(status.get("abstractGameState") or "").strip().lower()
    phase = "upcoming" if abstract in {"preview", "pre"} else ("final" if abstract in {"final", "completed"} else "live")
    away_score = _integer_score(away.get("score"))
    home_score = _integer_score(home.get("score"))
    return {
        "away": away,
        "home": home,
        "phase": phase,
        "selectable": phase == "live",
        "status": str(status.get("detailedState") or status.get("abstractGameState") or phase.title()),
        "last_play": "",
        "network": "",
        "top_25": False,
        "score_margin": abs(away_score - home_score) if away_score is not None and home_score is not None else None,
    }


def _best_match(
    row: dict,
    candidates: list[dict],
    scorer: Callable[[dict, dict], int],
    bonus: Callable[[dict, dict], int],
) -> dict | None:
    ranked: list[tuple[int, dict]] = []
    for candidate in candidates:
        score = int(scorer(row, candidate))
        if score >= 0:
            score += int(bonus(row, candidate))
        ranked.append((score, candidate))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1] if ranked and ranked[0][0] >= 0 else None


def _football_group(rows: list[dict], group: str, anchor: datetime) -> dict[str, dict]:
    events = _football_scoreboard_events(group, anchor)
    output: dict[str, dict] = {}
    for row in rows:
        event = _best_match(row, events, _football_event_match_score, _football_time_bonus)
        if event is not None:
            output[_event_key(row)] = _football_snapshot(row, event, group)
    return output


def _mlb_group(rows: list[dict], anchor: datetime) -> dict[str, dict]:
    games = _mlb_schedule_games(anchor, (1,))
    output: dict[str, dict] = {}
    for row in rows:
        game = _best_match(row, games, _mlb_event_match_score, _mlb_time_bonus)
        if game is not None:
            output[_event_key(row)] = _mlb_snapshot(row, game)
    return output


def _snapshots(rows: list[dict], anchor: datetime) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(_group(row), []).append(row)
    jobs = []
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="remote-sports-source") as pool:
        if grouped.get("mlb"):
            jobs.append(pool.submit(_mlb_group, grouped["mlb"], anchor))
        for group in ("nfl", "ncaaf"):
            if grouped.get(group):
                jobs.append(pool.submit(_football_group, grouped[group], group, anchor))
        output: dict[str, dict] = {}
        for job in jobs:
            try:
                output.update(job.result())
            except Exception:
                continue
    return output


def _signature(rows: list[dict]) -> tuple:
    return tuple(
        (_event_key(row), int(row.get("assigned_number") or 0), str(row.get("event_start") or ""))
        for row in rows
    )


def _refresh(rows: list[dict], signature: tuple, anchor: datetime) -> None:
    global _CACHE_SIGNATURE, _CACHE, _CACHE_AT, _REFRESHING
    try:
        snapshots = _snapshots(rows, anchor)
        with _LOCK:
            _CACHE_SIGNATURE = signature
            _CACHE = snapshots
            _CACHE_AT = time.monotonic()
    finally:
        with _LOCK:
            _REFRESHING = False


def _start_refresh(rows: list[dict], signature: tuple, anchor: datetime) -> None:
    global _REFRESHING
    with _LOCK:
        if _REFRESHING:
            return
        _REFRESHING = True
    threading.Thread(
        target=_refresh,
        args=([dict(row) for row in rows], signature, anchor),
        name="remote-sports-refresh",
        daemon=True,
    ).start()


def game_cards(
    rows: list[dict],
    *,
    timezone_name: str = "America/New_York",
    now: datetime | None = None,
    refresh: bool = True,
) -> list[dict]:
    primary = primary_rows(rows)
    anchor = _local_now(timezone_name, now)
    signature = _signature(primary)
    with _LOCK:
        cached = dict(_CACHE) if _CACHE_SIGNATURE == signature else {}
        stale = _CACHE_SIGNATURE != signature or time.monotonic() - _CACHE_AT >= REFRESH_SECONDS
    if refresh and stale:
        _start_refresh(primary, signature, anchor)

    cards = []
    for row in primary:
        card = _base_card(row, anchor)
        card.update(cached.get(_event_key(row), {}))
        card["selectable"] = bool(card.get("selectable")) and bool(str(row.get("url") or "").strip())
        cards.append(card)
    return cards


def retain_recent_finals(
    db_path: Path | str,
    cards: list[dict],
    *,
    timezone_name: str = "America/New_York",
    now: datetime | None = None,
) -> list[dict]:
    """Persist final score cards for 24 hours without retaining stream targets."""
    anchor = _local_now(timezone_name, now)
    anchor_utc = anchor.astimezone(timezone.utc)
    active_keys = {_event_key(card) for card in cards if _event_key(card)}
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        conn.execute(
            "DELETE FROM sports_remote_score_history WHERE expires_at <= ?",
            (anchor_utc.isoformat(),),
        )
        for card in cards:
            event_key = _event_key(card)
            if (
                not event_key
                or str(card.get("phase") or "") != "final"
            ):
                continue
            historical = dict(card)
            historical.update(
                id=f"history:{event_key}",
                number="",
                selectable=False,
                history=True,
            )
            expires_at = anchor_utc + SCORE_HISTORY_RETENTION
            conn.execute(
                """
                INSERT INTO sports_remote_score_history
                    (event_key, sport, finished_at, expires_at, card_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO UPDATE SET
                    sport = excluded.sport,
                    card_json = excluded.card_json,
                    updated_at = excluded.updated_at
                """,
                (
                    event_key,
                    str(card.get("sport") or ""),
                    anchor_utc.isoformat(),
                    expires_at.isoformat(),
                    json.dumps(historical, separators=(",", ":")),
                    anchor_utc.isoformat(),
                ),
            )
        rows = conn.execute(
            """
            SELECT event_key, card_json
            FROM sports_remote_score_history
            WHERE expires_at > ?
            ORDER BY finished_at DESC
            """,
            (anchor_utc.isoformat(),),
        ).fetchall()
        conn.commit()

    output = list(cards)
    for row in rows:
        event_key = str(row["event_key"] or "")
        if not event_key or event_key in active_keys:
            continue
        historical = _s._json_load(row["card_json"], {})
        if not isinstance(historical, dict):
            continue
        historical.update(
            id=f"history:{event_key}",
            number="",
            phase="final",
            selectable=False,
            history=True,
        )
        output.append(historical)
    return _hide_superseded_finals(output, anchor)


def _card_teams(card: dict) -> set[str]:
    teams = []
    for side in ("away", "home"):
        team = card.get(side) if isinstance(card.get(side), dict) else {}
        teams.extend((team.get("name"), team.get("abbr")))
    return {
        normalized
        for value in teams
        if (normalized := re.sub(r"[^a-z0-9]+", "", str(value or "").casefold()))
    }


def _hide_superseded_finals(cards: list[dict], anchor: datetime) -> list[dict]:
    soon_teams: set[str] = set()
    for card in cards:
        if str(card.get("phase") or "") != "upcoming":
            continue
        start = _datetime(card.get("start"))
        if start is None:
            continue
        until_start = start - anchor.astimezone(start.tzinfo)
        if timedelta(0) <= until_start <= UPCOMING_REPLACES_HISTORY:
            soon_teams.update(_card_teams(card))
    if not soon_teams:
        return cards
    return [
        card
        for card in cards
        if not (
            str(card.get("phase") or "") == "final"
            and bool(_card_teams(card) & soon_teams)
        )
    ]
