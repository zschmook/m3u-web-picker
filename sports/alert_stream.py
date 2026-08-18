from __future__ import annotations

import hashlib
import io
import random
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

from media.ffmpeg import terminate

from . import game_alert_demo as demo
from . import generated
from . import live_stats


_LOCK = threading.RLock()
_SESSIONS: dict[int, "AlertSession"] = {}


@dataclass(frozen=True)
class RoutedAlert:
    alert: demo.DemoAlert
    source_event_key: str


@dataclass
class AlertSession:
    directory: Path
    parent_url: str
    watched_number: int
    watched_event_key: str
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_monotonic: float = field(default_factory=time.monotonic)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_error: str = ""
    alert_slot: int = -1
    routed_alert: RoutedAlert | None = None


_PLAYERS = (
    "Jordan Vega",
    "Marcus Reed",
    "Tyler Brooks",
    "Alex Ramirez",
    "Chris Daniels",
    "Devin Carter",
    "Ryan Knox",
    "Malik Hayes",
)


def _seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _team(name: str, league: str) -> demo.DemoTeam:
    cleaned = str(name or "").strip() or "Team"
    digest = hashlib.sha256(cleaned.encode("utf-8")).digest()
    primary = tuple(48 + (value % 160) for value in digest[:3])
    secondary = tuple(96 + (value % 128) for value in digest[3:6])
    return demo.DemoTeam(league, cleaned, "", primary, secondary)


def _split_matchup(title: str) -> tuple[str, str]:
    value = re.sub(r"\s+", " ", str(title or "").strip())
    if not value:
        return "Away", "Home"
    for pattern in (
        r"\s+@\s+",
        r"\s+at\s+",
        r"\s+vs\.?\s+",
        r"\s+versus\s+",
        r"\s+–\s+",
        r"\s+—\s+",
    ):
        parts = re.split(pattern, value, maxsplit=1, flags=re.I)
        if len(parts) == 2 and all(part.strip() for part in parts):
            return parts[0].strip(), parts[1].strip()
    return value, "Opponent"


def _league_label(row: dict) -> str:
    raw = str(row.get("league_id") or "").strip()
    lower = raw.lower()
    if "mlb" in lower or "baseball" in lower:
        return "MLB"
    if "nfl" in lower:
        return "NFL"
    if "ncaa" in lower and ("football" in lower or "ncaaf" in lower):
        return "NCAA"
    if "nba" in lower:
        return "NBA"
    if "nhl" in lower:
        return "NHL"
    return raw.upper()[:10] if raw else "SPORTS"


def _fake_score(
    rng: random.Random,
    league: str,
    scoring_is_away: bool,
) -> tuple[int, int, str]:
    player = rng.choice(_PLAYERS)
    if league == "MLB":
        away = rng.randint(0, 8)
        home = rng.randint(0, 8)
        runs = rng.choice((1, 1, 1, 2, 2, 3))
        if scoring_is_away:
            away = max(away, home - 2) + runs
        else:
            home = max(home, away - 2) + runs
        play = rng.choice((
            f"{player} — solo home run",
            f"{player} — 2-run home run",
            f"{player} — RBI double",
            f"{player} — 2-run single",
        ))
        return away, home, play

    if league in {"NFL", "NCAA"}:
        away = rng.randint(3, 35)
        home = rng.randint(3, 35)
        if scoring_is_away:
            away += 6
        else:
            home += 6
        play = rng.choice((
            f"{player} — {rng.choice((4, 8, 12, 18, 27, 41))}-yard TD reception",
            f"{player} — {rng.choice((1, 3, 7, 14, 22))}-yard rushing TD",
            f"{player} — pick-six",
        ))
        return away, home, play

    if league == "NBA":
        away = rng.randint(78, 118)
        home = rng.randint(78, 118)
        points = rng.choice((2, 2, 3))
        if scoring_is_away:
            away += points
        else:
            home += points
        return away, home, rng.choice((
            f"{player} — 3-pointer",
            f"{player} — driving layup",
            f"{player} — dunk",
        ))

    if league == "NHL":
        away = rng.randint(0, 5)
        home = rng.randint(0, 5)
        if scoring_is_away:
            away += 1
        else:
            home += 1
        return away, home, f"{player} — goal"

    away = rng.randint(0, 10)
    home = rng.randint(0, 10)
    if scoring_is_away:
        away += 1
    else:
        home += 1
    return away, home, "Scoring play"


def _snapshot(db_path: Path | str) -> list[dict]:
    return [
        row
        for row in generated.generated_rows(db_path)
        if str(row.get("url") or "").strip()
    ]


def _watched_row(rows: list[dict], assigned_number: int) -> dict | None:
    number = int(assigned_number)
    return next(
        (
            row
            for row in rows
            if int(row.get("assigned_number", -1)) == number
        ),
        None,
    )


