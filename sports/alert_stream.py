from __future__ import annotations

import sys

from . import alert_stream_base as _base


# Preserve the old renderer verbatim so non-Phillies alerts keep the existing
# compact card + poof behavior.  The shim swaps only the public render hook.
if not hasattr(_base, "_standard_render_alert"):
    _base._standard_render_alert = _base.render_alert

from .phillies_alert import render_alert as _render_alert

_base.render_alert = _render_alert
render_alert = _render_alert

# Keep alert_stream as the original module object so callers/tests that patch
# its private helpers (generated, _team, _score_row, etc.) keep working exactly
# as before.
sys.modules[__name__] = _base
