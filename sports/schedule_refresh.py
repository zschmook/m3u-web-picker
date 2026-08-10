from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sports as _s


SCHEDULE_FETCH_CONCURRENCY = 2


@dataclass(frozen=True)
class ScheduleFetchWork:
    dataset: dict
    schedule_date: date
    season: int
    timezone: str
    fetched_on: str
    had_cache: bool


async def _refresh_reference_if_needed(
    db_path: Path | str,
    *,
    dataset: dict,
    plan: dict,
    api_key: str,
    season: int,
    cancel_check: _s.CancelCheck,
) -> tuple[dict | None, str]:
    if (
        dataset["id"] != "ncaa"
        or "ncaa_membership" not in set(plan.get("reference_datasets") or [])
    ):
        return None, ""
    try:
        result = await asyncio.to_thread(
            _s._refresh_ncaa_reference_metadata_if_needed,
            db_path,
            api_key=api_key,
            season=season,
            force=False,
            cancel_check=cancel_check,
        )
        return result, ""
    except ValueError as exc:
        return None, str(exc)


def _cached_request_matches(
    db_path: Path | str,
    *,
    dataset: dict,
    schedule_date: date,
    season: int,
    fetched_on: str,
    request_key: str,
) -> tuple[bool, bool]:
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            """
            SELECT fetched_on, request_key FROM sports_schedule_api_cache
            WHERE source = ? AND league_id = ? AND season = ? AND schedule_date = ?
            """,
            (
                dataset["source"],
                dataset["league_id"],
                season,
                schedule_date.isoformat(),
            ),
        ).fetchone()
    if not row:
        return False, False
    current = (
        str(row["fetched_on"] or "") == fetched_on
        and str(row["request_key"] or "") == request_key
    )
    return True, current


async def _fetch_dataset_date(
    db_path: Path | str,
    work: ScheduleFetchWork,
    *,
    api_key: str,
    cancel_check: _s.CancelCheck,
    semaphore: asyncio.Semaphore,
) -> tuple[dict | None, str, dict | None]:
    async with semaphore:
        _s._raise_if_cancelled(cancel_check)
        try:
            result = await asyncio.to_thread(
                _s._fetch_schedule_api_dataset_date,
                db_path,
                dataset=work.dataset,
                api_key=api_key,
                schedule_date=work.schedule_date,
                season=work.season,
                timezone=work.timezone,
                fetched_on=work.fetched_on,
                cancel_check=cancel_check,
            )
            return result, "", None
        except ValueError as exc:
            stale = None
            if work.had_cache:
                stale = {
                    "dataset": work.dataset["id"],
                    "date": work.schedule_date.isoformat(),
                    "stale": True,
                }
            return None, str(exc), stale


def _available_canonical_event_count(db_path: Path | str, plan: dict) -> int:
    available = 0
    with closing(_s._connect(db_path)) as conn:
        for dataset in plan["datasets"]:
            available += int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM sports_schedule_events
                    WHERE source = ? AND league_id = ?
                    """,
                    (dataset["source"], dataset["league_id"]),
                ).fetchone()[0]
            )
    return available


async def refresh_schedule_api_if_due_async(
    db_path: Path | str,
    scan_anchor: datetime | None = None,
    *,
    force: bool = False,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    """Refresh independent schedule datasets with bounded concurrent I/O."""
    settings = _s.get_settings(db_path)
    timezone = ZoneInfo(str(settings.get("timezone", "America/New_York")))
    local_now = (scan_anchor or datetime.now().astimezone()).astimezone(timezone)
    state = _s.schedule_api_status(db_path)
    plan = state.get("plan") or _s.schedule_api_request_plan(db_path)
    if not state.get("effective"):
        return {
            "enabled": False,
            "used": False,
            "fetched": [],
            "cached": [],
            "warning": "",
            "plan": plan,
        }
    if not plan.get("datasets"):
        return {
            "enabled": True,
            "used": False,
            "fetched": [],
            "cached": [],
            "warning": "",
            "plan": plan,
            "message": (
                "No API-backed sports are selected; legacy matching remains active."
            ),
        }

    api_key = _s._schedule_api_secret(db_path)
    required_dates = _s._schedule_api_required_dates(local_now, settings)
    fetched_on = local_now.date().isoformat()
    timezone_name = str(settings.get("timezone", "America/New_York"))
    fetched: list[dict] = []
    cached: list[dict] = []
    warnings: list[str] = []
    reference: list[dict] = []
    work_items: list[ScheduleFetchWork] = []

    for dataset in plan["datasets"]:
        _s._raise_if_cancelled(cancel_check)
        season = _s._schedule_api_dataset_season(dataset, local_now)
        reference_result, reference_warning = await _refresh_reference_if_needed(
            db_path,
            dataset=dataset,
            plan=plan,
            api_key=api_key,
            season=season,
            cancel_check=cancel_check,
        )
        if reference_result is not None:
            reference.append(reference_result)
        if reference_warning:
            warnings.append(reference_warning)

        for schedule_date in required_dates:
            _s._raise_if_cancelled(cancel_check)
            request_key = _s._schedule_api_request_key(
                dataset,
                schedule_date=schedule_date,
                season=season,
                timezone=timezone_name,
            )
            had_cache, current_cache = _cached_request_matches(
                db_path,
                dataset=dataset,
                schedule_date=schedule_date,
                season=season,
                fetched_on=fetched_on,
                request_key=request_key,
            )
            if not force and current_cache:
                cached.append(
                    {"dataset": dataset["id"], "date": schedule_date.isoformat()}
                )
                continue
            work_items.append(
                ScheduleFetchWork(
                    dataset=dataset,
                    schedule_date=schedule_date,
                    season=season,
                    timezone=timezone_name,
                    fetched_on=fetched_on,
                    had_cache=had_cache,
                )
            )

    semaphore = asyncio.Semaphore(SCHEDULE_FETCH_CONCURRENCY)
    results = await asyncio.gather(
        *(
            _fetch_dataset_date(
                db_path,
                work,
                api_key=api_key,
                cancel_check=cancel_check,
                semaphore=semaphore,
            )
            for work in work_items
        )
    )
    for result, warning, stale in results:
        if result is not None:
            fetched.append(result)
        if warning:
            warnings.append(warning)
        if stale is not None:
            cached.append(stale)

    available = _available_canonical_event_count(db_path, plan)
    return {
        "enabled": True,
        "used": available > 0,
        "fetched": fetched,
        "cached": cached,
        "reference": reference,
        "warning": " ".join(dict.fromkeys(warnings)),
        "canonical_events_available": available,
        "plan": plan,
    }


def _run_coroutine_sync(coro):
    """Preserve the legacy synchronous API even if a caller already has a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


def refresh_schedule_api_if_due(
    db_path: Path | str,
    scan_anchor: datetime | None = None,
    *,
    force: bool = False,
    cancel_check: _s.CancelCheck = None,
) -> dict:
    return _run_coroutine_sync(
        refresh_schedule_api_if_due_async(
            db_path,
            scan_anchor,
            force=force,
            cancel_check=cancel_check,
        )
    )
