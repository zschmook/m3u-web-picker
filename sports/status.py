from __future__ import annotations

from datetime import datetime
from pathlib import Path

import sports as _s


def schedule_api_status_payload(db_path: Path | str) -> dict:
    """Return credential-free API status with persisted per-dataset health."""
    api = dict(_s.schedule_api_status(db_path) or {})
    health = _s.schedule_api_refresh_health(db_path)
    health_by_dataset = dict(health.get("datasets") or {})
    entries = []

    for raw in api.get("apis") or []:
        entry = dict(raw)
        dataset_id = str(entry.get("id") or "")
        attempt = dict(health_by_dataset.get(dataset_id) or {})
        entry["last_attempt_at"] = attempt.get("last_attempt_at")
        entry["last_attempt_dates"] = list(attempt.get("last_attempt_dates") or [])
        entry["last_success_at"] = attempt.get("last_success_at")
        entry["last_error"] = str(attempt.get("last_error") or "")
        entry["last_attempt_status"] = str(attempt.get("last_attempt_status") or "")
        entry["stale_cache_used"] = bool(attempt.get("stale_cache_used"))
        entry["reference_error"] = str(attempt.get("reference_error") or "")
        entry["reference_error_at"] = attempt.get("reference_error_at")

        has_cache_record = bool(entry.get("last_fetch_at"))
        enabled = bool(entry.get("enabled"))
        configured = bool(entry.get("configured"))
        attempt_status = entry["last_attempt_status"]

        if not configured:
            status_code = "needs_key"
            status_label = "Needs key"
        elif not enabled:
            status_code = "disabled"
            status_label = "Disabled"
        elif attempt_status == "failed":
            if has_cache_record or entry["stale_cache_used"]:
                status_code = "stale"
                status_label = "Using cached fallback"
            else:
                status_code = "error"
                status_label = "Refresh failed"
        elif attempt_status == "partial":
            status_code = "partial"
            status_label = "Partial refresh"
        elif has_cache_record:
            status_code = "cached"
            status_label = "Cached"
        else:
            status_code = "no_cache"
            status_label = "No successful cache"

        entry["status_code"] = status_code
        entry["status_label"] = status_label
        entries.append(entry)

    api["apis"] = entries
    api["refresh_health"] = {
        "last_refresh_at": health.get("last_refresh_at"),
        "last_warning": str(health.get("last_warning") or ""),
    }
    api["dataset_summary"] = {
        "planned": len(entries),
        "cached": sum(1 for item in entries if item.get("last_fetch_at")),
        "healthy": sum(1 for item in entries if item.get("status_code") == "cached"),
        "issues": sum(
            1
            for item in entries
            if item.get("status_code") in {"error", "stale", "partial"}
        ),
        "no_cache": sum(1 for item in entries if item.get("status_code") == "no_cache"),
    }
    return api


def status_payload(db_path: Path | str, now: datetime | None = None) -> dict:
    settings = _s.get_settings(db_path)
    generated = _s.generated_rows(db_path, now=now)
    cache = _s.disabled_cache_status(db_path, now)
    next_run = _s.next_update_at(db_path, now)
    return {
        "settings": settings,
        "rules": _s.get_rules(db_path),
        "catalog": _s.catalog_payload(db_path),
        "generated": [
            {
                "id": row["id"],
                "display_name": row["display_name"],
                "subtitle": row["subtitle"],
                "feed_type": row["feed_type"],
                "assigned_number": row["assigned_number"],
                "event_start": row["event_start"],
                "generated_at": row["generated_at"],
            }
            for row in generated
        ],
        "last_scan": _s.last_scan(db_path),
        "scan": _s.scan_state(db_path, now),
        "next_update": next_run.isoformat(),
        "disabled_cache": cache,
        "numbering": _s.numbering_plan(settings),
        "schedule_api": schedule_api_status_payload(db_path),
    }
