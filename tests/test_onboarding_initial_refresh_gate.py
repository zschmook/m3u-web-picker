from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_first_guide_gate_is_loaded_and_blocks_direct_guide_access():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
    assert "/static/js/onboarding_initial_refresh_gate.js?v=onboarding-guide-gate-1" in template
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


def test_onboarding_can_hide_sd_and_low_bandwidth_channels():
    onboarding_ui = (ROOT / "static" / "js" / "onboarding.js").read_text(
        encoding="utf-8"
    )

    assert "Hide SD / Low Bandwidth Channels" in onboarding_ui
    assert 'document.getElementById("devExcludeSdChannels")?.checked' in onboarding_ui
    assert "JSON.stringify({enabled, exclude_sd: excludeSd})" in onboarding_ui
    assert "ctx.payload.sports.settings.exclude_sd = excludeSd" in onboarding_ui


def test_onboarding_jellyfin_save_requires_risk_acknowledgement():
    onboarding_ui = (ROOT / "static" / "js" / "onboarding.js").read_text(
        encoding="utf-8"
    )

    assert 'button.disabled = !ack?.checked' in onboarding_ui
    assert 'if (!ack?.checked)' in onboarding_ui
    assert "before enabling cache cleanup" in onboarding_ui
    assert "cleanup_enabled: true" in onboarding_ui
    assert "acknowledged: true" in onboarding_ui
