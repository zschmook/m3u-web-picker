from __future__ import annotations

"""Sports Automation public facade.

Implementation concerns live in focused ``sports.*`` modules while this package
preserves the historical ``import sports`` surface used by the application and
its characterization tests.
"""

from contextlib import closing
from importlib import import_module

from sports_taxonomy import *  # noqa: F401,F403


def _install(module, names: tuple[str, ...]) -> None:
    for name in names:
        globals()[name] = getattr(module, name)


# Bootstrap primitives first. Downstream modules intentionally call back through
# this facade so tests can continue monkey-patching ``sports.<helper>``.
_common = import_module(f"{__name__}.common")
_install(
    _common,
    (
        "MAX_MALFORMED_SAMPLES", "XMLTV_GENERATOR_NAME", "GUIDE_PREGAME_HOURS",
        "GUIDE_POSTGAME_HOURS", "SPORTS_DISABLED_CACHE_HOURS", "SPORTS_DISABLED_AT_KEY",
        "SPORTS_INTERVAL_ANCHOR_KEY", "SCHEDULE_MODES", "MIN_INTERVAL_HOURS",
        "MAX_INTERVAL_HOURS", "ESTIMATED_EVENT_HOURS", "MalformedSportsEntry",
        "ScanCancelled", "CancelCheck", "EVENT_END_GRACE", "EVENT_MERGE_TOLERANCE",
        "REPLAY_ATTACH_WINDOW", "LOGICAL_EVENT_DAY_ROLLOVER_HOUR",
        "MAX_ESTIMATED_EVENT_DURATION", "_raise_if_cancelled", "_new_scan_diagnostics",
        "_record_malformed_entry", "_malformed_count", "_log_malformed_summary",
        "_now_iso", "_slug", "_normalize", "_smart_team_name", "_json_load",
        "_is_sd_channel", "_clock_text", "_schedule_text",
    ),
)

_storage = import_module(f"{__name__}.storage")
_install(
    _storage,
    (
        "connect", "_canonical_refresh_time", "_refresh_time_parts", "init_db",
        "get_settings", "_disabled_at_from_conn", "disabled_cache_status",
        "purge_expired_disabled_cache", "clear_generated_channels", "update_settings",
    ),
)
globals()["_connect"] = _storage.connect

_numbering = import_module(f"{__name__}.numbering")
_install(_numbering, ("_classification_id", "_classification_label", "_block_index_map", "assigned_channel_number", "effective_start_channel", "numbering_plan"))

_scheduling = import_module(f"{__name__}.scheduling")
_install(_scheduling, ("_sports_day", "_target_window", "_parse_scheduled_datetime", "_interval_anchor_at", "next_update_at", "should_run_scheduled"))

_scan_state = import_module(f"{__name__}.scan_state")
_install(_scan_state, ("_record_scan", "begin_scan_state", "update_scan_stage", "finish_scan_state", "scan_state", "recover_interrupted_scan", "record_scan_cancelled", "record_scan_failure", "last_scan"))

_catalog = import_module(f"{__name__}.catalog")
_install(_catalog, ("_catalog_rows", "catalog_payload", "_upsert_catalog_item", "_team_feed_identity", "_known_mlb_aliases", "discover_catalog_from_channels", "get_rules", "add_rules", "add_rule", "update_rule", "delete_rule"))

_schedule_api = import_module(f"{__name__}.schedule_api")
_install(
    _schedule_api,
    (
        "_schedule_api_secret", "_schedule_api_rule_league_id", "schedule_api_request_plan",
        "_schedule_api_dataset_season", "_schedule_api_cache_summary", "schedule_api_status",
        "update_schedule_api_config", "_schedule_api_required_dates", "_schedule_api_request_key",
        "_schedule_api_dataset_games_url", "_schedule_api_games_url", "_schedule_api_scheduled_start",
        "_schedule_api_game_fields", "_upsert_schedule_api_team", "_fetch_schedule_api_dataset_date",
        "_conference_catalog_map", "_match_ncaa_conference_id", "_refresh_ncaa_reference_metadata_if_needed",
        "_schedule_api_authoritative_leagues", "_filter_provider_events_by_authoritative_schedule",
        "schedule_api_events_for_window",
    ),
)

# API-NFL/API-NCAA use a stricter request envelope than the already-working
# baseball adapter.  Install the compatibility fetcher last so callers keep the
# historical sports._fetch_schedule_api_dataset_date surface while American
# football requests avoid urllib's default User-Agent and a guessed season.
_schedule_api_requests = import_module(f"{__name__}.schedule_api_requests")
_install(_schedule_api_requests, ("_fetch_schedule_api_dataset_date",))

