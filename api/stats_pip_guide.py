from __future__ import annotations

import re
from datetime import datetime
from xml.etree import ElementTree

from flask import Flask, Response

import core
import sports
import sports.guide as sports_guide
from media import browser
from settings import load_settings
from sports import mlb_stats_pip
from . import guide as guide_api


_PIP_PLAY_RE = re.compile(r"^/guide/play/stats-pip/(\d+)$")
_installed = False
_original_curated_channels = None
_original_play_target_resolver = None
_original_build_sports_xmltv = None


def _internal_pip_hls_url(assigned_number: int) -> str:
    settings = load_settings()
    return f"http://127.0.0.1:{settings.port}/sports/stats-pip/{int(assigned_number)}/stream.m3u8"


def _inject_pip_guide(items: list[dict]) -> list[dict]:
    rows = {
        int(row.get("assigned_number") or 0): row
        for row in mlb_stats_pip.live_rows(core.DB_PATH)
    }
    if not rows:
        return items

    output: list[dict] = []
    inserted: set[int] = set()
    for item in items:
        output.append(item)
        if not bool(item.get("stats_companion")):
            continue
        try:
            number = int(item.get("stats_parent") or 0)
        except (TypeError, ValueError):
            continue
        row = rows.get(number)
        if row is None:
            continue
        output.append(mlb_stats_pip.guide_item(row))
        inserted.add(number)

    # The existing .1 guide layer normally gives us a reliable insertion point.
    # Keep a fallback so a temporary .1 filtering bug cannot also hide .2.
    for number, row in sorted(rows.items()):
        if number not in inserted:
            output.append(mlb_stats_pip.guide_item(row))
    return output


def install() -> None:
    global _installed
    global _original_curated_channels
    global _original_play_target_resolver
    global _original_build_sports_xmltv

    if _installed:
        return

    _original_curated_channels = core.curated_channels_for_guide
    _original_play_target_resolver = guide_api._resolve_guide_play_target
    _original_build_sports_xmltv = sports_guide.build_sports_xmltv

    def curated_channels_for_guide() -> list[dict]:
        return _inject_pip_guide(list(_original_curated_channels()))

    def resolve_guide_play_target(play_url: str) -> str:
        value = str(play_url or "").split("?", 1)[0].strip()
        match = _PIP_PLAY_RE.fullmatch(value)
        if match:
            assigned = int(match.group(1))
            if mlb_stats_pip.live_row_for_number(core.DB_PATH, assigned) is not None:
                return _internal_pip_hls_url(assigned)
            return ""
        return _original_play_target_resolver(play_url)

    def build_sports_xmltv(generated, settings, *, generated_at: datetime | None = None):
        payload = _original_build_sports_xmltv(
            generated,
            settings,
            generated_at=generated_at,
        )
        root = ElementTree.fromstring(payload)
        mlb_stats_pip.append_xmltv_for_db(
            root,
            core.DB_PATH,
            str(settings.get("timezone", "America/New_York")),
            generated_at=generated_at,
        )
        if hasattr(ElementTree, "indent"):
            ElementTree.indent(root, space="  ")
        return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)

    core.curated_channels_for_guide = curated_channels_for_guide
    guide_api._resolve_guide_play_target = resolve_guide_play_target
    sports_guide.build_sports_xmltv = build_sports_xmltv
    sports.build_sports_xmltv = build_sports_xmltv
    _installed = True

    try:
        core.ensure_epg_exports_current(force=True)
    except Exception:
        # The guide may not exist yet during first-run setup; route registration
        # must still succeed and the next normal export will include PiP rows.
        pass


def register_stats_pip_guide_routes(app: Flask) -> None:
    @app.get("/guide/play/stats-pip/<int:assigned_number>")
    def guide_play_mlb_stats_pip(assigned_number: int):
        if mlb_stats_pip.live_row_for_number(core.DB_PATH, assigned_number) is None:
            return Response(
                "MLB PiP companion is not currently live.\n",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
        return browser.response_for(_internal_pip_hls_url(assigned_number))
