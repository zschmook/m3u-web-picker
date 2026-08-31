from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import sports as _s


_BARE_SXX_EXX_RE = re.compile(r"^\s*S(\d+)\s*E(\d+)\s*$", re.IGNORECASE)
_DESCRIPTION_SXX_EXX_RE = re.compile(r"^\s*S(\d+)\s*E(\d+)\b", re.IGNORECASE)
_INLINE_NEW_MARKER_RE = re.compile(r"ᴺᵉʷ")
_INLINE_STATUS_SUFFIX_RE = re.compile(r"(?:\s*(?:ᴺᵉʷ|ᴸᶦᵛᵉ))+\s*$")
_RECURRING_NEWS_TIME_RE = re.compile(
    r"^(?P<prefix>.*?\bnews\b.*?)\s+at\s+"
    r"(?P<hour>1[0-2]|0?[1-9])"
    r"(?::(?P<minute>[0-5]\d))?\s*"
    r"(?P<meridiem>a(?:m)?|p(?:m)?)?\s*$",
    re.IGNORECASE,
)
_DVR_SERIES_EXCLUDED_CHANNEL_IDS = {"nbcnewsnow.us"}
_DVR_SERIES_EXCLUDED_CHANNEL_PREFIXES = ("m3u-picker-sports-",)
_PROGRAMME_TAIL_TAGS = {
    "video",
    "audio",
    "previously-shown",
    "premiere",
    "last-chance",
    "new",
    "subtitles",
    "rating",
    "star-rating",
    "review",
    "image",
}


def _xmltv_time(value: datetime) -> str:
    local = value if value.tzinfo else value.replace(tzinfo=ZoneInfo("UTC"))
    return local.strftime("%Y%m%d%H%M%S %z")


def _parse_iso_datetime(value: str | None, fallback_tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=fallback_tz)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _insert_before_programme_tail(
    programme: ElementTree.Element,
    child: ElementTree.Element,
) -> None:
    for index, existing in enumerate(programme):
        if existing.tag.rsplit("}", 1)[-1] in _PROGRAMME_TAIL_TAGS:
            programme.insert(index, child)
            return
    programme.append(child)


def _stable_programme_id(title: str, season: int, episode: int) -> str:
    identity = f"{title.strip().casefold()}\x1f{season}\x1f{episode}"
    return "m3u-picker:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _ensure_episode_identifiers(
    programme: ElementTree.Element,
    *,
    title: str,
    season: int,
    episode: int,
) -> None:
    existing_systems = {
        str(child.attrib.get("system", "")).strip().casefold()
        for child in programme.findall("episode-num")
    }
    if "xmltv_ns" not in existing_systems:
        node = ElementTree.Element("episode-num", {"system": "xmltv_ns"})
        node.text = f"{max(0, season - 1)}.{max(0, episode - 1)}."
        _insert_before_programme_tail(programme, node)
    if "dd_progid" not in existing_systems:
        node = ElementTree.Element("episode-num", {"system": "dd_progid"})
        node.text = _stable_programme_id(title, season, episode)
        _insert_before_programme_tail(programme, node)


def _canonical_recurring_news_title(
    title: str,
    start: datetime,
    timezone: ZoneInfo,
) -> str:
    match = _RECURRING_NEWS_TIME_RE.fullmatch(title.strip())
    if not match:
        return ""
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = str(match.group("meridiem") or "").lower()
    if meridiem.startswith("a"):
        hour24 = 0 if hour == 12 else hour
    elif meridiem.startswith("p"):
        hour24 = 12 if hour == 12 else hour + 12
    else:
        local_hour = start.astimezone(timezone).hour
        candidates = (0, 12) if hour == 12 else (hour, hour + 12)
        hour24 = min(
            candidates,
            key=lambda value: min(
                abs(value - local_hour),
                24 - abs(value - local_hour),
            ),
        )
    display_hour = hour24 % 12 or 12
    suffix = "AM" if hour24 < 12 else "PM"
    prefix = re.sub(r"\s+", " ", match.group("prefix")).strip()
    return f"{prefix} at {display_hour}:{minute:02d} {suffix}"


