from pathlib import Path

from PIL import Image

from sports import game_alert_demo as demo


def _write_logo(path: Path, color: tuple[int, int, int, int]) -> None:
    Image.new("RGBA", (24, 16), color).save(path, format="PNG")


def test_warm_cached_logos_decodes_every_cached_sport(monkeypatch, tmp_path):
    cache = tmp_path / "logo_cache"
    events = cache / "events"
    cache.mkdir()
    events.mkdir()
    _write_logo(cache / "mlb-digest.bin", (255, 0, 0, 255))
    _write_logo(cache / "nfl-digest.bin", (0, 0, 255, 255))
    (cache / "not-an-image.bin").write_bytes(b"broken")
    monkeypatch.setattr(demo.event_logos, "_paths", lambda: (cache, events, tmp_path / "app.db"))
    demo._DECODED_LOGO_CACHE.clear()

    assert demo.warm_cached_logos() == 2
    assert set(demo._DECODED_LOGO_CACHE) == {"mlb-digest", "nfl-digest"}
    assert demo.warm_cached_logos() == 0


def test_fetch_logo_reuses_predecoded_digest(monkeypatch):
    team = demo.DemoTeam("NFL", "Philadelphia Eagles", "PHI", (0, 0, 0), (255, 255, 255))
    warmed = Image.new("RGBA", (demo.LOGO_SIZE, demo.LOGO_SIZE), (1, 2, 3, 255))
    demo._DECODED_LOGO_CACHE.clear()
    demo._DECODED_LOGO_CACHE["cached-digest"] = warmed
    monkeypatch.setattr(demo, "_shared_logo_request", lambda _team: ("nfl:eagles", "https://example/logo.png", ""))
    monkeypatch.setattr(
        demo.event_logos,
        "_resolve_team_asset",
        lambda _request: (b"this does not need decoding", "cached-digest"),
    )

    result = demo._fetch_logo(team)

    assert result is not None
    assert result.getpixel((0, 0)) == (1, 2, 3, 255)
