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


def test_live_alert_is_shared_but_hidden_on_its_source_channel(monkeypatch):
    alert = alert_stream.demo.DemoAlert(
        league="MLB",
        scoring_team=alert_stream._team("Phillies", "MLB"),
        away=alert_stream._team("Phillies", "MLB"),
        home=alert_stream._team("Mets", "MLB"),
        away_score=4,
        home_score=2,
        play="Home run",
        source_channel="1070",
    )

    class Tracker:
        @staticmethod
        def current(_db):
            return alert

    monkeypatch.setattr(alert_stream, "_live_tracker_for", lambda _db: Tracker())
    source_session = alert_stream.AlertSession(
        directory=alert_stream.Path("/tmp/source"),
        parent_url="http://provider.test/source",
        watched_number=1070,
        watched_event_key="source-game",
    )
    other_session = alert_stream.AlertSession(
        directory=alert_stream.Path("/tmp/other"),
        parent_url="http://provider.test/other",
        watched_number=1080,
        watched_event_key="other-game",
    )

    assert alert_stream._live_alert_for(source_session, "db.sqlite") is None
    assert alert_stream._live_alert_for(other_session, "db.sqlite") is alert


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


def test_logo_contrast_adds_consistent_light_disc_without_covering_mark():
    icon = Image.new("RGBA", (72, 72), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon, "RGBA")
    draw.rectangle((20, 20, 52, 52), fill=(5, 8, 12, 255))

    contrasted = alert_stream._with_logo_contrast(icon)

    backing = contrasted.getpixel((10, 36))
    center = contrasted.getpixel((36, 36))
    assert backing[3] > 0
    assert min(backing[:3]) > 150
    assert center[:3] == (5, 8, 12)
    assert center[3] == 255


def test_alert_motion_scales_to_75_percent_and_keeps_bottom_anchor():
    image = Image.new(
        "RGBA",
        (alert_stream.demo.FRAME_WIDTH, alert_stream.demo.FRAME_HEIGHT),
        (255, 255, 255, 255),
    )

    transformed = alert_stream._apply_alert_motion(image, 0.0)
    bbox = transformed.getchannel("A").getbbox()

    assert bbox is not None
    assert bbox[2] - bbox[0] == round(
        alert_stream.demo.FRAME_WIDTH * alert_stream.ALERT_BASE_SCALE
    )
    assert bbox[3] - bbox[1] == round(
        alert_stream.demo.FRAME_HEIGHT * alert_stream.ALERT_BASE_SCALE
    )
    assert bbox[3] == alert_stream.demo.FRAME_HEIGHT


def test_alert_poof_pops_then_shrinks_and_fades():
    before_scale, before_opacity = alert_stream._motion_values(
        alert_stream.ALERT_POOF_START_SECONDS
    )
    pop_scale, pop_opacity = alert_stream._motion_values(
        alert_stream.ALERT_POOF_START_SECONDS
        + alert_stream.ALERT_POOF_DURATION_SECONDS * 0.28
    )
    end_scale, end_opacity = alert_stream._motion_values(
        alert_stream.ALERT_POOF_START_SECONDS
        + alert_stream.ALERT_POOF_DURATION_SECONDS
    )

    assert before_scale == alert_stream.ALERT_BASE_SCALE
    assert before_opacity == 1.0
    assert pop_scale > before_scale
    assert pop_opacity == 1.0
    assert end_scale < before_scale
    assert end_opacity == 0.0


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