def _normalize_jellyfin_series_metadata(
    programme: ElementTree.Element,
    timezone: ZoneInfo,
) -> None:
    """Make trustworthy recurring programme metadata parseable by Jellyfin."""
    title_node = programme.find("title")
    title = str(title_node.text or "").strip() if title_node is not None else ""
    if not title:
        return
    has_inline_new_marker = _INLINE_NEW_MARKER_RE.search(title) is not None
    clean_title = _INLINE_STATUS_SUFFIX_RE.sub("", title).strip()
    if clean_title != title:
        title = clean_title
        title_node.text = title
        if has_inline_new_marker and programme.find("new") is None:
            programme.append(ElementTree.Element("new"))

    channel_id = str(programme.attrib.get("channel", "")).strip().casefold()
    if channel_id in _DVR_SERIES_EXCLUDED_CHANNEL_IDS or channel_id.startswith(
        _DVR_SERIES_EXCLUDED_CHANNEL_PREFIXES
    ):
        return

    if title.casefold().startswith("nbc nightly news"):
        title = "NBC Nightly News"
        title_node.text = title

    for episode_node in programme.findall("episode-num"):
        if str(episode_node.attrib.get("system", "")).strip():
            continue
        match = _BARE_SXX_EXX_RE.fullmatch(str(episode_node.text or ""))
        if not match:
            continue
        season = int(match.group(1))
        episode = int(match.group(2))
        episode_node.attrib["system"] = "SxxExx"
        episode_node.text = f"S{season:02d}E{episode:02d}"
        _ensure_episode_identifiers(
            programme,
            title=title,
            season=season,
            episode=episode,
        )
        return

    if programme.findall("episode-num"):
        return
    description = str(programme.findtext("desc") or "")
    description_episode = _DESCRIPTION_SXX_EXX_RE.match(description)
    if description_episode:
        season = int(description_episode.group(1))
        episode = int(description_episode.group(2))
        node = ElementTree.Element("episode-num", {"system": "SxxExx"})
        node.text = f"S{season:02d}E{episode:02d}"
        _insert_before_programme_tail(programme, node)
        _ensure_episode_identifiers(
            programme,
            title=title,
            season=season,
            episode=episode,
        )
        return
    start = _s._parse_xmltv_time(
        str(programme.attrib.get("start", "") or ""),
        timezone,
    )
    if start is None:
        return
    canonical_title = _canonical_recurring_news_title(
        title,
        start,
        timezone,
    )
    if not canonical_title:
        return
    title_node.text = canonical_title
    local_date = start.astimezone(timezone).date()
    season = local_date.year
    episode = local_date.timetuple().tm_yday
    node = ElementTree.Element("episode-num", {"system": "SxxExx"})
    node.text = f"S{season}E{episode:03d}"
    _insert_before_programme_tail(programme, node)
    _ensure_episode_identifiers(
        programme,
        title=canonical_title,
        season=season,
        episode=episode,
    )


def _serialize_programme_record(programme: dict) -> dict:
    output = dict(programme)
    for field in ("start", "stop"):
        value = output.get(field)
        output[field] = value.isoformat() if isinstance(value, datetime) else None
    output["categories"] = [
        str(value).strip()
        for value in output.get("categories", [])
        if str(value).strip()
    ]
    return output


def _serialize_epg_programme(event: dict) -> dict:
    programme = event.get("epg_programme")
    if not isinstance(programme, dict) or not programme:
        return {}
    output = _serialize_programme_record(programme)
    output["airings"] = [
        _serialize_programme_record(item)
        for item in event.get("epg_programmes", []) or []
        if isinstance(item, dict) and item
    ]
    return output


def _parse_programme_record(programme: dict, timezone: ZoneInfo) -> dict:
    output = dict(programme)
    output["start"] = _parse_iso_datetime(output.get("start"), timezone)
    output["stop"] = _parse_iso_datetime(output.get("stop"), timezone)
    output["categories"] = [
        str(value).strip()
        for value in output.get("categories", [])
        if str(value).strip()
    ]
    return output