def _candidate_rows(rows: list[dict], watched_event_key: str) -> list[dict]:
    # One destination per logical event. generated_rows() is ordered by channel
    # number, so the first feed for an event is also the preferred alert target.
    by_event: dict[str, dict] = {}
    for row in rows:
        event_key = str(row.get("event_key") or "").strip()
        if not event_key or event_key == watched_event_key:
            continue
        by_event.setdefault(event_key, row)
    return list(by_event.values())


def fake_alert_for_slot(
    db_path: Path | str,
    watched_number: int,
    watched_event_key: str,
    slot: int,
) -> RoutedAlert | None:
    rows = _snapshot(db_path)
    watched = _watched_row(rows, watched_number)
    if watched is None or str(watched.get("event_key") or "") != watched_event_key:
        return None

    candidates = _candidate_rows(rows, watched_event_key)
    if not candidates:
        return None

    rng = random.Random(_seed(watched_number, watched_event_key, slot))
    source = rng.choice(candidates)
    league = _league_label(source)
    away_name, home_name = _split_matchup(
        str(source.get("event_title") or source.get("display_name") or "")
    )
    away = _team(away_name, league)
    home = _team(home_name, league)
    scoring_is_away = bool(rng.getrandbits(1))
    away_score, home_score, play = _fake_score(rng, league, scoring_is_away)
    alert = demo.DemoAlert(
        league=league,
        scoring_team=away if scoring_is_away else home,
        away=away,
        home=home,
        away_score=away_score,
        home_score=home_score,
        play=play,
        source_channel=str(source["assigned_number"]),
    )
    return RoutedAlert(alert=alert, source_event_key=str(source["event_key"]))


def _routed_alert_valid(
    rows: list[dict],
    session: AlertSession,
    routed: RoutedAlert,
) -> bool:
    watched = _watched_row(rows, session.watched_number)
    if watched is None or str(watched.get("event_key") or "") != session.watched_event_key:
        return False
    return any(
        str(row.get("assigned_number")) == routed.alert.source_channel
        and str(row.get("event_key") or "") == routed.source_event_key
        and routed.source_event_key != session.watched_event_key
        and str(row.get("url") or "").strip()
        for row in rows
    )


