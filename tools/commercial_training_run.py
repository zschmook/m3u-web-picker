#!/usr/bin/env python3
"""Collect commercial-detector observations from several live channels.

This is an analysis-only runner.  It decodes a low-bandwidth video rendition,
feeds sampled frames through the same LiveLogoDetector used by MPEG-TS
playback, and persists the normal per-channel observations in the app SQLite
database.  It does not create a playback stream or toggle the commercial
overlay.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import commercial_profiles
import core
from media.ffmpeg import executable, terminate
from media.logo_detector import LiveLogoDetector


DEFAULT_DURATION_SECONDS = 3 * 60 * 60
STATUS_INTERVAL_SECONDS = 10.0
RESTART_DELAY_SECONDS = 5.0


@dataclass(frozen=True)
class Feed:
    network: str
    call_sign: str
    name: str
    epg_id: str
    url: str
    video_stream_index: int

    @property
    def identity(self) -> str:
        return f"tvg:{self.epg_id}"


DEFAULT_FEEDS = (
    Feed(
        "ABC",
        "WCVB",
        "ABC WCVB Boston MA",
        "ABCWCVB.us",
        "https://aegis-cloudfront-1.tubi.video/c2e3094d-ad56-4c5f-9655-cd80df71fbab/playlist.m3u8",
        2,
    ),
    Feed(
        "CBS",
        "KUTV",
        "CBS KUTV Salt Lake City UT",
        "CBSKUTV.us",
        "https://linear-707.frequency.stream/dist/stirr/707/hls/master/playlist.m3u8",
        3,
    ),
    Feed(
        "CBS",
        "KCCI",
        "CBS KCCI Des Moines IA",
        "CBSKCCI.us",
        "https://aegis-cloudfront-1.tubi.video/8d1f164d-34ef-4f70-b948-ae33826a0aaa/playlist.m3u8",
        2,
    ),
    Feed(
        "NBC",
        "KOB",
        "NBC KOB Albuquerque NM",
        "NBCKOB.us",
        "https://amg01942-amg01942c1-stirr-us-10167.playouts.now.amagi.tv/playlist.m3u8",
        5,
    ),
    Feed(
        "NBC",
        "WTMJ",
        "NBC WTMJ Milwaukee WI",
        "NBCWTMJ.us",
        "https://aegis-cloudfront-1.tubi.video/d4d923f2-17de-4371-b86d-43490499bcdb/playlist.m3u8",
        4,
    ),
)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path, payload: dict, lock: threading.Lock) -> None:
    line = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)


@dataclass
class FeedRunner:
    feed: Feed
    output_dir: Path
    db_path: Path
    epg_path: Path | None
    json_lock: threading.Lock
    detector: LiveLogoDetector = field(init=False)
    process: subprocess.Popen | None = None
    restarts: int = 0
    _last_transition: bool | None = None

    def __post_init__(self) -> None:
        self.detector = LiveLogoDetector.create(
            self._on_transition,
            sports_generated=False,
            channel_identity=self.feed.identity,
            profile_db_path=self.db_path,
            epg_path=self.epg_path,
            timezone_name="America/New_York",
        )

    @property
    def events_path(self) -> Path:
        return self.output_dir / f"{self.feed.call_sign.lower()}-events.jsonl"

    @property
    def snapshots_path(self) -> Path:
        return self.output_dir / f"{self.feed.call_sign.lower()}-snapshots.jsonl"

    @property
    def stderr_path(self) -> Path:
        return self.output_dir / f"{self.feed.call_sign.lower()}-ffmpeg.log"

    def _on_transition(self, commercial: bool) -> None:
        self._last_transition = bool(commercial)
        append_jsonl(
            self.events_path,
            {
                "observed_at": utc_timestamp(),
                "channel_identity": self.feed.identity,
                "call_sign": self.feed.call_sign,
                "commercial": bool(commercial),
                "status": self.detector.status(),
            },
            self.json_lock,
        )

    def start_detector(self) -> None:
        self.detector.start()

    def start_ffmpeg(self) -> None:
        self.restarts += 1
        stderr_handle = self.stderr_path.open("ab")
        command = [
            executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-fflags",
            "+genpts",
            "-rw_timeout",
            "15000000",
            "-i",
            self.feed.url,
            # Decode only the verified low-bandwidth rendition selected for
            # this feed.  The manifests do not expose renditions in a common
            # order, so each channel carries its own video-stream index.
            "-map",
            f"0:v:{self.feed.video_stream_index}",
            "-an",
            "-vf",
            "fps=2,scale=-2:360",
            "-q:v",
            "6",
            "-threads",
            "1",
            "-f",
            "image2",
            str(self.detector.frame_pattern),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_handle,
            )
        finally:
            stderr_handle.close()
        append_jsonl(
            self.events_path,
            {
                "observed_at": utc_timestamp(),
                "channel_identity": self.feed.identity,
                "call_sign": self.feed.call_sign,
                "event": "ffmpeg-started",
                "attempt": self.restarts,
                "pid": self.process.pid if self.process else None,
            },
            self.json_lock,
        )

    def ensure_running(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        exit_code = self.process.poll() if self.process is not None else None
        if self.process is not None:
            append_jsonl(
                self.events_path,
                {
                    "observed_at": utc_timestamp(),
                    "channel_identity": self.feed.identity,
                    "call_sign": self.feed.call_sign,
                    "event": "ffmpeg-exited",
                    "exit_code": exit_code,
                },
                self.json_lock,
            )
            time.sleep(RESTART_DELAY_SECONDS)
        self.start_ffmpeg()

    def snapshot(self) -> dict:
        status = self.detector.status()
        payload = {
            "observed_at": utc_timestamp(),
            "network": self.feed.network,
            "call_sign": self.feed.call_sign,
            "name": self.feed.name,
            "channel_identity": self.feed.identity,
            "ffmpeg_running": bool(self.process and self.process.poll() is None),
            "ffmpeg_restarts": max(0, self.restarts - 1),
            "status": status,
        }
        append_jsonl(self.snapshots_path, payload, self.json_lock)
        return payload

    def stop(self) -> None:
        if self.process is not None:
            terminate(self.process, timeout=5.0)
        self.detector.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=DEFAULT_DURATION_SECONDS,
        help="Collection duration; defaults to three hours.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=core.DATA_DIR / "commercial-training",
        help="Directory for run metadata and raw detector snapshots.",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=core.DB_PATH,
        help="SQLite database receiving detector observations.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="CALL_SIGN",
        help="Run only a selected call sign; repeat to select several.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    duration_seconds = max(1, int(args.duration_seconds))
    selected = {str(item).strip().upper() for item in args.only if str(item).strip()}
    feeds = tuple(feed for feed in DEFAULT_FEEDS if not selected or feed.call_sign in selected)
    if not feeds:
        raise SystemExit("No configured feeds matched --only.")

    run_started = datetime.now().astimezone()
    run_name = run_started.strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_root / run_name
    output_dir.mkdir(parents=True, exist_ok=False)
    epg_path = core.public_epg_cache_path("US")
    if not epg_path.is_file():
        epg_path = None
    db_path = args.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    commercial_profiles.ensure_schema(db_path)

    stop_requested = threading.Event()
    json_lock = threading.Lock()

    def request_stop(_signum=None, _frame=None) -> None:
        stop_requested.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    manifest_path = output_dir / "manifest.json"
    manifest = {
        "run_name": run_name,
        "started_at": run_started.isoformat(timespec="seconds"),
        "planned_duration_seconds": duration_seconds,
        "database": str(db_path),
        "epg": str(epg_path or ""),
        "sample_interval_seconds": STATUS_INTERVAL_SECONDS,
        "feeds": [feed.__dict__ | {"channel_identity": feed.identity} for feed in feeds],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    runners = [
        FeedRunner(feed, output_dir, db_path, epg_path, json_lock)
        for feed in feeds
    ]
    deadline = time.monotonic() + duration_seconds
    try:
        for runner in runners:
            runner.start_detector()
            runner.start_ffmpeg()
        while not stop_requested.is_set() and time.monotonic() < deadline:
            loop_started = time.monotonic()
            for runner in runners:
                runner.ensure_running()
                runner.snapshot()
            wait_seconds = max(0.0, STATUS_INTERVAL_SECONDS - (time.monotonic() - loop_started))
            stop_requested.wait(wait_seconds)
    finally:
        for runner in runners:
            runner.stop()

    finished_at = datetime.now().astimezone()
    summary = {
        **manifest,
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "elapsed_seconds": round((finished_at - run_started).total_seconds(), 1),
        "stopped_early": stop_requested.is_set() and time.monotonic() < deadline,
        "channels": [],
    }
    for runner in runners:
        profile = commercial_profiles.profile(db_path, runner.feed.identity)
        summary["channels"].append(
            {
                "call_sign": runner.feed.call_sign,
                "channel_identity": runner.feed.identity,
                "ffmpeg_restarts": max(0, runner.restarts - 1),
                "program_samples": int(profile.get("program_samples") or 0),
                "commercial_samples": int(profile.get("commercial_samples") or 0),
                "ready": bool(profile.get("ready")),
                "final_status": runner.detector.status(),
            }
        )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "output_dir": str(output_dir), "summary": summary["channels"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