def _epg_programme_from_item(item: dict, timezone: ZoneInfo) -> dict:
    programme = item.get("epg_programme")
    if isinstance(programme, str):
        programme = _s._json_load(programme, {})
    if not isinstance(programme, dict) or not programme:
        return {}
    output = _parse_programme_record(programme, timezone)
    output["airings"] = [
        _parse_programme_record(airing, timezone)
        for airing in programme.get("airings", []) or []
        if isinstance(airing, dict)
    ]
    return output


def _event_duration(league_id: str) -> timedelta:
    return timedelta(hours=_s.ESTIMATED_EVENT_HOURS.get(league_id, 3))


def _clean_feed_subtitle(value: str) -> str:
    return re.sub(
        r"\s*•\s*\d{1,2}:\d{2}\s+(?:AM|PM)\s*$",
        "",
        value or "",
        flags=re.I,
    ).strip()


def _add_text(
    parent: ElementTree.Element,
    tag: str,
    text: str,
    **attrs,
) -> ElementTree.Element:
    element = ElementTree.SubElement(
        parent,
        tag,
        {key: str(value) for key, value in attrs.items()},
    )
    element.text = str(text)
    return element


def _add_programme(
    root: ElementTree.Element,
    *,
    channel_id: str,
    start: datetime,
    stop: datetime,
    title: str,
    subtitle: str,
    description: str,
    categories: Iterable[str],
    is_live: bool = False,
    is_replay: bool = False,
    is_new: bool = False,
) -> bool:
    if stop <= start:
        return False
    programme = ElementTree.SubElement(
        root,
        "programme",
        {
            "start": _xmltv_time(start),
            "stop": _xmltv_time(stop),
            "channel": channel_id,
        },
    )
    _add_text(programme, "title", title, lang="en")
    if subtitle:
        _add_text(programme, "sub-title", subtitle, lang="en")
    if description:
        _add_text(programme, "desc", description, lang="en")
    for category in categories:
        if category:
            _add_text(programme, "category", category, lang="en")
    if is_live:
        ElementTree.SubElement(programme, "live")
    if is_replay:
        ElementTree.SubElement(programme, "previously-shown")
    if is_new:
        ElementTree.SubElement(programme, "new")
    return True


def _guide_coverage_window(anchor: datetime, settings: dict) -> tuple[datetime, datetime]:
    target_start, target_end, _ = _s._target_window(anchor, settings)
    return (
        min(target_start, anchor - timedelta(hours=6)),
        max(target_end, anchor + timedelta(hours=30)),
    )


def _add_exact_programme_airings(
    root: ElementTree.Element,
    *,
    channel_id: str,
    retained_airings: list[dict],
    event_title: str,
    feed_subtitle: str,
    categories: list[str],
    coverage_start: datetime,
    coverage_end: datetime,
    timezone: ZoneInfo,
) -> None:
    for airing_index, airing in enumerate(retained_airings):
        local_start = airing["start"].astimezone(timezone)
        local_stop = airing["stop"].astimezone(timezone)
        airing_replay = bool(airing.get("is_replay"))
        airing_categories = list(
            dict.fromkeys([*categories, *airing.get("categories", [])])
        )
        if airing_replay and "Replay" not in airing_categories:
            airing_categories.append("Replay")

        airing_title = str(airing.get("title") or event_title).strip()
        if airing_replay and not _s.REPLAY_RE.search(airing_title):
            airing_title = f"Replay: {airing_title}"
        airing_description = str(airing.get("description") or "").strip()
        airing_subtitle = str(airing.get("subtitle") or "").strip()
        description_parts = [
            value
            for value in (airing_description, airing_subtitle, feed_subtitle)
            if value
        ]
        exact_description = " • ".join(dict.fromkeys(description_parts))

        exact_start = max(local_start, coverage_start)
        exact_stop = min(local_stop, coverage_end)
        _add_programme(
            root,
            channel_id=channel_id,
            start=exact_start,
            stop=exact_stop,
            title=airing_title,
            subtitle=feed_subtitle,
            description=exact_description,
            categories=airing_categories,
            is_live=bool(airing.get("is_live")) and not airing_replay,
            is_replay=airing_replay,
            is_new=bool(airing.get("is_new")),
        )

        post_start = max(local_stop, coverage_start)
        post_stop = min(local_stop + _s.EVENT_END_GRACE, coverage_end)
        if airing_index + 1 < len(retained_airings):
            next_start = retained_airings[airing_index + 1]["start"].astimezone(timezone)
            post_stop = min(post_stop, next_start)
        if post_start < post_stop:
            _add_programme(
                root,
                channel_id=channel_id,
                start=post_start,
                stop=post_stop,
                title=f"{event_title} — Event window",
                subtitle=feed_subtitle,
                description=(
                    "The generated event feed remains available during the "
                    "post-event grace period."
                ),
                categories=airing_categories,
            )


