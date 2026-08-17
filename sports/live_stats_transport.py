from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from PIL import Image, ImageDraw

from . import mlb_live_source
from . import mlb_stats_carousel
from . import mlb_stats_companions


SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary"
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"

_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.espn.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
}

_ESPN_FALLBACK_BY_MLB_GAME: dict[str, str] = {}


def _json(url: str, *, timeout: float = 8.0) -> dict:
    request = urllib.request.Request(url, headers=_BROWSER_HEADERS, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    data = json.loads(payload.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise RuntimeError("ESPN returned an unexpected response.")
    return data


def _scoreboard_event(event_id: str) -> dict:
    # The no-date scoreboard is the fastest path for a currently live event.
    # If it is not present there, also check yesterday/today/tomorrow to survive
    # midnight and timezone boundaries while a late game is still in progress.
    now = datetime.now().astimezone()
    dates = ["", now.strftime("%Y%m%d")]
    try:
        from datetime import timedelta
        dates.extend([
            (now - timedelta(days=1)).strftime("%Y%m%d"),
            (now + timedelta(days=1)).strftime("%Y%m%d"),
        ])
    except Exception:
        pass

    seen: set[str] = set()
    for date in dates:
        if date in seen:
            continue
        seen.add(date)
        query = {"limit": "100"}
        if date:
            query["dates"] = date
        data = _json(f"{SCOREBOARD_URL}?{urllib.parse.urlencode(query)}")
        events = data.get("events") if isinstance(data.get("events"), list) else []
        for event in events:
            if isinstance(event, dict) and str(event.get("id", "") or "") == str(event_id):
                return event
    raise RuntimeError(f"ESPN scoreboard no longer contains event {event_id}.")


def _scoreboard_state(live_stats, event_id: str, event: dict, *, mlb_game_pk: str) -> dict:
    competitions = event.get("competitions") if isinstance(event.get("competitions"), list) else []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    competitors = live_stats._competitor_map(competition)
    away = live_stats._team_payload(competitors.get("away", {}))
    home = live_stats._team_payload(competitors.get("home", {}))

    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    return {
        "espn_event_id": str(event_id),
        "source_event_id": str(mlb_game_pk),
        "mlb_game_pk": str(mlb_game_pk),
        "away": away,
        "home": home,
        "status": str(
            status_type.get("shortDetail", "")
            or status_type.get("detail", "")
            or status.get("displayClock", "")
            or "MLB"
        ),
        "state": str(status_type.get("state", "") or ""),
        "period": int(status.get("period", 0) or 0),
        "clock": str(status.get("displayClock", "") or ""),
        # The scoreboard endpoint does not always expose pitch/base situation.
        # Keep those fields stable so the renderer continues to run rather than
        # failing the entire synthetic channel when detailed feeds are blocked.
        "balls": 0,
        "strikes": 0,
        "outs": 0,
        "on_first": False,
        "on_second": False,
        "on_third": False,
        "batter": "",
        "pitcher": "",
        "last_play": "Live ESPN scoreboard fallback (detailed MLB feed unavailable)",
        "pitch": {},
        "batted_ball": {},
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_source": "espn-scoreboard-fallback",
        "data_source_label": "ESPN fallback",
    }


def _resolve_espn_fallback(live_stats, row: dict) -> str:
    query = urllib.parse.urlencode({"dates": live_stats._event_date(row), "limit": "100"})
    data = _json(f"{SCOREBOARD_URL}?{query}")
    events = data.get("events") if isinstance(data.get("events"), list) else []
    ranked = sorted(
        (
            (live_stats._event_match_score(row, event), event)
            for event in events
            if isinstance(event, dict)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 0:
        return ""
    return str(ranked[0][1].get("id", "") or "").strip()


def _espn_state(live_stats, mlb_game_pk: str, espn_event_id: str) -> dict:
    query = urllib.parse.urlencode({"event": str(espn_event_id)})
    try:
        summary = _json(f"{SUMMARY_URL}?{query}")
        state = live_stats.normalize_mlb_summary(summary, espn_event_id=str(espn_event_id))
        state["source_event_id"] = str(mlb_game_pk)
        state["mlb_game_pk"] = str(mlb_game_pk)
        state["data_source"] = "espn-summary-fallback"
        state["data_source_label"] = "ESPN fallback"
        return state
    except urllib.error.HTTPError as exc:
        if exc.code not in {403, 429}:
            raise
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        pass

    event = _scoreboard_event(str(espn_event_id))
    return _scoreboard_state(
        live_stats,
        str(espn_event_id),
        event,
        mlb_game_pk=str(mlb_game_pk),
    )


def _escape_m3u(value: object) -> str:
    return str(value or "").replace('"', "'")


def install(live_stats) -> None:
    """Install MLB StatsAPI primary source plus ESPN fallback and companions."""
    if getattr(live_stats, "_resilient_transport_installed", False):
        return

    def resolve_event(row: dict) -> tuple[str, dict]:
        game_pk, schedule_game = mlb_live_source.resolve_game(row)
        try:
            espn_event_id = _resolve_espn_fallback(live_stats, row)
        except Exception:
            espn_event_id = ""
        if espn_event_id:
            _ESPN_FALLBACK_BY_MLB_GAME[str(game_pk)] = espn_event_id
        return str(game_pk), schedule_game

    def fetch_mlb_state(source_event_id: str) -> dict:
        game_pk = str(source_event_id or "").strip()
        try:
            return mlb_live_source.fetch_live_state(game_pk)
        except Exception as mlb_exc:
            espn_event_id = _ESPN_FALLBACK_BY_MLB_GAME.get(game_pk, "")
            if espn_event_id:
                try:
                    state = _espn_state(live_stats, game_pk, espn_event_id)
                    state["fallback_reason"] = str(mlb_exc)
                    return state
                except Exception as espn_exc:
                    raise RuntimeError(
                        f"MLB StatsAPI failed ({mlb_exc}); ESPN fallback also failed ({espn_exc})."
                    ) from mlb_exc
            raise RuntimeError(f"MLB StatsAPI failed: {mlb_exc}") from mlb_exc

    def mlb_stats_rows(db_path) -> list[dict]:
        # Home/away/national feeds may all represent one logical game. Only the
        # lowest-numbered generated feed owns the decimal .1 stats companion.
        return mlb_stats_companions.primary_mlb_rows(live_stats._s.generated_rows(db_path))

    def inject_stats_channels(text: str, db_path, base_url: str) -> str:
        rows = {
            int(row.get("assigned_number") or 0): row
            for row in mlb_stats_rows(db_path)
        }
        if not rows:
            return text

        carousel_lines = mlb_stats_carousel.m3u_lines(base_url)
        output: list[str] = []
        carousel_inserted = False
        for line in str(text or "").splitlines():
            output.append(line)
            if not carousel_inserted and line.strip().startswith("#EXTM3U"):
                output.extend(carousel_lines)
                carousel_inserted = True

            match = re.search(r"/sports/stream/(\d+)(?:\?.*)?$", line.strip())
            if not match:
                continue
            number = int(match.group(1))
            row = rows.get(number)
            if row is None:
                continue

            display_name = mlb_stats_companions.stats_title(row)
            attrs = [
                f'tvg-id="{_escape_m3u(mlb_stats_companions.stats_tvg_id(row))}"',
                f'tvg-chno="{mlb_stats_companions.stats_number(row)}"',
                f'tvg-name="{_escape_m3u(display_name)}"',
                f'group-title="{_escape_m3u(row.get("group_title") or "Sports Today")}"',
                'x-sports-stats="mlb"',
                f'x-sports-parent="{number}"',
                f'x-sports-event="{_escape_m3u(mlb_stats_companions.logical_event_key(row))}"',
            ]
            logo = str(row.get("tvg_logo", "") or "").strip()
            if logo:
                attrs.append(f'tvg-logo="{_escape_m3u(logo)}"')
            output.append(f"#EXTINF:-1 {' '.join(attrs)},{display_name}")
            output.append(
                f"{base_url.rstrip('/')}{mlb_stats_companions.stats_stream_path(row)}"
            )

        if not carousel_inserted:
            output = [*carousel_lines, *output]
        return "\n".join(output) + "\n"

    # Keep the legacy renderer body for now, but make its footer source-agnostic.
    # 1.2 covers this footer with its SIMULATED label after rendering.
    original_render = live_stats.render_mlb_frame

    def render_mlb_frame(state: dict, *, width: int = 1280, height: int = 720) -> bytes:
        payload = original_render(state, width=width, height=height)
        image = Image.open(io.BytesIO(payload)).convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, height - 52, width, height), fill=(8, 14, 24))
        source_label = str(state.get("data_source_label") or "Live baseball data")
        event_id = str(
            state.get("source_event_id")
            or state.get("mlb_game_pk")
            or state.get("espn_event_id")
            or ""
        )
        updated = str(state.get("updated_at") or "")
        updated_clock = updated[11:19] if len(updated) >= 19 else updated
        footer = f"{source_label} • game {event_id} • updated {updated_clock}"
        draw.text((42, height - 38), footer, font=live_stats._font(15), fill=(144, 160, 180))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()

    def state_payload(db_path, assigned_number: int) -> dict:
        number = int(assigned_number)
        row = live_stats._generated_mlb_row(db_path, number)
        if row is None:
            raise RuntimeError("MLB stats channel was not found.")
        session = live_stats.get_session(number)
        if session is not None and session.last_state:
            state = session.last_state
            game_pk = str(session.espn_event_id or "")
            active = True
            error = session.last_error
        else:
            game_pk, _game = resolve_event(row)
            state = fetch_mlb_state(game_pk)
            active = False
            error = ""
        return {
            "assigned_number": number,
            "stats_number": f"{number}.1",
            "event_key": str(row.get("event_key", "") or ""),
            "source_event_id": game_pk,
            "mlb_game_pk": game_pk,
            "espn_event_id": _ESPN_FALLBACK_BY_MLB_GAME.get(game_pk, ""),
            "data_source": str(state.get("data_source") or ""),
            "active": active,
            "error": error,
            "state": state,
        }

    # live_stats still uses these historic function/field names internally.
    # Patch their behavior now; a later multi-sport refactor can rename them to
    # generic source_event_id / fetch_state without touching the renderer/HLS path.
    live_stats.resolve_espn_event = resolve_event
    live_stats.fetch_mlb_state = fetch_mlb_state
    live_stats.mlb_stats_rows = mlb_stats_rows
    live_stats.inject_stats_channels = inject_stats_channels
    live_stats.render_mlb_frame = render_mlb_frame
    live_stats.state_payload = state_payload

    # The original prototype used x264's still-image tune and waited exactly 14
    # seconds for two HLS segments. At 2 fps that could land on the same boundary
    # as the timeout. Keep the lightweight encoder but make startup deterministic.
    original_ffmpeg_command = live_stats._ffmpeg_command

    def low_latency_ffmpeg_command(directory):
        command = list(original_ffmpeg_command(directory))
        try:
            tune_index = command.index("-tune")
            if tune_index + 1 < len(command):
                command[tune_index + 1] = "zerolatency"
        except ValueError:
            pass
        return command

    live_stats._ffmpeg_command = low_latency_ffmpeg_command
    live_stats.STARTUP_TIMEOUT = max(float(getattr(live_stats, "STARTUP_TIMEOUT", 14.0)), 24.0)
    live_stats._resilient_transport_installed = True
