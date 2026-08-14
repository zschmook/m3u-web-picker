from __future__ import annotations

import gzip
from pathlib import Path

import public_epg_logos


def _write_public_epg(path: Path) -> None:
    payload = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<tv>
  <channel id=\"WCAU.us\">
    <display-name>NBC 10 Philadelphia</display-name>
    <icon src=\"https://epg.example/wcau.png\" />
  </channel>
  <channel id=\"WPVI.us\">
    <display-name>6 ABC Philadelphia</display-name>
    <icon src=\"https://epg.example/wpvi.png\" />
  </channel>
</tv>
"""
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(payload)


def test_public_epg_logo_wins_for_manual_channel(tmp_path: Path) -> None:
    epg = tmp_path / "epg-us.filtered.xml.gz"
    _write_public_epg(epg)
    channel = {
        "tvg_id": "WCAU.us",
        "name": "NBC 10 Philadelphia",
        "tvg_logo": "https://provider.example/wcau.png",
    }
    assert public_epg_logos.prefer_public_epg_logo(channel, [epg]) == "https://epg.example/wcau.png"


def test_public_epg_logo_can_match_manual_channel_name(tmp_path: Path) -> None:
    epg = tmp_path / "epg-us.filtered.xml.gz"
    _write_public_epg(epg)
    channel = {
        "tvg_id": "",
        "name": "6 ABC Philadelphia",
        "tvg_logo": "https://provider.example/wpvi.png",
    }
    assert public_epg_logos.logo_for_channel(channel, [epg]) == "https://epg.example/wpvi.png"


def test_provider_logo_remains_when_public_epg_has_no_match(tmp_path: Path) -> None:
    epg = tmp_path / "epg-us.filtered.xml.gz"
    _write_public_epg(epg)
    channel = {
        "tvg_id": "MISSING.us",
        "name": "Missing Channel",
        "tvg_logo": "https://provider.example/missing.png",
    }
    assert public_epg_logos.prefer_public_epg_logo(channel, [epg]) == "https://provider.example/missing.png"


def test_playlist_rewrite_changes_manual_but_not_generated_sports(tmp_path: Path) -> None:
    epg = tmp_path / "epg-us.filtered.xml.gz"
    _write_public_epg(epg)
    playlist = """#EXTM3U
#EXTINF:-1 tvg-id=\"WCAU.us\" tvg-logo=\"https://provider.example/wcau.png\",NBC 10 Philadelphia
http://provider/manual
#EXTINF:-1 tvg-id=\"m3u-picker-sports-4000\" tvg-logo=\"https://sports.example/game.png\" x-sports-event=\"game-1\",NFL Game
/sports/stream/4000
"""
    rewritten = public_epg_logos.rewrite_manual_playlist_logos(playlist, [epg])
    assert 'tvg-logo="https://epg.example/wcau.png"' in rewritten
    assert 'tvg-logo="https://sports.example/game.png" x-sports-event="game-1"' in rewritten
