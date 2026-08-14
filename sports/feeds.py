from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

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


def _preferred_feed_logo(
    event: dict,
    feed: dict,
    channel: dict,
    team_catalog: dict[str, dict],
) -> str:
    """Choose stable artwork for a generated feed.

    Team/API artwork is preferred over provider channel artwork. Provider logos
    are often generic event tiles and are more likely to be short-lived or
    hotlink-protected. For a league-level event feed with no explicit team feed,
    use the away team first because it is the first team in the displayed
    "Away at Home" event name, then the home team.
    """
    feed_team_id = str(feed.get("team_id") or "")
    home_team_id = str(event.get("home_team_id") or "")
    away_team_id = str(event.get("away_team_id") or "")

    if feed_team_id == home_team_id:
        candidates = (
            str(event.get("api_home_logo") or ""),
            _catalog_team_logo(team_catalog, home_team_id),
        )
    elif feed_team_id == away_team_id:
        candidates = (
            str(event.get("api_away_logo") or ""),
            _catalog_team_logo(team_catalog, away_team_id),
        )
    else:
        candidates = (
            str(event.get("api_away_logo") or ""),
            _catalog_team_logo(team_catalog, away_team_id),
            str(event.get("api_home_logo") or ""),
            _catalog_team_logo(team_catalog, home_team_id),
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
