from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

import espn_team_logos
import event_logos
import sports as _s


def _provider_priority(channel: dict) -> int:
    try:
        return max(0, int(channel.get("_provider_priority", 0)))
    except (TypeError, ValueError):
        return 0


def _team_feed_index(
    channels: Iterable[dict],
) -> tuple[dict[str, list[dict]], set[int]]:
    output: dict[str, list[dict]] = defaultdict(list)
    channel_ids: set[int] = set()
    for channel in channels:
        identity = _s._team_feed_identity(channel)
        if identity:
            _league_id, team_id, _team_name = identity
            output[team_id].append(channel)
            channel_ids.add(id(channel))
    return output, channel_ids


def _team_feeds(channels: Iterable[dict]) -> dict[str, list[dict]]:
    return _team_feed_index(channels)[0]


def _feed_type(channel: dict, event: dict, team_id: str = "") -> str:
    text = _s._channel_text(channel).lower()
    if "backup" in text:
        return "backup"
    if re.search(r"espa[nñ]ol|spanish|\bes\b", text, re.I):
        return "spanish"
    if team_id and team_id == event.get("away_team_id"):
        return "away"
    if team_id and team_id == event.get("home_team_id"):
        return "home"
    if any(word in text for word in _s.NETWORK_WORDS):
        return "national"
    return "event"


def _feed_label(feed_type: str, event: dict, team_id: str) -> tuple[str, str]:
    if feed_type == "away":
        team = event.get("away_team_name") or "Away"
        return f"{team.split()[-1]} Feed", f"Away broadcast • {team}"
    if feed_type == "home":
        team = event.get("home_team_name") or "Home"
        return f"{team.split()[-1]} Feed", f"Home broadcast • {team}"
    if feed_type == "national":
        return "National Feed", "National broadcast"
    if feed_type == "spanish":
        return "Spanish Feed", "Spanish-language broadcast"
    if feed_type == "backup":
        return "Backup Feed", "Backup stream"
    return "Event Feed", "Provider event stream"


def _catalog_team_logo(team_catalog: dict[str, dict], team_id: str) -> str:
    if not team_id:
        return ""
    team = team_catalog.get(team_id)
    return str(team.get("logo_url", "") or "") if team else ""


def _espn_team_logo(event: dict, *, team_name: str) -> str:
    if not team_name:
        return ""
    try:
        return espn_team_logos.espn_full_default_url(
            event.get("league_id") or _s._classification_id(event),
            team_name,
            event.get("sport_id") or "",
        )
    except Exception:
        return ""


def _preferred_feed_logo(
    event: dict,
    feed: dict,
    channel: dict,
    team_catalog: dict[str, dict],
) -> str:
    """Choose stable artwork for a generated feed.

    Every generated matchup checks ESPN for the ordinary ``full/default`` team
    mark first, with provider/Xtream artwork retained as fallback. ESPN catalog
    discovery and results are cached, and the event compositor checks the
    persistent image cache before it downloads either source. Generic event,
    national, Spanish, and backup feeds continue to use the local event-keyed
    ``Away @ Home`` composite.
    """
    feed_team_id = str(feed.get("team_id") or "")
    feed_type = str(feed.get("feed_type") or "event").strip().lower()
    home_team_id = str(event.get("home_team_id") or "")
    away_team_id = str(event.get("away_team_id") or "")
    home_team_name = str(event.get("home_team_name") or "Home")
    away_team_name = str(event.get("away_team_name") or "Away")
    home_api_logo = str(event.get("api_home_logo") or "")
    away_api_logo = str(event.get("api_away_logo") or "")
    home_catalog_logo = _catalog_team_logo(team_catalog, home_team_id)
    away_catalog_logo = _catalog_team_logo(team_catalog, away_team_id)

    home_espn_logo = _espn_team_logo(event, team_name=home_team_name)
    away_espn_logo = _espn_team_logo(event, team_name=away_team_name)

    if (
        feed_type not in {"home", "away"}
        and away_team_id
        and home_team_id
        and event.get("event_key")
    ):
        matchup_logo = event_logos.register_matchup_logo(
            event_key=event.get("event_key"),
            away_team_id=away_team_id,
            away_team_name=away_team_name,
            away_logo_url=away_espn_logo or away_catalog_logo or away_api_logo,
            away_fallback_logo_url=away_catalog_logo or away_api_logo,
            home_team_id=home_team_id,
            home_team_name=home_team_name,
            home_logo_url=home_espn_logo or home_catalog_logo or home_api_logo,
            home_fallback_logo_url=home_catalog_logo or home_api_logo,
            event_end=_s._event_end(event),
        )
        if matchup_logo:
            return matchup_logo

    if feed_team_id == home_team_id or feed_type == "home":
        candidates = (home_espn_logo, home_catalog_logo, home_api_logo)
    elif feed_team_id == away_team_id or feed_type == "away":
        candidates = (away_espn_logo, away_catalog_logo, away_api_logo)
    else:
        candidates = (
            away_espn_logo,
            away_catalog_logo,
            away_api_logo,
            home_espn_logo,
            home_catalog_logo,
            home_api_logo,
        )

    for logo in candidates:
        if logo:
            return logo
    return str(channel.get("tvg_logo", "") or "")


