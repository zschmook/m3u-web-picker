from __future__ import annotations

from flask import Flask

import core
from api.providers import register_provider_routes


def _client(monkeypatch):
    monkeypatch.setattr(core, "primary_provider_source", lambda: None)
    monkeypatch.setattr(core, "source_mode", "")
    app = Flask(__name__)
    register_provider_routes(app)
    return app.test_client()


def test_provider_validation_waits_for_both_xtream_credentials(monkeypatch):
    client = _client(monkeypatch)
    called = False

    def unexpected_detect(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("detect_provider_source should not run with partial credentials")

    monkeypatch.setattr(core, "detect_provider_source", unexpected_detect)

    response = client.post(
        "/api/providers/validate",
        json={
            "url": "https://provider.example:8080",
            "username": "someone",
            "password": "",
        },
    )

    assert response.status_code == 409
    payload = response.get_json()
    assert payload["valid"] is False
    assert payload["waiting_for_credentials"] is True
    assert called is False


def test_provider_validation_is_probe_only(monkeypatch):
    client = _client(monkeypatch)
    calls = []

    def fake_detect(name, url, **kwargs):
        calls.append((name, url, kwargs))
        return (
            {
                "kind": "xtream",
                "xtream_api": True,
                "account_status": "Active",
                "expires_at": None,
            },
            "",
            [],
        )

    monkeypatch.setattr(core, "detect_provider_source", fake_detect)
    monkeypatch.setattr(
        core,
        "install_primary_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("validation must not install")),
    )

    response = client.post(
        "/api/providers/validate",
        json={
            "name": "Primary",
            "url": "https://provider.example:8080",
            "username": "someone",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["valid"] is True
    assert payload["kind"] == "xtream"
    assert payload["credentials_used"] is True
    assert len(calls) == 1
    assert calls[0][2]["load_channels"] is False
    assert calls[0][2]["role"] == "primary"


def test_failed_direct_url_waits_for_possible_credentials(monkeypatch):
    client = _client(monkeypatch)

    def fake_detect(*args, **kwargs):
        raise ValueError("HTTP Error 404: Not Found")

    monkeypatch.setattr(core, "detect_provider_source", fake_detect)

    response = client.post(
        "/api/providers/validate",
        json={"url": "https://provider.example:8080", "username": "", "password": ""},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["valid"] is False
    assert payload["waiting_for_credentials"] is True
    assert "404" in payload["error"]