def _add_provider_programme_guide(
    root: ElementTree.Element,
    *,
    channel_id: str,
    source_programme: dict,
    event_title: str,
    feed_subtitle: str,
    categories: list[str],
    league_label: str,
    is_replay: bool,
    coverage_start: datetime,
    coverage_end: datetime,
    timezone: ZoneInfo,
) -> bool:
    source_start = source_programme.get("start")
    source_stop = source_programme.get("stop")
    if not (
        isinstance(source_start, datetime)
        and isinstance(source_stop, datetime)
        and source_stop > source_start
    ):
        return False

    retained_airings = [source_programme]
    retained_airings.extend(
        airing
        for airing in source_programme.get("airings", []) or []
        if isinstance(airing, dict)
        and isinstance(airing.get("start"), datetime)
        and isinstance(airing.get("stop"), datetime)
        and airing["stop"] > airing["start"]
    )
    retained_airings.sort(key=lambda airing: airing["start"])

    latest_retained_stop = max(
        airing["stop"].astimezone(timezone) for airing in retained_airings
    )
    if latest_retained_stop + _s.EVENT_END_GRACE <= coverage_start:
        stale_title = str(retained_airings[0].get("title") or event_title).strip()
        _add_programme(
            root,
            channel_id=channel_id,
            start=coverage_start,
            stop=coverage_end,
            title=stale_title,
            subtitle=feed_subtitle,
            description=(
                "Generated sports event channel. Provider schedule data was stale "
                "or unavailable; guide coverage is being held until the next refresh."
            ),
            categories=categories,
            is_replay=bool(retained_airings[0].get("is_replay") or is_replay),
        )
        return True

    primary_start = retained_airings[0]["start"].astimezone(timezone)
    upcoming_stop = min(primary_start, coverage_end)
    if coverage_start < upcoming_stop:
        primary_title = str(retained_airings[0].get("title") or event_title).strip()
        scheduled = _s._schedule_text(primary_start)
        _add_programme(
            root,
            channel_id=channel_id,
            start=coverage_start,
            stop=upcoming_stop,
            title=f"Upcoming: {primary_title}",
            subtitle=feed_subtitle,
            description=f"{league_label} event scheduled for {scheduled}. {feed_subtitle}.",
            categories=list(
                dict.fromkeys(
                    [*categories, *retained_airings[0].get("categories", [])]
                )
            ),
        )

    _add_exact_programme_airings(
        root,
        channel_id=channel_id,
        retained_airings=retained_airings,
        event_title=event_title,
        feed_subtitle=feed_subtitle,
        categories=categories,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        timezone=timezone,
    )
    return True


