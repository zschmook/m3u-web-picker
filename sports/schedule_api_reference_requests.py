from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import sports as _s
from .schedule_api_requests import (
    _MAX_RESPONSE_BYTES,
    _api_error_text,
    _open_api_nfl,
    _read_http_error,
)


def _refresh_ncaa_reference_metadata_if_needed(
    db_path: Path | str,
    *,
    api_key: str,
    season: int,
    force: bool = False,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    """Cache NCAA conference membership using the API-NFL-safe opener."""
    dataset = _s.SCHEDULE_API_DATASETS["ncaa"]
    cache_key = "ncaa-standings-membership"
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT fetched_at, remaining_quota, raw_json
            FROM sports_schedule_reference_cache
            WHERE source = ? AND cache_key = ? AND season = ?
            """,
            (dataset["source"], cache_key, season),
        ).fetchone()
    if row and not force:
        return {
            "cached": True,
            "fetched_at": row["fetched_at"],
            "remaining_quota": row["remaining_quota"],
        }

    _s._raise_if_cancelled(cancel_check)
    query = urllib.parse.urlencode(
        {"league": dataset["remote_league_id"], "season": season}
    )
    url = f"{dataset['base_url']}/standings?{query}"
    request = urllib.request.Request(
        url,
        headers={"x-apisports-key": api_key},
        method="GET",
    )
    try:
        with _open_api_nfl(request, timeout=30) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError(
                    "Schedule API reference response exceeded the 8 MB safety limit."
                )
            remaining_header = response.headers.get(
                "x-ratelimit-requests-remaining"
            )
    except urllib.error.HTTPError as exc:
        detail = _read_http_error(exc, api_key=api_key)
        raise ValueError(
            f"Could not refresh NCAA conference membership ({detail})."
        ) from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            "Could not refresh NCAA conference membership "
            f"({type(exc).__name__})."
        ) from exc

    try:
        payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except Exception as exc:
        raise ValueError(
            "Schedule API returned invalid NCAA standings JSON."
        ) from exc

    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        detail = _api_error_text(errors, api_key=api_key)
        suffix = f": {detail}" if detail else "."
        raise ValueError(f"Schedule API reported an NCAA standings error{suffix}")

    standings = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(standings, list):
        raise ValueError("Schedule API did not return NCAA standings.")

    conferences = _s._conference_catalog_map(db_path)
    memberships: dict[str, list[str]] = defaultdict(list)
    remaining = None
    try:
        if remaining_header is not None:
            remaining = int(remaining_header)
    except (TypeError, ValueError):
        pass

    fetched_at = _s._now_iso()
    with closing(_s._connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for item in standings:
            if not isinstance(item, dict):
                continue
            conference_name = str(item.get("conference") or "").strip()
            conference_id = _s._match_ncaa_conference_id(
                conference_name,
                conferences,
            )
            team = item.get("team") or {}
            _s._upsert_schedule_api_team(
                conn,
                dataset=dataset,
                team=team,
                conference=conference_name,
            )
            team_name = str(team.get("name") or "").strip()
            if conference_id and team_name:
                memberships[conference_id].append(team_name)

        for conference_id, team_names in memberships.items():
            item = conferences.get(conference_id) or {}
            metadata = dict(item.get("metadata") or {})
            metadata["teams"] = sorted(set(team_names), key=str.casefold)
            metadata["sport_id"] = "football"
            metadata["family"] = "Football"
            metadata["api_provider"] = _s.SCHEDULE_API_PROVIDER_NAME
            metadata["api_product"] = dataset["product"]
            _s._upsert_catalog_item(
                conn,
                scope_type="conference",
                scope_id=conference_id,
                display_name=str(item.get("name") or conference_id),
                subtitle=str(item.get("subtitle") or "FBS conference games"),
                league_id="ncaaf-fbs",
                aliases=list(item.get("aliases") or []),
                logo_url=str(item.get("logo_url") or ""),
                metadata=metadata,
                source="api-sports",
            )

        conn.execute(
            """
            INSERT OR REPLACE INTO sports_schedule_reference_cache
                (source, cache_key, season, fetched_at, remaining_quota, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                dataset["source"],
                cache_key,
                season,
                fetched_at,
                remaining,
                json.dumps(
                    {"memberships": memberships},
                    separators=(",", ":"),
                ),
            ),
        )
        conn.commit()

    return {
        "cached": False,
        "fetched_at": fetched_at,
        "remaining_quota": remaining,
        "conference_count": len(memberships),
        "team_count": sum(len(values) for values in memberships.values()),
    }
