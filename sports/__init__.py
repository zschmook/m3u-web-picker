from __future__ import annotations

"""Sports Automation compatibility facade.

The pre-refactor implementation remains in the repository root as ``sports.py``
while behavior is moved into focused modules. Executing that implementation in
this package namespace preserves the long-standing ``sports.<name>`` surface —
including tests and callers that monkey-patch private helpers — while individual
concerns below replace the legacy definitions one at a time.

Once every concern has moved, the compatibility bootstrap can be deleted without
changing callers.
"""

from pathlib import Path
from importlib import import_module

_LEGACY_SOURCE = Path(__file__).resolve().parent.parent / "sports.py"
exec(compile(_LEGACY_SOURCE.read_text(encoding="utf-8"), str(_LEGACY_SOURCE), "exec"), globals(), globals())


def _install(module, names: tuple[str, ...]) -> None:
    for name in names:
        globals()[name] = getattr(module, name)


_common = import_module(f"{__name__}.common")
_storage = import_module(f"{__name__}.storage")
_numbering = import_module(f"{__name__}.numbering")
_scheduling = import_module(f"{__name__}.scheduling")
_scan_state = import_module(f"{__name__}.scan_state")
_catalog = import_module(f"{__name__}.catalog")
_schedule_api = import_module(f"{__name__}.schedule_api")
_generated = import_module(f"{__name__}.generated")
_events = import_module(f"{__name__}.events")
_rules = import_module(f"{__name__}.rules")
_feeds = import_module(f"{__name__}.feeds")
_epg_io = import_module(f"{__name__}.epg_io")
_guide = import_module(f"{__name__}.guide")
_guide_validation = import_module(f"{__name__}.guide_validation")
_scan = import_module(f"{__name__}.scan")
_schedule_refresh = import_module(f"{__name__}.schedule_refresh")
_status = import_module(f"{__name__}.status")

_install(
    _common,
    (
        "MAX_MALFORMED_SAMPLES",
        "XMLTV_GENERATOR_NAME",
        "GUIDE_PREGAME_HOURS",
        "GUIDE_POSTGAME_HOURS",
        "SPORTS_DISABLED_CACHE_HOURS",
        "SPORTS_DISABLED_AT_KEY",
        "SPORTS_INTERVAL_ANCHOR_KEY",
        "SCHEDULE_MODES",
        "MIN_INTERVAL_HOURS",
        "MAX_INTERVAL_HOURS",
        "ESTIMATED_EVENT_HOURS",
        "MalformedSportsEntry",
        "ScanCancelled",
        "CancelCheck",
        "EVENT_END_GRACE",
        "EVENT_MERGE_TOLERANCE",
        "REPLAY_ATTACH_WINDOW",
        "LOGICAL_EVENT_DAY_ROLLOVER_HOUR",
        "MAX_ESTIMATED_EVENT_DURATION",
        "_raise_if_cancelled",
        "_new_scan_diagnostics",
        "_record_malformed_entry",
        "_malformed_count",
        "_log_malformed_summary",
        "_now_iso",
        "_slug",
        "_normalize",
        "_smart_team_name",
        "_json_load",
    ),
)
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
_install(
    _schedule_api,
    (
        "_schedule_api_secret",
        "_schedule_api_rule_league_id",
        "schedule_api_request_plan",
        "_schedule_api_dataset_season",
        "_schedule_api_cache_summary",
        "schedule_api_status",
        "update_schedule_api_config",
        "_schedule_api_required_dates",
        "_schedule_api_request_key",
        "_schedule_api_dataset_games_url",
        "_schedule_api_games_url",
        "_schedule_api_scheduled_start",
        "_schedule_api_game_fields",
        "_upsert_schedule_api_team",
        "_fetch_schedule_api_dataset_date",
        "_conference_catalog_map",
        "_match_ncaa_conference_id",
        "_refresh_ncaa_reference_metadata_if_needed",
        "_schedule_api_authoritative_leagues",
        "_filter_provider_events_by_authoritative_schedule",
        "schedule_api_events_for_window",
    ),
)
_install(
    _generated,
    (
        "_rewrite_extinf",
        "generated_stream_path",
        "_generated_raw",
        "_generated_tvg_id",
        "purge_stale_generated",
        "generated_rows",
        "generated_stream_target",
        "generated_channel_payloads",
    ),
)
_install(
    _events,
    (
        "_utc_instant",
        "_channel_text",
        "_league_matches",
        "_college_football_match",
        "_detect_league",
        "_detect_sport_tags",
        "_detect_sport",
        "_strip_provider_prefix",
        "_extract_event_datetime",
        "_team_catalog",
        "_build_team_lookup",
        "_find_team_id",
        "_infer_baseball_league",
        "_event_from_text",
        "_event_has_usable_timing",
        "_primary_event_end",
        "_event_end",
        "_event_overlaps_window",
        "_event_overlaps_replay_context",
        "_event_is_stale",
    ),
)
_install(
    _rules,
    (
        "_conference_team_map",
        "_conference_matches",
        "_build_rule_index",
        "_matching_rules",
        "_explicit_team_rules",
        "_select_controlling_rule",
    ),
)
_install(
    _feeds,
    (
        "_provider_priority",
        "_team_feed_index",
        "_team_feeds",
        "_feed_type",
        "_feed_label",
        "_preferred_feed_logo",
        "_build_feeds",
    ),
)
_install(
    _epg_io,
    (
        "derive_xmltv_url",
        "refresh_epg_cache",
        "download_xmltv_bytes",
        "_parse_xmltv_time",
        "_iterparse_xmltv",
    ),
)
_install(
    _guide,
    (
        "_xmltv_time",
        "_parse_iso_datetime",
        "_serialize_programme_record",
        "_serialize_epg_programme",
        "_parse_programme_record",
        "_epg_programme_from_item",
        "_event_duration",
        "_clean_feed_subtitle",
        "build_sports_xmltv",
        "build_combined_xmltv",
        "_write_prepared_epg_files",
        "rebuild_epg_exports",
    ),
)
_install(
    _guide_validation,
    (
        "_local_xml_name",
        "_xmltv_index",
        "_playlist_tvg_ids",
        "validate_guide_exports",
    ),
)
_install(_scan, ("scan_channels",))
_install(
    _schedule_refresh,
    ("refresh_schedule_api_if_due", "refresh_schedule_api_if_due_async"),
)
_install(_status, ("status_payload",))