def _add_synthetic_event_guide(
    root: ElementTree.Element,
    *,
    channel_id: str,
    start: datetime | None,
    end: datetime | None,
    league_id: str,
    league_label: str,
    event_title: str,
    feed_subtitle: str,
    categories: list[str],
    is_replay: bool,
    coverage_start: datetime,
    coverage_end: datetime,
    timezone: ZoneInfo,
) -> None:
    if start:
        local_start = start.astimezone(timezone)
        live_end = (end or (start + _event_duration(league_id))).astimezone(timezone)
        if live_end <= local_start:
            live_end = local_start + _event_duration(league_id)
        scheduled = _s._schedule_text(local_start)

        if live_end + timedelta(hours=_s.GUIDE_POSTGAME_HOURS) <= coverage_start:
            _add_programme(
                root,
                channel_id=channel_id,
                start=coverage_start,
                stop=coverage_end,
                title=f"{league_label} • {event_title}",
                subtitle=feed_subtitle,
                description=(
                    "Generated sports event channel. Provider schedule data was stale "
                    "or unavailable; guide coverage is being held until the next refresh."
                ),
                categories=categories,
                is_replay=is_replay,
            )
            return

        upcoming_stop = min(local_start, coverage_end)
        if coverage_start < upcoming_stop:
            _add_programme(
                root,
                channel_id=channel_id,
                start=coverage_start,
                stop=upcoming_stop,
                title=f"Upcoming: {event_title}",
                subtitle=feed_subtitle,
                description=f"{league_label} event scheduled for {scheduled}. {feed_subtitle}.",
                categories=categories,
            )

        live_start = max(local_start, coverage_start)
        live_stop = min(live_end, coverage_end)
        live_prefix = "Replay" if is_replay else league_label
        _add_programme(
            root,
            channel_id=channel_id,
            start=live_start,
            stop=live_stop,
            title=f"{live_prefix} • {event_title}",
            subtitle=feed_subtitle,
            description=f"{event_title}. {feed_subtitle}.",
            categories=categories,
            is_live=not is_replay,
            is_replay=is_replay,
        )

        post_start = max(live_end, coverage_start)
        post_stop = min(live_end + _s.EVENT_END_GRACE, coverage_end)
        if post_start < post_stop:
            _add_programme(
                root,
                channel_id=channel_id,
                start=post_start,
                stop=post_stop,
                title=f"{event_title} — Event window",
                subtitle=feed_subtitle,
                description=(
                    "The generated event feed remains available during the "
                    "post-event grace period."
                ),
                categories=categories,
            )
        return

    _add_programme(
        root,
        channel_id=channel_id,
        start=coverage_start,
        stop=coverage_end,
        title=f"{league_label} • {event_title}",
        subtitle=feed_subtitle,
        description="Provider sports event or replay; exact schedule data was unavailable.",
        categories=categories,
        is_replay=is_replay,
    )


def build_sports_xmltv(
    generated: list[dict],
    settings: dict,
    *,
    generated_at: datetime | None = None,
) -> bytes:
    """Build a standalone XMLTV guide for generated sports channels."""
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    anchor = (generated_at or datetime.now().astimezone()).astimezone(timezone)
    root = ElementTree.Element(
        "tv",
        {
            "generator-info-name": _s.XMLTV_GENERATOR_NAME,
            "source-info-name": "Generated sports guide",
        },
    )

    for item in generated:
        channel_id = str(item.get("tvg_id", "") or "").strip()
        if not channel_id:
            continue
        channel = ElementTree.SubElement(root, "channel", {"id": channel_id})
        _add_text(channel, "display-name", item.get("display_name", "Sports"), lang="en")
        assigned_number = str(item.get("assigned_number", "") or "").strip()
        if assigned_number:
            _add_text(channel, "display-name", assigned_number, lang="en")
            _add_text(channel, "display-name", f"CH {assigned_number}", lang="en")
        logo = str(item.get("tvg_logo", "") or "").strip()
        if logo:
            ElementTree.SubElement(channel, "icon", {"src": logo})

    coverage_start, coverage_end = _guide_coverage_window(anchor, settings)
    for item in generated:
        channel_id = str(item.get("tvg_id", "") or "").strip()
        if not channel_id:
            continue
        league_id = str(item.get("league_id", "") or "")
        league_label = _s.LEAGUE_NAMES.get(league_id, "Sports")
        event_title = str(item.get("event_title", "") or "").strip()
        if not event_title:
            display = str(item.get("display_name", "Sports event") or "Sports event")
            event_title = re.sub(r"^[^•]+•\s*", "", display)
            event_title = re.sub(r"\s+—\s+[^—]+$", "", event_title).strip()
        feed_subtitle = _clean_feed_subtitle(str(item.get("subtitle", "") or ""))
        start = _parse_iso_datetime(item.get("event_start"), timezone)
        end = _parse_iso_datetime(item.get("event_end"), timezone)
        is_replay = bool(item.get("is_replay"))
        categories = ["Sports", league_label]
        if is_replay:
            categories.append("Replay")

        source_programme = _epg_programme_from_item(item, timezone)
        if _add_provider_programme_guide(
            root,
            channel_id=channel_id,
            source_programme=source_programme,
            event_title=event_title,
            feed_subtitle=feed_subtitle,
            categories=categories,
            league_label=league_label,
            is_replay=is_replay,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            timezone=timezone,
        ):
            continue
        _add_synthetic_event_guide(
            root,
            channel_id=channel_id,
            start=start,
            end=end,
            league_id=league_id,
            league_label=league_label,
            event_title=event_title,
            feed_subtitle=feed_subtitle,
            categories=categories,
            is_replay=is_replay,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            timezone=timezone,
        )

    for programme in root.findall("programme"):
        _normalize_jellyfin_series_metadata(programme, timezone)

    if hasattr(ElementTree, "indent"):
        ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _xmltv_fragments(elements: Iterable[ElementTree.Element]) -> bytes:
    return b"\n".join(
        ElementTree.tostring(
            child,
            encoding="unicode",
            short_empty_elements=True,
        ).encode("ascii", errors="xmlcharrefreplace")
        for child in elements
    )


