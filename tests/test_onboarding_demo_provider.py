from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_demo_provider_helper_is_loaded_by_index_template():
    template_source = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert "/static/js/onboarding_demo_provider.js?v=demo-provider-1" in template_source


def test_demo_provider_defaults_to_iptv_org_us_playlist():
    source = (ROOT / "static" / "js" / "onboarding_demo_provider.js").read_text(
        encoding="utf-8"
    )
    assert "I don't have an IPTV service yet — just testing" in source
    assert "Recommended • larger U.S. public-stream catalog" in source
    assert "https://iptv-org.github.io/iptv/countries/us.m3u" in source
    recommended_block = source.split('id: "iptv-org-us"', 1)[1].split("},", 1)[0]
    assert "recommended: true" in recommended_block


def test_demo_provider_offers_alternate_public_playlist_choices():
    source = (ROOT / "static" / "js" / "onboarding_demo_provider.js").read_text(
        encoding="utf-8"
    )
    assert (
        "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists/playlist_usa.m3u8"
        in source
    )
    assert "https://iptv-org.github.io/iptv/index.category.m3u" in source
