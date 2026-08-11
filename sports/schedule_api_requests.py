from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import date
from pathlib import Path

import sports as _s
from . import schedule_api as _base


_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_ERROR_TEXT = 600


def _american_football_games_url(
    dataset: dict,
    *,
    schedule_date: date,
    timezone: str,
) -> str:
    """Build the smallest documented API-NFL schedule request.

    API-NFL accepts ``date`` as a complete game filter and supports ``timezone``
    independently.  We intentionally do not send a derived season here.  The
    returned day can contain both NFL and NCAA games; the existing local parser
    filters the response by the dataset's stable remote league id (1 or 2).
    This avoids false API errors around season transitions while preserving the
    same local cache/event model.
    """
    base_url = str(dataset.get("base_url") or "").strip()
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Schedule API adapter URL is invalid.")
    path = parsed.path.rstrip("/")
    path = f"{path}/games" if path else "/games"
    query = urllib.parse.urlencode(
        {
            "date": schedule_date.isoformat(),
            "timezone": str(timezone),
        }
    )
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, path, "", query, "")
    )


def _flatten_error_values(value) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            nested = _flatten_error_values(item)
            if nested:
                values.extend(
                    f"{key}: {message}" if key else message for message in nested
                )
            elif key:
                values.append(str(key))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            values.extend(_flatten_error_values(item))
    elif value not in (None, "", False):
        values.append(str(value))
    return values


def _api_error_text(errors, *, api_key: str = "") -> str:
    messages = list(dict.fromkeys(_flatten_error_values(errors)))
    text = "; ".join(messages).strip()
    if api_key:
        text = text.replace(api_key, "[redacted]")
    if len(text) > _MAX_ERROR_TEXT:
        text = text[: _MAX_ERROR_TEXT - 1].rstrip() + "…"
    return text


def _read_http_error(exc: urllib.error.HTTPError, *, api_key: str) -> str:
    detail = ""
    try:
        raw = exc.read(64 * 1024)
        payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
        if isinstance(payload, dict):
            detail = _api_error_text(payload.get("errors"), api_key=api_key)
            if not detail:
                detail = str(payload.get("message") or "").strip()
    except Exception:
        detail = ""
    if api_key and detail:
        detail = detail.replace(api_key, "[redacted]")
    if len(detail) > _MAX_ERROR_TEXT:
        detail = detail[: _MAX_ERROR_TEXT - 1].rstrip() + "…"
    suffix = f": {detail}" if detail else ""
    return f"HTTP {int(getattr(exc, 'code', 0) or 0)}{suffix}"


def _open_api_nfl(request: urllib.request.Request, *, timeout: int = 30):
    """Open API-NFL without urllib's default Python User-Agent header.

    API-NFL's current integration guide warns that automatically added headers
    can cause the service to return an API-level error.  ``build_opener``
    normally installs ``User-Agent: Python-urllib/...`` in ``addheaders``;
    clearing that list keeps our application-level request header limited to
    ``x-apisports-key`` while the HTTP stack still supplies required transport
    headers such as Host.
    """
    opener = urllib.request.build_opener()
    opener.addheaders = []
    return opener.open(request, timeout=timeout)


