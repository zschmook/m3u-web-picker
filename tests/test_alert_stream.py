from PIL import Image, ImageDraw

from sports import alert_stream


def _row(number, event_key, title, league="mlb", url=None):
    return {
        "assigned_number": number,
        "event_key": event_key,
        "event_title": title,
        "display_name": title,
        "league_id": league,
        "url": url or f"http://provider.test/{number}",
    }


def test_fake_alert_uses_another_generated_logical_event(monkeypatch):
    rows = [
        _row(1000, "game-a", "Phillies @ Mets"),
        _row(1001, "game-a", "Phillies @ Mets"),
        _row(1010, "game-b", "Bills @ Dolphins", league="nfl"),
        _row(1011, "game-b", "Bills @ Dolphins", league="nfl"),
    ]
    monkeypatch.setattr(alert_stream.generated, "generated_rows", lambda _db: rows)

    routed = alert_stream.fake_alert_for_slot("db.sqlite", 1000, "game-a", 0)

    assert routed is not None
    assert routed.source_event_key == "game-b"
    assert routed.alert.source_channel == "1010"
    assert routed.alert.league == "NFL"


def test_fake_alert_does_not_use_another_feed_of_watched_game(monkeypatch):
    rows = [
        _row(1000, "game-a", "Phillies @ Mets"),
        _row(1001, "game-a", "Phillies @ Mets"),
    ]
    monkeypatch.setattr(alert_stream.generated, "generated_rows", lambda _db: rows)

    routed = alert_stream.fake_alert_for_slot("db.sqlite", 1000, "game-a", 0)

    assert routed is None


def test_alert_is_invalid_if_destination_channel_disappears():
    source = _row(1010, "game-b", "Bills @ Dolphins", league="nfl")
    watched = _row(1000, "game-a", "Phillies @ Mets")
    alert = alert_stream.fake_alert_for_slot

    routed_alert = alert_stream.RoutedAlert(
        alert=alert_stream.demo.DemoAlert(
            league="NFL",
            scoring_team=alert_stream._team("Bills", "NFL"),
            away=alert_stream._team("Bills", "NFL"),
            home=alert_stream._team("Dolphins", "NFL"),
            away_score=27,
            home_score=24,
            play="Pick-six",
            source_channel="1010",
        ),
        source_event_key="game-b",
    )
    session = alert_stream.AlertSession(
        directory=alert_stream.Path("/tmp/test-alert"),
        parent_url=watched["url"],
        watched_number=1000,
        watched_event_key="game-a",
    )

    assert alert_stream._routed_alert_valid(
        [watched, source], session, routed_alert
    )
    assert not alert_stream._routed_alert_valid(
        [watched], session, routed_alert
    )


def test_score_row_uses_real_team_icons_when_abbreviations_exist(monkeypatch):
    away = alert_stream.demo.DemoTeam("MLB", "Phillies", "PHI", (1, 2, 3), (4, 5, 6))
    home = alert_stream.demo.DemoTeam("MLB", "Mets", "NYM", (1, 2, 3), (4, 5, 6))
    calls = []

    def fake_team_icon(team):
        calls.append(team.abbr)
        return Image.new(
            "RGBA",
            (alert_stream.demo.LOGO_SIZE, alert_stream.demo.LOGO_SIZE),
            (0, 0, 0, 0),
        )

    monkeypatch.setattr(alert_stream.demo, "_team_icon", fake_team_icon)
    image = Image.new(
        "RGBA",
        (alert_stream.demo.FRAME_WIDTH, alert_stream.demo.FRAME_HEIGHT),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image, "RGBA")
    alert = alert_stream.demo.DemoAlert(
        league="MLB",
        scoring_team=away,
        away=away,
        home=home,
        away_score=2,
        home_score=1,
        play="Home run",
        source_channel="1000",
    )

    alert_stream._score_row(image, draw, alert)

    assert calls == ["PHI", "NYM"]


def test_fit_line_ellipsizes_long_play_text():
    image = Image.new(
        "RGBA",
        (alert_stream.demo.FRAME_WIDTH, alert_stream.demo.FRAME_HEIGHT),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image, "RGBA")
    value = "Very long scoring play description " * 10

    text, font = alert_stream._fit_line(
        draw,
        value,
        max_width=alert_stream.demo.FRAME_WIDTH - 64,
        start_size=22,
        minimum=16,
    )

    box = draw.textbbox((0, 0), text, font=font)
    assert text.endswith("…")
    assert box[2] - box[0] <= alert_stream.demo.FRAME_WIDTH - 64
