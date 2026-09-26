"""Opaque, short-lived playback tickets for restarting Plex movies."""

from __future__ import annotations

import re
import secrets
import threading
import time
from urllib.parse import urljoin, urlsplit


TICKET_TTL_SECONDS = 12 * 60 * 60
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")
_PART_RE = re.compile(r"^/library/parts/[A-Za-z0-9._~%/-]+$")
_LOCK = threading.RLock()
_TICKETS: dict[str, dict] = {}
_SOURCES: dict[tuple[str, str, str], str] = {}


def _purge(now: float) -> None:
    expired = [ticket for ticket, row in _TICKETS.items() if float(row["expires_at"]) <= now]
    for ticket in expired:
        row = _TICKETS.pop(ticket)
        _SOURCES.pop((row["server"], row["part"], row["token"]), None)


def issue_plex(server: str, token: str, part: str, *, title: str = "", now: float | None = None) -> str:
    """Return an opaque browser URL without exposing Plex credentials or paths."""
    clock = time.time() if now is None else float(now)
    server = str(server or "").strip().rstrip("/")
    token = str(token or "").strip()
    part = str(part or "").strip()
    parsed = urlsplit(server)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Plex server address is invalid.")
    if parsed.query or parsed.fragment:
        raise ValueError("Plex server address must not contain a query or fragment.")
    if not token or "\r" in token or "\n" in token:
        raise ValueError("Plex token is invalid.")
    if not _PART_RE.fullmatch(part) or ".." in part.split("/"):
        raise ValueError("Plex movie part is invalid.")

    key = (server, part, token)
    with _LOCK:
        _purge(clock)
        existing = _SOURCES.get(key)
        if existing and existing in _TICKETS:
            _TICKETS[existing]["expires_at"] = clock + TICKET_TTL_SECONDS
            if title:
                _TICKETS[existing]["title"] = str(title)
            return f"/guide/play/restart/{existing}"
        identity = secrets.token_urlsafe(24)
        _TICKETS[identity] = {
            "server": server,
            "part": part,
            "token": token,
            "title": str(title or "Movie"),
            "expires_at": clock + TICKET_TTL_SECONDS,
        }
        _SOURCES[key] = identity
        return f"/guide/play/restart/{identity}"


def resolve(identity: str, *, now: float | None = None) -> dict:
    """Resolve a ticket to a server-side FFmpeg input and private headers."""
    clock = time.time() if now is None else float(now)
    identity = str(identity or "").strip()
    if not _TOKEN_RE.fullmatch(identity):
        raise ValueError("Restart ticket is invalid.")
    with _LOCK:
        _purge(clock)
        row = _TICKETS.get(identity)
        if row is None:
            raise ValueError("Restart ticket has expired.")
        return {
            "target": urljoin(row["server"] + "/", row["part"].lstrip("/")),
            "input_headers": {"X-Plex-Token": row["token"]},
            "title": row["title"],
        }


def reset_for_tests() -> None:
    with _LOCK:
        _TICKETS.clear()
        _SOURCES.clear()
