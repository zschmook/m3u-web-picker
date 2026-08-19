import io
from dataclasses import replace

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


def test_phillies_atv_travels_left_to_right():
    assert phillies_alert.atv_x_offset(0.0) < -phillies_alert.ATV_WIDTH
    assert 0 < phillies_alert.atv_x_offset(3.8) < phillies_alert.CANVAS_WIDTH
    assert phillies_alert.atv_x_offset(phillies_alert.ATV_TRAVEL_SECONDS) > phillies_alert.CANVAS_WIDTH


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


def test_phillies_atv_gif_loads_and_advances():
    phillies_alert._ATV_FRAMES = ()
    phillies_alert._ATV_DURATIONS = ()
    phillies_alert._ATV_TOTAL_MS = 0

    first = phillies_alert._atv_frame(0.0)
    later = phillies_alert._atv_frame(0.5)

    assert first is not None
    assert later is not None
    assert first.size == (phillies_alert.ATV_WIDTH, phillies_alert.ATV_HEIGHT)
    assert first.tobytes() != later.tobytes()


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


def test_atv_frames_are_used_only_for_explicit_atv_variant(monkeypatch):
    alert = _alert(_team("Philadelphia Phillies", "PHI"))
    monkeypatch.setattr(alert_stream, "_animation_elapsed", lambda _alert: 2.0)
    calls = []
    monkeypatch.setattr(
        phillies_alert,
        "_atv_frame",
        lambda elapsed: calls.append(elapsed) or Image.new(
            "RGBA",
            (phillies_alert.ATV_WIDTH, phillies_alert.ATV_HEIGHT),
            (255, 0, 0, 255),
        ),
    )

    phillies_alert.render_alert(alert)
    assert calls == []

    phillies_alert.render_alert(replace(alert, visual_variant="phillies-atv"))
    assert calls == [2.0]
