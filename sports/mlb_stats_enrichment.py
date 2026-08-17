from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from PIL import Image, ImageDraw

import espn_known_logos
from settings import load_settings


STANDINGS_URL = "https://statsapi.mlb.com/api/v1/standings"
_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "M3U-Web-Picker/31 sports-stats",
}

_LOCK = threading.RLock()
_CACHE_DATE = ""
_CACHE_BY_ABBR: dict[str, str] = {}
_CACHE_BY_NAME: dict[str, str] = {}
_ACCENT_BY_TEAM: dict[str, tuple[int, int, int] | None] = {}
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


def _out_dot_count(state: dict) -> int:
    """Map MLB's transitional 3-out value onto the two-dot scoreboard display."""
    try:
        outs = int(state.get("outs", 0) or 0)
    except (TypeError, ValueError):
        outs = 0
    return max(0, min(2, outs))


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


def _cached_team_logo(team: dict) -> Image.Image | None:
    """Read an already-cached MLB team logo without making a network request."""
    candidates: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        url = str(value or "").strip()
        try:
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            return
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or url in seen:
            return
        seen.add(url)
        candidates.append(url)

    # ESPN fallback states already carry a logo URL. MLB StatsAPI states do not,
    # so use our canonical MLB taxonomy to derive the same ordinary ESPN mark
    # used by the sports artwork pipeline. This does not contact ESPN.
    add(team.get("logo"))
    try:
        add(
            espn_known_logos.direct_full_default_url(
                "mlb",
                team.get("name") or team.get("abbr"),
            )
        )
    except Exception:
        pass

    cache_dir = load_settings().data_dir / "logo_cache"
    for url in candidates:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = cache_dir / f"{digest}.bin"
        try:
            payload = path.read_bytes()
            logo = Image.open(io.BytesIO(payload)).convert("RGBA")
            logo.load()
            return logo
        except (OSError, ValueError, TypeError):
            continue
    return None


def _team_key(team: dict) -> str:
    abbreviation = str(team.get("abbr") or team.get("abbreviation") or "").strip().upper()
    if abbreviation:
        return abbreviation
    return _norm(team.get("name") or "")


def _blend_color(base: tuple[int, int, int], accent: tuple[int, int, int], weight: float) -> tuple[int, int, int]:
    weight = max(0.0, min(1.0, float(weight)))
    return tuple(
        int(round(base[index] * (1.0 - weight) + accent[index] * weight))
        for index in range(3)
    )


