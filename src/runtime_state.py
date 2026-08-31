from __future__ import annotations

from dataclasses import dataclass, field
import threading


@dataclass
class RuntimeState:
    """Process-local synchronization and transient progress state.

    Persisted configuration remains in core/config/SQLite. This object owns only
    in-memory runtime coordination that should never be serialized directly.
    """

    state_lock: threading.RLock = field(default_factory=threading.RLock)
    scan_lock: threading.Lock = field(default_factory=threading.Lock)
    scan_cancel_event: threading.Event = field(default_factory=threading.Event)
    provider_progress_lock: threading.Lock = field(default_factory=threading.Lock)
    provider_progress: dict = field(
        default_factory=lambda: {
            "active": False,
            "stage": "Idle",
            "detail": "",
            "channel_count": None,
            "started_at": None,
            "updated_at": None,
            "status": "idle",
        }
    )
    master_update: dict = field(
        default_factory=lambda: {
            "running": False,
            "started_at": None,
            "trigger": None,
            "started_monotonic": None,
        }
    )


RUNTIME_STATE = RuntimeState()
