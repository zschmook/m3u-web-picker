from __future__ import annotations

import io
import json
import re
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from PIL import Image, ImageDraw


STANDINGS_URL = "https://statsapi.mlb.com/api/v1/standings"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "M3U-Web-Picker/31 sports-stats",
}

_LOCK = threading.RLock()
_CACHE_DATE = ""
_CACHE_BY_ABBR: dict[str, str] = {}
_CACHE_BY_NAME: dict[str, str] = {}
_LAST_ERROR = ""


def _json(url: str, *, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers=_HEADERS, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise RuntimeError("MLB standings returned an unexpected response.")
    return data


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _display_games_back(value: object) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if text in {"-", "--", "0", "0.0"}:
        return "—"
    return text


def _parse_standings(payload: dict) -> tuple[dict[str, str], dict[str, str]]:
    by_abbr: dict[str, str] = {}
    by_name: dict[str, str] = {}
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    for record in records:
        if not isinstance(record, dict):
            continue
        team_records = record.get("teamRecords") if isinstance(record.get("teamRecords"), list) else []
        for item in team_records:
            if not isinstance(item, dict):
                continue
            team = item.get("team") if isinstance(item.get("team"), dict) else {}
            games_back = _display_games_back(item.get("gamesBack"))
            if not games_back:
                continue
            abbreviation = str(team.get("abbreviation") or "").strip().upper()
            name = _norm(team.get("name") or team.get("teamName") or "")
            if abbreviation:
                by_abbr[abbreviation] = games_back
            if name:
                by_name[name] = games_back
    return by_abbr, by_name


def refresh_standings(*, force: bool = False) -> dict[str, Any]:
    """Load MLB games-back standings at most once per local calendar day.

    This is intentionally cold data. The normal overnight Master Update primes
    it once, while a process that starts later in the day can lazily fill the
    same cache on the first MLB stats request. A failed attempt is also marked
    for the day so a temporary standings outage cannot become a 3-second poll.
    """
    global _CACHE_DATE, _CACHE_BY_ABBR, _CACHE_BY_NAME, _LAST_ERROR

    today = _today()
    with _LOCK:
        if not force and _CACHE_DATE == today:
            return {
                "date": _CACHE_DATE,
                "teams": max(len(_CACHE_BY_ABBR), len(_CACHE_BY_NAME)),
                "error": _LAST_ERROR,
                "cached": True,
            }

        season = datetime.now().astimezone().year
        query = urllib.parse.urlencode(
            {
                "leagueId": "103,104",
                "season": str(season),
                "standingsTypes": "regularSeason",
                "hydrate": "team",
            }
        )
        try:
            payload = _json(f"{STANDINGS_URL}?{query}")
            by_abbr, by_name = _parse_standings(payload)
            if not by_abbr and not by_name:
                raise RuntimeError("MLB standings did not contain team records.")
            _CACHE_BY_ABBR = by_abbr
            _CACHE_BY_NAME = by_name
            _LAST_ERROR = ""
        except Exception as exc:
            # Keep yesterday's values if they exist, but do not retry every live
            # game poll. Tomorrow's Master Update gets another clean attempt.
            _LAST_ERROR = str(exc)
        _CACHE_DATE = today
        return {
            "date": _CACHE_DATE,
            "teams": max(len(_CACHE_BY_ABBR), len(_CACHE_BY_NAME)),
            "error": _LAST_ERROR,
            "cached": False,
        }


def _games_back(team: dict) -> str:
    if not isinstance(team, dict):
        return ""
    today = _today()
    with _LOCK:
        needs_refresh = _CACHE_DATE != today
    if needs_refresh:
        refresh_standings()

    abbreviation = str(team.get("abbr") or team.get("abbreviation") or "").strip().upper()
    name = _norm(team.get("name") or "")
    with _LOCK:
        if abbreviation and abbreviation in _CACHE_BY_ABBR:
            return _CACHE_BY_ABBR[abbreviation]
        if name and name in _CACHE_BY_NAME:
            return _CACHE_BY_NAME[name]
    return ""


def _batting_abbr(state: dict) -> str:
    """Return the batting/up-next team abbreviation from MLB inning state."""
    explicit = str(state.get("batting_team") or "").strip().upper()
    if explicit:
        return explicit

    away = state.get("away") if isinstance(state.get("away"), dict) else {}
    home = state.get("home") if isinstance(state.get("home"), dict) else {}
    away_abbr = str(away.get("abbr") or "").strip().upper()
    home_abbr = str(home.get("abbr") or "").strip().upper()
    status = str(state.get("status") or "").strip().lower()

    if status.startswith("top"):
        return away_abbr
    if status.startswith("bottom"):
        return home_abbr
    # During the half-inning break, show the team that is about to hit.
    if status.startswith("middle") or status.startswith("mid "):
        return home_abbr
    if status.startswith("end"):
        return away_abbr
    return ""


def enrich_state(state: dict) -> dict:
    if not isinstance(state, dict):
        return state
    for side in ("away", "home"):
        team = state.get(side)
        if not isinstance(team, dict):
            continue
        games_back = _games_back(team)
        if games_back:
            team["games_back"] = games_back
    batting_team = _batting_abbr(state)
    if batting_team:
        state["batting_team"] = batting_team
    return state


def _draw_games_back(draw: ImageDraw.ImageDraw, live_stats, state: dict, width: int) -> None:
    muted = (112, 132, 158)
    away = state.get("away") if isinstance(state.get("away"), dict) else {}
    home = state.get("home") if isinstance(state.get("home"), dict) else {}

    away_gb = str(away.get("games_back") or "").strip()
    if away_gb:
        live_stats._text(draw, (60, 121), f"GB {away_gb}", size=16, fill=muted)

    home_gb = str(home.get("games_back") or "").strip()
    if home_gb:
        label = f"GB {home_gb}"
        font = live_stats._font(16)
        bbox = draw.textbbox((0, 0), label, font=font)
        live_stats._text(draw, (width - 60 - (bbox[2] - bbox[0]), 121), label, size=16, fill=muted)


def _draw_at_bat_team(draw: ImageDraw.ImageDraw, live_stats, state: dict) -> None:
    panel = (17, 27, 41)
    muted = (144, 160, 180)
    batting_team = _batting_abbr(state)
    label = "AT BAT" if not batting_team else f"AT BAT: {batting_team}"
    # Cover the prototype's shorter header and redraw the richer label without
    # disturbing the surrounding panel border or the base diamond below it.
    draw.rectangle((802, 198, 1045, 232), fill=panel)
    live_stats._text(draw, (808, 205), label, size=20, bold=True, fill=muted)


def _redraw_team_stats(draw: ImageDraw.ImageDraw, live_stats, state: dict) -> None:
    panel = (17, 27, 41)
    muted = (144, 160, 180)
    away = state.get("away") if isinstance(state.get("away"), dict) else {}
    home = state.get("home") if isinstance(state.get("home"), dict) else {}

    # The prototype values were sitting on the panel's bottom border. Clear the
    # interior and redraw this small section with an actual lower margin.
    draw.rectangle((48, 392, 744, 480), fill=panel)
    live_stats._text(draw, (58, 397), "TEAM STATS", size=18, bold=True, fill=muted)

    stat_rows = [
        ("Hits", live_stats._stat_value(away, "hits"), live_stats._stat_value(home, "hits")),
        ("Walks", live_stats._stat_value(away, "walks", "baseOnBalls"), live_stats._stat_value(home, "walks", "baseOnBalls")),
        ("Strikeouts", live_stats._stat_value(away, "strikeouts", "totalStrikeouts"), live_stats._stat_value(home, "strikeouts", "totalStrikeouts")),
        ("Errors", live_stats._stat_value(away, "errors", fallback="0"), live_stats._stat_value(home, "errors", fallback="0")),
    ]
    x_positions = (190, 326, 462, 598)
    for x, (label, away_value, home_value) in zip(x_positions, stat_rows):
        live_stats._text(draw, (x, 427), label, size=14, fill=muted)
        live_stats._text(draw, (x, 450), f"{away_value}  /  {home_value}", size=17, bold=True)


def install(live_stats) -> None:
    """Add daily standings context and small MLB renderer cleanup."""
    if getattr(live_stats, "_mlb_stats_enrichment_installed", False):
        return

    original_fetch = live_stats.fetch_mlb_state
    original_render = live_stats.render_mlb_frame
    original_state_payload = live_stats.state_payload

    def fetch_mlb_state(source_event_id: str) -> dict:
        return enrich_state(original_fetch(source_event_id))

    def render_mlb_frame(state: dict, *, width: int = 1280, height: int = 720) -> bytes:
        payload = original_render(state, width=width, height=height)
        image = Image.open(io.BytesIO(payload)).convert("RGB")
        draw = ImageDraw.Draw(image)
        _draw_games_back(draw, live_stats, state, width)
        _draw_at_bat_team(draw, live_stats, state)
        _redraw_team_stats(draw, live_stats, state)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()

    def state_payload(db_path, assigned_number: int) -> dict:
        payload = original_state_payload(db_path, assigned_number)
        if isinstance(payload, dict) and isinstance(payload.get("state"), dict):
            payload["state"] = enrich_state(payload["state"])
        return payload

    live_stats.fetch_mlb_state = fetch_mlb_state
    live_stats.render_mlb_frame = render_mlb_frame
    live_stats.state_payload = state_payload
    live_stats._mlb_stats_enrichment_installed = True