def _unfiltered_combined_xmltv(base_epg_path: Path, sports_xmltv: bytes) -> bytes:
    base = base_epg_path.read_bytes()
    close_matches = list(
        re.finditer(
            rb"</(?:[A-Za-z_][A-Za-z0-9_.-]*:)?tv\s*>",
            base,
            flags=re.I,
        )
    )
    if not close_matches:
        return sports_xmltv

    overlay_root = ElementTree.fromstring(sports_xmltv)
    channels = [
        child for child in overlay_root if child.tag.rsplit("}", 1)[-1] == "channel"
    ]
    programmes = [
        child for child in overlay_root if child.tag.rsplit("}", 1)[-1] == "programme"
    ]
    channel_fragment = _xmltv_fragments(channels)
    programme_fragment = _xmltv_fragments(programmes)

    close_start = close_matches[-1].start()
    programme_match = re.search(
        rb"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?programme(?:\s|>)",
        base[:close_start],
        flags=re.I,
    )
    channel_insert = programme_match.start() if programme_match else close_start

    pieces = [base[:channel_insert]]
    if channel_fragment:
        pieces.extend([b"\n", channel_fragment, b"\n"])
    pieces.append(base[channel_insert:close_start])
    if programme_fragment:
        pieces.extend([b"\n", programme_fragment, b"\n"])
    pieces.append(base[close_start:])
    return b"".join(pieces)


def _filtered_provider_xmltv(
    base_epg_path: Path,
    allowed_channel_ids: set[str],
    *,
    timezone: ZoneInfo = ZoneInfo("UTC"),
    cancel_check: _s.CancelCheck = None,
) -> tuple[dict[str, str], list[bytes], list[bytes], set[str], set[str]]:
    root_attributes: dict[str, str] = {}
    channel_fragments: list[bytes] = []
    programme_fragments: list[bytes] = []
    found_channels: set[str] = set()
    found_programmes: set[str] = set()
    allowed = {
        str(value).strip() for value in allowed_channel_ids if str(value).strip()
    }

    document_root = None
    try:
        for index, (event, element) in enumerate(
            _s._iterparse_xmltv(base_epg_path, events=("start", "end"))
        ):
            if index % 1000 == 0:
                _s._raise_if_cancelled(cancel_check)
            tag = element.tag.rsplit("}", 1)[-1]
            if event == "start" and tag == "tv" and document_root is None:
                document_root = element
                root_attributes = {
                    str(key): str(value) for key, value in element.attrib.items()
                }
                continue
            if event != "end":
                continue
            if tag == "channel":
                channel_id = str(element.attrib.get("id", "")).strip()
                if channel_id in allowed:
                    channel_fragments.append(
                        ElementTree.tostring(element, encoding="utf-8")
                    )
                    found_channels.add(channel_id)
                element.clear()
                if document_root is not None:
                    document_root.clear()
            elif tag == "programme":
                channel_id = str(element.attrib.get("channel", "")).strip()
                if channel_id in allowed:
                    _normalize_jellyfin_series_metadata(element, timezone)
                    programme_fragments.append(
                        ElementTree.tostring(element, encoding="utf-8")
                    )
                    found_programmes.add(channel_id)
                element.clear()
                if document_root is not None:
                    document_root.clear()
    except (ElementTree.ParseError, OSError, EOFError):
        return {}, [], [], set(), set()
    return (
        root_attributes,
        channel_fragments,
        programme_fragments,
        found_channels,
        found_programmes,
    )


def _programme_window(
    fragment: bytes,
    fallback_tz: ZoneInfo,
) -> tuple[str, datetime | None, datetime | None]:
    try:
        element = ElementTree.fromstring(fragment)
        channel_id = str(element.attrib.get("channel", "")).strip()
        start = _s._parse_xmltv_time(
            str(element.attrib.get("start", "") or ""),
            fallback_tz,
        )
        stop = _s._parse_xmltv_time(
            str(element.attrib.get("stop", "") or ""),
            fallback_tz,
        )
        if start and stop and stop > start:
            return channel_id, start, stop
        return channel_id, None, None
    except Exception:
        return "", None, None


def _overlaps_higher_priority(
    channel_id: str,
    start: datetime,
    stop: datetime,
    higher: dict[str, list[tuple[datetime, datetime]]],
) -> bool:
    return any(
        start < existing_stop and stop > existing_start
        for existing_start, existing_stop in higher.get(channel_id, [])
    )


def _ordered_guide_sources(
    base_epg_path: Path | None,
    fallback_epg_paths: Iterable[Path] | None,
) -> list[Path]:
    ordered: list[Path] = []
    if base_epg_path and base_epg_path.exists() and base_epg_path.stat().st_size:
        ordered.append(Path(base_epg_path))
    for raw_candidate in fallback_epg_paths or []:
        candidate = Path(raw_candidate)
        if not candidate.exists() or not candidate.stat().st_size:
            continue
        if not any(candidate.resolve() == existing.resolve() for existing in ordered):
            ordered.append(candidate)
    return ordered


def build_combined_xmltv(
    base_epg_path: Path | None,
    sports_xmltv: bytes,
    allowed_base_channel_ids: set[str] | None = None,
    *,
    fallback_epg_paths: Iterable[Path] | None = None,
    timezone_name: str = "UTC",
    cancel_check: _s.CancelCheck = None,
) -> bytes:
    """Merge provider/public guide data with generated sports XMLTV."""
    _s._raise_if_cancelled(cancel_check)
    valid_base = bool(
        base_epg_path and base_epg_path.exists() and base_epg_path.stat().st_size
    )
    if allowed_base_channel_ids is None:
        if valid_base:
            return _unfiltered_combined_xmltv(Path(base_epg_path), sports_xmltv)
        return sports_xmltv

    allowed = {
        str(value).strip()
        for value in allowed_base_channel_ids
        if str(value).strip()
    }
    attrs: dict[str, str] = {}
    provider_channels: list[bytes] = []
    provider_programmes: list[bytes] = []
    supplied_channels: set[str] = set()
    supplied_programmes: set[str] = set()
    accepted_intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    xmltv_default_tz = ZoneInfo("UTC")
    programme_timezone = ZoneInfo(str(timezone_name or "UTC"))

    for source_index, source_path in enumerate(
        _ordered_guide_sources(base_epg_path, fallback_epg_paths)
    ):
        _s._raise_if_cancelled(cancel_check)
        source_attrs, channels, programmes, channel_ids, _programme_ids = (
            _filtered_provider_xmltv(
                source_path,
                allowed,
                timezone=programme_timezone,
                cancel_check=cancel_check,
            )
        )
        if source_index == 0 and source_attrs:
            attrs = source_attrs
        for fragment in channels:
            try:
                element = ElementTree.fromstring(fragment)
                channel_id = str(element.attrib.get("id", "")).strip()
            except Exception:
                channel_id = ""
            if channel_id and channel_id not in supplied_channels:
                provider_channels.append(fragment)
                supplied_channels.add(channel_id)
        supplied_channels.update(channel_ids)

        higher_priority_intervals = {
            channel_id: list(values)
            for channel_id, values in accepted_intervals.items()
        }
        source_intervals: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
        for fragment in programmes:
            channel_id, start, stop = _programme_window(fragment, xmltv_default_tz)
            if not channel_id:
                continue
            if source_index > 0:
                if start and stop:
                    if _overlaps_higher_priority(
                        channel_id,
                        start,
                        stop,
                        higher_priority_intervals,
                    ):
                        continue
                elif channel_id in supplied_programmes:
                    continue
            provider_programmes.append(fragment)
            supplied_programmes.add(channel_id)
            if start and stop:
                source_intervals[channel_id].append((start, stop))
        for channel_id, windows in source_intervals.items():
            accepted_intervals[channel_id].extend(windows)

    overlay_root = ElementTree.fromstring(sports_xmltv)
    sports_channels = [
        ElementTree.tostring(child, encoding="utf-8")
        for child in overlay_root
        if child.tag.rsplit("}", 1)[-1] == "channel"
    ]
    sports_programmes = [
        ElementTree.tostring(child, encoding="utf-8")
        for child in overlay_root
        if child.tag.rsplit("}", 1)[-1] == "programme"
    ]

    root = ElementTree.Element(
        "tv",
        attrs
        or {
            "generator-info-name": _s.XMLTV_GENERATOR_NAME,
            "source-info-name": (
                "Filtered provider/public guide plus generated sports guide"
            ),
        },
    )
    shell = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    opening = shell[:-3] + b">" if shell.endswith(b" />") else shell.rsplit(b"</tv>", 1)[0]
    fragments = [
        *provider_channels,
        *sports_channels,
        *provider_programmes,
        *sports_programmes,
    ]
    if not fragments:
        return opening + b"</tv>"
    return opening + b"\n" + b"\n".join(fragments) + b"\n</tv>"


