from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sports as _s


SCHEDULE_FETCH_CONCURRENCY = 2
SCHEDULE_API_HEALTH_SETTING = "__schedule_api_health"


@dataclass(frozen=True)
class ScheduleFetchWork:
    dataset: dict
    schedule_date: date
    season: int
    timezone: str
    fetched_on: str
    had_cache: bool


def schedule_api_refresh_health(db_path: Path | str) -> dict:
    """Return persisted, credential-free refresh-attempt health."""
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM sports_settings WHERE key = ?",
            (SCHEDULE_API_HEALTH_SETTING,),
        ).fetchone()
    if not row:
        return {"datasets": {}, "last_refresh_at": None, "last_warning": ""}
    value = _s._json_load(row["value"], {})
    if not isinstance(value, dict):
        return {"datasets": {}, "last_refresh_at": None, "last_warning": ""}
    datasets = value.get("datasets")
    value["datasets"] = datasets if isinstance(datasets, dict) else {}
    value.setdefault("last_refresh_at", None)
    value.setdefault("last_warning", "")
    return value


def _save_schedule_api_refresh_health(db_path: Path | str, health: dict) -> None:
    _s.init_db(db_path)
    with closing(_s._connect(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sports_settings(key, value) VALUES (?, ?)",
            (SCHEDULE_API_HEALTH_SETTING, json.dumps(health, separators=(",", ":"))),
        )
        conn.commit()


def _record_schedule_api_refresh_health(
    db_path: Path | str,
    *,
    plan: dict,
    fetched: list[dict],
    cached: list[dict],
    failures: list[dict],
    reference_failures: list[dict],
    warning: str,
) -> None:
    health = schedule_api_refresh_health(db_path)
    datasets = dict(health.get("datasets") or {})
    recorded_at = _s._now_iso()

    for planned in plan.get("datasets") or []:
        dataset_id = str(planned.get("id") or "")
        if not dataset_id:
            continue
        entry = dict(datasets.get(dataset_id) or {})
        entry.update(
            {
                "id": dataset_id,
                "label": str(planned.get("label") or dataset_id),
                "product": str(planned.get("product") or ""),
            }
        )

        successes = [
            item for item in fetched if str(item.get("dataset") or "") == dataset_id
        ]
        dataset_failures = [
            item for item in failures if str(item.get("dataset") or "") == dataset_id
        ]
        cache_hits = [
            item for item in cached if str(item.get("dataset") or "") == dataset_id
        ]
        dataset_reference_failures = [
            item
            for item in reference_failures
            if str(item.get("dataset") or "") == dataset_id
        ]

        if successes or dataset_failures:
            entry["last_attempt_at"] = recorded_at
            entry["last_attempt_dates"] = sorted(
                {
                    str(item.get("date") or "")
                    for item in [*successes, *dataset_failures]
                    if str(item.get("date") or "")
                }
            )

        if successes:
            successful_times = [
                str(item.get("fetched_at") or "") for item in successes if item.get("fetched_at")
            ]
            entry["last_success_at"] = max(successful_times) if successful_times else recorded_at

        if dataset_failures:
            errors = list(
                dict.fromkeys(
                    str(item.get("error") or "Schedule API refresh failed.")
                    for item in dataset_failures
                )
            )
            entry["last_error"] = " ".join(errors)
            entry["last_attempt_status"] = "partial" if successes else "failed"
            entry["stale_cache_used"] = any(
                bool(item.get("stale_cache_used")) for item in dataset_failures
            )
        elif successes:
            entry["last_error"] = ""
            entry["last_attempt_status"] = "success"
            entry["stale_cache_used"] = False
        elif cache_hits:
            entry["last_cache_use_at"] = recorded_at

        if dataset_reference_failures:
            entry["reference_error"] = " ".join(
                dict.fromkeys(
                    str(item.get("error") or "Schedule API reference refresh failed.")
                    for item in dataset_reference_failures
                )
            )
            entry["reference_error_at"] = recorded_at
        elif dataset_id == "ncaa" and successes:
            # A successful NCAA schedule fetch does not guarantee standings/reference
            # data was requested, so only clear an old reference error when this run
            # did not report one and the plan still requires that reference dataset.
            if "ncaa_membership" in set(plan.get("reference_datasets") or []):
                entry["reference_error"] = ""

        datasets[dataset_id] = entry

    health.update(
        {
            "datasets": datasets,
            "last_refresh_at": recorded_at,
            "last_warning": str(warning or ""),
        }
    )
    _save_schedule_api_refresh_health(db_path, health)


async def _refresh_reference_if_needed(
    db_path: Path | str,
    *,
    dataset: dict,
    plan: dict,
    api_key: str,
    season: int,
    cancel_check: _s.CancelCheck,
) -> tuple[dict | None, dict | None]:
    if (
        dataset["id"] != "ncaa"
        or "ncaa_membership" not in set(plan.get("reference_datasets") or [])
    ):
        return None, None
    try:
        result = await asyncio.to_thread(
            _s._refresh_ncaa_reference_metadata_if_needed,
            db_path,
            api_key=api_key,
            season=season,
            force=False,
            cancel_check=cancel_check,
        )
        return result, None
    except ValueError as exc:
        return None, {
            "dataset": str(dataset.get("id") or "ncaa"),
            "scope": str(dataset.get("label") or "NCAA Football"),
            "kind": "reference",
            "error": str(exc),
        }


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
) -> tuple[dict | None, dict | None, dict | None]:
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
            return result, None, None
        except ValueError as exc:
            failure = {
                "dataset": str(work.dataset.get("id") or ""),
                "scope": str(work.dataset.get("label") or ""),
                "date": work.schedule_date.isoformat(),
                "error": str(exc),
                "stale_cache_used": bool(work.had_cache),
            }
            stale = None
            if work.had_cache:
                stale = {
                    "dataset": work.dataset["id"],
                    "date": work.schedule_date.isoformat(),
                    "stale": True,
                }
            return None, failure, stale


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
            "failures": [],
            "reference_failures": [],
            "warning": "",
            "plan": plan,
        }
    if not plan.get("datasets"):
        return {
            "enabled": True,
            "used": False,
            "fetched": [],
            "cached": [],
            "failures": [],
            "reference_failures": [],
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
    failures: list[dict] = []
    warnings: list[str] = []
    reference: list[dict] = []
    reference_failures: list[dict] = []
    work_items: list[ScheduleFetchWork] = []

    for dataset in plan["datasets"]:
        _s._raise_if_cancelled(cancel_check)
        season = _s._schedule_api_dataset_season(dataset, local_now)
        reference_result, reference_failure = await _refresh_reference_if_needed(
            db_path,
            dataset=dataset,
            plan=plan,
            api_key=api_key,
            season=season,
            cancel_check=cancel_check,
        )
        if reference_result is not None:
            reference.append(reference_result)
        if reference_failure is not None:
            reference_failures.append(reference_failure)
            warnings.append(str(reference_failure.get("error") or ""))

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
    for result, failure, stale in results:
        if result is not None:
            fetched.append(result)
        if failure is not None:
            failures.append(failure)
            warnings.append(str(failure.get("error") or ""))
        if stale is not None:
            cached.append(stale)

    warning = " ".join(dict.fromkeys(item for item in warnings if item))
    _record_schedule_api_refresh_health(
        db_path,
        plan=plan,
        fetched=fetched,
        cached=cached,
        failures=failures,
        reference_failures=reference_failures,
        warning=warning,
    )

    available = _available_canonical_event_count(db_path, plan)
    return {
        "enabled": True,
        "used": available > 0,
        "fetched": fetched,
        "cached": cached,
        "failures": failures,
        "reference": reference,
        "reference_failures": reference_failures,
        "warning": warning,
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
