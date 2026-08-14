from __future__ import annotations

import difflib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from settings import load_settings


CATALOG_TTL_SECONDS = 24 * 60 * 60
MAX_CATALOG_BYTES = 8 * 1024 * 1024
ESPN_CORE_API = "https://sports.core.api.espn.com/v2"
ESPN_SITE_API = "https://site.api.espn.com/apis/site/v2/sports"

# Known aliases take precedence, but they are no longer a gate. Every generated
# matchup may ask ESPN for a logo. Unknown leagues fall through to ESPN's own
# sport/league discovery and a conservative league-name match.
ESPN_LEAGUES: dict[str, tuple[str, str]] = {
    "mlb": ("baseball", "mlb"),
    "ncaa-baseball": ("baseball", "college-baseball"),
    "nhl": ("hockey", "nhl"),
    "nba": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "nba-g-league": ("basketball", "nba-development"),
    "nfl": ("football", "nfl"),
    "ncaaf-fbs": ("football", "college-football"),
    "ncaaf-fcs": ("football", "college-football"),
    "ncaaf-d2": ("football", "college-football"),
    "ncaaf-d3": ("football", "college-football"),
    "ncaab-men": ("basketball", "mens-college-basketball"),
    "ncaab-women": ("basketball", "womens-college-basketball"),
    "mls": ("soccer", "usa.1"),
    "nwsl": ("soccer", "usa.nwsl"),
    "premier-league": ("soccer", "eng.1"),
    "la-liga": ("soccer", "esp.1"),
    "uefa-champions-league": ("soccer", "uefa.champions"),
    "pga-tour": ("golf", "pga"),
    "ufc": ("mma", "ufc"),
    "formula-1": ("racing", "f1"),
    "indycar": ("racing", "irl"),
}

# Most internal sport IDs already match ESPN. Only translate the ones whose
# common ESPN slug is different; unsupported ESPN sports simply return no hit.
ESPN_SPORT_ALIASES: dict[str, str] = {
    "motorsports": "racing",
    "track-field": "track-and-field",
    "rugby-union": "rugby",
    "rugby-league": "rugby",
}

_LOCK = threading.RLock()
_MEMORY_INDEX: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_MEMORY_AVAILABLE_SPORTS: tuple[float, set[str]] | None = None
_MEMORY_LEAGUES: dict[str, tuple[float, set[str]]] = {}


def _catalog_dir() -> Path:
    path = load_settings().data_dir / "espn_logo_catalog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize(value: object) -> str:
    text = str(value or "").casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


def _tokens(value: object) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(value or "").casefold())
        if token
        and token
        not in {
            "the",
            "league",
            "series",
            "championship",
            "championships",
            "division",
            "international",
        }
    }


def _clean_http_url(value: object) -> str:
    url = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return url[:4096]


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (M3U Web Picker ESPN logo catalog)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > MAX_CATALOG_BYTES:
            raise ValueError("ESPN catalog is too large.")
        payload = response.read(MAX_CATALOG_BYTES + 1)
        if len(payload) > MAX_CATALOG_BYTES:
            raise ValueError("ESPN catalog is too large.")
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("ESPN catalog response is not an object.")
    return decoded


def _full_default_logo(team: dict) -> str:
    """Return only ESPN's ordinary full/default logo variant."""
    for logo in team.get("logos") or []:
        if not isinstance(logo, dict):
            continue
        rel = {str(value).strip().casefold() for value in logo.get("rel") or []}
        if (
            "full" in rel
            and "default" in rel
            and "dark" not in rel
            and "scoreboard" not in rel
        ):
            url = _clean_http_url(logo.get("href"))
            if url:
                return url
    return ""


