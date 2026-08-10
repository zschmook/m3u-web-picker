from __future__ import annotations

"""Sports Automation compatibility facade.

The pre-refactor implementation remains in the repository root as ``sports.py``
while behavior is moved into focused modules.  Executing that implementation in
this package namespace preserves the long-standing ``sports.<name>`` surface —
including tests and callers that monkey-patch private helpers — while individual
concerns below replace the legacy definitions one at a time.

Once every concern has moved, the compatibility bootstrap can be deleted without
changing callers.
"""

from pathlib import Path

_LEGACY_SOURCE = Path(__file__).resolve().parent.parent / "sports.py"
exec(compile(_LEGACY_SOURCE.read_text(encoding="utf-8"), str(_LEGACY_SOURCE), "exec"), globals(), globals())


def _install(module, names: tuple[str, ...]) -> None:
    for name in names:
        globals()[name] = getattr(module, name)


from . import storage as _storage  # noqa: E402
from . import numbering as _numbering  # noqa: E402
from . import scheduling as _scheduling  # noqa: E402
from . import scan_state as _scan_state  # noqa: E402
from . import catalog as _catalog  # noqa: E402

_install(
    _storage,
    (
        "connect",
        "_canonical_refresh_time",
        "_refresh_time_parts",
        "init_db",
        "get_settings",
        "_disabled_at_from_conn",
        "disabled_cache_status",
        "purge_expired_disabled_cache",
        "clear_generated_channels",
        "update_settings",
    ),
)
globals()["_connect"] = _storage.connect

_install(
    _numbering,
    (
        "_classification_id",
        "_classification_label",
        "_block_index_map",
        "assigned_channel_number",
        "effective_start_channel",
        "numbering_plan",
    ),
)
_install(
    _scheduling,
    (
        "_sports_day",
        "_target_window",
        "_parse_scheduled_datetime",
        "_interval_anchor_at",
        "next_update_at",
        "should_run_scheduled",
    ),
)
_install(
    _scan_state,
    (
        "_record_scan",
        "begin_scan_state",
        "update_scan_stage",
        "finish_scan_state",
        "scan_state",
        "recover_interrupted_scan",
        "record_scan_cancelled",
        "record_scan_failure",
        "last_scan",
    ),
)

_install(
    _catalog,
    (
        "_catalog_rows",
        "catalog_payload",
        "_upsert_catalog_item",
        "_team_feed_identity",
        "_known_mlb_aliases",
        "discover_catalog_from_channels",
        "get_rules",
        "add_rules",
        "add_rule",
        "update_rule",
        "delete_rule",
    ),
)
