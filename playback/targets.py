from __future__ import annotations

import re

import core
import sports


_MANUAL_PLAY = re.compile(r"/guide/play/manual/([^/]+)")
_SPORTS_PLAY = re.compile(r"/guide/play/sports/(\d+)")


def resolve_play_target(play_url: str) -> str:
    """Resolve an app-owned opaque play path to its provider stream target."""
    value = str(play_url or "").split("?", 1)[0].strip()

    manual = _MANUAL_PLAY.fullmatch(value)
    if manual:
        return core.manual_stream_target(manual.group(1))

    generated = _SPORTS_PLAY.fullmatch(value)
    if generated:
        return sports.generated_stream_target(core.DB_PATH, int(generated.group(1)))

    return ""


def lineup_channel(guide_number: str) -> dict | None:
    """Find one channel in the currently served curated lineup by guide number."""
    wanted = str(guide_number or "").strip()
    if not wanted:
        return None
    for channel in core.curated_channels_for_guide():
        if str(channel.get("number", "") or "").strip() == wanted:
            return channel
    return None
