from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

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


def _scoreboard_state(live_stats, event_id: str, event: dict) -> dict:
    competitions = event.get("competitions") if isinstance(event.get("competitions"), list) else []
    competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
    competitors = live_stats._competitor_map(competition)
    away = live_stats._team_payload(competitors.get("away", {}))
    home = live_stats._team_payload(competitors.get("home", {}))

    status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
    status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
    return {
        "espn_event_id": str(event_id),
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
        # failing the entire synthetic channel when ESPN blocks summary data.
        "balls": 0,
        "strikes": 0,
        "outs": 0,
        "on_first": False,
        "on_second": False,
        "on_third": False,
        "batter": "",
        "pitcher": "",
        "last_play": "Live ESPN scoreboard data (detailed Gamecast feed unavailable)",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_source": "scoreboard-fallback",
    }


def _escape_m3u(value: object) -> str:
    return str(value or "").replace('"', "'")


def install(live_stats) -> None:
    """Install resilient ESPN transport plus event-driven MLB companions."""
    if getattr(live_stats, "_resilient_transport_installed", False):
        return

    def fetch_mlb_state(event_id: str) -> dict:
        query = urllib.parse.urlencode({"event": str(event_id)})
        try:
            summary = _json(f"{SUMMARY_URL}?{query}")
            state = live_stats.normalize_mlb_summary(summary, espn_event_id=str(event_id))
            state["data_source"] = "summary"
            return state
        except urllib.error.HTTPError as exc:
            if exc.code not in {403, 429}:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass

        event = _scoreboard_event(str(event_id))
        return _scoreboard_state(live_stats, str(event_id), event)

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

        output: list[str] = []
        for line in str(text or "").splitlines():
            output.append(line)
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
        return "\n".join(output) + "\n"

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

    live_stats.fetch_mlb_state = fetch_mlb_state
    live_stats.mlb_stats_rows = mlb_stats_rows
    live_stats.inject_stats_channels = inject_stats_channels
    live_stats._ffmpeg_command = low_latency_ffmpeg_command
    live_stats.STARTUP_TIMEOUT = max(float(getattr(live_stats, "STARTUP_TIMEOUT", 14.0)), 24.0)
    live_stats._resilient_transport_installed = True