# NCAA conference membership comes from the same API-NFL host and must use the
# same clean request envelope as games.
_schedule_api_reference_requests = import_module(
    f"{__name__}.schedule_api_reference_requests"
)
_install(
    _schedule_api_reference_requests,
    ("_refresh_ncaa_reference_metadata_if_needed",),
)

_epg_io = import_module(f"{__name__}.epg_io")
_install(_epg_io, ("derive_xmltv_url", "refresh_epg_cache", "download_xmltv_bytes", "_parse_xmltv_time", "_iterparse_xmltv"))

_events = import_module(f"{__name__}.events")
_install(
    _events,
    (
        "_utc_instant", "_channel_text", "_league_matches", "_college_football_match",
        "_detect_league", "_detect_sport_tags", "_detect_sport", "_strip_provider_prefix",
        "_extract_event_datetime", "_team_catalog", "_build_team_lookup", "_find_team_id",
        "_infer_baseball_league", "_event_from_text", "_event_has_usable_timing",
        "_primary_event_end", "_event_end", "_event_overlaps_window",
        "_event_overlaps_replay_context", "_event_is_stale",
    ),
)

_event_sources = import_module(f"{__name__}.event_sources")
_install(_event_sources, ("_m3u_events", "_epg_events", "_previous_generated_event_anchors"))

_event_merge = import_module(f"{__name__}.event_merge")
_install(
    _event_merge,
    (
        "_timing_rank", "_epg_programme_quality", "_adopt_event_timing", "_merge_event_records",
        "_timed_events_are_same_slot", "_event_programme", "_event_is_live_airing",
        "_event_is_replay_airing", "_schedule_api_candidate_text", "_schedule_api_supporting_content",
        "_schedule_api_candidate_duration", "_schedule_api_live_candidate_score",
        "_schedule_api_provider_clusters", "_merge_schedule_api_group", "_programme_identity",
        "_append_replay_airing", "_canonical_replay_anchor_end", "_is_later_airing_of",
        "_event_current_at_scan", "_event_has_embedded_anchor", "_nearest_replay_anchor",
        "_assign_merged_event_keys", "_logical_broadcast_day", "_cluster_is_history",
        "_bucket_has_schedule_anchor", "_canonical_bucket_anchor", "_is_overnight_repeat",
        "_schedule_api_anchor_events", "_apply_schedule_api_identity", "_merge_events",
    ),
)

_rules = import_module(f"{__name__}.rules")
_install(_rules, ("_conference_team_map", "_conference_matches", "_build_rule_index", "_matching_rules", "_explicit_team_rules", "_select_controlling_rule"))

_feeds = import_module(f"{__name__}.feeds")
_install(_feeds, ("_provider_priority", "_team_feed_index", "_team_feeds", "_feed_type", "_feed_label", "_preferred_feed_logo", "_build_feeds"))

# Guide helpers are installed before generated persistence because persisted rows
# deserialize programme timestamps through the guide serializer helpers.
_guide = import_module(f"{__name__}.guide")
_install(_guide, ("_xmltv_time", "_parse_iso_datetime", "_serialize_programme_record", "_serialize_epg_programme", "_parse_programme_record", "_epg_programme_from_item", "_event_duration", "_clean_feed_subtitle", "build_sports_xmltv", "build_combined_xmltv", "_write_prepared_epg_files", "rebuild_epg_exports"))

_generated = import_module(f"{__name__}.generated")
_install(_generated, ("_rewrite_extinf", "generated_stream_path", "_generated_raw", "_generated_tvg_id", "purge_stale_generated", "generated_rows", "generated_stream_target", "generated_channel_payloads"))

_guide_validation = import_module(f"{__name__}.guide_validation")
_install(_guide_validation, ("_local_xml_name", "_xmltv_index", "_playlist_tvg_ids", "validate_guide_exports"))

_schedule_refresh = import_module(f"{__name__}.schedule_refresh")
_install(
    _schedule_refresh,
    (
        "refresh_schedule_api_if_due",
        "refresh_schedule_api_if_due_async",
        "schedule_api_refresh_health",
    ),
)

_scan = import_module(f"{__name__}.scan")
_install(_scan, ("scan_channels",))

_status = import_module(f"{__name__}.status")
_install(_status, ("status_payload", "schedule_api_status_payload"))
