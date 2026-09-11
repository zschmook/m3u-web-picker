from __future__ import annotations

import sqlite3

import pytest

import push_alerts


def subscription(endpoint: str = "https://fcm.googleapis.com/fcm/send/example") -> dict:
    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "p" * 88,
            "auth": "a" * 24,
        },
    }


def test_subscription_is_saved_updated_and_removed(tmp_path):
    db_path = tmp_path / "alerts.db"

    saved = push_alerts.save(db_path, subscription(), user_agent="Phone browser")
    push_alerts.save(db_path, subscription(), user_agent="Updated phone browser")

    assert saved["endpoint"].startswith("https://fcm.googleapis.com/")
    assert push_alerts.count(db_path) == 1
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT user_agent FROM web_push_subscriptions"
        ).fetchone()
    assert row == ("Updated phone browser",)
    assert push_alerts.remove(db_path, str(saved["endpoint"])) is True
    assert push_alerts.count(db_path) == 0


def test_subscription_rejects_a_local_or_unknown_push_endpoint(tmp_path):
    with pytest.raises(ValueError, match="unsupported push service"):
        push_alerts.save(db_path=tmp_path / "alerts.db", payload=subscription("https://10.0.0.2/push"))
