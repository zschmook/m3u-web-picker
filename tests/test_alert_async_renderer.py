import threading

from sports import alert_stream


def _alert(score: int = 1):
    away = alert_stream.demo.DemoTeam("NFL", "Eagles", "PHI", (0, 0, 0), (255, 255, 255))
    home = alert_stream.demo.DemoTeam("NFL", "Cowboys", "DAL", (0, 0, 0), (255, 255, 255))
    return alert_stream.demo.DemoAlert(
        league="NFL",
        scoring_team=away,
        away=away,
        home=home,
        away_score=score,
        home_score=0,
        play="Touchdown",
        source_channel="1000",
    )


def test_async_renderer_keeps_old_frame_until_new_alert_is_complete(monkeypatch):
    render_started = threading.Event()
    allow_render = threading.Event()
    elapsed_values = []

    def slow_render(alert, elapsed):
        if alert is None:
            return b"transparent"
        elapsed_values.append(elapsed)
        render_started.set()
        allow_render.wait(timeout=1.0)
        return f"score-{alert.away_score}".encode()

    monkeypatch.setattr(alert_stream, "_render_at_elapsed", slow_render)
    stop = threading.Event()
    renderer = alert_stream._AsyncAlertRenderer(stop)
    renderer.start()
    try:
        renderer.request(_alert(7))
        assert render_started.wait(timeout=1.0)
        assert renderer.frame_for_stream() == b"transparent"
        allow_render.set()
        for _ in range(100):
            if renderer.frame_for_stream() == b"score-7":
                break
            threading.Event().wait(0.01)
        assert renderer.frame_for_stream() == b"score-7"
        assert elapsed_values[0] == 0.0
        assert renderer._presented_at is not None
    finally:
        stop.set()
        renderer.stop()


def test_render_elapsed_override_is_thread_local(monkeypatch):
    seen = []
    monkeypatch.setattr(alert_stream, "render_alert", lambda alert: seen.append(alert_stream._animation_elapsed(alert)) or b"png")

    assert alert_stream._render_at_elapsed(_alert(), 2.5) == b"png"
    assert seen == [2.5]
    assert not hasattr(alert_stream._RENDER_ELAPSED, "value")
