from __future__ import annotations

from datetime import datetime
from pathlib import Path

import sports as _s


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
        "schedule_api": _s.schedule_api_status(db_path),
    }
