from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from media.ffmpeg import terminate

from . import game_alert_demo as demo
from . import generated
from . import live_stats
from . import mlb_live_source
from .alert_stream import render_alert


CHANNEL_NUMBER = 1
STREAM_PATH = "/sports/mlb-score-alerts/1/stream.m3u8"
POLL_SECONDS = 3.0
ALERT_VISIBLE_SECONDS = 8.0

_LOCK = threading.RLock()
_SESSION: "ChannelOneAlertSession | None" = None


@dataclass(frozen=True)
class MlbScoreAlert:
    event_key: str
    game_pk: str
    source_channel: int
    alert: demo.DemoAlert
    detected_monotonic: float = field(default_factory=time.monotonic)


@dataclass
class MlbScoreTracker:
    baselines: dict[str, tuple[int, int]] = field(default_factory=dict)
    event_game_ids: dict[str, str] = field(default_factory=dict)
    pending: deque[MlbScoreAlert] = field(default_factory=deque)
    active: MlbScoreAlert | None = None
    active_until: float = 0.0
    last_error: str = ""
    valid_destinations: set[tuple[str, int]] = field(default_factory=set)
    state_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def _mlb_rows(self, db_path: Path | str) -> list[dict]:
        by_event: dict[str, dict] = {}
        for row in generated.generated_rows(db_path):
            if str(row.get("league_id") or "").strip().lower() != "mlb":
                continue
            if not str(row.get("url") or "").strip():
                continue
            event_key = str(row.get("event_key") or "").strip()
            if not event_key:
                continue
            by_event.setdefault(event_key, row)
        return list(by_event.values())

    @staticmethod
    def _score(entry: object) -> int:
        if not isinstance(entry, dict):
            return 0
        try:
            return int(entry.get("score") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _game_pk(game: dict) -> str:
        value = str(game.get("gamePk") or "").strip()
        return value if value.isdigit() else ""

    @staticmethod
    def _team_from_game(game: dict, side: str) -> demo.DemoTeam:
        teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
        entry = teams.get(side) if isinstance(teams.get(side), dict) else {}
        team = entry.get("team") if isinstance(entry.get("team"), dict) else {}
        name = str(team.get("name") or team.get("teamName") or side.title()).strip()
        abbr = str(team.get("abbreviation") or team.get("fileCode") or "").strip().upper()
        # MLB notifications have real abbreviations/logos. Colors are only a
        # fallback if the remote logo cannot be fetched.
        seed = sum(ord(ch) for ch in name)
        primary = (
            48 + (seed * 17) % 160,
            48 + (seed * 31) % 160,
            48 + (seed * 47) % 160,
        )
        secondary = (
            96 + (seed * 13) % 128,
            96 + (seed * 29) % 128,
            96 + (seed * 43) % 128,
        )
        return demo.DemoTeam("MLB", name or side.title(), abbr, primary, secondary)

    def _resolve_games(self, rows: list[dict]) -> dict[str, dict]:
        anchors: dict[str, datetime] = {}
        for row in rows:
            event_key = str(row.get("event_key") or "").strip()
            if not event_key:
                continue
            anchors[event_key] = mlb_live_source._event_date(row)

        dates = sorted({anchor.date().isoformat() for anchor in anchors.values()})
        games: list[dict] = []
        for value in dates:
            try:
                games.extend(
                    mlb_live_source._schedule_games(
                        datetime.fromisoformat(value).astimezone()
                    )
                )
            except Exception:
                continue

        by_pk = {
            self._game_pk(game): game
            for game in games
            if isinstance(game, dict) and self._game_pk(game)
        }
        resolved: dict[str, dict] = {}
        for row in rows:
            event_key = str(row.get("event_key") or "").strip()
            with self.state_lock:
                cached_pk = self.event_game_ids.get(event_key, "")
            if cached_pk and cached_pk in by_pk:
                resolved[event_key] = by_pk[cached_pk]
                continue

            ranked: list[tuple[int, dict]] = []
            for game in games:
                score = mlb_live_source.event_match_score(row, game)
                if score >= 0:
                    score += mlb_live_source._time_bonus(row, game)
                ranked.append((score, game))
            ranked.sort(key=lambda item: item[0], reverse=True)
            if not ranked or ranked[0][0] < 0:
                continue
            game = ranked[0][1]
            game_pk = self._game_pk(game)
            if not game_pk:
                continue
            with self.state_lock:
                self.event_game_ids[event_key] = game_pk
            resolved[event_key] = game
        return resolved

    def poll(self, db_path: Path | str) -> None:
        rows = self._mlb_rows(db_path)
        rows_by_event = {
            str(row.get("event_key") or "").strip(): row
            for row in rows
        }
        if not rows:
            with self.state_lock:
                self.baselines.clear()
                self.event_game_ids.clear()
                self.pending.clear()
                self.active = None
                self.valid_destinations.clear()
            return

        with self.state_lock:
            self.valid_destinations = {
                (
                    str(row.get("event_key") or "").strip(),
                    int(row.get("assigned_number") or 0),
                )
                for row in rows
            }

        resolved = self._resolve_games(rows)
        current_game_ids: set[str] = set()

        for event_key, row in rows_by_event.items():
            game = resolved.get(event_key)
            if not game:
                continue
            game_pk = self._game_pk(game)
            if not game_pk:
                continue
            current_game_ids.add(game_pk)

            teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
            away_entry = teams.get("away") if isinstance(teams.get("away"), dict) else {}
            home_entry = teams.get("home") if isinstance(teams.get("home"), dict) else {}
            away_score = self._score(away_entry)
            home_score = self._score(home_entry)
            with self.state_lock:
                previous = self.baselines.get(game_pk)
                self.baselines[game_pk] = (away_score, home_score)

            # Baseline on first sight so starting the stream in the middle of a
            # 7-5 game never dumps historical scoring alerts on the viewer.
            if previous is None:
                continue

            away_delta = away_score - previous[0]
            home_delta = home_score - previous[1]
            if away_delta <= 0 and home_delta <= 0:
                continue

            away = self._team_from_game(game, "away")
            home = self._team_from_game(game, "home")
            scoring_team = away if away_delta > 0 else home
            play = f"{scoring_team.name} scored"
            try:
                state = mlb_live_source.fetch_live_state(game_pk)
                latest = str(state.get("last_play") or "").strip()
                if latest:
                    play = latest
            except Exception as exc:
                with self.state_lock:
                    self.last_error = str(exc)

            source_channel = int(row.get("assigned_number") or 0)
            if source_channel <= 0:
                continue
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
            with self.state_lock:
                self.pending.append(
                    MlbScoreAlert(
                        event_key=event_key,
                        game_pk=game_pk,
                        source_channel=source_channel,
                        alert=alert,
                    )
                )

        # Forget finished/removed games after Sports Automation removes them.
        active_events = set(rows_by_event)
        with self.state_lock:
            for game_pk in list(self.baselines):
                if game_pk not in current_game_ids:
                    self.baselines.pop(game_pk, None)
            for event_key in list(self.event_game_ids):
                if event_key not in active_events:
                    self.event_game_ids.pop(event_key, None)

    def current(self, db_path: Path | str) -> demo.DemoAlert | None:
        del db_path  # validity is refreshed by the polling thread
        now = time.monotonic()
        with self.state_lock:
            valid = set(self.valid_destinations)
            if self.active is not None and now >= self.active_until:
                self.active = None

            if self.active is not None:
                key = (self.active.event_key, self.active.source_channel)
                if key in valid:
                    return self.active.alert
                self.active = None

            while self.pending:
                candidate = self.pending.popleft()
                if (candidate.event_key, candidate.source_channel) not in valid:
                    continue
                self.active = candidate
                self.active_until = now + ALERT_VISIBLE_SECONDS
                return candidate.alert
        return None

    def state_payload(self, db_path: Path | str) -> dict:
        current = self.current(db_path)
        with self.state_lock:
            queued = len(self.pending)
            tracked = len(self.baselines)
            last_error = self.last_error
        return {
            "channel_number": CHANNEL_NUMBER,
            "mode": "all-mlb-scores",
            "active_alert": demo._alert_payload(current),
            "queued_alerts": queued,
            "tracked_games": tracked,
            "last_error": last_error,
        }


@dataclass
class ChannelOneAlertSession:
    directory: Path
    parent_url: str
    tracker: MlbScoreTracker = field(default_factory=MlbScoreTracker)
    process: subprocess.Popen | None = None
    thread: threading.Thread | None = None
    poll_thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    last_access_monotonic: float = field(default_factory=time.monotonic)
    last_error: str = ""


def _directory() -> Path:
    return Path(live_stats.STATS_ROOT) / "mlb-score-alerts-channel-1"


def _poll_loop(session: ChannelOneAlertSession, db_path: Path | str) -> None:
    while not session.stop_event.is_set():
        process = session.process
        if process is None or process.poll() is not None:
            return
        if (
            time.monotonic() - session.last_access_monotonic
            > float(live_stats.IDLE_SECONDS)
        ):
            return
        try:
            session.tracker.poll(db_path)
        except Exception as exc:
            session.last_error = str(exc)
            with session.tracker.state_lock:
                session.tracker.last_error = str(exc)
        session.stop_event.wait(POLL_SECONDS)


def _run(session: ChannelOneAlertSession, db_path: Path | str) -> None:
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

            alert = session.tracker.current(db_path)
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
            global _SESSION
            if _SESSION is session:
                _SESSION = None
        shutil.rmtree(session.directory, ignore_errors=True)


def start_session(db_path: Path | str) -> ChannelOneAlertSession:
    global _SESSION
    target = demo.parent_target()
    if not target:
        raise RuntimeError("Channel 1 is not available for MLB scoring alerts.")

    with _LOCK:
        current = _SESSION
        if (
            current is not None
            and current.process is not None
            and current.process.poll() is None
            and current.parent_url == target
        ):
            current.last_access_monotonic = time.monotonic()
            return current

    if current is not None:
        stop_session()

    directory = _directory()
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
        raise RuntimeError(f"Could not start channel 1 alert wrapper: {exc}") from exc

    session = ChannelOneAlertSession(
        directory=directory,
        parent_url=target,
        process=process,
    )
    session.thread = threading.Thread(
        target=_run,
        args=(session, db_path),
        name="mlb-score-alerts-channel-1-render",
        daemon=True,
    )
    session.poll_thread = threading.Thread(
        target=_poll_loop,
        args=(session, db_path),
        name="mlb-score-alerts-channel-1-poll",
        daemon=True,
    )
    with _LOCK:
        _SESSION = session
    session.thread.start()
    session.poll_thread.start()

    playlist = directory / "stream.m3u8"
    deadline = time.monotonic() + demo.STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            error = session.last_error
            stop_session()
            raise RuntimeError(
                error or "Channel 1 alert wrapper stopped before the stream became ready."
            )
        try:
            text = playlist.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "#EXTINF:" in text:
            session.last_access_monotonic = time.monotonic()
            return session
        time.sleep(0.1)

    stop_session()
    raise RuntimeError("Channel 1 alert wrapper did not become ready in time.")


def stop_session() -> bool:
    global _SESSION
    with _LOCK:
        session = _SESSION
        _SESSION = None
        if session is not None:
            session.stop_event.set()
    if session is None:
        return False
    if session.process is not None:
        terminate(session.process)
    shutil.rmtree(session.directory, ignore_errors=True)
    return True


def get_session() -> ChannelOneAlertSession | None:
    with _LOCK:
        session = _SESSION
    if session is None or session.process is None or session.process.poll() is not None:
        return None
    session.last_access_monotonic = time.monotonic()
    return session


def safe_media_file(db_path: Path | str, filename: str) -> Path | None:
    if not re.fullmatch(r"(?:stream\.m3u8|segment_\d{6}\.ts)", str(filename or "")):
        return None
    session = get_session()
    if session is None and filename == "stream.m3u8":
        session = start_session(db_path)
    if session is None:
        return None
    path = session.directory / filename
    return path if path.exists() else None


def state_payload(db_path: Path | str) -> dict:
    session = get_session()
    if session is None:
        return {
            "channel_number": CHANNEL_NUMBER,
            "mode": "all-mlb-scores",
            "active": False,
            "active_alert": None,
            "queued_alerts": 0,
            "tracked_games": 0,
            "last_error": "",
        }
    payload = session.tracker.state_payload(db_path)
    payload["active"] = True
    if session.last_error:
        payload["last_error"] = session.last_error
    return payload


def route_channel_one(text: str, base_url: str) -> str:
    """Point served channel 1 at the temporary MLB-scoring alert wrapper."""
    lines = str(text or "").splitlines()
    target = f"{base_url.rstrip('/')}{STREAM_PATH}"
    for index, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        if not re.search(r'\btvg-chno="1"', line):
            continue
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].startswith("#"):
            cursor += 1
        if cursor < len(lines):
            lines[cursor] = target
        break
    return "\n".join(lines) + ("\n" if lines else "")
