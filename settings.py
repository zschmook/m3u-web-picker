from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class AppSettings:
    """Environment-derived application settings.

    The object is intentionally immutable. Call ``load_settings`` again when a
    request needs to observe an environment override that may have changed
    since module import (for example the experimental LAN relay host).
    """

    data_dir: Path
    port: int
    dev_port: int
    max_upload_bytes: int
    schedule_hour: int
    schedule_minute: int
    max_provider_channels: int
    provider_channel_warning: int
    max_provider_playlist_bytes: int
    max_provider_json_bytes: int
    max_public_epg_compressed_bytes: int
    cast_hls_dir: Path
    lan_host: str
    external_port: int


def load_settings() -> AppSettings:
    data_dir = Path(os.environ.get("M3U_DATA_DIR", str(APP_DIR))).expanduser().resolve()
    cast_hls_dir = Path(
        os.environ.get("M3U_CAST_HLS_DIR", "/tmp/m3u-web-picker-cast-hls")
    ).expanduser()
    return AppSettings(
        data_dir=data_dir,
        port=_env_int("M3U_PORT", 9999),
        dev_port=_env_int("M3U_DEV_PORT", 9998),
        max_upload_bytes=_env_int("M3U_MAX_UPLOAD_BYTES", 50 * 1024 * 1024),
        schedule_hour=_env_int("MASTER_REFRESH_HOUR", 3),
        schedule_minute=_env_int("MASTER_REFRESH_MINUTE", 0),
        max_provider_channels=_env_int("M3U_MAX_PROVIDER_CHANNELS", 50000),
        provider_channel_warning=_env_int("M3U_PROVIDER_CHANNEL_WARNING", 20000),
        max_provider_playlist_bytes=_env_int("M3U_MAX_PROVIDER_PLAYLIST_BYTES", 96 * 1024 * 1024),
        max_provider_json_bytes=_env_int("M3U_MAX_PROVIDER_JSON_BYTES", 96 * 1024 * 1024),
        max_public_epg_compressed_bytes=_env_int(
            "M3U_MAX_PUBLIC_EPG_COMPRESSED_BYTES", 256 * 1024 * 1024
        ),
        cast_hls_dir=cast_hls_dir,
        lan_host=str(os.environ.get("M3U_LAN_HOST", "") or "").strip(),
        external_port=_env_int("M3U_EXTERNAL_PORT", 1000),
    )


SETTINGS = load_settings()
