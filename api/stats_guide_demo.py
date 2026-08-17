from __future__ import annotations

from flask import Flask

import core
from media import browser
from settings import load_settings
from sports import nfl_demo_stats
from . import guide as guide_api


_DEMO_PLAY_URL = "/guide/play/stats-demo/1"
_installed = False
_original_curated_channels = None
_original_play_target_resolver = None


def _internal_demo_hls_url() -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}/sports/stats-demo/1/stream.m3u8"


def _demo_channel() -> dict:
    return {
        "number": nfl_demo_stats.DEMO_GUIDE_NUMBER,
        "name": "NFL Stats Demo · Eagles at Ravens",
        "group": "Sports Stats Lab",
        "logo": "",
        "tvg_id": "m3u-picker-sports-stats-demo-1",
        "subtitle": "Completed ESPN game stats · Experimental",
        "generated": True,
        "play_url": _DEMO_PLAY_URL,
        "stats_demo": True,
    }


def install() -> None:
    """Inject the permanent experimental 1.1 channel into the Picker TV Guide."""
    global _installed, _original_curated_channels, _original_play_target_resolver
    if _installed:
        return

    _original_curated_channels = core.curated_channels_for_guide
    _original_play_target_resolver = guide_api._resolve_guide_play_target

    def curated_channels_for_guide() -> list[dict]:
        items = list(_original_curated_channels())
        if any(str(item.get("play_url", "") or "") == _DEMO_PLAY_URL for item in items):
            return items

        demo = _demo_channel()
        insert_at = 0
        for index, item in enumerate(items):
            if str(item.get("number", "") or "") == "1":
                insert_at = index + 1
                break
        items.insert(insert_at, demo)
        return items

    def resolve_guide_play_target(play_url: str) -> str:
        value = str(play_url or "").split("?", 1)[0].strip()
        if value == _DEMO_PLAY_URL:
            return _internal_demo_hls_url()
        return _original_play_target_resolver(play_url)

    core.curated_channels_for_guide = curated_channels_for_guide
    guide_api._resolve_guide_play_target = resolve_guide_play_target
    _installed = True


def register_stats_guide_demo_routes(app: Flask) -> None:
    @app.get(_DEMO_PLAY_URL)
    def guide_play_stats_demo():
        return browser.response_for(_internal_demo_hls_url())
