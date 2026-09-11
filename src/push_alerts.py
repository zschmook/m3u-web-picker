from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlparse

from settings import load_settings


_KEY_LOCK = threading.Lock()
_ALLOWED_PUSH_HOST_SUFFIXES = (
    "googleapis.com",
    "push.services.mozilla.com",
    "web.push.apple.com",
    "notify.windows.com",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _private_key_path() -> Path:
    return load_settings().data_dir / "web-push-vapid-private.pem"


def _validated_subscription(payload: dict) -> dict[str, object]:
    endpoint = str(payload.get("endpoint") or "").strip()
    keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    parsed = urlparse(endpoint)
    host = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or len(endpoint) > 4096:
        raise ValueError("The browser returned an invalid push endpoint.")
    if not any(host == suffix or host.endswith(f".{suffix}") for suffix in _ALLOWED_PUSH_HOST_SUFFIXES):
        raise ValueError("The browser returned an unsupported push service.")
    if not (20 <= len(p256dh) <= 512 and 8 <= len(auth) <= 512):
        raise ValueError("The browser returned incomplete notification keys.")
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def _load_or_create_private_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    path = _private_key_path()
    with _KEY_LOCK:
        if path.exists():
            return serialization.load_pem_private_key(path.read_bytes(), password=None)
        path.parent.mkdir(parents=True, exist_ok=True)
        private_key = ec.generate_private_key(ec.SECP256R1())
        encoded = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(encoded)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        return private_key


def public_key() -> str:
    from cryptography.hazmat.primitives import serialization

    raw = _load_or_create_private_key().public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def save(db_path: Path | str, payload: dict, *, user_agent: str = "") -> dict[str, object]:
    import database

    subscription = _validated_subscription(payload)
    endpoint = str(subscription["endpoint"])
    keys = subscription["keys"]
    now = _now()
    with database.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO web_push_subscriptions
                (endpoint, p256dh, auth, user_agent, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                p256dh = excluded.p256dh,
                auth = excluded.auth,
                user_agent = excluded.user_agent,
                updated_at = excluded.updated_at
            """,
            (
                endpoint,
                str(keys["p256dh"]),
                str(keys["auth"]),
                str(user_agent or "")[:500],
                now,
                now,
            ),
        )
    return subscription


def remove(db_path: Path | str, endpoint: str) -> bool:
    import database

    with database.connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM web_push_subscriptions WHERE endpoint = ?",
            (str(endpoint or "").strip(),),
        )
    return bool(cursor.rowcount)


def count(db_path: Path | str) -> int:
    import database

    with database.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM web_push_subscriptions").fetchone()
    return int(row[0] if row else 0)


def _saved(db_path: Path | str, endpoint: str) -> dict[str, object]:
    import database

    with database.connect(db_path) as conn:
        row = conn.execute(
            "SELECT endpoint, p256dh, auth FROM web_push_subscriptions WHERE endpoint = ?",
            (str(endpoint or "").strip(),),
        ).fetchone()
    if row is None:
        raise ValueError("This phone is not subscribed to alerts.")
    return {
        "endpoint": str(row[0]),
        "keys": {"p256dh": str(row[1]), "auth": str(row[2])},
    }


def send(
    db_path: Path | str,
    endpoint: str,
    payload: dict,
    *,
    ttl: int = 300,
) -> None:
    from pywebpush import WebPushException, webpush

    subscription = _saved(db_path, endpoint)
    settings = load_settings()
    configured_url = str(settings.remote_url or "").strip()
    parsed = urlparse(configured_url)
    subject = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme == "https" and parsed.netloc else "https://localhost"
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps(payload, separators=(",", ":")),
            vapid_private_key=str(_private_key_path()),
            vapid_claims={"sub": subject},
            ttl=max(0, min(int(ttl), 86400)),
            timeout=10,
        )
    except WebPushException as exc:
        status = int(getattr(exc, "status_code", 0) or 0)
        if status in {404, 410}:
            remove(db_path, str(subscription["endpoint"]))
        raise RuntimeError(f"The phone's push service rejected the alert ({status or 'network error'}).") from exc
