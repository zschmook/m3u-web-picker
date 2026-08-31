from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable
from zoneinfo import ZoneInfo

import sports as _s
from . import generated as _generated


@dataclass
class ScanContext:
    db_path: Path | str
    channels: list[dict]
    epg_path: Path | None
    provider_epg_sources: list[tuple[Path, list[dict]]] | None
    sports_epg_path: Path | None
    combined_epg_path: Path | None
    trigger: str
    scan_anchor: datetime
    started_at: str
    base_channel_ids: set[str] | None
    fallback_epg_paths: Iterable[Path] | None
    manual_channel_count: int
    cancel_check: _s.CancelCheck
    settings: dict
    target_date: str
    timings: dict[str, float] = field(default_factory=dict)
    pipeline_trace: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)
    team_lookup: dict = field(default_factory=dict)
    team_feed_map: dict[str, list[dict]] = field(default_factory=dict)
    team_feed_channel_ids: set[int] = field(default_factory=set)
    rule_index: dict = field(default_factory=dict)
    team_catalog: dict[str, dict] = field(default_factory=dict)
    epg_events: list[dict] = field(default_factory=list)
    m3u_events: list[dict] = field(default_factory=list)
    previous_anchors: list[dict] = field(default_factory=list)
    schedule_api_state: dict = field(default_factory=dict)
    authoritative_api_leagues: set[str] = field(default_factory=set)
    api_anchors: list[dict] = field(default_factory=list)
    provider_events: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    selected_events: list[dict] = field(default_factory=list)
    generated: list[dict] = field(default_factory=list)
    untimed_skipped: int = 0
    classification_ids: set[str] = field(default_factory=set)
    classification_blocks: dict[str, int] = field(default_factory=dict)
    event_positions: dict[str, int] = field(default_factory=dict)
    configured_start_number: int = 0
    start_number: int = 0
    block_size: int = 0
    generated_at: str = ""

    def timed(self, name: str, started: float) -> None:
        self.timings[name] = round(perf_counter() - started, 3)


def _prepare_context(ctx: ScanContext) -> None:
    if ctx.settings.get("exclude_sd"):
        ctx.channels = [
            channel for channel in ctx.channels if not _s._is_sd_channel(channel)
        ]
    rules = [rule for rule in _s.get_rules(ctx.db_path) if rule["enabled"]]
    ctx.diagnostics = _s._new_scan_diagnostics()

    started = perf_counter()
    ctx.team_lookup = _s._build_team_lookup(ctx.db_path)
    ctx.team_feed_map, ctx.team_feed_channel_ids = _s._team_feed_index(ctx.channels)
    ctx.rule_index = _s._build_rule_index(
        rules,
        _s._conference_team_map(ctx.db_path),
    )
    ctx.team_catalog = {
        item["id"]: item for item in ctx.team_lookup.get("teams", [])
    }
    ctx.timed("index_build", started)


def _collect_epg_events(ctx: ScanContext) -> list[dict]:
    output: list[dict] = []
    if ctx.provider_epg_sources is None:
        output.extend(
            _s._epg_events(
                ctx.db_path,
                ctx.epg_path,
                ctx.channels,
                ctx.settings,
                ctx.scan_anchor,
                ctx.diagnostics,
                ctx.cancel_check,
                team_lookup=ctx.team_lookup,
            )
        )
        return output

    for source_index, (source_epg_path, source_channels) in enumerate(
        ctx.provider_epg_sources
    ):
        if source_index % 5 == 0:
            _s._raise_if_cancelled(ctx.cancel_check)
        output.extend(
            _s._epg_events(
                ctx.db_path,
                source_epg_path,
                source_channels,
                ctx.settings,
                ctx.scan_anchor,
                ctx.diagnostics,
                ctx.cancel_check,
                team_lookup=ctx.team_lookup,
                source_priority=source_index,
            )
        )
    return output


