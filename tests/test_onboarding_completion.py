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
    assert completed["answers"]["initial_refresh_required"] is True
    assert completed["answers"]["initial_refresh_pending"] is True
    assert onboarding.initial_refresh_required(completed) is True

    assert onboarding.claim_initial_refresh(db_path, provider_configured=True) is True
    claimed = onboarding.get_state(db_path, provider_configured=True)
    assert claimed["answers"]["initial_refresh_pending"] is False
    assert claimed["answers"]["initial_refresh_in_progress"] is True
    assert claimed["answers"]["initial_refresh_claimed_at"]

    # A second caller cannot stack another full update while the claimed one is active.
    assert onboarding.claim_initial_refresh(db_path, provider_configured=True) is False

    finished = onboarding.finish_initial_refresh(
        db_path,
        provider_configured=True,
        success=True,
    )
    assert finished["answers"]["initial_refresh_in_progress"] is False
    assert finished["answers"]["initial_refresh_pending"] is False
    assert finished["answers"]["initial_refresh_completed_at"]
    assert onboarding.initial_refresh_required(finished) is False

    # Repeating the completion request must not resurrect the one-shot.
    repeated = onboarding.mark_complete(db_path, provider_configured=True)
    assert repeated["answers"]["initial_refresh_pending"] is False
    assert onboarding.claim_initial_refresh(db_path, provider_configured=True) is False


def test_failed_initial_refresh_rearms_gate_for_explicit_retry(tmp_path: Path):
    db_path = tmp_path / "picker.db"
    onboarding.update_state(db_path, provider_configured=True, current_step=7)
    onboarding.mark_complete(db_path, provider_configured=True)
    assert onboarding.claim_initial_refresh(db_path, provider_configured=True) is True

    failed = onboarding.finish_initial_refresh(
        db_path,
        provider_configured=True,
        success=False,
        error="US public EPG did not download.",
    )
    assert failed["answers"]["initial_refresh_pending"] is True
    assert failed["answers"]["initial_refresh_in_progress"] is False
    assert failed["answers"]["initial_refresh_error"] == "US public EPG did not download."
    assert onboarding.initial_refresh_required(failed) is True

    assert onboarding.claim_initial_refresh(db_path, provider_configured=True) is True
    retried = onboarding.get_state(db_path, provider_configured=True)
    assert "initial_refresh_error" not in retried["answers"]
    assert retried["answers"]["initial_refresh_in_progress"] is True
