from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw

from . import mlb_stats_enrichment


# Primary MLB team colors are intentionally static presentation metadata, not
# live-game data. Values are the first/primary RGB color in the MLB team-color
# reference table used for this experiment. Keeping the 30-team map in-process
# avoids logo-pixel heuristics and any runtime color lookup.
_PRIMARY_BY_ABBR: dict[str, tuple[int, int, int]] = {
    "ARI": (167, 25, 48),
    "ATL": (206, 17, 65),
    "BAL": (223, 70, 1),
    "BOS": (189, 48, 57),
    "CHC": (14, 51, 134),
    "CWS": (39, 37, 31),
    "CIN": (198, 1, 31),
    "CLE": (0, 56, 93),
    "COL": (51, 51, 102),
    "DET": (12, 35, 64),
    "HOU": (0, 45, 98),
    "KC": (0, 70, 135),
    "LAA": (0, 50, 99),
    "LAD": (0, 90, 156),
    "MIA": (0, 163, 224),
    "MIL": (18, 40, 75),
    "MIN": (0, 43, 92),
    "NYM": (0, 45, 114),
    "NYY": (0, 48, 135),
    "ATH": (0, 56, 49),
    "PHI": (232, 24, 40),
    "PIT": (39, 37, 31),
    "SD": (47, 36, 29),
    "SF": (253, 90, 30),
    "SEA": (12, 44, 86),
    "STL": (196, 30, 58),
    "TB": (9, 44, 92),
    "TEX": (0, 50, 120),
    "TOR": (19, 74, 142),
    "WSH": (171, 0, 3),
}

_ABBR_ALIASES = {
    "CHW": "CWS",
    "KCR": "KC",
    "OAK": "ATH",
    "SDP": "SD",
    "SFG": "SF",
    "TBR": "TB",
    "WSN": "WSH",
}

_PANEL = (17, 27, 41)
_MUTED = (144, 160, 180)
_ACCENT = (79, 140, 255)
_INACTIVE = (34, 47, 65)
_OUTLINE = (120, 143, 170)
_TEXT = (238, 243, 249)


def _blend(base: tuple[int, int, int], accent: tuple[int, int, int], weight: float) -> tuple[int, int, int]:
    weight = max(0.0, min(1.0, float(weight)))
    return tuple(
        int(round(base[index] * (1.0 - weight) + accent[index] * weight))
        for index in range(3)
    )


def _canonical_team_accent(team: dict, _logo=None) -> tuple[int, int, int] | None:
    abbreviation = str(team.get("abbr") or team.get("abbreviation") or "").strip().upper()
    abbreviation = _ABBR_ALIASES.get(abbreviation, abbreviation)
    accent = _PRIMARY_BY_ABBR.get(abbreviation)
    if accent is None:
        return None

    luminance = 0.2126 * accent[0] + 0.7152 * accent[1] + 0.0722 * accent[2]
    if luminance < 70:
        accent = _blend(accent, (255, 255, 255), 0.18)
    return accent


def _ordinal(number: int) -> str:
    value = max(1, int(number or 1))
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def _inning_marker(state: dict) -> tuple[str, str, bool]:
    """Return (direction, label, show_outs) for the compact center scorebug."""
    status = str(state.get("status") or "MLB").strip()
    match = re.match(r"^(top|bottom|middle|mid|end)\s+(.+)$", status, re.IGNORECASE)
    if not match:
        return "", status, False

    phase = match.group(1).lower()
    label = match.group(2).strip()
    period = int(state.get("period", 0) or 0)

    if phase == "top":
        return "up", label, True
    if phase == "bottom":
        return "down", label, True
    if phase in {"middle", "mid"}:
        return "down", label, True
    return "up", _ordinal(period + 1 if period > 0 else 1), True


def _draw_triangle(draw: ImageDraw.ImageDraw, *, direction: str, center_x: int, center_y: int) -> None:
    half_width = 9
    half_height = 8
    if direction == "down":
        points = [
            (center_x - half_width, center_y - half_height),
            (center_x + half_width, center_y - half_height),
            (center_x, center_y + half_height),
        ]
    else:
        points = [
            (center_x - half_width, center_y + half_height),
            (center_x + half_width, center_y + half_height),
            (center_x, center_y - half_height),
        ]
    draw.polygon(points, fill=_ACCENT)