def _collect_source_events(ctx: ScanContext) -> None:
    started = perf_counter()
    ctx.epg_events = _collect_epg_events(ctx)
    ctx.timed("epg_parse", started)

    started = perf_counter()
    ctx.m3u_events = _s._m3u_events(
        ctx.db_path,
        ctx.channels,
        ctx.settings,
        ctx.scan_anchor,
        ctx.diagnostics,
        ctx.cancel_check,
        team_lookup=ctx.team_lookup,
        team_feed_channel_ids=ctx.team_feed_channel_ids,
    )
    ctx.timed("m3u_parse", started)

    started = perf_counter()
    ctx.previous_anchors = _s._previous_generated_event_anchors(
        ctx.db_path,
        ctx.settings,
        ctx.scan_anchor,
        team_lookup=ctx.team_lookup,
    )
    ctx.timed("history_anchors", started)


def _reconcile_schedule_api(ctx: ScanContext) -> list[dict]:
    started = perf_counter()
    ctx.schedule_api_state = _s.schedule_api_status(ctx.db_path)
    ctx.authoritative_api_leagues = _s._schedule_api_authoritative_leagues(
        ctx.db_path,
        ctx.scan_anchor,
    )
    schedule_rows = _s.schedule_api_events_for_window(ctx.db_path, ctx.scan_anchor)
    ctx.api_anchors = _s._schedule_api_anchor_events(
        schedule_rows,
        ctx.settings,
        ctx.team_lookup,
    )
    ctx.provider_events = _s._apply_schedule_api_identity(
        [*ctx.previous_anchors, *ctx.m3u_events, *ctx.epg_events],
        ctx.api_anchors,
    )
    ctx.provider_events = _s._filter_provider_events_by_authoritative_schedule(
        ctx.provider_events,
        ctx.authoritative_api_leagues,
        include_replays=bool(ctx.settings.get("include_replays")),
    )
    active = [
        event
        for event in ctx.api_anchors
        if str(event.get("api_status_short") or "").upper()
        not in {"POST", "PST", "CANC", "ABD", "SUSP"}
    ]
    ctx.timed("schedule_mapping", started)
    return active


def _merge_and_filter_events(ctx: ScanContext, active_api_anchors: list[dict]) -> None:
    started = perf_counter()
    ctx.events = _s._merge_events(
        [*active_api_anchors, *ctx.provider_events],
        ctx.cancel_check,
        ctx.settings,
    )
    ctx.timed("logical_merge", started)

    started = perf_counter()
    window_start, window_end, _ = _s._target_window(ctx.scan_anchor, ctx.settings)
    untimed_events = [
        event for event in ctx.events if not _s._event_has_usable_timing(event)
    ]
    if ctx.settings.get("everything_mode"):
        relevant_untimed_events = untimed_events
    else:
        # Provider catalogs contain many permanent sports networks and feeds
        # whose names resemble events (F1 onboard cameras, wrestling networks,
        # soccer placeholders, and similar channels). They are still excluded
        # without schedule confirmation, but only an untimed candidate covered
        # by an enabled rule should appear in the update summary.
        relevant_untimed_events = [
            event
            for event in untimed_events
            if _s._matching_rules(event, ctx.rule_index)
        ]
    ctx.untimed_skipped = len(relevant_untimed_events)
    ctx.events = [
        event
        for event in ctx.events
        if _s._event_has_usable_timing(event)
        and _s._event_overlaps_window(event, window_start, window_end)
        and not _s._event_is_stale(event, ctx.scan_anchor)
    ]
    ctx.timed("window_filter", started)


def _select_events(ctx: ScanContext) -> None:
    _s._raise_if_cancelled(ctx.cancel_check)
    started = perf_counter()
    everything_mode = bool(ctx.settings.get("everything_mode"))
    selected = []
    for index, event in enumerate(ctx.events):
        if index % 100 == 0:
            _s._raise_if_cancelled(ctx.cancel_check)
        if everything_mode:
            event["matched_rule"] = {
                "id": 0,
                "scope_type": "sport",
                "scope_id": event.get("sport_id")
                or event.get("league_id")
                or "sports",
                "display_name": "Everything Mode",
                "feed_preference": "best",
                "enabled": 1,
            }
            event["matched_rules"] = [event["matched_rule"]]
            event["expanded_feeds"] = False
        else:
            matched = _s._matching_rules(event, ctx.rule_index)
            if not matched:
                continue
            controlling_rule, expanded_feeds = _s._select_controlling_rule(
                event,
                matched,
            )
            event["matched_rules"] = matched
            event["matched_rule"] = controlling_rule
            event["expanded_feeds"] = expanded_feeds
        selected.append(event)
    ctx.selected_events = selected
    ctx.timed("rule_matching", started)
    ctx.pipeline_trace.append("sports_scan_match")

    ctx.classification_ids = {
        _s._classification_id(event) for event in ctx.selected_events
    }
    ctx.classification_blocks = _s._block_index_map(ctx.classification_ids)
    ctx.selected_events.sort(
        key=lambda event: (
            ctx.classification_blocks[_s._classification_id(event)],
            event.get("start")
            or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            event.get("display_name", "").lower(),
        )
    )


def _build_generated_channels(ctx: ScanContext) -> None:
    ctx.configured_start_number = int(ctx.settings.get("start_channel", 1000))
    ctx.start_number = _s.effective_start_channel(
        ctx.configured_start_number,
        ctx.manual_channel_count,
    )
    ctx.block_size = int(ctx.settings.get("channels_per_event", 10))
    group_title = str(ctx.settings.get("group_title", "Sports Today"))
    positions: dict[str, int] = defaultdict(int)
    generated = []

    started = perf_counter()
    for event_index, event in enumerate(ctx.selected_events):
        if event_index % 50 == 0:
            _s._raise_if_cancelled(ctx.cancel_check)
        classification_id = _s._classification_id(event)
        classification_event_index = positions[classification_id]
        positions[classification_id] += 1
        feeds = _s._build_feeds(
            event,
            ctx.team_feed_map,
            event["matched_rule"],
            ctx.settings,
        )[: ctx.block_size]
        for feed_index, feed in enumerate(feeds):
            channel = feed["channel"]
            feed_type = feed["feed_type"]
            feed_label, subtitle = _s._feed_label(
                feed_type,
                event,
                feed.get("team_id", ""),
            )
            if event.get("start"):
                start_text = event["start"].astimezone(
                    ZoneInfo(str(ctx.settings.get("timezone", "America/New_York")))
                )
                start_text = _s._clock_text(start_text)
                subtitle = f"{subtitle} • {start_text}"
            display_name = (
                f"{_s._classification_label(classification_id)} • "
                f"{event['display_name']} — {feed_label}"
            )
            assigned = _s.assigned_channel_number(
                classification_id,
                classification_event_index,
                feed_index,
                start_channel=ctx.start_number,
                channels_per_event=ctx.block_size,
                block_index=ctx.classification_blocks[classification_id],
            )
            logo = _s._preferred_feed_logo(
                event,
                feed,
                channel,
                ctx.team_catalog,
            )
            source_channel_key = str(channel.get("url", "") or "")
            channel_key = (
                f"sports:{event['event_key']}:{feed_type}:{source_channel_key}"
            )
            event_start = event.get("start")
            event_end = _s._event_end(event)
            item = {
                "channel_key": channel_key,
                "source_channel_key": source_channel_key,
                "event_key": event["event_key"],
                "league_id": classification_id,
                "display_name": display_name,
                "subtitle": subtitle,
                "feed_type": feed_type,
                "assigned_number": assigned,
                "group_title": group_title,
                "url": source_channel_key,
                "playback_url": _s.generated_stream_path(assigned),
                "tvg_id": _s._generated_tvg_id(channel_key),
                "source_tvg_id": str(channel.get("tvg_id", "") or ""),
                "tvg_logo": logo,
                "event_title": event.get("display_name", ""),
                "event_start": event_start.isoformat() if event_start else None,
                "event_end": event_end.isoformat() if event_end else None,
                "is_replay": bool(event.get("is_replay")),
                "epg_programme": _s._serialize_epg_programme(event),
            }
            item["raw"] = _s._generated_raw(channel, item)
            generated.append(item)
    ctx.generated = generated
    ctx.event_positions = dict(positions)
    ctx.timed("feed_selection", started)
    ctx.pipeline_trace.append("channel_build")


