from __future__ import annotations

from pathlib import Path

import onboarding


def test_completion_queues_exactly_one_initial_refresh(tmp_path: Path):
    db_path = tmp_path / "picker.db"

    onboarding.update_state(
        db_path,
        provider_configured=True,
        current_step=2,
        answers={
            "schedule_enabled": True,
            "schedule_time": "03:00",
            "schedule_timezone": "America/New_York",
        },
    )
    completed = onboarding.mark_complete(db_path, provider_configured=True)

    assert completed["completed"] is True
    assert completed["answers"]["schedule_time"] == "03:00"
    assert completed["answers"]["initial_refresh_pending"] is True

    assert onboarding.claim_initial_refresh(db_path, provider_configured=True) is True
    claimed = onboarding.get_state(db_path, provider_configured=True)
    assert claimed["answers"]["initial_refresh_pending"] is False
    assert claimed["answers"]["initial_refresh_claimed_at"]

    # A reload of the main screen must not launch a second full update.
    assert onboarding.claim_initial_refresh(db_path, provider_configured=True) is False
