from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

import sports as _s


def _timing_rank(event: dict) -> int:
    return {"untimed": 0, "embedded": 1, "xmltv": 2, "schedule_api": 3}.get(
        str(event.get("timing_source") or "untimed"),
        0,
    )


def _epg_programme_quality(event: dict) -> tuple[int, int, int, int, int, int]:
    programme = event.get("epg_programme")
    if not isinstance(programme, dict) or not programme:
        return (-1_000_000, 0, 0, 0, 0, 0)
    try:
        source_priority = int(programme.get("source_priority", 0))
    except (TypeError, ValueError):
        source_priority = 0
    return (
        -source_priority,
        1 if programme.get("current_at_scan") else 0,
        1 if isinstance(programme.get("stop"), datetime) else 0,
        1 if programme.get("is_live") else 0,
        1 if programme.get("title") else 0,
        1 if programme.get("description") else 0,
    )


def _adopt_event_timing(target: dict, source: dict) -> None:
    target["start"] = source.get("start")
    target["end"] = source.get("end")
    target["timing_source"] = source.get("timing_source")
    programme = source.get("epg_programme")
    if isinstance(programme, dict) and programme:
        target["epg_programme"] = dict(programme)


def _merge_event_records(existing: dict, incoming: dict) -> dict:
    if existing.get("historical_anchor") and not incoming.get("historical_anchor"):
        existing.pop("historical_anchor", None)
        existing["source_kind"] = (
            incoming.get("source_kind") or existing.get("source_kind")
        )

    seen = {str(ch.get("url", "")) for ch in existing["source_channels"]}
    for channel in incoming["source_channels"]:
        channel_url = str(channel.get("url", ""))
        if channel_url not in seen:
            existing["source_channels"].append(channel)
            seen.add(channel_url)

    existing_rank = _timing_rank(existing)
    incoming_rank = _timing_rank(incoming)
    if (
        existing_rank == 3
        and incoming_rank == 2
        and _epg_programme_quality(incoming) > _epg_programme_quality(existing)
    ):
        programme = incoming.get("epg_programme")
        if isinstance(programme, dict) and programme:
            existing["epg_programme"] = dict(programme)
    elif (
        incoming_rank == 3
        and existing_rank == 2
        and _epg_programme_quality(existing) > _epg_programme_quality(incoming)
    ):
        programme = existing.get("epg_programme")
        if isinstance(programme, dict) and programme:
            incoming["epg_programme"] = dict(programme)
    if incoming_rank > existing_rank:
        _adopt_event_timing(existing, incoming)
    elif incoming_rank == existing_rank and incoming_rank > 0:
        if incoming_rank == 2:
            if _epg_programme_quality(incoming) > _epg_programme_quality(existing):
                _adopt_event_timing(existing, incoming)
        else:
            incoming_start = incoming.get("start")
            existing_start = existing.get("start")
            if isinstance(incoming_start, datetime) and (
                not isinstance(existing_start, datetime)
                or incoming_start < existing_start
            ):
                existing["start"] = incoming_start
            incoming_end = incoming.get("end")
            existing_end = existing.get("end")
            if isinstance(incoming_end, datetime) and (
                not isinstance(existing_end, datetime)
                or incoming_end > existing_end
            ):
                existing["end"] = incoming_end

    existing["time_is_explicit"] = _timing_rank(existing) > 0
    existing["has_embedded_anchor"] = bool(
        existing.get("has_embedded_anchor") or incoming.get("has_embedded_anchor")
    )
    existing["has_schedule_api_anchor"] = bool(
        existing.get("has_schedule_api_anchor")
        or incoming.get("has_schedule_api_anchor")
    )
    for key in (
        "api_event_id",
        "api_source",
        "api_canonical_start",
        "api_status_short",
        "api_status_long",
        "api_home_id",
        "api_away_id",
        "api_home_logo",
        "api_away_logo",
    ):
        if not existing.get(key) and incoming.get(key):
            existing[key] = incoming.get(key)
    source_kinds = set(existing.get("source_kinds", []))
    source_kinds.add(str(existing.get("source_kind") or ""))
    source_kinds.update(incoming.get("source_kinds", []))
    source_kinds.add(str(incoming.get("source_kind") or ""))
    existing["source_kinds"] = sorted(value for value in source_kinds if value)
    existing["is_replay"] = bool(
        existing.get("is_replay") or incoming.get("is_replay")
    )
    return existing


