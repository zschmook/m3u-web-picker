from __future__ import annotations

import gzip
import re
import threading
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


_cache_lock = threading.Lock()
_cache_signature: tuple[tuple[str, int, int], ...] | None = None
_cache_ids: dict[str, str] = {}
_cache_names: dict[str, str] = {}


def _normalize(value: object) -> str:
    text = str(value or "").replace("&", " and ").replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _signature(paths: Iterable[Path]) -> tuple[tuple[str, int, int], ...]:
    output: list[tuple[str, int, int]] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            stat = path.stat()
            output.append((str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size)))
        except OSError:
            continue
    return tuple(output)


def _open_xmltv(path: Path):
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def _build_index(paths: Iterable[Path]) -> tuple[dict[str, str], dict[str, str]]:
    ids: dict[str, str] = {}
    names: dict[str, str] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or not path.stat().st_size:
            continue
        try:
            with _open_xmltv(path) as handle:
                for _event, element in ElementTree.iterparse(handle, events=("end",)):
                    if element.tag.rsplit("}", 1)[-1] != "channel":
                        continue
                    channel_id = str(element.attrib.get("id", "") or "").strip()
                    icon_url = ""
                    display_names: list[str] = []
                    for child in element:
                        tag = child.tag.rsplit("}", 1)[-1]
                        if tag == "icon" and not icon_url:
                            candidate = str(child.attrib.get("src", "") or "").strip()
                            if candidate.startswith(("http://", "https://")):
                                icon_url = candidate
                        elif tag == "display-name" and child.text:
                            display_names.append(child.text.strip())
                    if icon_url:
                        if channel_id:
                            ids.setdefault(channel_id, icon_url)
                            ids.setdefault(channel_id.casefold(), icon_url)
                        for name in display_names:
                            normalized = _normalize(name)
                            if normalized:
                                names.setdefault(normalized, icon_url)
                    element.clear()
        except (OSError, EOFError, ElementTree.ParseError):
            continue
    return ids, names


def _indexes(paths: Iterable[Path]) -> tuple[dict[str, str], dict[str, str]]:
    global _cache_signature, _cache_ids, _cache_names
    path_list = [Path(path) for path in paths]
    signature = _signature(path_list)
    with _cache_lock:
        if signature != _cache_signature:
            _cache_ids, _cache_names = _build_index(path_list)
            _cache_signature = signature
        return dict(_cache_ids), dict(_cache_names)


def logo_for_channel(channel: dict, paths: Iterable[Path]) -> str:
    """Return a public-EPG icon for one manual channel, using exact matches only."""
    ids, names = _indexes(paths)
    tvg_id = str(channel.get("tvg_id", "") or "").strip()
    if tvg_id:
        logo = ids.get(tvg_id) or ids.get(tvg_id.casefold())
        if logo:
            return logo
    for value in (channel.get("tvg_name", ""), channel.get("name", "")):
        normalized = _normalize(value)
        if normalized and normalized in names:
            return names[normalized]
    return ""


def prefer_public_epg_logo(channel: dict, paths: Iterable[Path]) -> str:
    """Manual-logo precedence: public EPG first, provider/Xtream second."""
    return logo_for_channel(channel, paths) or str(channel.get("tvg_logo", "") or "").strip()


def apply_to_guide_items(items: list[dict], paths: Iterable[Path]) -> list[dict]:
    path_list = list(paths)
    if not path_list:
        return items
    output: list[dict] = []
    for raw_item in items:
        item = dict(raw_item)
        if not item.get("generated"):
            epg_logo = logo_for_channel(item, path_list)
            if epg_logo:
                item["logo"] = epg_logo
                item["logo_source"] = "public-epg"
        output.append(item)
    return output


def _extinf_attrs(line: str) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in re.findall(r'([A-Za-z0-9_-]+)="([^"]*)"', line)
    }


def _rewrite_logo_attr(line: str, logo_url: str) -> str:
    escaped = str(logo_url).replace('"', "'")
    if re.search(r'\btvg-logo="[^"]*"', line, flags=re.I):
        return re.sub(
            r'\btvg-logo="[^"]*"',
            f'tvg-logo="{escaped}"',
            line,
            count=1,
            flags=re.I,
        )
    comma = line.rfind(",")
    if comma >= 0:
        return f'{line[:comma]} tvg-logo="{escaped}"{line[comma:]}'
    return f'{line} tvg-logo="{escaped}"'


def rewrite_manual_playlist_logos(text: str, paths: Iterable[Path]) -> str:
    """Prefer public-EPG logos on manual EXTINF rows; leave sports rows untouched."""
    path_list = list(paths)
    if not text or not path_list:
        return text
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        attrs = _extinf_attrs(line)
        if "x-sports-event" in attrs or str(attrs.get("tvg-id", "")).startswith("m3u-picker-sports-"):
            continue
        display_name = line.rsplit(",", 1)[1].strip() if "," in line else ""
        channel = {
            "tvg_id": attrs.get("tvg-id", ""),
            "tvg_name": attrs.get("tvg-name", ""),
            "name": display_name,
            "tvg_logo": attrs.get("tvg-logo", ""),
        }
        epg_logo = logo_for_channel(channel, path_list)
        if epg_logo:
            lines[index] = _rewrite_logo_attr(line, epg_logo)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix
