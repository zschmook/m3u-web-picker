from __future__ import annotations

import copy
import csv
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import logo_registry
import commercial_profiles


CHANNEL_SECONDS = 20 * 60
MIN_CHANNEL_SECONDS = 5 * 60
MAX_CHANNEL_SECONDS = 120 * 60
STREAM_ORIGIN = "http://127.0.0.1:9999"
READ_SIZE = 64 * 1024
RECONNECT_DELAY_SECONDS = 3
INACTIVE_PROBE_SECONDS = 3 * 60
INACTIVE_CONFIRM_SECONDS = 60
EVENT_FEED_PATTERN = re.compile(r"(?:4k|events?|upcoming|test[ -]?feed)", re.I)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stream_path(play_url: object) -> str:
    value = str(play_url or "").split("?", 1)[0].strip()
    manual = re.fullmatch(r"/guide/play/manual/([^/]+)", value)
    if manual:
        return f"/stream/channel/manual/{manual.group(1)}/mpegts"
    sports = re.fullmatch(r"/guide/play/sports/(\d+)", value)
    if sports:
        return f"/stream/channel/sports/{sports.group(1)}/mpegts"
    return ""


class ChannelLearningRotation:
    """Run one production FFmpeg learning stream at a time in guide order."""

    def __init__(
        self,
        *,
        channel_seconds: int = CHANNEL_SECONDS,
        stream_origin: str = STREAM_ORIGIN,
    ) -> None:
        self.channel_seconds = max(1, int(channel_seconds))
        self.stream_origin = str(stream_origin).rstrip("/")
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._db_path: Path | None = None
        self._run_dir: Path | None = None
        self._status = self._empty_status()

    def _empty_status(self) -> dict:
        return {
            "running": False,
            "phase": "idle",
            "message": "Ready to cycle through every guide channel",
            "channel_seconds": self.channel_seconds,
            "current_index": 0,
            "total_channels": 0,
            "channels_completed": 0,
            "total_channel_slots_completed": 0,
            "pass_number": 0,
            "passes_completed": 0,
            "current_channel": {},
            "started_at": None,
            "channel_started_at": None,
            "finished_at": None,
            "elapsed_seconds": 0,
            "remaining_seconds": self.channel_seconds,
            "bytes_received": 0,
            "total_bytes_received": 0,
            "reconnects": 0,
            "last_error": "",
            "run_directory": "",
            "snapshots_saved": 0,
            "channels_skipped_inactive": 0,
            "channel_skip_reason": "",
            "priority_mode": "guide-order",
        }

    def status(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._status)

    def _update(self, **values: object) -> None:
        with self._lock:
            self._status.update(values)

    def start(
        self,
        channels: Iterable[dict],
        *,
        db_path: Path | str | None = None,
        archive_root: Path | str | None = None,
        channel_seconds: int | None = None,
    ) -> dict:
        requested_channel_seconds = self.channel_seconds
        if channel_seconds is not None:
            requested_channel_seconds = int(channel_seconds)
        if channel_seconds is not None and not (
            MIN_CHANNEL_SECONDS <= requested_channel_seconds <= MAX_CHANNEL_SECONDS
        ):
            raise ValueError("Minutes per channel must be between 5 and 120.")
        prepared = []
        for channel in channels:
            if bool(channel.get("generated")):
                continue
            path = stream_path(channel.get("play_url"))
            if not path:
                continue
            prepared.append({
                "number": int(channel.get("number") or 0),
                "name": str(channel.get("name", "") or ""),
                "tvg_id": str(channel.get("tvg_id", "") or ""),
                "generated": bool(channel.get("generated")),
                "play_url": str(channel.get("play_url", "") or ""),
                "stream_path": path,
                "profile_identity": logo_registry.channel_identity(channel),
                "stream_identity": path.removeprefix("/stream/channel/").removesuffix("/mpegts").replace("/", ":", 1),
            })
        if not prepared:
            raise ValueError("No playable guide channels are available.")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return copy.deepcopy(self._status)
            self.channel_seconds = requested_channel_seconds
            self._stop.clear()
            self._db_path = Path(db_path) if db_path else None
            self._run_dir = None
            if archive_root:
                run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")
                self._run_dir = Path(archive_root) / run_name
                self._run_dir.mkdir(parents=True, exist_ok=False)
            self._status = self._empty_status()
            self._status.update(
                running=True,
                phase="starting",
                message="Preparing channel 1",
                total_channels=len(prepared),
                started_at=_utc_now(),
                remaining_seconds=self.channel_seconds,
                run_directory=str(self._run_dir or ""),
            )
            self._write_manifest(prepared)
            self._thread = threading.Thread(
                target=self._run,
                args=(prepared,),
                name="channel-learning-rotation",
                daemon=True,
            )
            self._thread.start()
            return copy.deepcopy(self._status)

    def stop(self) -> dict:
        self._stop.set()
        with self._lock:
            if self._status["running"]:
                self._status.update(
                    phase="stopping",
                    message="Stopping after the current stream read closes",
                )
            return copy.deepcopy(self._status)

    def _run(self, channels: list[dict]) -> None:
        stopped = False
        pass_number = 1
        total_completed = 0
        try:
            while not self._stop.is_set():
                ordered_channels = (
                    list(channels)
                    if pass_number == 1
                    else self._priority_order(channels)
                )
                self._update(
                    pass_number=pass_number,
                    channels_completed=0,
                    phase="starting" if pass_number == 1 else "switching",
                    message=f"Starting pass {pass_number}",
                    priority_mode=(
                        "guide-order" if pass_number == 1 else "candidate-priority"
                    ),
                )
                for index, channel in enumerate(ordered_channels, start=1):
                    if self._stop.is_set():
                        stopped = True
                        break
                    self._sample_channel(index, len(ordered_channels), channel)
                    if self._stop.is_set():
                        stopped = True
                        break
                    total_completed += 1
                    self._update(
                        channels_completed=index,
                        total_channel_slots_completed=total_completed,
                        phase="switching",
                        message=(
                            f"Moving to channel {index + 1}"
                            if index < len(ordered_channels)
                            else f"Pass {pass_number} complete · returning to channel 1"
                        ),
                    )
                if stopped:
                    break
                self._update(passes_completed=pass_number)
                self._write_manifest()
                pass_number += 1
        finally:
            self._update(
                running=False,
                phase="stopped",
                message="Rotation stopped",
                finished_at=_utc_now(),
                remaining_seconds=0,
            )
            self._write_manifest()

    def _sample_channel(self, index: int, total: int, channel: dict) -> None:
        started = time.monotonic()
        started_at = _utc_now()
        deadline = started + self.channel_seconds
        channel_bytes = 0
        reconnects = 0
        total_before = int(self.status().get("total_bytes_received") or 0)
        last_status_second = -1
        inactive_seconds = 0
        inactive_skip = False
        skip_reason = ""
        folder = self._channel_folder(index, channel)
        snapshots_dir = folder / "snapshots" if folder else None
        if snapshots_dir:
            snapshots_dir.mkdir(parents=True, exist_ok=True)
        public_channel = {
            key: channel[key]
            for key in ("number", "name", "tvg_id", "generated", "play_url")
        }
        self._update(
            phase="connecting",
            message=f"Connecting to channel {channel['number']}: {channel['name']}",
            current_index=index,
            total_channels=total,
            current_channel=public_channel,
            channel_started_at=started_at,
            elapsed_seconds=0,
            remaining_seconds=self.channel_seconds,
            bytes_received=0,
            reconnects=0,
            last_error="",
            snapshots_saved=0,
            channel_skip_reason="",
        )

        while (
            not self._stop.is_set()
            and not inactive_skip
            and time.monotonic() < deadline
        ):
            url = self.stream_origin + channel["stream_path"]
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "m3u-picker-channel-learning/1"},
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    if int(response.status) != 200:
                        raise RuntimeError(f"Stream returned HTTP {response.status}")
                    self._update(
                        phase="collecting",
                        message=f"Collecting channel {channel['number']}: {channel['name']}",
                        last_error="",
                    )
                    self._set_snapshot_archive(
                        str(channel.get("stream_identity") or ""),
                        snapshots_dir,
                    )
                    while (
                        not self._stop.is_set()
                        and not inactive_skip
                        and time.monotonic() < deadline
                    ):
                        chunk = response.read(READ_SIZE)
                        if not chunk:
                            raise EOFError("Stream ended before the channel sample completed")
                        channel_bytes += len(chunk)
                        elapsed = min(self.channel_seconds, int(time.monotonic() - started))
                        if elapsed != last_status_second:
                            last_status_second = elapsed
                            self._update(
                                elapsed_seconds=elapsed,
                                remaining_seconds=max(0, self.channel_seconds - elapsed),
                                bytes_received=channel_bytes,
                                total_bytes_received=total_before + channel_bytes,
                                snapshots_saved=(
                                    len(list(snapshots_dir.glob("observation-*.jpg")))
                                    if snapshots_dir else 0
                                ),
                            )
                            if (
                                elapsed >= INACTIVE_PROBE_SECONDS
                                and self._event_like_channel(channel)
                            ):
                                detector = self._detector_status(
                                    str(channel.get("stream_identity") or "")
                                )
                                if self._inactive_event_status(detector):
                                    inactive_seconds += 1
                                else:
                                    inactive_seconds = 0
                                if inactive_seconds >= INACTIVE_CONFIRM_SECONDS:
                                    inactive_skip = True
                                    skip_reason = "Static event or 4K feed"
                                    skipped = int(
                                        self.status().get("channels_skipped_inactive") or 0
                                    ) + 1
                                    self._update(
                                        phase="skipping",
                                        message=(
                                            f"Skipping inactive channel {channel['number']}: "
                                            f"{channel['name']}"
                                        ),
                                        channel_skip_reason=skip_reason,
                                        channels_skipped_inactive=skipped,
                                    )
                                    break
            except (EOFError, OSError, RuntimeError, urllib.error.URLError) as exc:
                if self._stop.is_set() or time.monotonic() >= deadline:
                    break
                reconnects += 1
                elapsed = min(self.channel_seconds, int(time.monotonic() - started))
                self._update(
                    phase="reconnecting",
                    message=f"Reconnecting channel {channel['number']}",
                    elapsed_seconds=elapsed,
                    remaining_seconds=max(0, self.channel_seconds - elapsed),
                    bytes_received=channel_bytes,
                    total_bytes_received=total_before + channel_bytes,
                    reconnects=reconnects,
                    last_error=str(exc)[:300],
                )
                self._stop.wait(min(RECONNECT_DELAY_SECONDS, max(0, deadline - time.monotonic())))

        self._set_snapshot_archive(str(channel.get("stream_identity") or ""), None)
        ended_at = _utc_now()
        try:
            self._archive_channel(
                index=index,
                channel=channel,
                started_at=started_at,
                ended_at=ended_at,
                bytes_received=channel_bytes,
                reconnects=reconnects,
                skip_reason=skip_reason,
            )
        except Exception as exc:
            self._update(last_error=f"Could not save channel archive: {exc}"[:300])

    def _priority_order(self, channels: list[dict]) -> list[dict]:
        """Revisit likely reusable ads first after the initial guide-order pass."""
        if not self._db_path or not self._db_path.exists():
            return list(channels)
        scores: dict[str, tuple[int, int, int]] = {}
        try:
            with closing(sqlite3.connect(self._db_path, timeout=30)) as conn:
                rows = conn.execute(
                    """
                    WITH signatures AS (
                        SELECT channel_identity,
                               SUM(status IN ('probable', 'classified')) AS repeated,
                               COUNT(*) AS candidates
                        FROM commercial_ad_signatures_v2
                        GROUP BY channel_identity
                    ), observations AS (
                        SELECT channel_identity,
                               SUM(label = 'commercial') AS commercial_samples
                        FROM commercial_channel_observations
                        GROUP BY channel_identity
                    )
                    SELECT observations.channel_identity,
                           COALESCE(signatures.repeated, 0),
                           COALESCE(signatures.candidates, 0),
                           COALESCE(observations.commercial_samples, 0)
                    FROM observations
                    LEFT JOIN signatures
                      ON signatures.channel_identity = observations.channel_identity
                    """
                ).fetchall()
            scores = {
                str(row[0] or ""): (int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
                for row in rows
            }
        except sqlite3.Error:
            return list(channels)
        indexed = list(enumerate(channels))
        indexed.sort(
            key=lambda item: (
                *scores.get(str(item[1].get("profile_identity") or ""), (0, 0, 0)),
                -item[0],
            ),
            reverse=True,
        )
        return [channel for _index, channel in indexed]

    @staticmethod
    def _event_like_channel(channel: dict) -> bool:
        text = " ".join(
            str(channel.get(key) or "") for key in ("name", "tvg_id")
        )
        return bool(EVENT_FEED_PATTERN.search(text))

    @staticmethod
    def _detector_status(stream_identity: str) -> dict:
        if not stream_identity:
            return {}
        from media import mpegts

        for stream in mpegts.commercial_status().get("streams", []):
            if str(stream.get("identity") or "") == stream_identity:
                return dict(stream.get("logo_detector") or {})
        return {}

    @staticmethod
    def _inactive_event_status(status: dict) -> bool:
        if not status:
            return False
        features = dict(status.get("channel_features") or {})
        static_picture = bool(
            float(features.get("cut_density") or 0) <= 1.5
            and float(features.get("mean_color_change") or 0) <= 1.5
            and float(features.get("color_volatility") or 0) <= 3.5
        )
        prolonged_filler = bool(
            status.get("commercial")
            and str(status.get("commercial_reason") or "") == "countdown-clock"
            and float(status.get("commercial_observed_seconds") or 0)
            >= INACTIVE_PROBE_SECONDS
        )
        return static_picture or prolonged_filler

    @staticmethod
    def _safe_name(value: object) -> str:
        cleaned = re.sub(r"[^a-z0-9]+", "-", str(value or "").casefold()).strip("-")
        return cleaned[:60] or "channel"

    def _channel_folder(self, index: int, channel: dict) -> Path | None:
        if not self._run_dir:
            return None
        pass_number = max(1, int(self.status().get("pass_number") or 1))
        return self._run_dir / f"pass-{pass_number:03d}" / (
            f"{index:03d}-{int(channel.get('number') or 0)}-{self._safe_name(channel.get('name'))}"
        )

    @staticmethod
    def _set_snapshot_archive(stream_identity: str, directory: Path | None) -> bool:
        if not stream_identity:
            return False
        from media import mpegts

        return mpegts.set_inspection_archive(stream_identity, directory)

    def _observation_rows(self, identity: str, started_at: str, ended_at: str) -> list[dict]:
        if not self._db_path or not identity:
            return []
        with closing(sqlite3.connect(self._db_path, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT *
                FROM commercial_channel_observations
                WHERE channel_identity = ? AND observed_at >= ? AND observed_at <= ?
                ORDER BY observed_at, id
                """,
                (identity, started_at, ended_at),
            ).fetchall()
        return [dict(row) for row in rows]

    def _write_manifest(self, channels: list[dict] | None = None) -> None:
        if not self._run_dir:
            return
        manifest_path = self._run_dir / "manifest.json"
        existing = {}
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
        if channels is not None:
            existing.update({
                "started_at": self._status.get("started_at"),
                "channel_seconds": self.channel_seconds,
                "channels": [
                    {key: channel.get(key) for key in ("number", "name", "tvg_id", "profile_identity")}
                    for channel in channels
                ],
                "completed": [],
            })
        existing["status"] = self.status()
        manifest_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    def _archive_channel(
        self,
        *,
        index: int,
        channel: dict,
        started_at: str,
        ended_at: str,
        bytes_received: int,
        reconnects: int,
        skip_reason: str = "",
    ) -> None:
        if not self._run_dir:
            return
        folder = self._channel_folder(index, channel)
        if folder is None:
            return
        folder.mkdir(parents=True, exist_ok=True)
        rows = self._observation_rows(
            str(channel.get("profile_identity") or ""),
            started_at,
            ended_at,
        )
        episodes = commercial_profiles.episodes_between(
            self._db_path,
            str(channel.get("profile_identity") or ""),
            started_at,
            ended_at,
        ) if self._db_path else []
        (folder / "observations.json").write_text(
            json.dumps(rows, indent=2),
            encoding="utf-8",
        )
        if rows:
            with (folder / "observations.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        (folder / "commercial-episodes.json").write_text(
            json.dumps(episodes, indent=2),
            encoding="utf-8",
        )
        snapshots = sorted((folder / "snapshots").glob("observation-*.jpg"))
        summary = {
            "channel": {key: channel.get(key) for key in ("number", "name", "tvg_id", "profile_identity")},
            "pass_number": max(1, int(self.status().get("pass_number") or 1)),
            "started_at": started_at,
            "ended_at": ended_at,
            "inspection_samples": len(rows),
            "program_samples": sum(row.get("label") == "program" for row in rows),
            "commercial_samples": sum(row.get("label") == "commercial" for row in rows),
            "commercial_episodes": len(episodes),
            "bytes_received": bytes_received,
            "reconnects": reconnects,
            "image_snapshots": len(snapshots),
            "skipped_early": bool(skip_reason),
            "skip_reason": skip_reason,
            "snapshot_files": [str(path.relative_to(folder)) for path in snapshots],
        }
        (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        (folder / "graph.svg").write_text(
            self._graph_svg(rows, channel, started_at, ended_at),
            encoding="utf-8",
        )

        manifest_path = self._run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("completed", []).append(summary)
        manifest["status"] = self.status()
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    @staticmethod
    def _graph_svg(rows: list[dict], channel: dict, started_at: str, ended_at: str) -> str:
        width, height = 1400, 520
        left, right, top, bottom = 70, 25, 55, 70
        plot_width, plot_height = width - left - right, height - top - bottom
        start_epoch = datetime.fromisoformat(started_at).timestamp()
        end_epoch = max(start_epoch + 1, datetime.fromisoformat(ended_at).timestamp())
        signals = (
            ("cut_density", "#38bdf8", "Cut frequency"),
            ("color_volatility", "#f59e0b", "Color changes"),
            ("program_graphics_confidence", "#34d399", "Program graphic"),
            ("bug_identity_confidence", "#ef4444", "Bug confidence"),
            ("commercial_confidence", "#ffffff", "Commercial confidence"),
        )

        def points(name: str) -> str:
            values = []
            for row in rows:
                try:
                    observed = datetime.fromisoformat(str(row.get("observed_at"))).timestamp()
                    value = max(0.0, min(1.0, float(row.get(name) or 0)))
                except (TypeError, ValueError):
                    continue
                x = left + ((observed - start_epoch) / (end_epoch - start_epoch)) * plot_width
                y = top + (1 - value) * plot_height
                values.append(f"{x:.1f},{y:.1f}")
            return " ".join(values)

        escaped_name = str(channel.get("name") or "Channel").replace("&", "&amp;").replace("<", "&lt;")
        elements = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#0b111b"/>',
            f'<text x="{left}" y="30" fill="#e5edf7" font-family="sans-serif" font-size="20" font-weight="700">{escaped_name}</text>',
        ]
        for value in (0, 25, 50, 75, 100):
            y = top + (1 - value / 100) * plot_height
            elements.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#253044" stroke-width="1"/>')
            elements.append(f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" fill="#8190a5" font-family="sans-serif" font-size="12">{value}</text>')
        for tick in range(5):
            ratio = tick / 4
            x = left + ratio * plot_width
            stamp = datetime.fromtimestamp(start_epoch + ratio * (end_epoch - start_epoch), timezone.utc).astimezone().strftime("%I:%M:%S %p")
            elements.append(f'<text x="{x:.1f}" y="{height-40}" text-anchor="middle" fill="#8190a5" font-family="sans-serif" font-size="12">{stamp}</text>')
        for name, color, _label in signals:
            path_points = points(name)
            if path_points:
                elements.append(f'<polyline points="{path_points}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
        legend_x = left
        for _name, color, label in signals:
            elements.append(f'<circle cx="{legend_x}" cy="{height-15}" r="5" fill="{color}"/><text x="{legend_x+10}" y="{height-10}" fill="#b6c1d1" font-family="sans-serif" font-size="12">{label}</text>')
            legend_x += 210
        elements.append("</svg>")
        return "".join(elements)


rotation = ChannelLearningRotation()
