from __future__ import annotations

import os
import threading
from typing import Callable

import setup_wizard


_LOCK = threading.Lock()
_MAIN_APP: Callable | None = None


def _main_app() -> Callable:
    global _MAIN_APP
    if _MAIN_APP is not None:
        return _MAIN_APP
    with _LOCK:
        if _MAIN_APP is None:
            # Reapply here as well as in the Build endpoint so setup states
            # completed by an older preview-only build migrate cleanly.
            from setup_app import _apply_full_app_configuration

            _apply_full_app_configuration(setup_wizard.load_state())
            # Setup imports core with background work disabled. Enable it only
            # after the wizard has committed a complete configuration.
            os.environ["M3U_DISABLE_SCHEDULER"] = "false"
            from app import app as configured_app
            import core

            core.start_scheduler_once()
            _MAIN_APP = configured_app
    return _MAIN_APP


def application(environ, start_response):
    path = str(environ.get("PATH_INFO", "") or "")
    setup_request = path == "/setup" or path.startswith("/api/setup/")
    if setup_request or not setup_wizard.load_state().get("completed"):
        from setup_app import app as setup_app

        if path == "/setup":
            environ = dict(environ)
            environ["PATH_INFO"] = "/"
        return setup_app(environ, start_response)
    return _main_app()(environ, start_response)