def _logo_accent(logo: Image.Image | None) -> tuple[int, int, int] | None:
    """Pick a saturated team-ish color from a transparent cached logo."""
    if logo is None:
        return None

    sample = logo.copy().convert("RGBA")
    sample.thumbnail((64, 64))
    buckets: dict[tuple[int, int, int], int] = {}
    for red, green, blue, alpha in sample.getdata():
        if alpha < 96:
            continue
        high = max(red, green, blue)
        low = min(red, green, blue)
        brightness = (red + green + blue) / 3.0
        chroma = high - low
        # Ignore transparent edges plus black/white/gray logo furniture. The
        # remaining saturated pixels are much more likely to be the team mark.
        if brightness < 32 or brightness > 238 or chroma < 30:
            continue
        bucket = (
            min(255, (red // 32) * 32 + 16),
            min(255, (green // 32) * 32 + 16),
            min(255, (blue // 32) * 32 + 16),
        )
        buckets[bucket] = buckets.get(bucket, 0) + 1

    if not buckets:
        return None

    def score(color: tuple[int, int, int]) -> float:
        population = buckets[color]
        chroma = max(color) - min(color)
        return population * (1.0 + chroma / 96.0)

    accent = max(buckets, key=score)
    # Very dark navy marks can disappear into our dark panel. Lighten those a
    # touch while retaining the actual hue before using them as a gradient.
    luminance = 0.2126 * accent[0] + 0.7152 * accent[1] + 0.0722 * accent[2]
    if luminance < 70:
        accent = _blend_color(accent, (255, 255, 255), 0.18)
    return accent


def _team_accent(team: dict, logo: Image.Image | None) -> tuple[int, int, int] | None:
    key = _team_key(team)
    if key:
        with _LOCK:
            if key in _ACCENT_BY_TEAM:
                return _ACCENT_BY_TEAM[key]
    accent = _logo_accent(logo)
    if key:
        with _LOCK:
            _ACCENT_BY_TEAM[key] = accent
    return accent


def _draw_team_gradient(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    accent: tuple[int, int, int] | None,
    *,
    strong_at_left: bool,
) -> None:
    if accent is None:
        return
    panel = (17, 27, 41)
    left, top, right, bottom = box
    span = max(1, right - left)
    for x in range(left, right + 1):
        progress = (x - left) / span
        edge_strength = (1.0 - progress) if strong_at_left else progress
        # Keep it subtle in the dark UI: roughly 36% team color at the outside
        # edge, fading all the way back to the stock panel near the score.
        fill = _blend_color(panel, accent, 0.36 * edge_strength)
        draw.line((x, top, x, bottom), fill=fill)


def _paste_team_logo(image: Image.Image, logo: Image.Image | None, box: tuple[int, int, int, int]) -> bool:
    if logo is None:
        return False
    left, top, right, bottom = box
    max_width = max(1, right - left)
    max_height = max(1, bottom - top)
    rendered = logo.copy()
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS
    rendered.thumbnail((max_width, max_height), resample)
    x = left + (max_width - rendered.width) // 2
    y = top + (max_height - rendered.height) // 2
    image.paste(rendered, (x, y), rendered)
    return True


def _draw_team_headers(image: Image.Image, draw: ImageDraw.ImageDraw, live_stats, state: dict, width: int) -> None:
    panel = (17, 27, 41)
    muted = (112, 132, 158)
    away = state.get("away") if isinstance(state.get("away"), dict) else {}
    home = state.get("home") if isinstance(state.get("home"), dict) else {}

    away_logo = _cached_team_logo(away)
    home_logo = _cached_team_logo(home)

    # Clear only the team-identity portions of the top panel, then lay a soft
    # team-color wash behind each logo/record/GB. The gradient stops before the
    # score/status zone so the center remains the original dark scoreboard.
    away_box = (48, 38, 350, 151)
    home_box = (930, 38, width - 48, 151)
    draw.rectangle(away_box, fill=panel)
    draw.rectangle(home_box, fill=panel)
    _draw_team_gradient(draw, away_box, _team_accent(away, away_logo), strong_at_left=True)
    _draw_team_gradient(draw, home_box, _team_accent(home, home_logo), strong_at_left=False)

    away_has_logo = _paste_team_logo(image, away_logo, (56, 43, 114, 101))
    away_x = 130 if away_has_logo else 60
    away_label = str(away.get("abbr") or away.get("name") or "AWAY")
    live_stats._text(draw, (away_x, 46), away_label, size=38, bold=True)
    live_stats._text(draw, (away_x, 92), away.get("record"), size=20, fill=muted)
    away_gb = str(away.get("games_back") or "").strip()
    if away_gb:
        live_stats._text(draw, (away_x, 121), f"GB {away_gb}", size=16, fill=muted)

    home_has_logo = _paste_team_logo(image, home_logo, (width - 116, 43, width - 58, 101))
    right_edge = width - 132 if home_has_logo else width - 60
    home_label = str(home.get("abbr") or home.get("name") or "HOME")
    home_font = live_stats._font(38, bold=True)
    home_box_text = draw.textbbox((0, 0), home_label, font=home_font)
    live_stats._text(draw, (right_edge - (home_box_text[2] - home_box_text[0]), 46), home_label, size=38, bold=True)

    record = str(home.get("record", "") or "")
    record_box = draw.textbbox((0, 0), record, font=live_stats._font(20))
    live_stats._text(draw, (right_edge - (record_box[2] - record_box[0]), 92), record, size=20, fill=muted)
    home_gb = str(home.get("games_back") or "").strip()
    if home_gb:
        label = f"GB {home_gb}"
        gb_box = draw.textbbox((0, 0), label, font=live_stats._font(16))
        live_stats._text(draw, (right_edge - (gb_box[2] - gb_box[0]), 121), label, size=16, fill=muted)


def _draw_at_bat_team(draw: ImageDraw.ImageDraw, live_stats, state: dict) -> None:
    panel = (17, 27, 41)
    muted = (144, 160, 180)
    batting_team = _batting_abbr(state)
    label = "AT BAT" if not batting_team else f"AT BAT: {batting_team}"
    # Cover the prototype's shorter header and redraw the richer label without
    # disturbing the surrounding panel border or the base diamond below it.
    draw.rectangle((802, 198, 1045, 232), fill=panel)
    live_stats._text(draw, (808, 205), label, size=20, bold=True, fill=muted)


def _draw_outs_indicator(draw: ImageDraw.ImageDraw, live_stats, state: dict) -> None:
    panel = (17, 27, 41)
    muted = (144, 160, 180)
    active = (79, 140, 255)
    inactive = (34, 47, 65)
    outline = (120, 143, 170)

    # Remove the old numeric "O  n" row, leaving B/S as the only numeric count.
    # Keep clear of the left-most edge of the base diamond at roughly x=932.
    draw.rectangle((800, 334, 920, 382), fill=panel)

    # Treat OUTS + the dots as one horizontal indicator. The label is the same
    # 20px bold size as AT BAT and vertically centered against the two dots.
    center_y = 218
    first_dot_x = 1192
    second_dot_x = 1224
    label_font = live_stats._font(20, bold=True)
    label_box = draw.textbbox((0, 0), "OUTS", font=label_font)
    label_width = label_box[2] - label_box[0]
    label_height = label_box[3] - label_box[1]
    label_right = first_dot_x - 16
    label_x = label_right - label_width
    label_y = center_y - label_height // 2 - label_box[1]
    live_stats._text(
        draw,
        (label_x, label_y),
        "OUTS",
        size=20,
        bold=True,
        fill=muted,
    )

    outs = _out_dot_count(state)
    for index, center_x in enumerate((first_dot_x, second_dot_x)):
        fill = active if index < outs else inactive
        draw.ellipse(
            (center_x - 8, center_y - 8, center_x + 8, center_y + 8),
            fill=fill,
            outline=outline,
            width=2,
        )


def _redraw_team_stats(draw: ImageDraw.ImageDraw, live_stats, state: dict) -> None:
    panel = (17, 27, 41)
    canvas = (8, 14, 24)
    border = (42, 58, 78)
    muted = (144, 160, 180)
    away = state.get("away") if isinstance(state.get("away"), dict) else {}
    home = state.get("home") if isinstance(state.get("home"), dict) else {}

    # The original 18px values were drawn at y=468, so their descenders could
    # survive below the old y=480 cleanup rectangle and even cross the panel's
    # bottom border. Repaint both the lower panel interior and the gap beneath
    # it, then restore the bottom edge before drawing the compact stats rows.
    draw.rectangle((48, 392, 744, 483), fill=panel)
    draw.rectangle((48, 484, 744, 502), fill=canvas)
    draw.line((48, 484, 744, 484), fill=border, width=2)

    live_stats._text(draw, (58, 397), "TEAM STATS", size=18, bold=True, fill=muted)

    stat_rows = [
        ("Hits", live_stats._stat_value(away, "hits"), live_stats._stat_value(home, "hits")),
        ("Walks", live_stats._stat_value(away, "walks", "baseOnBalls"), live_stats._stat_value(home, "walks", "baseOnBalls")),
        ("Strikeouts", live_stats._stat_value(away, "strikeouts", "totalStrikeouts"), live_stats._stat_value(home, "strikeouts", "totalStrikeouts")),
        ("Errors", live_stats._stat_value(away, "errors", fallback="0"), live_stats._stat_value(home, "errors", fallback="0")),
    ]
    x_positions = (190, 326, 462, 598)
    for x, (label, away_value, home_value) in zip(x_positions, stat_rows):
        live_stats._text(draw, (x, 426), label, size=14, fill=muted)
        live_stats._text(draw, (x, 449), f"{away_value}  /  {home_value}", size=17, bold=True)


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
        _draw_team_headers(image, draw, live_stats, state, width)
        _draw_at_bat_team(draw, live_stats, state)
        _draw_outs_indicator(draw, live_stats, state)
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