def _score_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    alert: demo.DemoAlert,
) -> None:
    y = 120
    image.alpha_composite(demo._fallback_team_icon(alert.away), (95, y - 20))
    image.alpha_composite(
        demo._fallback_team_icon(alert.home),
        (demo.FRAME_WIDTH - 95 - demo.LOGO_SIZE, y - 20),
    )

    score_font = demo._font(48, bold=True)
    separator_font = demo._font(32, bold=True)
    away_score = str(alert.away_score)
    home_score = str(alert.home_score)
    away_box = draw.textbbox((0, 0), away_score, font=score_font)
    away_width = away_box[2] - away_box[0]
    center = demo.FRAME_WIDTH // 2

    draw.text(
        (center - 58 - away_width, y - 8),
        away_score,
        font=score_font,
        fill=(255, 255, 255, 255),
    )
    draw.text(
        (center - 9, y + 2),
        "–",
        font=separator_font,
        fill=(151, 163, 181, 255),
    )
    draw.text(
        (center + 58, y - 8),
        home_score,
        font=score_font,
        fill=(255, 255, 255, 255),
    )

    channel_text = f"On channel: {alert.source_channel}"
    channel_font = demo._font(17, bold=True)
    channel_box = draw.textbbox((0, 0), channel_text, font=channel_font)
    channel_width = channel_box[2] - channel_box[0]
    draw.text(
        ((demo.FRAME_WIDTH - channel_width) // 2, 183),
        channel_text,
        font=channel_font,
        fill=(171, 184, 201, 255),
    )


def render_alert(alert: demo.DemoAlert | None) -> bytes:
    image = Image.new(
        "RGBA",
        (demo.FRAME_WIDTH, demo.FRAME_HEIGHT),
        (0, 0, 0, 0),
    )
    if alert is not None:
        draw = ImageDraw.Draw(image, "RGBA")
        draw.rounded_rectangle(
            (10, 10, demo.FRAME_WIDTH - 10, demo.FRAME_HEIGHT - 10),
            radius=22,
            fill=(8, 14, 24, 226),
            outline=(255, 255, 255, 72),
            width=2,
        )
        pill = (24, 22, 137, 50)
        draw.rounded_rectangle(pill, radius=10, fill=(199, 32, 45, 245))
        league_label = f"{alert.league} SCORE"
        league_font = demo._font(15, bold=True)
        league_box = draw.textbbox((0, 0), league_label, font=league_font)
        league_width = league_box[2] - league_box[0]
        league_height = league_box[3] - league_box[1]
        league_x = pill[0] + ((pill[2] - pill[0]) - league_width) // 2 - league_box[0]
        league_y = pill[1] + ((pill[3] - pill[1]) - league_height) // 2 - league_box[1]
        draw.text(
            (league_x, league_y),
            league_label,
            font=league_font,
            fill=(255, 255, 255, 255),
        )

        headline = f"{alert.scoring_team.name} scored"
        headline_font = demo._fit_text(
            draw,
            headline,
            max_width=demo.FRAME_WIDTH - 185,
            start_size=26,
            minimum=18,
        )
        draw.text(
            (151, 22),
            headline,
            font=headline_font,
            fill=(255, 255, 255, 255),
        )

        play_font = demo._fit_text(
            draw,
            alert.play,
            max_width=demo.FRAME_WIDTH - 64,
            start_size=22,
            minimum=16,
        )
        draw.text(
            (32, 67),
            alert.play,
            font=play_font,
            fill=(210, 219, 231, 255),
        )
        _score_row(image, draw, alert)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _directory(assigned_number: int) -> Path:
    return Path(live_stats.STATS_ROOT) / f"sports-alert-{int(assigned_number)}"


def _stop_locked(assigned_number: int) -> AlertSession | None:
    session = _SESSIONS.pop(int(assigned_number), None)
    if session is not None:
        session.stop_event.set()
    return session


def stop_session(assigned_number: int) -> bool:
    with _LOCK:
        session = _stop_locked(assigned_number)
    if session is None:
        return False
    if session.process is not None:
        terminate(session.process)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def _run(session: AlertSession, db_path: Path | str) -> None:
    process = session.process
    if process is None or process.stdin is None:
        return

    frame_period = 1.0 / demo.FRAME_RATE
    try:
        while not session.stop_event.is_set():
            if process.poll() is not None:
                break
            if (
                time.monotonic() - session.last_access_monotonic
                > float(live_stats.IDLE_SECONDS)
            ):
                break

            elapsed = time.monotonic() - session.started_monotonic
            slot = int(max(0.0, elapsed) // demo.ALERT_SLOT_SECONDS)
            within = max(0.0, elapsed) % demo.ALERT_SLOT_SECONDS
            if slot != session.alert_slot:
                session.alert_slot = slot
                session.routed_alert = fake_alert_for_slot(
                    db_path,
                    session.watched_number,
                    session.watched_event_key,
                    slot,
                )

            rows = _snapshot(db_path)
            watched = _watched_row(rows, session.watched_number)
            if (
                watched is None
                or str(watched.get("event_key") or "") != session.watched_event_key
            ):
                break

            routed = session.routed_alert
            alert = None
            if (
                within < demo.ALERT_VISIBLE_SECONDS
                and routed is not None
                and _routed_alert_valid(rows, session, routed)
            ):
                alert = routed.alert

            try:
                process.stdin.write(render_alert(alert))
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                session.last_error = str(exc)
                break
            session.stop_event.wait(frame_period)
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        terminate(process)
        with _LOCK:
            if _SESSIONS.get(session.watched_number) is session:
                _SESSIONS.pop(session.watched_number, None)
        shutil.rmtree(session.directory, ignore_errors=True)


def start_session(db_path: Path | str, assigned_number: int) -> AlertSession:
    number = int(assigned_number)
    rows = _snapshot(db_path)
    watched = _watched_row(rows, number)
    if watched is None:
        raise RuntimeError("Sports channel is no longer available.")

    target = str(watched.get("url") or "").strip()
    event_key = str(watched.get("event_key") or "").strip()
    if not target:
        raise RuntimeError("Sports channel has no playable provider target.")
    if not event_key:
        raise RuntimeError("Sports channel has no logical event identity.")

    with _LOCK:
        current = _SESSIONS.get(number)
        if (
            current is not None
            and current.process is not None
            and current.process.poll() is None
            and current.parent_url == target
            and current.watched_event_key == event_key
        ):
            current.last_access_monotonic = time.monotonic()
            return current

    if current is not None:
        stop_session(number)

    directory = _directory(number)
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            demo._ffmpeg_command(target, directory),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise RuntimeError(f"Could not start sports alert ffmpeg: {exc}") from exc

    session = AlertSession(
        directory=directory,
        parent_url=target,
        watched_number=number,
        watched_event_key=event_key,
        process=process,
    )
    session.thread = threading.Thread(
        target=_run,
        args=(session, db_path),
        name=f"sports-alert-{number}",
        daemon=True,
    )
    with _LOCK:
        _SESSIONS[number] = session
    session.thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + demo.STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            error = session.last_error
            stop_session(number)
            raise RuntimeError(
                error or "Sports alert ffmpeg stopped before the stream became ready."
            )
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTINF:" in text:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.1)

    stop_session(number)
    raise RuntimeError("Sports alert stream did not become ready in time.")


def get_session(assigned_number: int) -> AlertSession | None:
    number = int(assigned_number)
    with _LOCK:
        session = _SESSIONS.get(number)
    if (
        session is None
        or session.process is None
        or session.process.poll() is not None
    ):
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def safe_media_file(
    db_path: Path | str,
    assigned_number: int,
    filename: str,
) -> Path | None:
    if not re.fullmatch(
        r"(?:stream\.m3u8|segment_\d{6}\.ts)",
        str(filename or ""),
    ):
        return None

    session = get_session(assigned_number)
    if session is None and filename == "stream.m3u8":
        session = start_session(db_path, assigned_number)
    if session is None:
        return None

    path = session.directory / filename
    return path if path.exists() else None
