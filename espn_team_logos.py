from __future__ import annotations

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

# Only explicit ESPN league mappings are queried. Unsupported sports keep their
# existing provider/Xtream artwork without speculative network requests.
ESPN_LEAGUES: dict[str, tuple[str, str]] = {
    "mlb": ("baseball", "mlb"),
    "nhl": ("hockey", "nhl"),
    "nba": ("basketball", "nba"),
    "wnba": ("basketball", "wnba"),
    "nfl": ("football", "nfl"),
    "ncaaf-fbs": ("football", "college-football"),
    "ncaaf-fcs": ("football", "college-football"),
    "ncaaf-d2": ("football", "college-football"),
    "ncaaf-d3": ("football", "college-football"),
    "ncaab-men": ("basketball", "mens-college-basketball"),
    "ncaab-women": ("basketball", "womens-college-basketball"),
    "ncaa-baseball": ("baseball", "college-baseball"),
}

_LOCK = threading.RLock()
_MEMORY_INDEX: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}


def _catalog_dir() -> Path:
    path = load_settings().data_dir / "espn_logo_catalog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize(value: object) -> str:
    text = str(value or "").casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", text)


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
            raise ValueError("ESPN team catalog is too large.")
        payload = response.read(MAX_CATALOG_BYTES + 1)
        if len(payload) > MAX_CATALOG_BYTES:
            raise ValueError("ESPN team catalog is too large.")
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("ESPN team catalog response is not an object.")
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
                # Never let a generic/ambiguous alias resolve to the wrong team.
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
            "https://site.api.espn.com/apis/site/v2/sports/"
            f"{urllib.parse.quote(espn_sport, safe='')}/"
            f"{urllib.parse.quote(espn_league, safe='.')}/teams?limit=1000"
        )
        try:
            index = _build_index(_fetch_json(url))
            _atomic_write_text(path, json.dumps({"index": index}, sort_keys=True))
        except Exception:
            # Keep using the last known team-to-logo map during a transient ESPN
            # outage. This file contains URLs only; image bytes live elsewhere.
            index = _load_index(path, allow_stale=True) or {}

        _MEMORY_INDEX[cache_key] = (now, index)
        return index


def espn_full_default_url(league_id: object, team_name: object) -> str:
    """Return ESPN's full/default logo URL for a strongly matched team name."""
    mapping = ESPN_LEAGUES.get(str(league_id or "").strip().casefold())
    key = _normalize(team_name)
    if not mapping or not key:
        return ""
    return _clean_http_url(_team_index(*mapping).get(key, ""))
