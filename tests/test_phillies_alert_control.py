from sports import channel_one_alerts
from sports import phillies_alert_control


def test_manual_phillies_trigger_uses_current_score(monkeypatch):
    tracker = channel_one_alerts.MlbScoreTracker()
    row = {
        "assigned_number": 1070,
        "event_key": "phillies-mets",
        "league_id": "mlb",
        "url": "http://provider.test/phillies",
    }
    game = {
        "gamePk": 12345,
        "status": {"abstractGameState": "Live"},
        "teams": {
            "away": {
                "score": 3,
                "team": {"name": "Philadelphia Phillies", "abbreviation": "PHI"},
            },
            "home": {
                "score": 2,
                "team": {"name": "New York Mets", "abbreviation": "NYM"},
            },
        },
    }
    monkeypatch.setattr(tracker, "_mlb_rows", lambda _db: [row])
    monkeypatch.setattr(tracker, "_resolve_games", lambda _rows: {"phillies-mets": game})
    monkeypatch.setattr(
        phillies_alert_control.alert_stream,
        "live_tracker",
        lambda _db: tracker,
    )
    monkeypatch.setattr(
        phillies_alert_control.alert_stream,
        "active_session_numbers",
        lambda: [1000],
    )
    monkeypatch.setattr(
        phillies_alert_control.mlb_live_source,
        "fetch_live_state",
        lambda _pk: {
            "away": {"name": "Philadelphia Phillies", "abbr": "PHI", "score": 4},
            "home": {"name": "New York Mets", "abbr": "NYM", "score": 2},
            "last_play": "Phillies scored",
        },
    )

    payload = phillies_alert_control.trigger_current_score("db.sqlite")

    assert payload["away"]["abbr"] == "PHI"
    assert payload["away"]["score"] == 4
    assert payload["home"]["score"] == 2
    assert payload["source_channel"] == 1070
    assert tracker.active is not None
    assert tracker.active.alert.scoring_team.abbr == "PHI"
    assert tracker.active.alert.show_on_source


def test_random_team_trigger_uses_latest_available_score(monkeypatch):
    tracker = channel_one_alerts.MlbScoreTracker()
    row = {
        "assigned_number": 1080,
        "event_key": "orioles-yankees",
        "league_id": "mlb",
        "url": "http://provider.test/orioles",
    }
    game = {
        "gamePk": 67890,
        "status": {"abstractGameState": "Final"},
        "teams": {
            "away": {
                "score": 5,
                "team": {"name": "Baltimore Orioles", "abbreviation": "BAL"},
            },
            "home": {
                "score": 3,
                "team": {"name": "New York Yankees", "abbreviation": "NYY"},
            },
        },
    }
    monkeypatch.setattr(tracker, "_mlb_rows", lambda _db: [row])
    monkeypatch.setattr(tracker, "_resolve_games", lambda _rows: {"orioles-yankees": game})
    monkeypatch.setattr(
        phillies_alert_control.alert_stream,
        "live_tracker",
        lambda _db: tracker,
    )
    monkeypatch.setattr(
        phillies_alert_control.alert_stream,
        "active_session_numbers",
        lambda: [1080],
    )
    monkeypatch.setattr(
        phillies_alert_control.mlb_live_source,
        "fetch_live_state",
        lambda _pk: {
            "away": {"name": "Baltimore Orioles", "abbr": "BAL", "score": 5},
            "home": {"name": "New York Yankees", "abbr": "NYY", "score": 3},
            "last_play": "Final",
        },
    )
    monkeypatch.setattr(
        phillies_alert_control.secrets,
        "choice",
        lambda values: list(values)[0],
    )

    payload = phillies_alert_control.trigger_random_score("db.sqlite")

    assert payload["selected_team"]["abbr"] == "BAL"
    assert payload["away"]["score"] == 5
    assert payload["home"]["score"] == 3
    assert payload["active_channels"] == [1080]
    assert tracker.active is not None
    assert tracker.active.alert.scoring_team.abbr == "BAL"
    assert tracker.active.alert.show_on_source