def _draw_center_status(draw: ImageDraw.ImageDraw, live_stats, state: dict, width: int) -> None:
    center_x = width // 2
    draw.rectangle((500, 40, width - 500, 148), fill=_PANEL)

    direction, label, show_outs = _inning_marker(state)
    font = live_stats._font(26, bold=True)
    text_box = draw.textbbox((0, 0), label, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]

    if direction:
        marker_width = 18
        gap = 10
        group_width = marker_width + gap + text_width
        group_left = center_x - group_width // 2
        _draw_triangle(
            draw,
            direction=direction,
            center_x=group_left + marker_width // 2,
            center_y=70,
        )
        text_x = group_left + marker_width + gap
    else:
        text_x = center_x - text_width // 2

    text_y = 70 - text_height // 2 - text_box[1]
    live_stats._text(draw, (text_x, text_y), label, size=26, bold=True, fill=_ACCENT)

    if not show_outs:
        return

    outs = mlb_stats_enrichment._out_dot_count(state)
    radius = 7
    for index, dot_x in enumerate((center_x - 13, center_x + 13)):
        fill = _ACCENT if index < outs else _INACTIVE
        draw.ellipse(
            (dot_x - radius, 112 - radius, dot_x + radius, 112 + radius),
            fill=fill,
            outline=_OUTLINE,
            width=2,
        )


def _draw_centered_score(
    draw: ImageDraw.ImageDraw,
    live_stats,
    value: object,
    *,
    center_x: int,
) -> None:
    text = str(value if value is not None else "0")
    font = live_stats._font(72, bold=True)
    box = draw.textbbox((0, 0), text, font=font)
    text_width = box[2] - box[0]
    live_stats._text(draw, (center_x - text_width // 2, 48), text, size=72, bold=True)


def _redraw_scores(draw: ImageDraw.ImageDraw, live_stats, state: dict, width: int) -> None:
    away = state.get("away") if isinstance(state.get("away"), dict) else {}
    home = state.get("home") if isinstance(state.get("home"), dict) else {}

    away_center = 420
    home_center = width - 420
    draw.rectangle((350, 40, 490, 148), fill=_PANEL)
    draw.rectangle((width - 490, 40, width - 350, 148), fill=_PANEL)
    _draw_centered_score(draw, live_stats, away.get("score", "0"), center_x=away_center)
    _draw_centered_score(draw, live_stats, home.get("score", "0"), center_x=home_center)


def _draw_count_row(
    draw: ImageDraw.ImageDraw,
    live_stats,
    *,
    label: str,
    count: int,
    maximum: int,
    y: int,
) -> None:
    live_stats._text(draw, (808, y - 13), label, size=21, bold=True, fill=_MUTED)
    first_x = 854
    spacing = 26
    radius = 7
    count = max(0, min(maximum, int(count or 0)))
    for index in range(maximum):
        x = first_x + index * spacing
        fill = _ACCENT if index < count else _INACTIVE
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=fill,
            outline=_OUTLINE,
            width=2,
        )


def _redraw_at_bat_details(draw: ImageDraw.ImageDraw, live_stats, state: dict) -> None:
    # Clear the old numeric B/S rows and old left-anchored batter/pitcher block.
    draw.rectangle((800, 246, 930, 336), fill=_PANEL)
    draw.rectangle((800, 392, 1230, 468), fill=_PANEL)

    _draw_count_row(
        draw,
        live_stats,
        label="B",
        count=int(state.get("balls", 0) or 0),
        maximum=3,
        y=276,
    )
    _draw_count_row(
        draw,
        live_stats,
        label="S",
        count=int(state.get("strikes", 0) or 0),
        maximum=2,
        y=322,
    )

    batter = str(state.get("batter") or "").strip()
    pitcher = str(state.get("pitcher") or "").strip()
    lines: list[tuple[str, bool]] = []
    if batter:
        lines.append((f"Batter: {batter}", True))
    if pitcher:
        lines.append((f"Pitcher: {pitcher}", False))
    if not lines:
        return

    measured: list[int] = []
    for text, bold in lines:
        box = draw.textbbox((0, 0), text, font=live_stats._font(18, bold=bold))
        measured.append(box[2] - box[0])
    block_width = max(measured)
    block_x = 1016 - block_width // 2
    start_y = 404 if len(lines) == 2 else 420

    for index, (text, bold) in enumerate(lines):
        live_stats._text(
            draw,
            (block_x, start_y + index * 31),
            text,
            size=18,
            bold=bold,
            fill=_TEXT,
        )


def _clear_at_bat_outs(draw: ImageDraw.ImageDraw, width: int) -> None:
    draw.rectangle((1090, 198, width - 40, 238), fill=_PANEL)


def install(live_stats) -> None:
    """Install canonical team colors and compact MLB scorebug presentation."""
    if getattr(live_stats, "_mlb_stats_scorebug_installed", False):
        return

    mlb_stats_enrichment._team_accent = _canonical_team_accent
    original_render = live_stats.render_mlb_frame

    def render_mlb_frame(state: dict, *, width: int = 1280, height: int = 720) -> bytes:
        payload = original_render(state, width=width, height=height)
        image = Image.open(io.BytesIO(payload)).convert("RGB")
        draw = ImageDraw.Draw(image)
        _redraw_scores(draw, live_stats, state, width)
        _clear_at_bat_outs(draw, width)
        _redraw_at_bat_details(draw, live_stats, state)
        _draw_center_status(draw, live_stats, state, width)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()

    live_stats.render_mlb_frame = render_mlb_frame
    live_stats._mlb_stats_scorebug_installed = True
