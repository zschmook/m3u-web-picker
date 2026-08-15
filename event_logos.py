from __future__ import annotations

import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

import logo_registry
from settings import load_settings


# Matchup images are intentionally rendered larger than the UI's ~35-40 px
# footprint. Jellyfin and browsers can scale this down cleanly without turning
# a small source raster into a fuzzy postage stamp.
TEAM_BOX_PX = 96
SEPARATOR_WIDTH_PX = 32
GAP_PX = 6
EVENT_LOGO_RETENTION_DAYS = 14
MAX_LOGO_BYTES = 2 * 1024 * 1024
_EVENT_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_last_purge_monotonic = 0.0


def _paths() -> tuple[Path, Path, Path]:
    data_dir = load_settings().data_dir
    logo_cache = data_dir / "logo_cache"
    event_dir = logo_cache / "events"
    db_path = data_dir / "m3u_picker.db"
    logo_cache.mkdir(parents=True, exist_ok=True)
    event_dir.mkdir(parents=True, exist_ok=True)
    return logo_cache, event_dir, db_path


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def event_digest(event_key: object, away_team_id: object, home_team_id: object) -> str:
    raw = "\x1f".join(
        (
            str(event_key or "").strip(),
            str(away_team_id or "").strip().casefold(),
            str(home_team_id or "").strip().casefold(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _public_event_logo_url(digest: str) -> str:
    settings = load_settings()
    if not settings.lan_host:
        # A relative tvg-logo is not portable to Jellyfin running elsewhere, so
        # callers should keep the ordinary provider/team logo when LAN host is
        # not configured.
        return ""
    return f"http://{settings.lan_host}:{settings.external_port}/api/event-logo/{digest}.png"


def _manifest_path(digest: str) -> Path:
    _logo_cache, event_dir, _db_path = _paths()
    return event_dir / f"{digest}.json"


def _rendered_path(digest: str) -> Path:
    _logo_cache, event_dir, _db_path = _paths()
    return event_dir / f"{digest}.png"


def _signature_path(digest: str) -> Path:
    _logo_cache, event_dir, _db_path = _paths()
    return event_dir / f"{digest}.sig"


def _clean_http_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url[:4096]


def _purge_old_event_logos() -> None:
    global _last_purge_monotonic
    now_mono = time.monotonic()
    if now_mono - _last_purge_monotonic < 60 * 60:
        return
    _last_purge_monotonic = now_mono
    _logo_cache, event_dir, _db_path = _paths()
    cutoff = time.time() - EVENT_LOGO_RETENTION_DAYS * 24 * 60 * 60
    try:
        entries = list(event_dir.iterdir())
    except OSError:
        return
    for path in entries:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def register_matchup_logo(
    *,
    event_key: object,
    away_team_id: object,
    away_team_name: object,
    away_logo_url: object,
    home_team_id: object,
    home_team_name: object,
    home_logo_url: object,
    away_fallback_logo_url: object = "",
    home_fallback_logo_url: object = "",
    event_end: datetime | None = None,
) -> str:
    """Persist a tiny reconstructable matchup manifest and return its LAN URL.

    The manifest is ephemeral. Team artwork remains owned by logo_registry and
    the shared logo cache. The composite PNG can be deleted at any time and is
    regenerated on demand from those persistent team assets.

    Each team may carry a preferred and fallback upstream URL. Resolution first
    checks whether the exact current preferred URL is already cached, then tries
    that preferred source (ESPN when available), then provider/Xtream fallback.
    An older provider cache entry therefore cannot permanently block a newly
    resolved ESPN logo for the same stable team identity.
    """
    away_id = str(away_team_id or "").strip()
    home_id = str(home_team_id or "").strip()
    if not event_key or not away_id or not home_id:
        return ""

    digest = event_digest(event_key, away_id, home_id)
    public_url = _public_event_logo_url(digest)
    if not public_url:
        return ""

    expiry = event_end
    if isinstance(expiry, datetime):
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        expiry_text = (expiry.astimezone(timezone.utc) + timedelta(days=7)).isoformat(timespec="seconds")
    else:
        expiry_text = ""

    manifest = {
        "version": 2,
        "event_key": str(event_key or "")[:512],
        "away": {
            "team_id": away_id[:240],
            "name": str(away_team_name or "Away")[:240],
            "identity": logo_registry.team_identity(away_id),
            "source_url": _clean_http_url(away_logo_url),
            "fallback_url": _clean_http_url(away_fallback_logo_url),
        },
        "home": {
            "team_id": home_id[:240],
            "name": str(home_team_name or "Home")[:240],
            "identity": logo_registry.team_identity(home_id),
            "source_url": _clean_http_url(home_logo_url),
            "fallback_url": _clean_http_url(home_fallback_logo_url),
        },
        "expires_at": expiry_text,
    }
    _atomic_write_text(_manifest_path(digest), json.dumps(manifest, sort_keys=True))
    _purge_old_event_logos()
    return public_url


def _read_manifest(digest: str) -> dict | None:
    if not _EVENT_DIGEST_RE.fullmatch(str(digest or "").lower()):
        return None
    try:
        payload = json.loads(_manifest_path(digest).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    # Version 1 manifests remain readable across the upgrade; they simply do
    # not have an explicit provider fallback URL.
    if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
        return None
    return payload


def _sniff_image_type(payload: bytes, header_type: str = "") -> str:
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


def _fetch_logo(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (M3U Web Picker event logo cache)",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://www.espn.com/" if "espn" in urllib.parse.urlsplit(url).netloc.casefold() else "",
        },
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > MAX_LOGO_BYTES:
            raise ValueError("Logo is too large.")
        payload = response.read(MAX_LOGO_BYTES + 1)
        if len(payload) > MAX_LOGO_BYTES:
            raise ValueError("Logo is too large.")
        content_type = _sniff_image_type(payload, response.headers.get("Content-Type", ""))
        if not payload or not content_type:
            raise ValueError("Upstream response is not an image.")
        final_url = urllib.parse.urlsplit(response.geturl())
        if final_url.scheme not in {"http", "https"}:
            raise ValueError("Invalid logo redirect.")
        return payload, content_type


def _shared_cache_paths(source_url: str) -> tuple[Path, Path, str]:
    logo_cache, _event_dir, _db_path = _paths()
    digest = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    return logo_cache / f"{digest}.bin", logo_cache / f"{digest}.json", digest


def _cached_payload_for_digest(digest: str) -> tuple[bytes, str] | None:
    logo_cache, _event_dir, _db_path = _paths()
    data_path = logo_cache / f"{digest}.bin"
    meta_path = logo_cache / f"{digest}.json"
    try:
        payload = data_path.read_bytes()
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        content_type = str(metadata.get("content_type") or "")
    except (OSError, ValueError, TypeError):
        return None
    if payload and content_type.startswith("image/"):
        return payload, content_type
    return None


def _source_candidates(team: dict, row: dict | None) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(value: object, kind: str) -> None:
        url = _clean_http_url(value)
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append((url, kind))

    add(team.get("source_url"), "event-logo:preferred")
    add(team.get("fallback_url"), "event-logo:provider-fallback")
    if row:
        add(row.get("source_url"), "event-logo:registry-fallback")
    return candidates


def _registered_cache_for_current_preferred(
    row: dict | None,
    preferred_url: str,
) -> tuple[bytes | None, str]:
    if not row:
        return None, ""
    registered_digest = str(row.get("cache_digest") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", registered_digest):
        return None, ""

    # If there is a current preferred URL, only an identity cache entry created
    # from that exact URL gets to short-circuit resolution. This preserves the
    # cache-first behavior for ESPN without letting a stale provider hit outrank
    # a newly discovered ESPN source.
    if preferred_url:
        _data_path, _meta_path, preferred_digest = _shared_cache_paths(preferred_url)
        if registered_digest != preferred_digest:
            return None, ""

    cached = _cached_payload_for_digest(registered_digest)
    if not cached:
        return None, ""
    return cached[0], registered_digest


def _resolve_team_asset(team: dict) -> tuple[bytes | None, str]:
    _logo_cache, _event_dir, db_path = _paths()
    identity = logo_registry.normalize_identity(team.get("identity"))
    row = logo_registry.lookup(db_path, identity) if identity else None
    preferred_url = _clean_http_url(team.get("source_url"))

    cached_payload, cached_digest = _registered_cache_for_current_preferred(
        row,
        preferred_url,
    )
    if cached_payload:
        return cached_payload, cached_digest

    for source_url, source_kind in _source_candidates(team, row):
        data_path, meta_path, digest = _shared_cache_paths(source_url)
        try:
            payload = data_path.read_bytes()
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
            content_type = str(metadata.get("content_type") or "")
            if payload and content_type.startswith("image/"):
                if identity:
                    logo_registry.record_success(
                        db_path,
                        identity,
                        source_url=source_url,
                        source_kind=source_kind,
                        cache_digest=digest,
                        content_type=content_type,
                    )
                return payload, digest
        except (OSError, ValueError, TypeError):
            pass

        try:
            payload, content_type = _fetch_logo(source_url)
            _atomic_write_bytes(data_path, payload)
            _atomic_write_text(
                meta_path,
                json.dumps({"content_type": content_type, "source": source_url}, sort_keys=True),
            )
            if identity:
                logo_registry.record_success(
                    db_path,
                    identity,
                    source_url=source_url,
                    source_kind=source_kind,
                    cache_digest=digest,
                    content_type=content_type,
                )
            return payload, digest
        except Exception:
            if identity:
                try:
                    logo_registry.record_failure(
                        db_path,
                        identity,
                        source_url=source_url,
                        source_kind=source_kind,
                    )
                except Exception:
                    pass

    return None, ""


def _fallback_initials(label: str) -> str:
    words = [
        re.sub(r"[^A-Za-z0-9]", "", word)
        for word in str(label or "TV").strip().split()
    ]
    words = [word for word in words if word]
    if len(words) >= 2:
        return (words[0][0] + words[-1][0]).upper()
    if words:
        return words[0][:2].upper()
    return "TV"


def _fallback_tile(label: str) -> Image.Image:
    tile = Image.new("RGBA", (TEAM_BOX_PX, TEAM_BOX_PX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    inset = 8
    draw.rounded_rectangle(
        (inset, inset, TEAM_BOX_PX - inset, TEAM_BOX_PX - inset),
        radius=18,
        fill=(24, 35, 52, 235),
        outline=(119, 136, 160, 210),
        width=2,
    )
    text = _fallback_initials(label)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text(
        ((TEAM_BOX_PX - width) / 2, (TEAM_BOX_PX - height) / 2 - bbox[1]),
        text,
        font=font,
        fill=(235, 241, 248, 255),
    )
    return tile


def _normalized_tile(payload: bytes | None, label: str) -> Image.Image:
    if not payload:
        return _fallback_tile(label)
    try:
        with Image.open(io.BytesIO(payload)) as image:
            try:
                image.seek(0)
            except EOFError:
                pass
            source = image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError):
        return _fallback_tile(label)

    # Trim transparent source padding before scaling. This makes the *visual*
    # mark size consistent instead of merely making every outer image 96x96.
    alpha = source.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        source = source.crop(bbox)
    if source.width <= 0 or source.height <= 0:
        return _fallback_tile(label)

    inner = TEAM_BOX_PX - 8
    scale = min(inner / source.width, inner / source.height)
    width = max(1, int(round(source.width * scale)))
    height = max(1, int(round(source.height * scale)))
    source = source.resize((width, height), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (TEAM_BOX_PX, TEAM_BOX_PX), (0, 0, 0, 0))
    tile.alpha_composite(source, ((TEAM_BOX_PX - width) // 2, (TEAM_BOX_PX - height) // 2))
    return tile


def _compose(away: Image.Image, home: Image.Image) -> bytes:
    width = TEAM_BOX_PX * 2 + SEPARATOR_WIDTH_PX + GAP_PX * 2
    canvas = Image.new("RGBA", (width, TEAM_BOX_PX), (0, 0, 0, 0))
    canvas.alpha_composite(away, (0, 0))
    home_x = TEAM_BOX_PX + GAP_PX * 2 + SEPARATOR_WIDTH_PX
    canvas.alpha_composite(home, (home_x, 0))

    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:
        font = ImageFont.load_default()
    text = "@"
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    center_x = TEAM_BOX_PX + GAP_PX + SEPARATOR_WIDTH_PX / 2
    draw.text(
        (center_x - text_width / 2, (TEAM_BOX_PX - text_height) / 2 - bbox[1]),
        text,
        font=font,
        fill=(226, 232, 240, 255),
        stroke_width=1,
        stroke_fill=(15, 23, 42, 255),
    )
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render_event_logo(digest: str) -> tuple[bytes, str] | None:
    """Return a normalized ephemeral matchup PNG and cache-state label."""
    digest = str(digest or "").strip().lower()
    manifest = _read_manifest(digest)
    if not manifest:
        return None

    away = dict(manifest.get("away") or {})
    home = dict(manifest.get("home") or {})
    away_payload, away_digest = _resolve_team_asset(away)
    home_payload, home_digest = _resolve_team_asset(home)
    signature = hashlib.sha256(
        "\x1f".join(
            (
                "event-logo-v2",
                away_digest or "fallback:" + str(away.get("name") or ""),
                home_digest or "fallback:" + str(home.get("name") or ""),
            )
        ).encode("utf-8")
    ).hexdigest()

    rendered_path = _rendered_path(digest)
    signature_path = _signature_path(digest)
    try:
        if rendered_path.exists() and signature_path.read_text(encoding="utf-8").strip() == signature:
            payload = rendered_path.read_bytes()
            if payload:
                return payload, "hit"
    except OSError:
        pass

    payload = _compose(
        _normalized_tile(away_payload, str(away.get("name") or "Away")),
        _normalized_tile(home_payload, str(home.get("name") or "Home")),
    )
    _atomic_write_bytes(rendered_path, payload)
    _atomic_write_text(signature_path, signature)
    return payload, "generated"