def _timed_events_are_same_slot(left: dict, right: dict) -> bool:
    left_start = left.get("start")
    right_start = right.get("start")
    if not isinstance(left_start, datetime) or not isinstance(right_start, datetime):
        return False
    try:
        left_utc = _s._utc_instant(left_start)
        right_utc = _s._utc_instant(right_start)
        if left_utc is None or right_utc is None:
            return False
        delta = abs((left_utc - right_utc).total_seconds())
    except Exception:
        return False
    return delta <= _s.EVENT_MERGE_TOLERANCE.total_seconds()


def _event_programme(event: dict) -> dict:
    programme = event.get("epg_programme")
    return programme if isinstance(programme, dict) else {}


def _event_is_live_airing(event: dict) -> bool:
    programme = _event_programme(event)
    return bool(programme.get("is_live")) and not bool(
        event.get("is_replay") or programme.get("is_replay")
    )


def _event_is_replay_airing(event: dict) -> bool:
    programme = _event_programme(event)
    return bool(event.get("is_replay") or programme.get("is_replay"))


def _schedule_api_candidate_text(event: dict) -> str:
    programme = _event_programme(event)
    return " ".join(
        str(value or "")
        for value in (
            event.get("source_text"),
            programme.get("title"),
            programme.get("description"),
        )
    )


def _schedule_api_supporting_content(event: dict) -> bool:
    programme = _event_programme(event)
    programme_text = " ".join(
        str(value or "")
        for value in (programme.get("title"), programme.get("description"))
    ).strip()
    candidate_text = programme_text or str(event.get("source_text") or "")
    return bool(_s.SCHEDULE_API_SUPPORT_RE.search(candidate_text))


def _schedule_api_candidate_duration(event: dict) -> timedelta | None:
    start = event.get("start")
    if not isinstance(start, datetime):
        return None
    programme = _event_programme(event)
    stop = programme.get("stop")
    if isinstance(stop, datetime) and stop > start:
        return stop - start
    end = event.get("end")
    if isinstance(end, datetime) and end > start:
        return end - start
    estimated = _s._primary_event_end(event)
    if isinstance(estimated, datetime) and estimated > start:
        return estimated - start
    return None


def _schedule_api_live_candidate_score(
    event: dict,
    canonical_start: datetime,
) -> tuple | None:
    start = event.get("start")
    if not isinstance(start, datetime):
        return None
    try:
        start_utc = _s._utc_instant(start)
        canonical_utc = _s._utc_instant(canonical_start)
        if start_utc is None or canonical_utc is None:
            return None
        delta = abs(start_utc - canonical_utc)
    except Exception:
        return None
    if delta > _s.SCHEDULE_API_LIVE_CANDIDATE_WINDOW:
        return None
    if _event_is_replay_airing(event) or _schedule_api_supporting_content(event):
        return None

    programme = _event_programme(event)
    duration = _schedule_api_candidate_duration(event)
    duration_seconds = duration.total_seconds() if duration is not None else 0
    full_game = 1 if duration_seconds >= 90 * 60 else 0
    live = 1 if programme.get("is_live") else 0
    current = 1 if programme.get("current_at_scan") else 0
    return (
        -delta.total_seconds(),
        live,
        full_game,
        current,
        _timing_rank(event),
        _epg_programme_quality(event),
    )


def _schedule_api_provider_clusters(events: list[dict]) -> list[dict]:
    timed = sorted(
        (event for event in events if _s._event_has_usable_timing(event)),
        key=lambda event: event["start"],
    )
    clusters: list[dict] = []
    for event in timed:
        same_slot = False
        if clusters:
            left = clusters[-1].get("start")
            right = event.get("start")
            if isinstance(left, datetime) and isinstance(right, datetime):
                try:
                    same_slot = abs((left - right).total_seconds()) <= 90
                except Exception:
                    same_slot = False
        if same_slot:
            _merge_event_records(clusters[-1], event)
        else:
            clusters.append(event)
    return clusters