def _build_index(payload: dict) -> dict[str, str]:
    sports = payload.get("sports") or []
    if not sports or not isinstance(sports[0], dict):
        return {}
    leagues = sports[0].get("leagues") or []
    if not leagues or not isinstance(leagues[0], dict):
        return {}

    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for entry in leagues[0].get("teams") or []:
        if not isinstance(entry, dict):
            continue
        team = entry.get("team") or {}
        if not isinstance(team, dict):
            continue
        logo = _full_default_logo(team)
        if not logo:
            continue

        names = {
            str(team.get("displayName") or ""),
            str(team.get("shortDisplayName") or ""),
            str(team.get("abbreviation") or ""),
            str(team.get("slug") or "").replace("-", " ").replace("_", " "),
        }
        location = str(team.get("location") or "").strip()
        nickname = str(team.get("name") or team.get("nickname") or "").strip()
        if location and nickname:
            names.add(f"{location} {nickname}")

        for name in names:
            key = _normalize(name)
            if not key or key in ambiguous:
                continue
            existing = aliases.get(key)
            if existing and existing != logo:
                aliases.pop(key, None)
                ambiguous.add(key)
            else:
                aliases[key] = logo
    return aliases


def _catalog_file(espn_sport: str, espn_league: str) -> Path:
    safe = re.sub(
        r"[^a-z0-9_.-]+",
        "-",
        f"{espn_sport}__{espn_league}".casefold(),
    ).strip("-")
    return _catalog_dir() / f"{safe}.json"


def _load_index(path: Path, *, allow_stale: bool) -> dict[str, str] | None:
    try:
        if not allow_stale and time.time() - path.stat().st_mtime > CATALOG_TTL_SECONDS:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        index = payload.get("index") if isinstance(payload, dict) else None
        if isinstance(index, dict):
            return {
                str(key): _clean_http_url(value)
                for key, value in index.items()
                if key and _clean_http_url(value)
            }
    except (OSError, ValueError, TypeError):
        pass
    return None


def _team_index(espn_sport: str, espn_league: str) -> dict[str, str]:
    cache_key = (espn_sport, espn_league)
    now = time.monotonic()
    with _LOCK:
        memory = _MEMORY_INDEX.get(cache_key)
        if memory and now - memory[0] <= CATALOG_TTL_SECONDS:
            return memory[1]

        path = _catalog_file(espn_sport, espn_league)
        cached = _load_index(path, allow_stale=False)
        if cached is not None:
            _MEMORY_INDEX[cache_key] = (now, cached)
            return cached

        url = (
            f"{ESPN_SITE_API}/"
            f"{urllib.parse.quote(espn_sport, safe='')}/"
            f"{urllib.parse.quote(espn_league, safe='.')}/teams?limit=1000"
        )
        try:
            index = _build_index(_fetch_json(url))
            _atomic_write_text(path, json.dumps({"index": index}, sort_keys=True))
        except Exception:
            index = _load_index(path, allow_stale=True) or {}

        _MEMORY_INDEX[cache_key] = (now, index)
        return index


def _slug_cache_file(name: str) -> Path:
    return _catalog_dir() / f"_{name}.json"


def _load_slug_cache(path: Path, key: str) -> set[str] | None:
    try:
        if time.time() - path.stat().st_mtime > CATALOG_TTL_SECONDS:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(values, list):
            return {str(value).strip().casefold() for value in values if str(value).strip()}
    except (OSError, ValueError, TypeError):
        pass
    return None


def _ref_slugs(payload: dict, marker: str) -> set[str]:
    output: set[str] = set()
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("$ref") or "")
        match = re.search(rf"/{re.escape(marker)}/([^/?]+)", ref)
        if match:
            output.add(urllib.parse.unquote(match.group(1)).casefold())
    return output


def _available_espn_sports() -> set[str]:
    global _MEMORY_AVAILABLE_SPORTS
    now = time.monotonic()
    with _LOCK:
        if (
            _MEMORY_AVAILABLE_SPORTS
            and now - _MEMORY_AVAILABLE_SPORTS[0] <= CATALOG_TTL_SECONDS
        ):
            return _MEMORY_AVAILABLE_SPORTS[1]

        path = _slug_cache_file("sports")
        cached = _load_slug_cache(path, "sports")
        if cached is None:
            try:
                cached = _ref_slugs(
                    _fetch_json(f"{ESPN_CORE_API}/sports?limit=1000"),
                    "sports",
                )
                _atomic_write_text(
                    path,
                    json.dumps({"sports": sorted(cached)}, sort_keys=True),
                )
            except Exception:
                cached = _load_slug_cache(path, "sports") or set()

        _MEMORY_AVAILABLE_SPORTS = (now, cached)
        return cached