def _fetch_american_football_dataset_date(
    db_path: Path | str,
    *,
    dataset: dict,
    api_key: str,
    schedule_date: date,
    season: int,
    timezone: str,
    fetched_on: str,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    _s._raise_if_cancelled(cancel_check)
    url = _american_football_games_url(
        dataset,
        schedule_date=schedule_date,
        timezone=timezone,
    )
    # Keep the existing request-key contract.  It intentionally includes the
    # local season and league identity so old cache rows are invalidated across
    # a season boundary even though the remote date request no longer needs a
    # season parameter.
    request_key = _s._schedule_api_request_key(
        dataset,
        schedule_date=schedule_date,
        season=season,
        timezone=timezone,
    )
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
                    "Schedule API response exceeded the 8 MB safety limit."
                )
            remaining_header = response.headers.get(
                "x-ratelimit-requests-remaining"
            )
            minute_remaining_header = response.headers.get(
                "X-RateLimit-Remaining"
            )
    except urllib.error.HTTPError as exc:
        detail = _read_http_error(exc, api_key=api_key)
        raise ValueError(
            f"Could not fetch {dataset['label']} schedule for "
            f"{schedule_date.isoformat()} ({detail})."
        ) from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            f"Could not fetch {dataset['label']} schedule for "
            f"{schedule_date.isoformat()} ({type(exc).__name__})."
        ) from exc

    _s._raise_if_cancelled(cancel_check)
    try:
        payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except Exception as exc:
        raise ValueError("Schedule API returned invalid JSON.") from exc

    errors = payload.get("errors") if isinstance(payload, dict) else None
    if errors:
        detail = _api_error_text(errors, api_key=api_key)
        suffix = f": {detail}" if detail else "."
        raise ValueError(f"Schedule API reported an error{suffix}")

    games = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(games, list):
        raise ValueError("Schedule API did not return a games list.")

    fetched_at = _s._now_iso()
    remaining = None
    minute_remaining = None
    try:
        if remaining_header is not None:
            remaining = int(remaining_header)
    except (TypeError, ValueError):
        pass
    try:
        if minute_remaining_header is not None:
            minute_remaining = int(minute_remaining_header)
    except (TypeError, ValueError):
        pass

    with closing(_s._connect(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            DELETE FROM sports_schedule_events
            WHERE source = ? AND league_id = ? AND season = ? AND schedule_date = ?
            """,
            (
                dataset["source"],
                dataset["league_id"],
                season,
                schedule_date.isoformat(),
            ),
        )
        stored = 0
        for game in games:
            fields = _s._schedule_api_game_fields(dataset, game, timezone)
            if not fields:
                continue
            home = fields["home"]
            away = fields["away"]
            _s._upsert_schedule_api_team(conn, dataset=dataset, team=home)
            _s._upsert_schedule_api_team(conn, dataset=dataset, team=away)
            conn.execute(
                """
                INSERT OR REPLACE INTO sports_schedule_events
                    (source, api_event_id, league_id, season, schedule_date,
                     scheduled_start, status_short, status_long,
                     home_api_id, home_name, home_logo,
                     away_api_id, away_name, away_logo,
                     raw_json, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset["source"],
                    fields["event_id"],
                    dataset["league_id"],
                    season,
                    schedule_date.isoformat(),
                    fields["scheduled_start"],
                    fields["status_short"],
                    fields["status_long"],
                    str(home.get("id") or ""),
                    str(home.get("name") or ""),
                    str(home.get("logo") or ""),
                    str(away.get("id") or ""),
                    str(away.get("name") or ""),
                    str(away.get("logo") or ""),
                    json.dumps(fields["raw"], separators=(",", ":")),
                    fetched_at,
                ),
            )
            stored += 1

        conn.execute(
            """
            INSERT OR REPLACE INTO sports_schedule_api_cache
                (source, league_id, season, schedule_date, request_key, fetched_on,
                 fetched_at, result_count, remaining_quota)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset["source"],
                dataset["league_id"],
                season,
                schedule_date.isoformat(),
                request_key,
                fetched_on,
                fetched_at,
                stored,
                remaining,
            ),
        )
        conn.commit()

    return {
        "dataset": dataset["id"],
        "scope": dataset["label"],
        "date": schedule_date.isoformat(),
        "games": stored,
        "remaining_quota": remaining,
        "minute_remaining": minute_remaining,
        "fetched_at": fetched_at,
        "url": url,
        "request_key": request_key,
    }


def _fetch_schedule_api_dataset_date(
    db_path: Path | str,
    *,
    dataset: dict,
    api_key: str,
    schedule_date: date,
    season: int,
    timezone: str,
    fetched_on: str,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    """Compatibility fetcher used by the sports facade.

    Baseball keeps the already-working implementation untouched.  Only the
    API-NFL/API-NCAA request envelope is specialized here.
    """
    if dataset.get("request_mode") != "american_football":
        return _base._fetch_schedule_api_dataset_date(
            db_path,
            dataset=dataset,
            api_key=api_key,
            schedule_date=schedule_date,
            season=season,
            timezone=timezone,
            fetched_on=fetched_on,
            cancel_check=cancel_check,
        )
    return _fetch_american_football_dataset_date(
        db_path,
        dataset=dataset,
        api_key=api_key,
        schedule_date=schedule_date,
        season=season,
        timezone=timezone,
        fetched_on=fetched_on,
        cancel_check=cancel_check,
    )