def _build_feeds(
    event: dict,
    channels: list[dict] | dict[str, list[dict]],
    rule: dict,
    settings: dict,
) -> list[dict]:
    team_feed_map = channels if isinstance(channels, dict) else _team_feeds(channels)
    candidates_by_url: dict[str, dict] = {}

    def add(channel: dict, team_id: str = "") -> None:
        url = str(channel.get("url", "") or "").strip()
        if not url:
            return
        kind = _feed_type(channel, event, team_id)
        candidate = {
            "channel": channel,
            "feed_type": kind,
            "team_id": team_id,
            "provider_priority": _provider_priority(channel),
        }
        existing = candidates_by_url.get(url)
        if (
            existing is None
            or candidate["provider_priority"] < existing["provider_priority"]
            or (
                candidate["provider_priority"] == existing["provider_priority"]
                and candidate.get("team_id")
                and not existing.get("team_id")
            )
        ):
            candidates_by_url[url] = candidate

    for source in event.get("source_channels", []):
        add(source)
    for team_id in (event.get("away_team_id"), event.get("home_team_id")):
        if team_id:
            for channel in team_feed_map.get(team_id, []):
                add(channel, team_id)

    candidates = list(candidates_by_url.values())
    if not settings.get("use_backup_feeds"):
        candidates = [candidate for candidate in candidates if candidate["feed_type"] != "backup"]
    elif any(candidate["feed_type"] != "backup" for candidate in candidates):
        if rule.get("feed_preference") != "all":
            candidates = [candidate for candidate in candidates if candidate["feed_type"] != "backup"]

    if candidates:
        winning_priority = min(candidate["provider_priority"] for candidate in candidates)
        candidates = [
            candidate
            for candidate in candidates
            if candidate["provider_priority"] == winning_priority
        ]

    preference = rule.get("feed_preference", "best")
    favorite_team_id = rule.get("scope_id") if rule.get("scope_type") == "team" else ""
    rank = {
        "national": 20,
        "event": 25,
        "home": 30,
        "away": 31,
        "spanish": 50,
        "backup": 90,
    }
    if preference == "favorite" and favorite_team_id:
        for candidate in candidates:
            if candidate["team_id"] == favorite_team_id:
                rank[candidate["feed_type"]] = -10
    elif preference == "home":
        rank["home"] = -10
    elif preference == "away":
        rank["away"] = -10
    elif preference == "national":
        rank["national"] = -10
        rank["event"] = 0

    candidates.sort(
        key=lambda candidate: (
            -10
            if favorite_team_id and candidate["team_id"] == favorite_team_id
            else rank.get(candidate["feed_type"], 60),
            str(candidate["channel"].get("name", "")).lower(),
        )
    )

    expanded_feeds = event.get("expanded_feeds")
    if expanded_feeds is None:
        expanded_feeds = rule.get("scope_type") == "team"
    if not expanded_feeds:
        return candidates[:1]
    return candidates
