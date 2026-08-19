import io

from PIL import Image

from sports import alert_stream
from sports import phillies_alert


def _team(name: str, abbr: str):
    return alert_stream.demo.DemoTeam(
        "MLB",
        name,
        abbr,
        (1, 2, 3),
        (4, 5, 6),
    )


def _alert(scoring_team):
    phillies = _team("Philadelphia Phillies", "PHI")
    mets = _team("New York Mets", "NYM")
    return alert_stream.demo.DemoAlert(
        league="MLB",
        scoring_team=scoring_team,
        away=phillies,
        home=mets,
        away_score=4,
        home_score=2,
        play="Phillies scored",
        source_channel="1070",
    )


def test_phillies_override_only_when_phillies_score():
    phillies = _team("Philadelphia Phillies", "PHI")
    mets = _team("New York Mets", "NYM")

    assert phillies_alert.is_phillies_scoring_alert(_alert(phillies))
    assert not phillies_alert.is_phillies_scoring_alert(_alert(mets))


def test_phillies_slide_rises_holds_and_drops_back_down():
    assert phillies_alert.phillies_slide_offset(0.0) == phillies_alert.CANVAS_HEIGHT
    assert phillies_alert.phillies_slide_offset(phillies_alert.SLIDE_IN_SECONDS) == 0
    assert phillies_alert.phillies_slide_offset(3.0) == 0
    assert phillies_alert.phillies_slide_offset(
        phillies_alert.SLIDE_OUT_START_SECONDS + phillies_alert.SLIDE_OUT_SECONDS
    ) == phillies_alert.CANVAS_HEIGHT


def test_phillies_graphic_uses_large_transparent_canvas():
    phillies_alert._GRAPHICS.clear()
    phillies_alert._ASSET = None
    alert = _alert(_team("Philadelphia Phillies", "PHI"))

    graphic = phillies_alert._build_graphic(alert)

    assert graphic is not None
    assert graphic.size == (
        phillies_alert.CANVAS_WIDTH,
        phillies_alert.CANVAS_HEIGHT,
    )
    assert graphic.getchannel("A").getbbox() is not None


def test_phillies_fireworks_animate_and_remain_transparent():
    first = phillies_alert._fireworks_layer(1.0)
    later = phillies_alert._fireworks_layer(1.25)

    assert first.size == (phillies_alert.CANVAS_WIDTH, phillies_alert.CANVAS_HEIGHT)
    assert first.getchannel("A").getbbox() is not None
    assert later.getchannel("A").getbbox() is not None
    assert first.tobytes() != later.tobytes()
    assert phillies_alert._fireworks_layer(0.0).getchannel("A").getbbox() is None


def test_phillies_render_is_ffmpeg_ready_large_png(monkeypatch):
    alert = _alert(_team("Philadelphia Phillies", "PHI"))
    monkeypatch.setattr(alert_stream, "_animation_elapsed", lambda _alert: 2.0)

    payload = phillies_alert.render_alert(alert)

    with Image.open(io.BytesIO(payload)) as rendered:
        assert rendered.size == (
            phillies_alert.CANVAS_WIDTH,
            phillies_alert.CANVAS_HEIGHT,
        )
        assert rendered.mode == "RGBA"
        assert rendered.getchannel("A").getbbox() is not None
