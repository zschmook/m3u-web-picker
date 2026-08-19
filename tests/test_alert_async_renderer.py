import threading
from pathlib import Path

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


def test_alert_monitor_keeps_database_work_off_writer(monkeypatch):
    session = alert_stream.AlertSession(
        directory=Path("/tmp/alert-monitor"),
        parent_url="http://provider.test/game",
        watched_number=1000,
        watched_event_key="game-a",
    )
    expected = _alert(7)
    checked = threading.Event()
    monkeypatch.setattr(
        alert_stream,
        "_snapshot",
        lambda _db: [{"assigned_number": 1000, "event_key": "game-a"}],
    )
    monkeypatch.setattr(
        alert_stream,
        "_live_alert_for",
        lambda _session, _db: checked.set() or expected,
    )
    monitor = alert_stream._AlertStateMonitor(session, "db.sqlite")
    monitor.start()
    try:
        assert checked.wait(timeout=1.0)
        assert monitor.snapshot() == (True, expected)
    finally:
        monitor.stop()


def test_alert_ffmpeg_clock_and_hls_segments_are_aligned(monkeypatch, tmp_path):
    monkeypatch.setattr(alert_stream.demo, "ffmpeg_executable", lambda: "ffmpeg")
    command = alert_stream.demo._ffmpeg_command("http://provider.test/game", tmp_path)
    graph = command[command.index("-filter_complex") + 1]

    assert "[0:v]setpts=PTS-STARTPTS[base]" in graph
    assert "[1:v]format=rgba,setpts=PTS-STARTPTS[alert]" in graph
    assert "repeatlast=1" in graph
    assert command[command.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*2)"
    assert command[command.index("-hls_time") + 1] == "2"
