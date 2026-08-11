from __future__ import annotations

import threading


class RemoteSessionRegistry:
    """Track relay tokens by stable remote-device identity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, str], dict] = {}

    def replace(self, kind: str, device_key: str, session: dict) -> dict | None:
        key = (str(kind), str(device_key))
        with self._lock:
            previous = self._sessions.get(key)
            self._sessions[key] = dict(session)
            return dict(previous) if previous else None

    def pop(self, kind: str, device_key: str) -> dict | None:
        with self._lock:
            value = self._sessions.pop((str(kind), str(device_key)), None)
            return dict(value) if value else None

    def find_by_host(self, kind: str, host: str) -> tuple[str, dict] | None:
        wanted = str(host)
        with self._lock:
            for (item_kind, device_key), session in self._sessions.items():
                if item_kind == kind and str(session.get("host", "")) == wanted:
                    return device_key, dict(session)
        return None

    def find_by_token(self, kind: str, token: str) -> tuple[str, dict] | None:
        wanted = str(token)
        with self._lock:
            for (item_kind, device_key), session in self._sessions.items():
                if item_kind == kind and str(session.get("token", "")) == wanted:
                    return device_key, dict(session)
        return None

    def snapshot(self, kind: str | None = None) -> list[dict]:
        with self._lock:
            rows = [
                {"kind": item_kind, "device_key": device_key, **dict(session)}
                for (item_kind, device_key), session in self._sessions.items()
                if kind is None or item_kind == kind
            ]
        return rows


REMOTE_SESSIONS = RemoteSessionRegistry()
