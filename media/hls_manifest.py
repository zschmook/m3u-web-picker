"""Keep receiver discontinuity epochs stable without modifying encoder input."""
from __future__ import annotations

from dataclasses import dataclass


_DISCONTINUITY = "#EXT-X-DISCONTINUITY"
_DISCONTINUITY_SEQUENCE = "#EXT-X-DISCONTINUITY-SEQUENCE:"
_MEDIA_SEQUENCE = "#EXT-X-MEDIA-SEQUENCE:"
_SEGMENT_TAGS = (
    "#EXTINF:", "#EXT-X-PROGRAM-DATE-TIME:", "#EXT-X-BYTERANGE:",
    "#EXT-X-KEY:", "#EXT-X-MAP:",
)


@dataclass(frozen=True)
class _Segment:
    sequence: int
    uri: str
    start_line: int
    markers: int

    @property
    def identity(self) -> tuple[int, str]:
        return self.sequence, self.uri


class HlsManifestNormalizer:
    """Normalize one session's media playlists; callers serialize access.

    FFmpeg may drop a discontinuity as its window advances, or add a leading
    marker when importing old media after recovery. Segment identities anchor
    their already-published epochs. New suffixes inherit the last anchor plus
    genuine subsequent markers. ``generation`` identifies encoder recoveries
    when no segment from the previous window survives.

    ``retain_window=False`` bounds the identity ledger for growing EVENT
    playlists. Their older entries are reconstructed from the recent anchors
    and retained internal markers instead of storing every identity forever.
    """

    def __init__(self, max_entries: int = 40, *, retain_window: bool = True) -> None:
        self.max_entries = max(1, int(max_entries))
        self.retain_window = retain_window
        self._epochs: dict[tuple[int, str], int] = {}
        self._last_epoch: int | None = None
        self._generation: int | None = None

    @property
    def tracked_segments(self) -> int:
        return len(self._epochs)

    def normalize(self, playlist: str, generation: int = 0) -> str:
        lines = playlist.splitlines()
        sequence, raw_base, markers = 0, 0, 0
        header_line = 1 if lines and lines[0].strip() == "#EXTM3U" else 0
        pending_start: int | None = None
        segments: list[_Segment] = []
        for index, raw_line in enumerate(lines):
            line = raw_line.strip()
            if line.startswith(_MEDIA_SEQUENCE):
                sequence = max(0, int(line.partition(":")[2]))
                header_line = index + 1
            elif line.startswith(_DISCONTINUITY_SEQUENCE):
                raw_base = max(0, int(line.partition(":")[2]))
            elif line == _DISCONTINUITY:
                markers += 1
                if pending_start is None:
                    pending_start = index
            elif line.startswith(_SEGMENT_TAGS):
                if pending_start is None:
                    pending_start = index
            elif line and not line.startswith("#"):
                segments.append(_Segment(sequence, line,
                    index if pending_start is None else pending_start, markers))
                sequence += 1
                pending_start = None

        # Never replace a last usable manifest with a normalized empty one.
        if not segments:
            return playlist

        known = {index: self._epochs[segment.identity]
                 for index, segment in enumerate(segments)
                 if segment.identity in self._epochs}
        epochs: list[int] = []
        if known:
            first_anchor = min(known)
            anchor_epoch = known[first_anchor]
            for index in range(first_anchor):
                epochs.append(max(0, anchor_epoch
                    - segments[first_anchor].markers + segments[index].markers))
            for index in range(first_anchor, len(segments)):
                if index in known:
                    epochs.append(known[index])
                else:
                    epoch = epochs[-1] + segments[index].markers - segments[index - 1].markers
                    # Redundant markers inside retained media cannot change a
                    # later known epoch. The next anchor limits unknown gaps.
                    next_anchor = next((key for key in known if key > index), None)
                    if next_anchor is not None:
                        epoch = min(epoch, known[next_anchor])
                    epochs.append(epoch)
        else:
            first_epoch = raw_base + segments[0].markers
            if self._last_epoch is not None:
                generation_delta = max(0, generation - (self._generation or 0))
                boundary = generation_delta if generation_delta else segments[0].markers
                first_epoch = max(first_epoch, self._last_epoch + boundary)
            epochs = [first_epoch + segment.markers - segments[0].markers
                      for segment in segments]

        insertions: dict[int, int] = {}
        for index in range(1, len(segments)):
            count = epochs[index] - epochs[index - 1]
            if count > 0:
                insertions[segments[index].start_line] = count
        output: list[str] = []
        for index in range(len(lines) + 1):
            if index == header_line:
                output.append(f"{_DISCONTINUITY_SEQUENCE}{epochs[0]}")
            output.extend([_DISCONTINUITY] * insertions.get(index, 0))
            if index == len(lines):
                break
            line = lines[index].strip()
            if line == _DISCONTINUITY or line.startswith(_DISCONTINUITY_SEQUENCE):
                continue
            output.append(lines[index])

        current = {segment.identity: epoch for segment, epoch in zip(segments, epochs)}
        if not self.retain_window and len(current) > self.max_entries:
            current = dict(list(current.items())[-self.max_entries:])
        limit = max(self.max_entries, len(current))
        recent = [(key, value) for key, value in self._epochs.items() if key not in current]
        remaining = limit - len(current)
        self._epochs = dict(recent[-remaining:]) if remaining else {}
        self._epochs.update(current)
        self._last_epoch = epochs[-1]
        self._generation = generation
        return "\n".join(output) + "\n"
