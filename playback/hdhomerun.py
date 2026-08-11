from __future__ import annotations

import threading
from dataclasses import dataclass

from settings import load_settings
from .hdhr_protocol import device_id_text


@dataclass(frozen=True)
class TunerLease:
    index: int


class TunerPool:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: set[int] = set()

    def _count(self) -> int:
        return max(1, int(load_settings().hdhr_tuner_count))

    def acquire(self, requested: int | None = None) -> TunerLease | None:
        count = self._count()
        with self._lock:
            if requested is not None:
                index = int(requested)
                if index < 0 or index >= count or index in self._active:
                    return None
                self._active.add(index)
                return TunerLease(index=index)

            for index in range(count):
                if index not in self._active:
                    self._active.add(index)
                    return TunerLease(index=index)
        return None

    def release(self, lease: TunerLease) -> None:
        with self._lock:
            self._active.discard(int(lease.index))

    def status(self) -> dict:
        with self._lock:
            active = sorted(self._active)
        count = self._count()
        return {"tuner_count": count, "active": active, "available": max(0, count - len(active))}


TUNERS = TunerPool()


def device_metadata(base_url: str) -> dict:
    settings = load_settings()
    base = str(base_url or "").rstrip("/")
    payload = {
        "FriendlyName": settings.hdhr_friendly_name,
        "ModelNumber": settings.hdhr_model_number,
        "FirmwareName": "hdhomerun5_atsc",
        "FirmwareVersion": "20260810",
        "DeviceID": device_id_text(settings.hdhr_device_id),
        "BaseURL": base,
        "LineupURL": f"{base}/lineup.json",
        "TunerCount": max(1, int(settings.hdhr_tuner_count)),
    }
    if settings.hdhr_device_auth:
        payload["DeviceAuth"] = settings.hdhr_device_auth
    return payload