def _espn_sport_slug(sport_id: object) -> str:
    value = str(sport_id or "").strip().casefold()
    return ESPN_SPORT_ALIASES.get(value, value)


def _league_slugs_for_sport(espn_sport: str) -> set[str]:
    now = time.monotonic()
    with _LOCK:
        memory = _MEMORY_LEAGUES.get(espn_sport)
        if memory and now - memory[0] <= CATALOG_TTL_SECONDS:
            return memory[1]

        path = _slug_cache_file(f"leagues__{espn_sport}")
        cached = _load_slug_cache(path, "leagues")
        if cached is None:
            try:
                cached = _ref_slugs(
                    _fetch_json(
                        f"{ESPN_CORE_API}/sports/"
                        f"{urllib.parse.quote(espn_sport, safe='')}/leagues?limit=1000"
                    ),
                    "leagues",
                )
                _atomic_write_text(
                    path,
                    json.dumps({"leagues": sorted(cached)}, sort_keys=True),
                )
            except Exception:
                cached = _load_slug_cache(path, "leagues") or set()

        _MEMORY_LEAGUES[espn_sport] = (now, cached)
        return cached


def _league_match_score(requested: str, candidate: str) -> float:
    requested_norm = _normalize(requested)
    candidate_norm = _normalize(candidate)
    if not requested_norm or not candidate_norm:
        return 0.0
    if requested_norm == candidate_norm:
        return 1.0

    requested_tokens = _tokens(requested)
    candidate_tokens = _tokens(candidate.replace(".", " "))
    overlap = requested_tokens & candidate_tokens
    token_score = 0.0
    if overlap:
        token_score = len(overlap) / max(len(requested_tokens), len(candidate_tokens), 1)

    sequence = difflib.SequenceMatcher(None, requested_norm, candidate_norm).ratio()
    return max(sequence, token_score)


def _dynamic_candidates(league_id: str, sport_id: str) -> list[tuple[str, str]]:
    espn_sport = _espn_sport_slug(sport_id)
    if not espn_sport or espn_sport not in _available_espn_sports():
        return []

    slugs = _league_slugs_for_sport(espn_sport)
    if not slugs:
        return []

    ranked = sorted(
        ((_league_match_score(league_id, slug), slug) for slug in slugs),
        reverse=True,
    )
    # Conservative on purpose: a provider logo is better than an ESPN logo
    # from the wrong league/team with the same generic mascot name.
    return [
        (espn_sport, slug)
        for score, slug in ranked[:3]
        if score >= 0.72
    ]


def espn_full_default_url(
    league_id: object,
    team_name: object,
    sport_id: object = "",
) -> str:
    """Return ESPN's full/default mark, then let the caller fall back to provider art.

    The explicit mappings cover common leagues. Every other generated event
    still checks ESPN by discovering ESPN's sports/leagues and conservatively
    matching this app's sport/league ID. Results and misses are cached for the
    process/catalog TTL, so repeated scans do not hammer ESPN.
    """
    league_key = str(league_id or "").strip().casefold()
    team_key = _normalize(team_name)
    if not team_key:
        return ""

    candidates: list[tuple[str, str]] = []
    explicit = ESPN_LEAGUES.get(league_key)
    if explicit:
        candidates.append(explicit)

    for candidate in _dynamic_candidates(
        league_key,
        str(sport_id or "").strip().casefold(),
    ):
        if candidate not in candidates:
            candidates.append(candidate)

    for espn_sport, espn_league in candidates:
        logo = _clean_http_url(_team_index(espn_sport, espn_league).get(team_key, ""))
        if logo:
            return logo
    return ""
