from __future__ import annotations

from typing import Iterable

import sports as _s


def _classification_id(event: dict) -> str:
    return str(event.get("league_id") or event.get("sport_id") or "sports")


def _classification_label(classification_id: str) -> str:
    return _s.LEAGUE_NAMES.get(
        classification_id,
        _s.SPORT_NAMES.get(
            classification_id,
            classification_id.replace("-", " ").title() or "Sports",
        ),
    )


def _block_index_map(classification_ids: Iterable[str]) -> dict[str, int]:
    """Return stable primary block indexes, including provider-only classes."""
    mapping = dict(_s.LEAGUE_BLOCK_INDEX)
    unknown = sorted(
        {str(value) for value in classification_ids if value and value not in mapping}
    )
    next_index = len(mapping)
    for classification_id in unknown:
        mapping[classification_id] = next_index
        next_index += 1
    return mapping


def assigned_channel_number(
    classification_id: str,
    event_index: int,
    feed_index: int,
    *,
    start_channel: int = 1000,
    channels_per_event: int = 10,
    block_index: int | None = None,
) -> int:
    """Assign one feed inside a league's 1,000-channel block."""
    per_event = max(1, int(channels_per_event))
    capacity = max(1, _s.LEAGUE_BLOCK_SIZE // per_event)
    resolved_index = (
        int(block_index)
        if block_index is not None
        else _s.LEAGUE_BLOCK_INDEX.get(classification_id, len(_s.LEAGUE_BLOCK_ORDER))
    )
    event_index = max(0, int(event_index))
    feed_index = max(0, int(feed_index))
    block_number = event_index // capacity
    slot_index = event_index % capacity
    if block_number == 0:
        block_start = int(start_channel) + resolved_index * _s.LEAGUE_BLOCK_SIZE
    else:
        block_start = (
            int(start_channel)
            + _s.OVERFLOW_BLOCK_OFFSET
            + resolved_index * 10_000
            + (block_number - 1) * _s.LEAGUE_BLOCK_SIZE
        )
    return block_start + slot_index * per_event + feed_index


def effective_start_channel(configured_start: int, manual_channel_count: int) -> int:
    """Return a sports block start that cannot collide with manual numbering."""
    configured = max(1, int(configured_start))
    manual_count = max(0, int(manual_channel_count))
    if manual_count < configured:
        return configured
    blocks_to_skip = ((manual_count - configured) // _s.LEAGUE_BLOCK_SIZE) + 1
    return configured + blocks_to_skip * _s.LEAGUE_BLOCK_SIZE


def numbering_plan(settings: dict) -> dict:
    start = int(settings.get("start_channel", 1000))
    per_event = int(settings.get("channels_per_event", 10))
    capacity = max(1, _s.LEAGUE_BLOCK_SIZE // max(1, per_event))
    blocks = []
    for league_id, name, sport_id, _subtitle, _aliases, _patterns in _s.LEAGUE_DEFINITIONS:
        index = _s.LEAGUE_BLOCK_INDEX[league_id]
        block_start = start + index * _s.LEAGUE_BLOCK_SIZE
        blocks.append(
            {
                "id": league_id,
                "name": name,
                "sport_id": sport_id,
                "sport": _s.SPORT_NAMES.get(sport_id, sport_id),
                "index": index,
                "start": block_start,
                "end": block_start + _s.LEAGUE_BLOCK_SIZE - 1,
            }
        )
    return {
        "start_channel": start,
        "league_block_size": _s.LEAGUE_BLOCK_SIZE,
        "channels_per_event": per_event,
        "events_per_primary_block": capacity,
        "overflow_start_offset": _s.OVERFLOW_BLOCK_OFFSET,
        "blocks": blocks,
    }
