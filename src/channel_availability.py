"""Bounded, cached background checks for manual guide streams."""
from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import subprocess
import threading
import time
import sys


def playback_in_use():
    """Do not open extra provider sessions while actual players are connected."""
    pipeline = sys.modules.get("media_pipeline")
    relay = sys.modules.get("media.upstream_relay")
    return bool((pipeline and pipeline.active_session_count()) or
                (relay and relay.active_count()))


def probe_stream(url):
    executable = shutil.which("ffprobe")
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-rw_timeout", "6000000",
             "-read_intervals", "%+#1", "-show_entries", "packet=size",
             "-of", "json", url],
            capture_output=True, timeout=10, check=False,
        )
        if result.returncode != 0:
            return False
        payload = json.loads(result.stdout or b"{}")
        return any(int(packet.get("size") or 0) > 0 for packet in payload.get("packets", []))
    except (ValueError, TypeError):
        return False
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return None


class AvailabilityCache:
    def __init__(self, probe=probe_stream, ttl=60, playback_active=lambda: False):
        self.probe = probe
        self.ttl = ttl
        self.playback_active = playback_active
        self.lock = threading.Lock()
        self.results = {}
        self.pending = set()
        # Only one health-check connection at a time across all guide clients.
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="guide-health")

    def _check(self, urls):
        available, target = False, ""
        try:
            for url in urls:
                if self.playback_active():
                    return
                result = self.probe(url)
                if result is True:
                    available, target = True, url
                    break
                if result is None:
                    available = None
            with self.lock:
                self.results[urls] = (time.monotonic(), available, target)
                if len(self.results) > 2048:
                    oldest = min(self.results, key=lambda key: self.results[key][0])
                    self.results.pop(oldest, None)
        finally:
            with self.lock:
                self.pending.discard(urls)

    def lookup(self, urls):
        urls = tuple(dict.fromkeys(url for url in urls if url))
        if not urls:
            return False, ""
        with self.lock:
            now = time.monotonic()
            cached = self.results.get(urls)
            if self.playback_active():
                # A stale result is unknown until a real play or an idle-time
                # probe verifies it; do not hide a recovered channel forever.
                if cached and now - cached[0] < self.ttl:
                    return cached[1], cached[2] or (urls[0] if cached[1] is None else "")
                return None, urls[0]
            if (cached is None or now - cached[0] >= self.ttl) and urls not in self.pending and len(self.pending) < 256:
                self.pending.add(urls)
                self.worker.submit(self._check, urls)
            # Keep the last result while a recheck waits behind other channels.
            if cached:
                return cached[1], cached[2] or (urls[0] if cached[1] is None else "")
        return None, urls[0]

    def resolve(self, urls):
        """Check an uncached playback request before choosing a provider."""
        urls = tuple(dict.fromkeys(url for url in urls if url))
        if not urls:
            return ""
        with self.lock:
            cached = self.results.get(urls)
        if cached is None or time.monotonic() - cached[0] >= self.ttl:
            self._check(urls)
        available, target = self.peek(urls)
        return target or (urls[0] if available is None else "")

    def peek(self, urls):
        """Read the latest result without scheduling a stream check."""
        key = tuple(dict.fromkeys(url for url in urls if url))
        with self.lock:
            cached = self.results.get(key)
            return (cached[1], cached[2]) if cached else (None, "")

    def remember(self, urls, available, target=""):
        """Record the result of real playback without launching another probe."""
        key = tuple(dict.fromkeys(url for url in urls if url))
        if not key:
            return
        chosen = str(target or "") if available is True else ""
        with self.lock:
            self.results[key] = (time.monotonic(), available, chosen)
            if len(self.results) > 2048:
                oldest = min(self.results, key=lambda item: self.results[item][0])
                self.results.pop(oldest, None)


availability = AvailabilityCache(playback_active=playback_in_use)


def candidate_urls(channel, primary_channels, fallback_sets, key_function):
    """Match station IDs or full names; never substitute a network affiliate."""
    result = []
    wanted_key = key_function(channel)
    wanted_id = str(channel.get("tvg_id") or "").strip().casefold()
    wanted_name = " ".join(str(channel.get("name") or "").casefold().split())
    for rows in [primary_channels, *fallback_sets]:
        for row in rows:
            row_id = str(row.get("tvg_id") or "").strip().casefold()
            row_name = " ".join(str(row.get("name") or "").casefold().split())
            exact = row.get("key") == wanted_key or (
                row.get("url") == channel.get("url") and key_function(row) == wanted_key)
            same_id = wanted_id and row_id == wanted_id
            same_name = wanted_name and row_name == wanted_name and not (wanted_id and row_id and wanted_id != row_id)
            if exact or same_id or same_name:
                url = str(row.get("url") or "").strip()
                if url and url not in result:
                    result.append(url)
    return result
