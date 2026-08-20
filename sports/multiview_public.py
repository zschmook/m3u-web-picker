from __future__ import annotations

import hashlib
import json
import random
import re
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from media.ffmpeg import executable as ffmpeg_executable


PLAYLIST_URL = "https://iptv-org.github.io/iptv/countries/us.m3u"
DOWNLOAD_TIMEOUT = 12
PROBE_TIMEOUT = 5
CACHE_SECONDS = 60 * 30
TARGET_COUNT = 8

_LOCK = threading.Lock()
_CHANNELS: list[dict] = []
_SCANNING = False
_ERROR = ""
_UPDATED = 0.0


def _parse(text: str) -> list[dict]:
    rows: list[dict] = []
    pending: dict | None = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            name = line.rsplit(",", 1)[-1].strip()
            group_match = re.search(r'group-title="([^"]*)"', line)
            id_match = re.search(r'tvg-id="([^"]*)"', line)
            pending = {
                "name": name,
                "group": group_match.group(1) if group_match else "Public TV",
                "tvg_id": id_match.group(1) if id_match else "",
            }
        elif pending is not None and line and not line.startswith("#"):
            if line.startswith(("http://", "https://")):
                pending["url"] = line
                rows.append(pending)
            pending = None
    return rows


def _local_score(channel: dict) -> int:
    name = str(channel.get("name") or "")
    group = str(channel.get("group") or "").lower()
    score = 0
    if re.search(r"\b[A-Z]{3,5}(?:-DT\d?)?\b", name):
        score += 5
    if re.search(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\s[A-Z]{2}\b", name):
        score += 4
    if any(word in group for word in ("news", "legislative", "education")):
        score += 3
    if any(word in name.lower() for word in ("local", "county", "city", "community", "public access")):
        score += 3
    if any(word in group for word in ("religious", "shopping", "animation")):
        score -= 5
    return score


def _eligible(channel: dict) -> bool:
    searchable = " ".join((
        str(channel.get("name") or ""),
        str(channel.get("group") or ""),
        str(channel.get("tvg_id") or ""),
    )).lower()
    return "community" not in searchable


def _ffprobe() -> str:
    return str(Path(ffmpeg_executable()).with_name("ffprobe"))


def _probe(channel: dict) -> dict | None:
    command = [
        _ffprobe(), "-v", "error", "-rw_timeout", str(PROBE_TIMEOUT * 1_000_000),
        "-show_entries", "stream=codec_type,width,height", "-of", "json", str(channel["url"]),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=PROBE_TIMEOUT + 1, check=False)
        if result.returncode:
            return None
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = any(stream.get("codec_type") == "audio" for stream in streams)
    if not video:
        return None
    value = dict(channel)
    value.update({"audio": audio, "width": int(video.get("width") or 0), "height": int(video.get("height") or 0)})
    return value


def _download() -> str:
    request = urllib.request.Request(PLAYLIST_URL, headers={"User-Agent": "m3u-web-picker/public-multiview"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        return response.read(16 * 1024 * 1024).decode("utf-8", errors="replace")


def _discover() -> list[dict]:
    candidates = [channel for channel in _parse(_download()) if _eligible(channel)]
    random.Random(int(time.time() // 86400)).shuffle(candidates)
    candidates.sort(key=_local_score, reverse=True)
    # Probe a broad but bounded sample. Audio-capable survivors sort first so
    # any pane can safely become the director's selected audio source.
    survivors: list[dict] = []
    for offset in range(0, min(len(candidates), 120), 16):
        batch = candidates[offset : offset + 16]
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="public-tv-probe") as pool:
            futures = [pool.submit(_probe, channel) for channel in batch]
            for future in as_completed(futures):
                channel = future.result()
                if channel:
                    survivors.append(channel)
        if len([row for row in survivors if row.get("audio")]) >= TARGET_COUNT:
            break
    survivors.sort(key=lambda row: (not bool(row.get("audio")), -_local_score(row), row["name"]))
    output: list[dict] = []
    seen_hosts: set[str] = set()
    for index, row in enumerate(survivors):
        host = re.sub(r"^https?://([^/]+).*$", r"\1", row["url"])
        if host in seen_hosts and len(survivors) > TARGET_COUNT:
            continue
        seen_hosts.add(host)
        digest = hashlib.sha1(row["url"].encode("utf-8")).hexdigest()[:10]
        output.append({**row, "id": f"public-{digest}", "weight": 900 - index * 10})
        if len(output) >= TARGET_COUNT:
            break
    if len(output) < TARGET_COUNT:
        selected = {row["url"] for row in output}
        for index, row in enumerate(survivors):
            if row["url"] in selected:
                continue
            digest = hashlib.sha1(row["url"].encode("utf-8")).hexdigest()[:10]
            output.append({**row, "id": f"public-{digest}", "weight": 800 - index * 10})
            selected.add(row["url"])
            if len(output) >= TARGET_COUNT:
                break
    if len(output) < 5:
        raise RuntimeError(f"Only {len(output)} public streams passed the probe")
    return output


def start(callback) -> bool:
    global _SCANNING, _ERROR
    with _LOCK:
        if _SCANNING:
            return False
        if len(_CHANNELS) >= 5 and time.monotonic() - _UPDATED < CACHE_SECONDS:
            callback(list(_CHANNELS), "")
            return True
        _SCANNING = True
        _ERROR = ""

    def run() -> None:
        global _CHANNELS, _SCANNING, _ERROR, _UPDATED
        try:
            channels = _discover()
            error = ""
        except Exception as exc:
            channels = []
            error = str(exc)
        with _LOCK:
            if channels:
                _CHANNELS = channels
                _UPDATED = time.monotonic()
            _ERROR = error
            _SCANNING = False
        callback(list(channels), error)

    threading.Thread(target=run, name="public-multiview-discovery", daemon=True).start()
    return True


def status() -> dict:
    with _LOCK:
        return {"scanning": _SCANNING, "error": _ERROR, "count": len(_CHANNELS)}