def _publish_outputs(ctx: ScanContext) -> None:
    generated_at_dt = ctx.scan_anchor.astimezone()
    ctx.generated_at = generated_at_dt.isoformat(timespec="seconds")

    started = perf_counter()
    prepared_epg = _s._write_prepared_epg_files(
        ctx.generated,
        ctx.settings,
        base_epg_path=ctx.epg_path,
        base_channel_ids=ctx.base_channel_ids,
        fallback_epg_paths=ctx.fallback_epg_paths,
        sports_epg_path=ctx.sports_epg_path,
        combined_epg_path=ctx.combined_epg_path,
        generated_at=generated_at_dt,
        cancel_check=ctx.cancel_check,
    )
    ctx.timed("guide_generation", started)
    _s._raise_if_cancelled(ctx.cancel_check)

    started = perf_counter()
    _generated.publish_generated(
        ctx.db_path,
        ctx.generated,
        prepared_epg,
        ctx.generated_at,
    )
    ctx.timed("persist", started)
    ctx.pipeline_trace.append("epg_publish")


def _result_message(ctx: ScanContext) -> tuple[str, int]:
    malformed_count = _s._malformed_count(ctx.diagnostics)
    mode_text = " detected" if ctx.settings.get("everything_mode") else " matching"
    message = (
        f"Generated {len(ctx.generated)} channels for "
        f"{len(ctx.selected_events)}{mode_text} events."
    )
    if malformed_count:
        message += (
            f" Skipped {malformed_count} malformed provider "
            f"entr{'y' if malformed_count == 1 else 'ies'}."
        )
        _s._log_malformed_summary(ctx.diagnostics)
    if ctx.untimed_skipped:
        message += (
            f" Skipped {ctx.untimed_skipped} untimed provider event"
            f"{'s' if ctx.untimed_skipped != 1 else ''} without XMLTV schedule confirmation."
        )
    if (
        ctx.schedule_api_state.get("effective")
        and ctx.schedule_api_state.get("plan", {}).get("datasets")
        and not ctx.api_anchors
    ):
        if ctx.authoritative_api_leagues and not ctx.settings.get("include_replays"):
            message += (
                " Schedule API confirmed no current canonical events for the "
                "covered league window; legacy historical matches were suppressed."
            )
        else:
            message += (
                " Schedule API supplied no canonical events usable for this window; "
                "legacy matching was used where API coverage was unavailable."
            )
    return message, malformed_count


def _log_metrics(ctx: ScanContext) -> None:
    print(
        "Sports scan timings: "
        + ", ".join(
            f"{name}={seconds:.3f}s" for name, seconds in ctx.timings.items()
        )
        + f"; team_cache={ctx.team_lookup.get('cache_hits', 0)} hits/"
        f"{ctx.team_lookup.get('cache_misses', 0)} misses"
        + f"; provider_channels={len(ctx.channels)}; epg_events={len(ctx.epg_events)}; "
        f"m3u_events={len(ctx.m3u_events)}; history_anchors={len(ctx.previous_anchors)}; "
        f"schedule_api_events={len(ctx.api_anchors)}; logical_events={len(ctx.events)}; "
        f"selected_events={len(ctx.selected_events)}; generated_channels={len(ctx.generated)}"
    )


