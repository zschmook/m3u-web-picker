from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from flask import Response, request

import core
import logo_registry
import sports


LOGO_CACHE_DIR = core.DATA_DIR / "logo_cache"
LOGO_CACHE_TTL_SECONDS = 24 * 60 * 60
LOGO_BROWSER_MAX_AGE_SECONDS = 6 * 60 * 60
MAX_LOGO_BYTES = 2 * 1024 * 1024
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

_observed_provider_signature: tuple | None = None
_observed_sports_signature: tuple | None = None
_last_observation_check = 0.0
_icon_update_lock = threading.Lock()
_icon_update_run_lock = threading.Lock()
_icon_update_state = {
    "requested": False,
    "running": False,
    "status": "idle",
    "stage": "",
    "detail": "",
    "total": 0,
    "processed": 0,
    "downloaded": 0,
    "cached": 0,
    "failed": 0,
    "identities": 0,
    "started_at": None,
    "finished_at": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_icon_update_state(**changes) -> dict:
    with _icon_update_lock:
        _icon_update_state.update(changes)
        return dict(_icon_update_state)


def icon_update_payload() -> dict:
    with _icon_update_lock:
        return dict(_icon_update_state)


def prepare_icon_update() -> dict:
    """Mark an explicitly requested manual icon warm-up as queued."""
    return _set_icon_update_state(
        requested=True,
        running=False,
        status="waiting",
        stage="Icon update",
        detail="Waiting for the manual provider/sports refresh to finish.",
        total=0,
        processed=0,
        downloaded=0,
        cached=0,
        failed=0,
        identities=0,
        started_at=None,
        finished_at=None,
    )


def finish_icon_update_early(detail: str, *, status: str = "skipped") -> dict:
    return _set_icon_update_state(
        requested=True,
        running=False,
        status=status,
        stage="Icon update",
        detail=str(detail or "Icon update did not run."),
        finished_at=_now_iso(),
    )


def _cache_mtime_ns(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except OSError:
        return 0


def _observe_known_candidates_if_changed() -> None:
    """Record candidate URLs without eagerly downloading thousands of images."""
    global _observed_provider_signature, _observed_sports_signature, _last_observation_check

    now = time.monotonic()
    if now - _last_observation_check < 2.0:
        return
    _last_observation_check = now

    provider_signature = (
        str(core.last_refresh or ""),
        len(core.channels),
        _cache_mtime_ns(core.MASTER_CACHE_PATH),
    )
    if provider_signature != _observed_provider_signature:
        logo_registry.observe_many(
            core.DB_PATH,
            (
                (
                    logo_registry.channel_identity(channel),
                    str(channel.get("tvg_logo", "") or ""),
                    "provider",
                )
                for channel in core.channels
                if str(channel.get("tvg_logo", "") or "").strip()
            ),
        )
        _observed_provider_signature = provider_signature

    try:
        last_scan = sports.last_scan(core.DB_PATH) or {}
        sports_signature = (
            str(last_scan.get("finished_at") or ""),
            int(last_scan.get("channel_count") or 0),
        )
        if sports_signature != _observed_sports_signature:
            catalog = sports.catalog_payload(core.DB_PATH, scope_type="team")
            logo_registry.observe_many(
                core.DB_PATH,
                (
                    (
                        logo_registry.team_identity(item.get("id")),
                        str(item.get("logo_url", "") or ""),
                        str(item.get("source", "") or "sports-catalog"),
                    )
                    for item in catalog
                    if str(item.get("logo_url", "") or "").strip()
                ),
            )
            _observed_sports_signature = sports_signature
    except Exception:
        # Logo discovery is opportunistic and must never break image serving.
        pass


def _known_logo_urls() -> set[str]:
    urls = {
        str(channel.get("tvg_logo", "") or "").strip()
        for channel in core.channels
        if str(channel.get("tvg_logo", "") or "").strip()
    }
    try:
        urls.update(
            str(item.get("logo_url", "") or "").strip()
            for item in sports.catalog_payload(core.DB_PATH)
            if str(item.get("logo_url", "") or "").strip()
        )
    except Exception:
        pass
    try:
        urls.update(
            str(row.get("tvg_logo", "") or "").strip()
            for row in sports.generated_rows(core.DB_PATH)
            if str(row.get("tvg_logo", "") or "").strip()
        )
    except Exception:
        pass
    return urls


def _digest_for_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_paths_from_digest(digest: str) -> tuple[Path, Path]:
    return LOGO_CACHE_DIR / f"{digest}.bin", LOGO_CACHE_DIR / f"{digest}.json"


def _cache_paths(url: str) -> tuple[Path, Path]:
    return _cache_paths_from_digest(_digest_for_url(url))


def _sniff_image_type(payload: bytes, header_type: str) -> str:
    content_type = str(header_type or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        return content_type
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    if payload.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    sample = payload[:512].lstrip().lower()
    if sample.startswith(b"<svg") or (sample.startswith(b"<?xml") and b"<svg" in sample):
        return "image/svg+xml"
    return ""


def _read_cached(data_path: Path, meta_path: Path) -> tuple[bytes, str] | None:
    try:
        payload = data_path.read_bytes()
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        content_type = str(metadata.get("content_type", "") or "")
        if not payload or not content_type.startswith("image/"):
            return None
        return payload, content_type
    except (OSError, ValueError, TypeError):
        return None


def _read_registered(identity_key: str) -> tuple[bytes, str] | None:
    if not identity_key:
        return None
    try:
        row = logo_registry.lookup(core.DB_PATH, identity_key)
    except Exception:
        return None
    if not row:
        return None
    digest = str(row.get("cache_digest", "") or "").strip().lower()
    content_type = str(row.get("content_type", "") or "").strip().lower()
    if not _DIGEST_RE.fullmatch(digest) or not content_type.startswith("image/"):
        return None
    data_path, _meta_path = _cache_paths_from_digest(digest)
    try:
        payload = data_path.read_bytes()
    except OSError:
        return None
    return (payload, content_type) if payload else None


def _serve_logo(payload: bytes, content_type: str, cache_state: str) -> Response:
    response = Response(payload, content_type=content_type)
    response.headers["Cache-Control"] = f"public, max-age={LOGO_BROWSER_MAX_AGE_SECONDS}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-M3U-Logo-Cache"] = cache_state
    return response


def _fetch_logo(url: str) -> tuple[bytes, str]:
    request_headers = {
        "User-Agent": "Mozilla/5.0 (M3U Web Picker logo cache)",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    }
    upstream_request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(upstream_request, timeout=6) as upstream:
        length = int(upstream.headers.get("Content-Length", "0") or 0)
        if length > MAX_LOGO_BYTES:
            raise ValueError("Logo is too large.")
        payload = upstream.read(MAX_LOGO_BYTES + 1)
        if len(payload) > MAX_LOGO_BYTES:
            raise ValueError("Logo is too large.")
        content_type = _sniff_image_type(payload, upstream.headers.get("Content-Type", ""))
        if not payload or not content_type:
            raise ValueError("Upstream response is not an image.")
        final_url = urllib.parse.urlsplit(upstream.geturl())
        if final_url.scheme not in {"http", "https"}:
            raise ValueError("Invalid logo redirect.")
        return payload, content_type


def _warmup_candidates() -> dict[str, list[tuple[str, str]]]:
    """Return known logo URLs grouped with identities; this never calls a schedule API."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    seen_identity_url: set[tuple[str, str]] = set()

    def add(identity_key: str, url: str, source_kind: str) -> None:
        key = logo_registry.normalize_identity(identity_key)
        clean_url = str(url or "").strip()
        parsed = urllib.parse.urlsplit(clean_url)
        if not key or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return
        pair = (key, clean_url)
        if pair in seen_identity_url:
            return
        seen_identity_url.add(pair)
        grouped.setdefault(clean_url, []).append((key, str(source_kind or "")[:80]))

    try:
        provider_sets = core.sports_provider_channel_sets()
    except Exception:
        provider_sets = []
    if provider_sets:
        for source, source_channels in provider_sets:
            source_kind = f"provider:{str(source.get('role') or 'source')}"
            for channel in source_channels:
                add(
                    logo_registry.channel_identity(channel),
                    str(channel.get("tvg_logo", "") or ""),
                    source_kind,
                )
    else:
        for channel in core.channels:
            add(
                logo_registry.channel_identity(channel),
                str(channel.get("tvg_logo", "") or ""),
                "provider",
            )

    # These are URLs already stored from normal schedule/catalog work. Reading
    # them from SQLite does not spend API-SPORTS quota.
    try:
        for item in sports.catalog_payload(core.DB_PATH, scope_type="team"):
            add(
                logo_registry.team_identity(item.get("id")),
                str(item.get("logo_url", "") or ""),
                str(item.get("source", "") or "sports-catalog"),
            )
    except Exception:
        pass
    return grouped


def _cache_warmup_url(url: str, identities: list[tuple[str, str]]) -> str:
    """Populate one unique URL, then point all observed identities at its bytes."""
    LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = _digest_for_url(url)
    data_path, meta_path = _cache_paths_from_digest(digest)
    cached = _read_cached(data_path, meta_path)
    if cached:
        content_type = cached[1]
        outcome = "cached"
    else:
        payload, content_type = _fetch_logo(url)
        core.atomic_write_bytes(data_path, payload)
        core.atomic_write_text(
            meta_path,
            json.dumps({"content_type": content_type, "source": url}, sort_keys=True),
        )
        outcome = "downloaded"

    for identity_key, source_kind in identities:
        logo_registry.record_success(
            core.DB_PATH,
            identity_key,
            source_url=url,
            source_kind=source_kind,
            cache_digest=digest,
            content_type=content_type,
        )
    return outcome


def warm_known_logos() -> dict:
    """Eagerly cache all known provider/team icons after an explicit manual update.

    The candidate list comes only from provider caches and the local sports
    catalog. It deliberately does not refresh or query any sports schedule API.
    """
    if not _icon_update_run_lock.acquire(blocking=False):
        return icon_update_payload()
    try:
        started_at = _now_iso()
        try:
            grouped = _warmup_candidates()
        except Exception as exc:
            return _set_icon_update_state(
                requested=True,
                running=False,
                status="failed",
                stage="Icon update",
                detail=f"Could not build icon list ({type(exc).__name__}).",
                finished_at=_now_iso(),
            )

        identities = sum(len(items) for items in grouped.values())
        _set_icon_update_state(
            requested=True,
            running=True,
            status="running",
            stage="Icon update",
            detail="Caching known provider and sports icons.",
            total=len(grouped),
            processed=0,
            downloaded=0,
            cached=0,
            failed=0,
            identities=identities,
            started_at=started_at,
            finished_at=None,
        )
        downloaded = 0
        cached_count = 0
        failed = 0
        processed = 0
        for url, url_identities in grouped.items():
            try:
                outcome = _cache_warmup_url(url, url_identities)
                if outcome == "downloaded":
                    downloaded += 1
                else:
                    cached_count += 1
            except Exception:
                failed += 1
                for identity_key, source_kind in url_identities:
                    try:
                        logo_registry.record_failure(
                            core.DB_PATH,
                            identity_key,
                            source_url=url,
                            source_kind=source_kind,
                        )
                    except Exception:
                        pass
            processed += 1
            host = urllib.parse.urlsplit(url).hostname or "logo host"
            _set_icon_update_state(
                processed=processed,
                downloaded=downloaded,
                cached=cached_count,
                failed=failed,
                detail=f"{processed:,}/{len(grouped):,} unique icons checked • {host}",
            )

        detail = (
            f"Checked {processed:,} unique icons: {downloaded:,} downloaded, "
            f"{cached_count:,} already cached, {failed:,} failed."
        )
        return _set_icon_update_state(
            running=False,
            status="complete",
            stage="Icon update",
            detail=detail,
            processed=processed,
            downloaded=downloaded,
            cached=cached_count,
            failed=failed,
            finished_at=_now_iso(),
        )
    finally:
        _icon_update_run_lock.release()


def register_image_routes(app):
    @app.get("/api/logo")
    def api_logo():
        _observe_known_candidates_if_changed()

        url = str(request.args.get("url", "") or "").strip()
        identity_key = logo_registry.normalize_identity(request.args.get("key", ""))
        source_kind = str(request.args.get("source", "") or "").strip()[:80]
        registry_row = logo_registry.lookup(core.DB_PATH, identity_key) if identity_key else None

        if not url:
            registered = _read_registered(identity_key)
            if registered:
                return _serve_logo(registered[0], registered[1], "registry")
            return Response("Logo not found.\n", status=404, content_type="text/plain; charset=utf-8")

        parsed = urllib.parse.urlsplit(url)
        if not url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return Response("Logo not found.\n", status=404, content_type="text/plain; charset=utf-8")

        known_url = url in _known_logo_urls()
        if not known_url and registry_row:
            known_url = str(registry_row.get("source_url", "") or "") == url
        if not known_url:
            return Response("Logo not found.\n", status=404, content_type="text/plain; charset=utf-8")

        if identity_key:
            logo_registry.observe(core.DB_PATH, identity_key, url, source_kind)

        LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        digest = _digest_for_url(url)
        data_path, meta_path = _cache_paths_from_digest(digest)
        cached = _read_cached(data_path, meta_path)
        if cached and time.time() - data_path.stat().st_mtime <= LOGO_CACHE_TTL_SECONDS:
            if identity_key:
                logo_registry.record_success(
                    core.DB_PATH,
                    identity_key,
                    source_url=url,
                    source_kind=source_kind,
                    cache_digest=digest,
                    content_type=cached[1],
                )
            return _serve_logo(cached[0], cached[1], "hit")

        try:
            payload, content_type = _fetch_logo(url)
            core.atomic_write_bytes(data_path, payload)
            core.atomic_write_text(
                meta_path,
                json.dumps({"content_type": content_type, "source": url}, sort_keys=True),
            )
            if identity_key:
                logo_registry.record_success(
                    core.DB_PATH,
                    identity_key,
                    source_url=url,
                    source_kind=source_kind,
                    cache_digest=digest,
                    content_type=content_type,
                )
            return _serve_logo(payload, content_type, "refresh")
        except Exception:
            if identity_key:
                logo_registry.record_failure(
                    core.DB_PATH,
                    identity_key,
                    source_url=url,
                    source_kind=source_kind,
                )
            if cached:
                return _serve_logo(cached[0], cached[1], "stale")
            registered = _read_registered(identity_key)
            if registered:
                return _serve_logo(registered[0], registered[1], "registry-stale")
            return Response("Logo unavailable.\n", status=502, content_type="text/plain; charset=utf-8")