def _write_prepared_epg_files(
    generated: list[dict],
    settings: dict,
    *,
    base_epg_path: Path | None,
    base_channel_ids: set[str] | None,
    fallback_epg_paths: Iterable[Path] | None = None,
    sports_epg_path: Path | None,
    combined_epg_path: Path | None,
    generated_at: datetime,
    cancel_check: _s.CancelCheck = None,
) -> list[tuple[Path, Path]]:
    prepared: list[tuple[Path, Path]] = []
    try:
        _s._raise_if_cancelled(cancel_check)
        sports_bytes = build_sports_xmltv(
            generated,
            settings,
            generated_at=generated_at,
        )
        ElementTree.fromstring(sports_bytes)
        payloads = (
            (sports_epg_path, sports_bytes),
            (
                combined_epg_path,
                build_combined_xmltv(
                    base_epg_path,
                    sports_bytes,
                    base_channel_ids,
                    fallback_epg_paths=fallback_epg_paths,
                    timezone_name=str(settings.get("timezone", "UTC")),
                    cancel_check=cancel_check,
                ),
            ),
        )
        for destination, payload in payloads:
            _s._raise_if_cancelled(cancel_check)
            if not destination:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            temp = destination.with_name(destination.name + ".tmp")
            temp.write_bytes(payload)
            prepared.append((temp, destination))
        return prepared
    except Exception:
        for temp, _destination in prepared:
            temp.unlink(missing_ok=True)
        raise


def rebuild_epg_exports(
    db_path: Path | str,
    *,
    base_epg_path: Path | None,
    base_channel_ids: set[str] | None = None,
    fallback_epg_paths: Iterable[Path] | None = None,
    sports_epg_path: Path,
    combined_epg_path: Path,
) -> None:
    settings = _s.get_settings(db_path)
    rows = _s.generated_rows(db_path)
    prepared = _write_prepared_epg_files(
        rows,
        settings,
        base_epg_path=base_epg_path,
        base_channel_ids=base_channel_ids,
        fallback_epg_paths=fallback_epg_paths,
        sports_epg_path=sports_epg_path,
        combined_epg_path=combined_epg_path,
        generated_at=datetime.now().astimezone(),
    )
    for temp, destination in prepared:
        temp.replace(destination)
