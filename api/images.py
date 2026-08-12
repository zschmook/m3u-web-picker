from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Response, request

import core
import sports


LOGO_CACHE_DIR = core.DATA_DIR / "logo_cache"
LOGO_CACHE_TTL_SECONDS = 24 * 60 * 60
LOGO_BROWSER_MAX_AGE_SECONDS = 6 * 60 * 60
MAX_LOGO_BYTES = 2 * 1024 * 1024


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


def _cache_paths(url: str) -> tuple[Path, Path]:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return LOGO_CACHE_DIR / f"{digest}.bin", LOGO_CACHE_DIR / f"{digest}.json"


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


def register_image_routes(app):
    @app.get("/api/logo")
    def api_logo():
        url = str(request.args.get("url", "") or "").strip()
        parsed = urllib.parse.urlsplit(url)
        if not url or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return Response("Logo not found.\n", status=404, content_type="text/plain; charset=utf-8")
        if url not in _known_logo_urls():
            return Response("Logo not found.\n", status=404, content_type="text/plain; charset=utf-8")

        LOGO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data_path, meta_path = _cache_paths(url)
        cached = _read_cached(data_path, meta_path)
        if cached and time.time() - data_path.stat().st_mtime <= LOGO_CACHE_TTL_SECONDS:
            return _serve_logo(cached[0], cached[1], "hit")

        try:
            payload, content_type = _fetch_logo(url)
            core.atomic_write_bytes(data_path, payload)
            core.atomic_write_text(
                meta_path,
                json.dumps({"content_type": content_type, "source": url}, sort_keys=True),
            )
            return _serve_logo(payload, content_type, "refresh")
        except Exception:
            if cached:
                return _serve_logo(cached[0], cached[1], "stale")
            return Response("Logo unavailable.\n", status=502, content_type="text/plain; charset=utf-8")
