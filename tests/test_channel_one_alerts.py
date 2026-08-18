from sports import channel_one_alerts


def _row(number=1000, event_key="game-a", league="mlb"):
    return {
        "assigned_number": number,
        "event_key": event_key,
        "event_title": "Phillies @ Mets",
        "display_name": "Phillies @ Mets",
        "league_id": league,
        "url": f"http://provider.test/{number}",
    }


def _game(away_score=1, home_score=0):
    return {
        "gamePk": 12345,
        "gameDate": "2026-08-18T23:05:00Z",
        "teams": {
            "away": {
                "score": away_score,
                "team": {
                    "name": "Philadelphia Phillies",
                    "abbreviation": "PHI",
                },
            },
            "home": {
                "score": home_score,
                "team": {
                    "name": "New York Mets",
                    "abbreviation": "NYM",
                },
            },
        },
    }


def test_route_channel_one_rewrites_only_channel_one():
    text = (
        '#EXTM3U\n'
        '#EXTINF:-1 tvg-chno="1",One\n'
        'http://provider.test/one\n'
        '#EXTINF:-1 tvg-chno="10",Ten\n'
        'http://provider.test/ten\n'
    )

    routed = channel_one_alerts.route_channel_one(text, "http://picker.test:9998")

    assert (
        "http://picker.test:9998/sports/mlb-score-alerts/1/stream.m3u8"
        in routed
    )
    assert "http://provider.test/ten" in routed
    assert "http://provider.test/one" not in routed


def test_mlb_score_tracker_baselines_then_alerts(monkeypatch):
    rows = [
        _row(1000),
        _row(1001),  # alternate feed of the same logical event
    ]
    game = _game(away_score=1, home_score=0)

    monkeypatch.setattr(
        channel_one_alerts.generated,
        "generated_rows",
        lambda _db: rows,
    )

    tracker = channel_one_alerts.MlbScoreTracker()
    monkeypatch.setattr(tracker, "_resolve_games", lambda _rows: {"game-a": game})
    monkeypatch.setattr(
        channel_one_alerts.mlb_live_source,
        "fetch_live_state",
        lambda _game_pk: {"last_play": "Kyle Schwarber homers to right field."},
    )

    tracker.poll("db.sqlite")
    assert tracker.current("db.sqlite") is None

    game["teams"]["away"]["score"] = 2
    tracker.poll("db.sqlite")
    alert = tracker.current("db.sqlite")

    assert alert is not None
    assert alert.league == "MLB"
    assert alert.scoring_team.name == "Philadelphia Phillies"
    assert alert.away_score == 2
    assert alert.home_score == 0
    assert alert.source_channel == "1000"
    assert "Schwarber" in alert.play


def test_mlb_score_tracker_ignores_score_corrections(monkeypatch):
    rows = [_row(1000)]
    game = _game(away_score=4, home_score=3)

    monkeypatch.setattr(
        channel_one_alerts.generated,
        "generated_rows",
        lambda _db: rows,
    )

    tracker = channel_one_alerts.MlbScoreTracker()
    monkeypatch.setattr(tracker, "_resolve_games", lambda _rows: {"game-a": game})

    tracker.poll("db.sqlite")
    game["teams"]["away"]["score"] = 3
    tracker.poll("db.sqlite")

    assert tracker.current("db.sqlite") is None
