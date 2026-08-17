from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any


MLB_API_BASE = "https://statsapi.mlb.com/api"
SCHEDULE_URL = f"{MLB_API_BASE}/v1/schedule"
LIVE_FEED_URL = f"{MLB_API_BASE}/v1.1/game/{{game_pk}}/feed/live"

_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "M3U-Web-Picker/31 sports-stats",
}


def _json(url: str, *, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers=_HEADERS, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise RuntimeError("MLB StatsAPI returned an unexpected response.")
    return data


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _event_date(row: dict) -> datetime:
    value = str(row.get("event_start", "") or "").strip()
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed
        except (TypeError, ValueError, OverflowError):
            pass
    return datetime.now().astimezone()


def _team_aliases(team: dict) -> set[str]:
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


def event_match_score(row: dict, game: dict) -> int:
    """Score an MLB schedule game against a generated sports event.

    Both teams must match. The schedule is already MLB-only, so mascot aliases
    are safe enough here while still avoiding a one-team accidental bind.
    """
    haystack = _norm(
        " ".join(
            str(row.get(key, "") or "")
            for key in ("event_title", "display_name", "subtitle")
        )
    )
    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    matched_teams = 0
    score = 0
    for side in ("away", "home"):
        entry = teams.get(side) if isinstance(teams.get(side), dict) else {}
        team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
        aliases = _team_aliases(team)
        best = 0
        for alias in aliases:
            if alias in haystack:
                best = max(best, 8 + min(4, len(alias) // 5))
                continue
            words = [word for word in alias.split() if len(word) >= 4]
            overlap = sum(1 for word in words if word in haystack)
            best = max(best, overlap * 2)
        if best >= 4:
            matched_teams += 1
        score += best
    return score if matched_teams == 2 else -1


def _schedule_games(date_value: datetime) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "sportId": "1",
            "date": date_value.strftime("%Y-%m-%d"),
            "hydrate": "team",
        }
    )
    payload = _json(f"{SCHEDULE_URL}?{query}")
    games: list[dict] = []
    dates = payload.get("dates") if isinstance(payload.get("dates"), list) else []
    for date_item in dates:
        if not isinstance(date_item, dict):
            continue
        for game in date_item.get("games", []) or []:
            if isinstance(game, dict):
                games.append(game)
    return games


def resolve_game(row: dict) -> tuple[str, dict]:
    """Resolve a generated MLB event to MLB's stable gamePk."""
    anchor = _event_date(row)
    ranked: list[tuple[int, dict]] = []
    seen: set[int] = set()
    for offset in (0, -1, 1):
        for game in _schedule_games(anchor + timedelta(days=offset)):
            game_pk = int(game.get("gamePk") or 0)
            if game_pk <= 0 or game_pk in seen:
                continue
            seen.add(game_pk)
            ranked.append((event_match_score(row, game), game))
        if ranked and max(score for score, _game in ranked) >= 0:
            break

    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < 0:
        title = str(row.get("event_title") or row.get("display_name") or "MLB game")
        raise RuntimeError(f"Could not match MLB StatsAPI game for {title}.")

    game = ranked[0][1]
    game_pk = str(game.get("gamePk") or "").strip()
    if not game_pk:
        raise RuntimeError("Matched MLB game did not include gamePk.")
    return game_pk, game


def fetch_live_feed(game_pk: str | int) -> dict:
    game_id = str(game_pk or "").strip()
    if not game_id.isdigit():
        raise RuntimeError("MLB gamePk was invalid.")
    return _json(f"{LIVE_FEED_URL.format(game_pk=game_id)}?language=en")


def _person_name(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return str(
        value.get("fullName")
        or value.get("name")
        or value.get("lastInitName")
        or ""
    ).strip()


def _team_record(team: dict) -> str:
    record = team.get("record") if isinstance(team.get("record"), dict) else {}
    wins = record.get("wins")
    losses = record.get("losses")
    if wins is not None and losses is not None:
        return f"{wins}-{losses}"
    return str(record.get("leagueRecord", "") or "")


def _box_team_stats(live_data: dict, side: str) -> dict[str, str]:
    boxscore = live_data.get("boxscore") if isinstance(live_data.get("boxscore"), dict) else {}
    teams = boxscore.get("teams") if isinstance(boxscore.get("teams"), dict) else {}
    team_box = teams.get(side) if isinstance(teams.get(side), dict) else {}
    team_stats = team_box.get("teamStats") if isinstance(team_box.get("teamStats"), dict) else {}
    batting = team_stats.get("batting") if isinstance(team_stats.get("batting"), dict) else {}
    return {
        "walks": str(batting.get("baseOnBalls", "") or ""),
        "strikeouts": str(batting.get("strikeOuts", "") or ""),
    }


def _team_state(game_data: dict, live_data: dict, linescore: dict, side: str) -> dict:
    teams = game_data.get("teams") if isinstance(game_data.get("teams"), dict) else {}
    team = teams.get(side) if isinstance(teams.get(side), dict) else {}
    line_teams = linescore.get("teams") if isinstance(linescore.get("teams"), dict) else {}
    totals = line_teams.get(side) if isinstance(line_teams.get(side), dict) else {}

    innings: list[str] = []
    for inning in linescore.get("innings", []) or []:
        if not isinstance(inning, dict):
            continue
        inning_side = inning.get(side) if isinstance(inning.get(side), dict) else {}
        runs = inning_side.get("runs")
        innings.append("-" if runs is None else str(runs))

    stats = _box_team_stats(live_data, side)
    stats.update(
        {
            "hits": str(totals.get("hits", 0) or 0),
            "errors": str(totals.get("errors", 0) or 0),
        }
    )
    return {
        "name": str(team.get("name") or team.get("teamName") or side.title()),
        "abbr": str(team.get("abbreviation") or team.get("fileCode") or "").upper(),
        "score": str(totals.get("runs", 0) or 0),
        "record": _team_record(team),
        "logo": "",
        "stats": stats,
        "innings": innings,
    }


def _last_play(live_data: dict) -> tuple[dict, str]:
    plays = live_data.get("plays") if isinstance(live_data.get("plays"), dict) else {}
    current = plays.get("currentPlay") if isinstance(plays.get("currentPlay"), dict) else {}
    result = current.get("result") if isinstance(current.get("result"), dict) else {}
    description = str(result.get("description") or "").strip()
    if description:
        return current, description

    all_plays = plays.get("allPlays") if isinstance(plays.get("allPlays"), list) else []
    for play in reversed(all_plays):
        if not isinstance(play, dict):
            continue
        result = play.get("result") if isinstance(play.get("result"), dict) else {}
        description = str(result.get("description") or "").strip()
        if description:
            return current or play, description
    return current, ""


def _latest_pitch(current_play: dict) -> dict[str, Any]:
    events = current_play.get("playEvents") if isinstance(current_play.get("playEvents"), list) else []
    for event in reversed(events):
        if not isinstance(event, dict) or not bool(event.get("isPitch")):
            continue
        details = event.get("details") if isinstance(event.get("details"), dict) else {}
        pitch_type = details.get("type") if isinstance(details.get("type"), dict) else {}
        pitch_data = event.get("pitchData") if isinstance(event.get("pitchData"), dict) else {}
        coordinates = pitch_data.get("coordinates") if isinstance(pitch_data.get("coordinates"), dict) else {}
        breaks = pitch_data.get("breaks") if isinstance(pitch_data.get("breaks"), dict) else {}
        return {
            "number": event.get("pitchNumber"),
            "description": str(details.get("description") or ""),
            "type": str(pitch_type.get("description") or pitch_type.get("code") or ""),
            "start_speed": pitch_data.get("startSpeed"),
            "end_speed": pitch_data.get("endSpeed"),
            "zone": pitch_data.get("zone"),
            "px": coordinates.get("pX"),
            "pz": coordinates.get("pZ"),
            "spin_rate": breaks.get("spinRate"),
        }
    return {}


def _latest_hit(current_play: dict) -> dict[str, Any]:
    events = current_play.get("playEvents") if isinstance(current_play.get("playEvents"), list) else []
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        hit = event.get("hitData") if isinstance(event.get("hitData"), dict) else {}
        if hit:
            return {
                "launch_speed": hit.get("launchSpeed"),
                "launch_angle": hit.get("launchAngle"),
                "distance": hit.get("totalDistance"),
                "trajectory": hit.get("trajectory"),
                "hardness": hit.get("hardness"),
            }
    return {}


def normalize_live_feed(feed: dict, *, game_pk: str = "") -> dict:
    game_data = feed.get("gameData") if isinstance(feed.get("gameData"), dict) else {}
    live_data = feed.get("liveData") if isinstance(feed.get("liveData"), dict) else {}
    linescore = live_data.get("linescore") if isinstance(live_data.get("linescore"), dict) else {}

    away = _team_state(game_data, live_data, linescore, "away")
    home = _team_state(game_data, live_data, linescore, "home")
    current_play, last_play = _last_play(live_data)
    matchup = current_play.get("matchup") if isinstance(current_play.get("matchup"), dict) else {}

    offense = linescore.get("offense") if isinstance(linescore.get("offense"), dict) else {}
    defense = linescore.get("defense") if isinstance(linescore.get("defense"), dict) else {}
    batter = offense.get("batter") if isinstance(offense.get("batter"), dict) else matchup.get("batter")
    pitcher = defense.get("pitcher") if isinstance(defense.get("pitcher"), dict) else matchup.get("pitcher")

    status_data = game_data.get("status") if isinstance(game_data.get("status"), dict) else {}
    inning_state = str(linescore.get("inningState") or "").strip()
    inning_ordinal = str(linescore.get("currentInningOrdinal") or "").strip()
    abstract_state = str(status_data.get("abstractGameState") or "").strip().lower()
    detailed_state = str(status_data.get("detailedState") or status_data.get("abstractGameState") or "MLB")
    if abstract_state in {"final", "completed"}:
        status = detailed_state
    elif inning_state and inning_ordinal:
        status = f"{inning_state} {inning_ordinal}"
    else:
        status = detailed_state

    state = {
        # Keep this legacy field populated until live_stats itself is made
        # source-agnostic; source_event_id is the field new code should use.
        "espn_event_id": str(game_pk or feed.get("gamePk") or ""),
        "source_event_id": str(game_pk or feed.get("gamePk") or ""),
        "mlb_game_pk": str(game_pk or feed.get("gamePk") or ""),
        "away": away,
        "home": home,
        "status": status,
        "state": abstract_state,
        "period": int(linescore.get("currentInning", 0) or 0),
        "clock": "",
        "balls": int(linescore.get("balls", 0) or 0),
        "strikes": int(linescore.get("strikes", 0) or 0),
        "outs": int(linescore.get("outs", 0) or 0),
        "on_first": bool(offense.get("first") or matchup.get("postOnFirst")),
        "on_second": bool(offense.get("second") or matchup.get("postOnSecond")),
        "on_third": bool(offense.get("third") or matchup.get("postOnThird")),
        "batter": _person_name(batter),
        "pitcher": _person_name(pitcher),
        "last_play": last_play,
        "pitch": _latest_pitch(current_play),
        "batted_ball": _latest_hit(current_play),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_source": "mlb-statsapi",
        "data_source_label": "MLB StatsAPI",
    }
    return state


def fetch_live_state(game_pk: str | int) -> dict:
    game_id = str(game_pk or "").strip()
    return normalize_live_feed(fetch_live_feed(game_id), game_pk=game_id)
