from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import sports as _s


def _conference_team_map(db_path) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for item in _s.catalog_payload(db_path, scope_type="conference"):
        teams = list((item.get("metadata") or {}).get("teams") or [])
        if teams:
            output[str(item["id"])] = teams
    return output


def _conference_matches(
    event: dict,
    conference_id: str,
    conference_teams: dict[str, list[str]] | None = None,
) -> bool:
    if event.get("league_id") != "ncaaf-fbs":
        return False
    team_names = list((conference_teams or {}).get(conference_id) or [])
    if not team_names:
        legacy_id = conference_id.replace("ncaaf-fbs:", "ncaaf:")
        team_names = _s.CONFERENCE_TEAMS.get(legacy_id, [])
    participant_text = _s._normalize(
        f"{event.get('away_team_name', '')} {event.get('home_team_name', '')}"
    )
    return any(_s._normalize(team) in participant_text for team in team_names)


def _build_rule_index(
    rules: list[dict],
    conference_teams: dict[str, list[str]] | None = None,
) -> dict:
    by_scope: dict[str, dict[str, list[dict]]] = {
        scope: defaultdict(list) for scope in _s.SCOPE_TYPES
    }
    for rule in rules:
        scope_type = str(rule.get("scope_type", ""))
        scope_id = str(rule.get("scope_id", ""))
        if scope_type in by_scope and scope_id:
            by_scope[scope_type][scope_id].append(rule)
    return {
        "by_scope": {scope: dict(values) for scope, values in by_scope.items()},
        "rules": rules,
        "conference_teams": conference_teams or {},
    }


def _matching_rules(event: dict, rules: list[dict] | dict) -> list[dict]:
    if not isinstance(rules, dict) or "by_scope" not in rules:
        rules = _build_rule_index(list(rules))
    by_scope = rules["by_scope"]
    matched: dict[int, dict] = {}

    def add(items: Iterable[dict]) -> None:
        for rule in items:
            matched[int(rule["id"])] = rule

    league_id = str(event.get("league_id", "") or "")
    if league_id:
        add(by_scope["league"].get(league_id, []))
    for team_id in {event.get("away_team_id"), event.get("home_team_id")}:
        if team_id:
            add(by_scope["team"].get(str(team_id), []))
    for conference_id, conference_rules in by_scope["conference"].items():
        if _conference_matches(event, conference_id, rules.get("conference_teams")):
            add(conference_rules)

    event_sports = set(event.get("sport_tags", [])) | {event.get("sport_id", "")}
    source_text = event.get("source_text", "")
    for sport_id, sport_rules in by_scope["sport"].items():
        if sport_id in event_sports or any(
            _s.re.search(pattern, source_text, _s.re.I)
            for pattern in _s.SPORT_PATTERNS.get(sport_id, [])
        ):
            add(sport_rules)

    return sorted(
        matched.values(),
        key=lambda rule: (_s.RULE_PRIORITY.get(rule["scope_type"], 99), rule["id"]),
    )


def _explicit_team_rules(event: dict, matched_rules: Iterable[dict]) -> list[dict]:
    participant_ids = {
        str(value)
        for value in (event.get("away_team_id"), event.get("home_team_id"))
        if value
    }
    return [
        rule
        for rule in matched_rules
        if rule.get("scope_type") == "team"
        and str(rule.get("scope_id", "")) in participant_ids
    ]


def _select_controlling_rule(event: dict, matched_rules: list[dict]) -> tuple[dict, bool]:
    """Choose one controlling rule without duplicating a logical game."""
    team_rules = _explicit_team_rules(event, matched_rules)
    if team_rules:
        return team_rules[0], True
    return matched_rules[0], False