def _result_payload(ctx: ScanContext, message: str, malformed_count: int) -> dict:
    return {
        "ok": True,
        "count": len(ctx.generated),
        "events": len(ctx.selected_events),
        "generated_at": ctx.generated_at,
        "target_date": ctx.target_date,
        "message": message,
        "skipped_entries": malformed_count,
        "malformed_m3u": ctx.diagnostics.get("malformed_m3u", 0),
        "malformed_epg": ctx.diagnostics.get("malformed_epg", 0),
        "untimed_skipped": ctx.untimed_skipped,
        "guide_channels": len(ctx.generated),
        "everything_mode": bool(ctx.settings.get("everything_mode")),
        "timings": ctx.timings,
        "pipeline_trace": ctx.pipeline_trace,
        "scan_metrics": {
            "provider_channels": len(ctx.channels),
            "epg_events": len(ctx.epg_events),
            "m3u_events": len(ctx.m3u_events),
            "history_anchors": len(ctx.previous_anchors),
            "schedule_api_effective": bool(ctx.schedule_api_state.get("effective")),
            "schedule_api_authoritative_leagues": sorted(
                ctx.authoritative_api_leagues
            ),
            "schedule_api_events": len(ctx.api_anchors),
            "schedule_api_mapped_provider_events": sum(
                1 for event in ctx.provider_events if event.get("api_event_id")
            ),
            "logical_events": len(ctx.events),
            "selected_events": len(ctx.selected_events),
            "generated_channels": len(ctx.generated),
            "team_cache_hits": int(ctx.team_lookup.get("cache_hits", 0)),
            "team_cache_misses": int(ctx.team_lookup.get("cache_misses", 0)),
        },
        "numbering": {
            "configured_start_channel": ctx.configured_start_number,
            "effective_start_channel": ctx.start_number,
            "manual_channel_count": max(0, int(ctx.manual_channel_count)),
            "auto_shifted": ctx.start_number != ctx.configured_start_number,
            "league_block_size": _s.LEAGUE_BLOCK_SIZE,
            "events_per_primary_block": max(
                1,
                _s.LEAGUE_BLOCK_SIZE // max(1, ctx.block_size),
            ),
            "used_blocks": [
                {
                    "id": classification_id,
                    "name": _s._classification_label(classification_id),
                    "index": ctx.classification_blocks[classification_id],
                    "events": ctx.event_positions[classification_id],
                    "start": ctx.start_number
                    + ctx.classification_blocks[classification_id]
                    * _s.LEAGUE_BLOCK_SIZE,
                    "end": ctx.start_number
                    + (ctx.classification_blocks[classification_id] + 1)
                    * _s.LEAGUE_BLOCK_SIZE
                    - 1,
                }
                for classification_id in sorted(
                    ctx.classification_ids,
                    key=lambda value: ctx.classification_blocks[value],
                )
            ],
        },
    }


def scan_channels(
    db_path: Path | str,
    channels: list[dict],
    epg_path: Path | None = None,
    *,
    provider_epg_sources: list[tuple[Path, list[dict]]] | None = None,
    sports_epg_path: Path | None = None,
    combined_epg_path: Path | None = None,
    trigger: str = "manual",
    now: datetime | None = None,
    started_at: str | None = None,
    base_channel_ids: set[str] | None = None,
    fallback_epg_paths: Iterable[Path] | None = None,
    manual_channel_count: int = 0,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    scan_clock = perf_counter()
    _s.init_db(db_path)
    _s._raise_if_cancelled(cancel_check)
    started_at = started_at or _s._now_iso()
    settings = _s.get_settings(db_path)
    scan_anchor = now or datetime.now().astimezone()
    target_date = _s._sports_day(scan_anchor, settings).isoformat()

    if not settings.get("enabled"):
        result = {
            "ok": True,
            "count": len(_s.generated_rows(db_path, include_cached=True)),
            "events": 0,
            "message": (
                "Sports Automation is disabled; cached generated channels remain "
                "hidden for up to 24 hours."
            ),
            "target_date": target_date,
        }
        _s._record_scan(
            db_path,
            started_at=started_at,
            status="skipped",
            message=result["message"],
            event_count=0,
            channel_count=result["count"],
            target_date=target_date,
            trigger=trigger,
        )
        return result

    ctx = ScanContext(
        db_path=db_path,
        channels=list(channels),
        epg_path=epg_path,
        provider_epg_sources=provider_epg_sources,
        sports_epg_path=sports_epg_path,
        combined_epg_path=combined_epg_path,
        trigger=trigger,
        scan_anchor=scan_anchor,
        started_at=started_at,
        base_channel_ids=base_channel_ids,
        fallback_epg_paths=fallback_epg_paths,
        manual_channel_count=manual_channel_count,
        cancel_check=cancel_check,
        settings=settings,
        target_date=target_date,
    )
    _prepare_context(ctx)
    _collect_source_events(ctx)
    active_api_anchors = _reconcile_schedule_api(ctx)
    _merge_and_filter_events(ctx, active_api_anchors)
    _select_events(ctx)
    _build_generated_channels(ctx)
    _publish_outputs(ctx)
    ctx.timings["total"] = round(perf_counter() - scan_clock, 3)

    message, malformed_count = _result_message(ctx)
    _log_metrics(ctx)
    _s._record_scan(
        db_path,
        started_at=started_at,
        status="success",
        message=message,
        event_count=len(ctx.selected_events),
        channel_count=len(ctx.generated),
        target_date=ctx.target_date,
        trigger=trigger,
    )
    return _result_payload(ctx, message, malformed_count)
