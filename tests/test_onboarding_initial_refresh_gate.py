from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_first_guide_gate_is_loaded_and_blocks_direct_guide_access():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "/static/js/onboarding_initial_refresh_gate.js?v=onboarding-guide-gate-1" in app_source
    assert "onboarding-initial-refresh-pending" in app_source
    assert "if _onboarding_initial_refresh_required():" in app_source
    assert 'return redirect("/")' in app_source


def test_first_guide_gate_waits_for_public_epg_and_combined_publish():
    gate = (ROOT / "static" / "js" / "onboarding_initial_refresh_gate.js").read_text(
        encoding="utf-8"
    )
    worker = (ROOT / "master_update_worker.py").read_text(encoding="utf-8")

    assert "Building Your First Guide" in gate
    assert "TV Guide is temporarily locked." in gate
    assert "/api/onboarding/initial-refresh" in gate
    assert "initial_refresh_completed_at" in gate
    assert "Retry First Update" in gate
    assert "Setup is still working" in gate
    assert "5–10 minutes" in gate
    assert "dev-initial-refresh-spinner" in gate
    assert 'location.replace("/#overview")' in gate
    assert "location.reload()" not in gate

    onboarding_ui = (ROOT / "static" / "js" / "onboarding.js").read_text(encoding="utf-8")
    assert "Starting Your First Update" in onboarding_ui
    assert 'classList.add("onboarding-initial-refresh-pending")' in onboarding_ui
    assert "overlay()?.remove()" not in onboarding_ui

    assert "core.public_epg_payload()" in worker
    assert 'item.get("cached")' in worker
    assert 'item.get("filtered_bytes")' in worker
    assert "core.COMBINED_EPG_PATH.exists()" in worker
    assert "_finish_onboarding_refresh(success=ready" in worker
