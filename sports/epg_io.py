from __future__ import annotations

import gzip
import io
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import sports as _s


def derive_xmltv_url(source_url: str) -> str:
    """Derive a conventional Xtream XMLTV URL without exposing credentials."""
    if not source_url:
        return ""
    parsed = urllib.parse.urlparse(source_url)
    query = urllib.parse.parse_qs(parsed.query)
    username = query.get("username", [""])[-1]
    password = query.get("password", [""])[-1]
    if not parsed.scheme or not parsed.netloc or not username or not password:
        return ""
    encoded = urllib.parse.urlencode({"username": username, "password": password})
    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "/xmltv.php", "", encoded, "")
    )


def refresh_epg_cache(
    source_url: str,
    cache_path: Path,
    timeout: int = 120,
    cancel_check: _s.CancelCheck = None,
) -> tuple[bool, str]:
    xmltv_url = derive_xmltv_url(source_url)
    if not xmltv_url:
        return False, "No Xtream XMLTV URL could be derived."

    try:
        raw = download_xmltv_bytes(
            xmltv_url,
            timeout=timeout,
            cancel_check=cancel_check,
        )
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temp_path.write_bytes(raw)
        temp_path.replace(cache_path)
        return True, f"Cached {len(raw)} bytes of XMLTV data."
    except _s.ScanCancelled:
        raise
    except Exception as exc:
        return False, f"EPG refresh failed: {exc}"


def download_xmltv_bytes(
    xmltv_url: str,
    timeout: int = 120,
    cancel_check: _s.CancelCheck = None,
) -> bytes:
    """Download, decompress, and minimally validate XMLTV bytes."""
    request = urllib.request.Request(
        xmltv_url,
        headers={
            "User-Agent": "M3U-Web-Picker/2.0",
            "Accept": "application/xml,text/xml,*/*",
            "Accept-Encoding": "gzip",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        chunks = []
        while True:
            _s._raise_if_cancelled(cancel_check)
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        content_encoding = str(response.headers.get("Content-Encoding", "")).lower()

    if content_encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/")]
            if not members:
                raise ValueError("The EPG archive was empty.")
            raw = archive.read(members[0])
    if b"<tv" not in raw[:10000]:
        raise ValueError("The provider response did not look like XMLTV data.")
    return raw


def _parse_xmltv_time(value: str, default_tz: ZoneInfo) -> datetime | None:
    import re

    value = str(value or "").strip()
    match = re.match(r"^(\d{14})(?:\s+([+-]\d{4}|Z))?", value)
    if not match:
        return None
    try:
        base = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
        offset = match.group(2)
        if not offset:
            return base.replace(tzinfo=default_tz)
        if offset == "Z":
            return base.replace(tzinfo=ZoneInfo("UTC"))
        sign = 1 if offset.startswith("+") else -1
        hours = int(offset[1:3])
        minutes = int(offset[3:5])
        if hours > 23 or minutes > 59:
            raise ValueError("invalid XMLTV UTC offset")
        return base.replace(
            tzinfo=timezone(sign * timedelta(hours=hours, minutes=minutes))
        )
    except (ValueError, OverflowError) as exc:
        raise _s.MalformedSportsEntry(
            f"Invalid XMLTV timestamp {value!r}."
        ) from exc


def _iterparse_xmltv(path: Path, *, events=("end",)):
    """Iterparse plain or gzip XMLTV without expanding gzip sources to disk."""
    if path.suffix.lower() == ".gz":
        with gzip.open(path, "rb") as handle:
            yield from ElementTree.iterparse(handle, events=events)
        return
    yield from ElementTree.iterparse(path, events=events)