def _merge_schedule_api_group(
    group: list[dict],
    *,
    include_replays: bool,
) -> dict | None:
    api_anchors = [event for event in group if event.get("has_schedule_api_anchor")]
    if not api_anchors:
        return None
    anchor = min(
        api_anchors,
        key=lambda event: event.get("start")
        or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
    )
    canonical_start = anchor.get("start")
    if not isinstance(canonical_start, datetime):
        return None

    provider_events = [
        event
        for event in group
        if event is not anchor and not event.get("has_schedule_api_anchor")
    ]
    clusters = _schedule_api_provider_clusters(provider_events)

    scored: list[tuple[tuple, dict]] = []
    live_cluster = None
    for cluster in clusters:
        score = _schedule_api_live_candidate_score(cluster, canonical_start)
        if score is not None:
            scored.append((score, cluster))
    if scored:
        _score, live_cluster = max(scored, key=lambda item: item[0])
        _merge_event_records(anchor, live_cluster)

    if include_replays:
        replay_cutoff = canonical_start + _s.SCHEDULE_API_LIVE_CANDIDATE_WINDOW
        for cluster in clusters:
            if live_cluster is not None and cluster is live_cluster:
                continue
            start = cluster.get("start")
            if not isinstance(start, datetime) or start <= canonical_start:
                continue
            if (
                start >= replay_cutoff
                and not _schedule_api_supporting_content(cluster)
            ):
                _append_replay_airing(
                    anchor,
                    cluster,
                    inferred=not _event_is_replay_airing(cluster),
                )
    return anchor


def _programme_identity(programme: dict) -> tuple[str, str, str, str]:
    def stamp(value) -> str:
        return value.isoformat() if isinstance(value, datetime) else str(value or "")

    return (
        stamp(programme.get("start")),
        stamp(programme.get("stop")),
        str(programme.get("source_channel_id") or ""),
        str(programme.get("title") or ""),
    )


def _append_replay_airing(
    target: dict,
    source: dict,
    *,
    inferred: bool = False,
) -> None:
    programme = _event_programme(source)
    if not programme:
        return
    replay = dict(programme)
    replay["is_replay"] = True
    replay["is_live"] = False
    if inferred:
        replay["inferred_replay"] = True
    airings = target.setdefault("epg_programmes", [])
    existing = {
        _programme_identity(item)
        for item in airings
        if isinstance(item, dict)
    }
    identity = _programme_identity(replay)
    if identity not in existing:
        airings.append(replay)
        airings.sort(
            key=lambda item: item.get("start")
            if isinstance(item.get("start"), datetime)
            else datetime.max.replace(tzinfo=ZoneInfo("UTC"))
        )


def _canonical_replay_anchor_end(event: dict) -> datetime | None:
    return _s._primary_event_end(event)


def _is_later_airing_of(anchor: dict, candidate: dict) -> bool:
    anchor_start = anchor.get("start")
    candidate_start = candidate.get("start")
    anchor_end = _canonical_replay_anchor_end(anchor)
    if not all(
        isinstance(value, datetime)
        for value in (anchor_start, candidate_start, anchor_end)
    ):
        return False
    try:
        return (
            candidate_start > anchor_start + _s.EVENT_MERGE_TOLERANCE
            and candidate_start <= anchor_end + _s.REPLAY_ATTACH_WINDOW
        )
    except Exception:
        return False


def _event_current_at_scan(event: dict) -> bool:
    return bool(_event_programme(event).get("current_at_scan"))


def _event_has_embedded_anchor(event: dict) -> bool:
    return bool(event.get("has_embedded_anchor"))


def _nearest_replay_anchor(
    candidate: dict,
    anchors: list[dict],
) -> dict | None:
    matches = [
        anchor for anchor in anchors if _is_later_airing_of(anchor, candidate)
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda event: event.get("start")
        or datetime.min.replace(tzinfo=ZoneInfo("UTC")),
    )


def _assign_merged_event_keys(
    events: list[dict],
    timezone_name: str = "America/New_York",
) -> list[dict]:
    event_timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    used: set[str] = set()
    for event in sorted(
        events,
        key=lambda item: (
            str(item.get("event_identity") or item.get("event_base_key") or ""),
            item.get("start") or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
        ),
    ):
        start = event.get("start")
        api_event_id = str(event.get("api_event_id") or "").strip()
        if api_event_id:
            candidate = (
                f"{str(event.get('api_source') or _s.SCHEDULE_API_SOURCE)}:"
                f"{api_event_id}"
            )
            serial = 2
            unique = candidate
            while unique in used:
                unique = f"{candidate}-{serial}"
                serial += 1
            event["event_key"] = unique
            event["event_base_key"] = candidate
            if isinstance(start, datetime):
                local_start = (
                    start
                    if start.tzinfo is not None
                    else start.replace(tzinfo=event_timezone)
                )
                event["event_date"] = (
                    local_start.astimezone(event_timezone).date().isoformat()
                )
            used.add(unique)
            continue
        identity = str(event.get("event_identity") or "sports")
        if isinstance(start, datetime):
            local_start = (
                start.replace(tzinfo=event_timezone)
                if start.tzinfo is None
                else start.astimezone(event_timezone)
            )
            event_date = local_start.date().isoformat()
            suffix = local_start.strftime("%H%M")
        else:
            event_date = str(event.get("event_date") or "untimed")
            suffix = "untimed"
        base_key = f"{event_date}:{identity}"
        event["event_date"] = event_date
        event["event_base_key"] = base_key
        candidate = f"{base_key}:{suffix}"
        serial = 2
        while candidate in used:
            candidate = f"{base_key}:{suffix}-{serial}"
            serial += 1
        event["event_key"] = candidate
        used.add(candidate)
    return events


def _logical_broadcast_day(
    event: dict,
    timezone_name: str,
) -> date | None:
    start = event.get("start")
    if not isinstance(start, datetime):
        return None
    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    try:
        local_start = (
            start.replace(tzinfo=timezone)
            if start.tzinfo is None
            else start.astimezone(timezone)
        )
    except Exception:
        return None
    return (
        local_start - timedelta(hours=_s.LOGICAL_EVENT_DAY_ROLLOVER_HOUR)
    ).date()


def _cluster_is_history(event: dict) -> bool:
    return bool(event.get("historical_anchor"))


def _bucket_has_schedule_anchor(events: list[dict]) -> bool:
    return any(
        event.get("has_schedule_api_anchor")
        or _event_has_embedded_anchor(event)
        or _cluster_is_history(event)
        for event in events
    )


def _canonical_bucket_anchor(events: list[dict]) -> dict:
    ranked = sorted(
        events,
        key=lambda event: (
            0
            if event.get("has_schedule_api_anchor")
            else 1
            if _event_has_embedded_anchor(event) and not _cluster_is_history(event)
            else 2
            if _cluster_is_history(event)
            else 3
            if _event_current_at_scan(event)
            else 4,
            event.get("start") or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
        ),
    )
    return ranked[0]


def _is_overnight_repeat(
    anchor: dict,
    candidate: dict,
    timezone_name: str,
) -> bool:
    anchor_start = anchor.get("start")
    candidate_start = candidate.get("start")
    if not isinstance(anchor_start, datetime) or not isinstance(candidate_start, datetime):
        return False
    timezone = ZoneInfo(str(timezone_name or "America/New_York"))
    try:
        anchor_local = anchor_start.astimezone(timezone)
        candidate_local = candidate_start.astimezone(timezone)
    except Exception:
        return False
    return bool(
        _logical_broadcast_day(anchor, timezone_name)
        == _logical_broadcast_day(candidate, timezone_name)
        and anchor_local.hour >= _s.LOGICAL_EVENT_DAY_ROLLOVER_HOUR
        and candidate_local.date() > anchor_local.date()
        and candidate_local.hour < _s.LOGICAL_EVENT_DAY_ROLLOVER_HOUR
    )


def _schedule_api_anchor_events(
    raw_events: list[dict],
    settings: dict,
    team_lookup: dict,
) -> list[dict]:
    teams = team_lookup.get("teams", [])
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    anchors = []
    for item in raw_events:
        start = item.get("scheduled_start")
        if not isinstance(start, datetime):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone)
        start = start.astimezone(timezone)

        league_id = str(item.get("league_id") or "").strip()
        sport_id = str(item.get("sport_id") or "").strip()
        api_source = str(item.get("api_source") or "").strip()
        api_dataset = str(item.get("api_dataset") or "").strip()
        if not league_id or not api_source:
            continue

        away_id, away_name = _s._find_team_id(
            str(item.get("away_name", "")),
            league_id,
            teams,
            team_lookup,
        )
        home_id, home_name = _s._find_team_id(
            str(item.get("home_name", "")),
            league_id,
            teams,
            team_lookup,
        )
        if not away_id or not home_id:
            continue
        event_id = str(item.get("api_event_id") or "").strip()
        if not event_id:
            continue

        status_short = str(item.get("status_short") or "").upper()
        identity = f"{api_source}:{event_id}"
        league_name = _s.LEAGUE_NAMES.get(league_id, league_id.upper())
        anchors.append(
            {
                "event_key": identity,
                "event_base_key": identity,
                "event_identity": identity,
                "event_date": start.date().isoformat(),
                "league_id": league_id,
                "sport_id": sport_id,
                "sport_tags": [sport_id] if sport_id else [],
                "display_name": f"{away_name} at {home_name}",
                "away_team_id": away_id,
                "away_team_name": away_name,
                "home_team_id": home_id,
                "home_team_name": home_name,
                "start": start,
                "end": None,
                "time_is_explicit": True,
                "timing_source": "schedule_api",
                "source_kind": "schedule_api",
                "source_kinds": ["schedule_api"],
                "source_channels": [],
                "source_text": f"{league_name} {away_name} at {home_name}",
                "has_schedule_api_anchor": True,
                "api_event_id": event_id,
                "api_source": api_source,
                "api_dataset": api_dataset,
                "api_status_short": status_short,
                "api_status_long": str(item.get("status_long") or ""),
                "api_home_id": str(item.get("home_api_id") or ""),
                "api_away_id": str(item.get("away_api_id") or ""),
                "api_home_logo": str(item.get("home_logo") or ""),
                "api_away_logo": str(item.get("away_logo") or ""),
            }
        )
    return anchors


def _apply_schedule_api_identity(
    provider_events: list[dict],
    api_anchors: list[dict],
) -> list[dict]:
    if not api_anchors:
        return provider_events

    by_matchup: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    supported_leagues: set[str] = set()
    for anchor in api_anchors:
        league_id = str(anchor.get("league_id") or "")
        supported_leagues.add(league_id)
        key = (
            league_id,
            str(anchor.get("away_team_id") or ""),
            str(anchor.get("home_team_id") or ""),
        )
        by_matchup[key].append(anchor)

    output = []
    for event in provider_events:
        league_id = str(event.get("league_id") or "")
        if league_id not in supported_leagues:
            output.append(event)
            continue

        away_id = str(event.get("away_team_id") or "")
        home_id = str(event.get("home_team_id") or "")
        candidates = list(by_matchup.get((league_id, away_id, home_id), []))
        if not candidates:
            candidates = list(by_matchup.get((league_id, home_id, away_id), []))
        if not candidates:
            output.append(event)
            continue

        event_start = event.get("start")
        if isinstance(event_start, datetime):
            ranked = []
            for anchor in candidates:
                anchor_start = anchor.get("start")
                if not isinstance(anchor_start, datetime):
                    continue
                event_utc = _s._utc_instant(event_start)
                anchor_utc = _s._utc_instant(anchor_start)
                if event_utc is None or anchor_utc is None:
                    continue
                delta = abs((event_utc - anchor_utc).total_seconds())
                if delta <= _s.SCHEDULE_API_MATCH_WINDOW.total_seconds():
                    ranked.append((delta, anchor_utc, anchor))
            if not ranked:
                output.append(event)
                continue
            _delta, _start, match = min(
                ranked,
                key=lambda item: (item[0], item[1]),
            )
        elif len(candidates) == 1:
            match = candidates[0]
        else:
            output.append(event)
            continue

        if str(match.get("api_status_short") or "").upper() in {
            "POST",
            "PST",
            "CANC",
            "ABD",
            "SUSP",
        }:
            continue
        event["event_identity"] = match["event_identity"]
        event["event_base_key"] = match["event_base_key"]
        event["event_date"] = match["event_date"]
        event["api_event_id"] = match["api_event_id"]
        event["api_source"] = match["api_source"]
        event["api_dataset"] = match.get("api_dataset", "")
        event["api_canonical_start"] = match["start"]
        event["has_schedule_api_identity"] = True
        event["away_team_id"] = match["away_team_id"]
        event["away_team_name"] = match["away_team_name"]
        event["home_team_id"] = match["home_team_id"]
        event["home_team_name"] = match["home_team_name"]
        event["display_name"] = match["display_name"]
        output.append(event)
    return output


def _merge_legacy_bucket(
    ordered: list[dict],
    *,
    include_replays: bool,
    timezone_name: str,
) -> list[dict]:
    logical_candidates: list[dict] = []
    explicit_replays: list[dict] = []
    clean_live_candidates = [
        item
        for item in ordered
        if _event_is_live_airing(item)
        and not _schedule_api_supporting_content(item)
    ]
    for candidate in ordered:
        if _event_is_replay_airing(candidate):
            explicit_replays.append(candidate)
            continue
        if clean_live_candidates and _schedule_api_supporting_content(candidate):
            continue

        prior = logical_candidates[-1] if logical_candidates else None
        if prior is not None and _is_overnight_repeat(
            prior,
            candidate,
            timezone_name,
        ):
            if include_replays:
                _append_replay_airing(prior, candidate, inferred=True)
            continue

        if clean_live_candidates and not _event_is_live_airing(candidate):
            candidate_start = candidate.get("start")
            candidate_duration = _schedule_api_candidate_duration(candidate)
            prior_live = next(
                (
                    item
                    for item in reversed(logical_candidates)
                    if _event_is_live_airing(item)
                    and isinstance(item.get("start"), datetime)
                    and isinstance(candidate_start, datetime)
                    and item["start"] < candidate_start
                ),
                None,
            )
            prior_live_end = _s._primary_event_end(prior_live) if prior_live else None
            if (
                prior_live is not None
                and isinstance(candidate_start, datetime)
                and isinstance(prior_live_end, datetime)
                and candidate_start >= prior_live_end
                and isinstance(candidate_duration, timedelta)
                and candidate_duration >= timedelta(minutes=90)
            ):
                if include_replays:
                    _append_replay_airing(prior_live, candidate, inferred=True)
                continue
        logical_candidates.append(candidate)

    if include_replays and logical_candidates:
        for replay in explicit_replays:
            anchor = (
                _nearest_replay_anchor(replay, logical_candidates)
                or logical_candidates[0]
            )
            _append_replay_airing(anchor, replay)
    return logical_candidates


def _merge_events(
    events: Iterable[dict],
    cancel_check: _s.CancelCheck = None,
    settings: dict | None = None,
) -> list[dict]:
    """Merge provider airings into stable logical games."""
    settings = settings or {}
    include_replays = bool(settings.get("include_replays"))
    timezone_name = str(settings.get("timezone", "America/New_York"))
    grouped: dict[str, list[dict]] = defaultdict(list)
    for index, event in enumerate(events):
        if index % 100 == 0:
            _s._raise_if_cancelled(cancel_check)
        identity = str(
            event.get("event_identity")
            or event.get("event_base_key")
            or event.get("event_key")
            or ""
        )
        grouped[identity].append(event)

    merged: list[dict] = []
    for group_index, group in enumerate(grouped.values()):
        if group_index % 100 == 0:
            _s._raise_if_cancelled(cancel_check)

        if any(event.get("has_schedule_api_anchor") for event in group):
            api_event = _merge_schedule_api_group(
                group,
                include_replays=include_replays,
            )
            if api_event is not None:
                merged.append(api_event)
            continue

        timed = sorted(
            (event for event in group if _s._event_has_usable_timing(event)),
            key=lambda event: event["start"],
        )
        untimed = [
            event for event in group if not _s._event_has_usable_timing(event)
        ]

        clusters: list[dict] = []
        for event in timed:
            if clusters and _timed_events_are_same_slot(clusters[-1], event):
                _merge_event_records(clusters[-1], event)
            else:
                clusters.append(event)

        if len(clusters) == 1:
            for event in untimed:
                _merge_event_records(clusters[0], event)
        elif not clusters and untimed:
            candidate = untimed[0]
            for event in untimed[1:]:
                _merge_event_records(candidate, event)
            clusters.append(candidate)

        if not clusters:
            continue

        day_buckets: dict[date | None, list[dict]] = defaultdict(list)
        for cluster in clusters:
            day_buckets[_logical_broadcast_day(cluster, timezone_name)].append(cluster)

        for bucket in sorted(
            day_buckets.values(),
            key=lambda items: min(
                (
                    item.get("start")
                    for item in items
                    if isinstance(item.get("start"), datetime)
                ),
                default=datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            ),
        ):
            ordered = sorted(
                bucket,
                key=lambda event: event.get("start")
                or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
            )
            if not _bucket_has_schedule_anchor(ordered):
                merged.extend(
                    _merge_legacy_bucket(
                        ordered,
                        include_replays=include_replays,
                        timezone_name=timezone_name,
                    )
                )
                continue

            anchor = _canonical_bucket_anchor(ordered)
            for candidate in ordered:
                if candidate is anchor:
                    continue
                explicit_replay = _event_is_replay_airing(candidate)
                if include_replays:
                    _append_replay_airing(
                        anchor,
                        candidate,
                        inferred=not explicit_replay,
                    )
            merged.append(anchor)

    return _assign_merged_event_keys(merged, timezone_name)
