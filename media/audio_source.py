"""Choose a provider's live playlist for Listen when it advertises one."""
import re
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


def prefer_live_playlist(target: str) -> str:
    """Probe only the HLS counterpart of an Xtream-style live TS URL."""
    parts = urlsplit(target)
    if parts.scheme not in ("http", "https") or not re.fullmatch(
        r"/live/[^/]+/[^/]+/\d+\.ts", parts.path,
    ):
        return target
    candidate = urlunsplit(parts._replace(path=parts.path[:-3] + ".m3u8"))
    try:
        request = Request(candidate, headers={
            "User-Agent": "M3U-Web-Picker/2.0", "Accept": "application/vnd.apple.mpegurl, */*",
        })
        with urlopen(request, timeout=3) as response:
            playlist = response.read(65537)
        if len(playlist) > 65536:
            return target
        text = playlist.decode("utf-8-sig").strip()
        if (text.startswith("#EXTM3U") and "#EXT-X-MEDIA-SEQUENCE:" in text
                and "#EXTINF:" in text and "#EXT-X-ENDLIST" not in text):
            return candidate
    except (OSError, ValueError):
        pass
    return target
