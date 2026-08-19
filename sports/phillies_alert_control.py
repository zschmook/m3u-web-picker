from __future__ import annotations

import time
from pathlib import Path

from . import channel_one_alerts
from . import channel_three_alerts
from . import game_alert_demo as demo
from . import mlb_live_source


def _is_phillies(team: demo.DemoTeam) -> bool:
    name = " ".join(str(team.name or "").strip().casefold().split())
    abbr = str(team.abbr or "").strip().upper()
    return abbr == "PHI" or name == "phillies" or name.endswith(" phillies")


def _game_priority(game: dict) -> int:
    status = game.get("status") if isinstance(game.get("status"), dict) else {}
    value = " ".join(
        str(status.get(key) or "")
        for key in ("abstractGameState", "detailedState", "codedGameState")
    ).casefold()
    if any(token in value for token in ("live", "in progress", "manager challenge")):
        return 0
    if any(token in value for token in ("final", "completed", "game over")):
        return 2
    return 1


def _score_value(entry: object, fallback: int) -> int:
    if isinstance(entry, dict):
        try:
            value = entry.get("score")
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return int(fallback)


def trigger_current_score(db_path: Path | str) -> dict:
    """Show the current Phillies score on the active channel-3 alert wrapper."""
    session = channel_three_alerts.get_session()
    if session is None:
        raise RuntimeError("Channel 3 alert stream is not active. Tune channel 3 first.")

    rows = session.tracker._mlb_rows(db_path)
    resolved = session.tracker._resolve_games(rows)
    candidates: list[tuple[int, dict, dict]] = []

    for row in rows:
        event_key = str(row.get("event_key") or "").strip()
        game = resolved.get(event_key)
        if not game:
            continue
        away = session.tracker._team_from_game(game, "away")
        home = session.tracker._team_from_game(game, "home")
        if _is_phillies(away) or _is_phillies(home):
            candidates.append((_game_priority(game), row, game))

    if not candidates:
        raise RuntimeError("No generated Phillies MLB game is available right now.")

    candidates.sort(key=lambda item: item[0])
    _priority, row, game = candidates[0]
    event_key = str(row.get("event_key") or "").strip()
    game_pk = session.tracker._game_pk(game)

    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    away_entry = teams.get("away") if isinstance(teams.get("away"), dict) else {}
    home_entry = teams.get("home") if isinstance(teams.get("home"), dict) else {}
    away_score = session.tracker._score(away_entry)
    home_score = session.tracker._score(home_entry)
    away = session.tracker._team_from_game(game, "away")
    home = session.tracker._team_from_game(game, "home")
    play = "Current Phillies score"

    if game_pk:
        try:
            state = mlb_live_source.fetch_live_state(game_pk)
            away = session.tracker._team_from_live_state(state, "away", away)
            home = session.tracker._team_from_live_state(state, "home", home)
            away_score = _score_value(state.get("away"), away_score)
            home_score = _score_value(state.get("home"), home_score)
            play = str(state.get("last_play") or play).strip() or play
        except Exception:
            pass

    scoring_team = away if _is_phillies(away) else home
    source_channel = int(row.get("assigned_number") or 0)
    if source_channel <= 0:
        raise RuntimeError("The Phillies game does not have a playable generated channel.")

    alert = demo.DemoAlert(
        league="MLB",
        scoring_team=scoring_team,
        away=away,
        home=home,
        away_score=away_score,
        home_score=home_score,
        play=play,
        source_channel=str(source_channel),
    )
    forced = channel_one_alerts.MlbScoreAlert(
        event_key=event_key,
        game_pk=game_pk,
        source_channel=source_channel,
        alert=alert,
    )

    now = time.monotonic()
    with session.tracker.state_lock:
        session.tracker.valid_destinations.add((event_key, source_channel))
        session.tracker.active = forced
        session.tracker.active_until = now + channel_one_alerts.ALERT_VISIBLE_SECONDS

    return {
        "ok": True,
        "channel_number": channel_three_alerts.CHANNEL_NUMBER,
        "source_channel": source_channel,
        "away": {"abbr": away.abbr, "name": away.name, "score": away_score},
        "home": {"abbr": home.abbr, "name": home.name, "score": home_score},
    }
