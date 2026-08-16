from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_demo_provider_helper_is_loaded_by_app():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "/static/js/onboarding_demo_provider.js?v=demo-provider-1" in app_source


def test_demo_provider_defaults_to_curated_free_tv_us_playlist():
    source = (ROOT / "static" / "js" / "onboarding_demo_provider.js").read_text(
        encoding="utf-8"
    )
    assert "I don't have an IPTV service yet — just testing" in source
    assert "Recommended • smaller curated U.S. list • quality over quantity" in source
    assert (
        "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_usa.m3u8"
        in source
    )
    assert "recommended: true" in source


def test_demo_provider_offers_larger_public_playlist_choices():
    source = (ROOT / "static" / "js" / "onboarding_demo_provider.js").read_text(
        encoding="utf-8"
    )
    assert "https://iptv-org.github.io/iptv/countries/us.m3u" in source
    assert "https://iptv-org.github.io/iptv/index.category.m3u" in source
