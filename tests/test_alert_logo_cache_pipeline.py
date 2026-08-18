import io

from PIL import Image

from sports import game_alert_demo as demo


def _png_bytes() -> bytes:
    image = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_arizona_alert_uses_canonical_guide_logo_identity(monkeypatch):
    team = demo.DemoTeam(
        "MLB",
        "Arizona Diamondbacks",
        "AZ",  # MLB StatsAPI abbreviation; ESPN's canonical code is ARI.
        (1, 2, 3),
        (4, 5, 6),
    )
    monkeypatch.setattr(
        demo.espn_team_logos,
        "espn_full_default_url",
        lambda *_args: "",
    )
    monkeypatch.setattr(
        demo.espn_known_logos,
        "direct_full_default_url",
        lambda *_args: "https://a.espncdn.com/i/teamlogos/mlb/500/ari.png",
    )

    team_id, preferred_url, fallback_url = demo._shared_logo_request(team)

    assert team_id == "arizona-diamondbacks"
    assert preferred_url.endswith("/ari.png")
    assert fallback_url == ""


def test_alert_logo_reads_through_event_logo_shared_cache(monkeypatch):
    team = demo.DemoTeam(
        "MLB",
        "Arizona Diamondbacks",
        "AZ",
        (1, 2, 3),
        (4, 5, 6),
    )
    monkeypatch.setattr(
        demo,
        "_shared_logo_request",
        lambda _team: (
            "arizona-diamondbacks",
            "https://a.espncdn.com/i/teamlogos/mlb/500/ari.png",
            "",
        ),
    )
    seen = {}

    def fake_resolve_team_asset(request):
        seen.update(request)
        return _png_bytes(), "digest"

    monkeypatch.setattr(
        demo.event_logos,
        "_resolve_team_asset",
        fake_resolve_team_asset,
    )

    icon = demo._fetch_logo(team)

    assert icon is not None
    assert icon.size == (demo.LOGO_SIZE, demo.LOGO_SIZE)
    assert seen["identity"] == "team:arizona-diamondbacks"
    assert seen["source_url"].endswith("/ari.png")
