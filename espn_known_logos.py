from __future__ import annotations

import re
import urllib.parse

from sports_taxonomy import MLB_TEAMS


_MLB_CODE_BY_NAME: dict[str, str] = {}


def _normalize(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


for _slug, _display_name, _aliases in MLB_TEAMS:
    if not _aliases:
        continue
    # The first alias in our canonical MLB taxonomy is the ESPN team code.
    # Examples: PHI, WSH, SD, KC, ATH. These map directly to ESPN's ordinary
    # full/default CDN path and give us a deterministic fallback if the ESPN
    # team-list endpoint is unavailable or omits a team's logos[] entry.
    _code = str(_aliases[0]).strip().casefold()
    for _value in (_display_name, _slug.replace("-", " "), *_aliases):
        _key = _normalize(_value)
        if _key:
            _MLB_CODE_BY_NAME.setdefault(_key, _code)


def direct_full_default_url(league_id: object, team_name: object) -> str:
    """Return a deterministic ESPN full/default URL for known MLB teams."""
    if str(league_id or "").strip().casefold() != "mlb":
        return ""
    code = _MLB_CODE_BY_NAME.get(_normalize(team_name), "")
    if not code:
        return ""
    return (
        "https://a.espncdn.com/i/teamlogos/mlb/500/"
        f"{urllib.parse.quote(code, safe='')}.png"
    )
