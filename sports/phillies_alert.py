from __future__ import annotations

import io
import math
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from . import alert_stream_base as base


CANVAS_WIDTH = 960
CANVAS_HEIGHT = 620

PHILLIES_RED = (232, 24, 40, 255)
PHILLIES_NAVY = (0, 45, 114, 255)
WHITE = (255, 255, 255, 255)

SLIDE_IN_SECONDS = 0.9
SLIDE_OUT_START_SECONDS = 6.6
SLIDE_OUT_SECONDS = 1.0

_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "phillies_phanatic.png"
_ASSET_LOCK = threading.RLock()
_ASSET: Image.Image | None = None
_GRAPHIC_LOCK = threading.RLock()
_GRAPHICS: dict[tuple[object, ...], Image.Image] = {}


def is_phillies_scoring_alert(alert) -> bool:
    if alert is None or str(alert.league or "").strip().upper() != "MLB":
        return False
    team = alert.scoring_team
    name = " ".join(str(team.name or "").strip().casefold().split())
    abbr = str(team.abbr or "").strip().upper()
    return (
        name in {"philadelphia phillies", "phillies"}
        or name.endswith(" phillies")
        or abbr == "PHI"
    )


def _load_asset() -> Image.Image | None:
    global _ASSET
    with _ASSET_LOCK:
        if _ASSET is not None:
            return _ASSET.copy()
        try:
            with Image.open(_ASSET_PATH) as source:
                image = source.convert("RGBA")
        except OSError:
            return None
        _ASSET = image
        return image.copy()


def _team_abbr(team) -> str:
    abbr = str(team.abbr or "").strip().upper()
    if abbr:
        return abbr
    name = " ".join(str(team.name or "").strip().split())
    if name.casefold().endswith("phillies"):
        return "PHI"
    last = name.rsplit(" ", 1)[-1] if name else "TEAM"
    return last[:3].upper()


def _centered_text(
    image: Image.Image,
    text: str,
    *,
    y: int,
    font_size: int,
    fill: tuple[int, int, int, int],
    inner_stroke: tuple[int, int, int, int] = WHITE,
    outer_stroke: tuple[int, int, int, int] = PHILLIES_NAVY,
    outer_width: int = 9,
    inner_width: int = 5,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    font = base.demo._font(font_size, bold=True)
    box = draw.textbbox((0, 0), text, font=font, stroke_width=outer_width)
    x = (image.width - (box[2] - box[0])) // 2 - box[0]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=outer_width,
        stroke_fill=outer_stroke,
    )
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=inner_width,
        stroke_fill=inner_stroke,
    )


def _arc_character(
    char: str,
    font,
    *,
    angle_degrees: float,
) -> Image.Image:
    pad = 16
    probe = Image.new("RGBA", (180, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe, "RGBA")
    box = draw.textbbox((0, 0), char, font=font, stroke_width=9)
    width = max(1, box[2] - box[0] + pad * 2)
    height = max(1, box[3] - box[1] + pad * 2)
    glyph = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyph, "RGBA")
    xy = (pad - box[0], pad - box[1])
    glyph_draw.text(
        xy,
        char,
        font=font,
        fill=PHILLIES_RED,
        stroke_width=9,
        stroke_fill=PHILLIES_NAVY,
    )
    glyph_draw.text(
        xy,
        char,
        font=font,
        fill=PHILLIES_RED,
        stroke_width=5,
        stroke_fill=WHITE,
    )
    return glyph.rotate(
        angle_degrees,
        resample=Image.Resampling.BICUBIC,
        expand=True,
    )


def _draw_arc_title(image: Image.Image) -> None:
    text = "PHILLIES SCORE!"
    font = base.demo._font(68, bold=True)
    left = 165.0
    right = float(image.width - 165)
    count = max(1, len(text) - 1)

    for index, char in enumerate(text):
        if char == " ":
            continue
        t = index / count
        normalized = (t - 0.5) * 2.0
        x = left + (right - left) * t
        y = 26.0 + 84.0 * (normalized * normalized)
        slope = (168.0 * normalized) / (right - left)
        angle = math.degrees(math.atan(slope))
        glyph = _arc_character(char, font, angle_degrees=angle)
        image.alpha_composite(
            glyph,
            (
                int(round(x - glyph.width / 2)),
                int(round(y - glyph.height / 2)),
            ),
        )


def _graphic_key(alert) -> tuple[object, ...]:
    return (
        _team_abbr(alert.away),
        int(alert.away_score),
        _team_abbr(alert.home),
        int(alert.home_score),
        str(alert.source_channel or ""),
    )


def _build_graphic(alert) -> Image.Image | None:
    key = _graphic_key(alert)
    with _GRAPHIC_LOCK:
        cached = _GRAPHICS.get(key)
        if cached is not None:
            return cached.copy()

    art = _load_asset()
    if art is None:
        return None

    # Deliberately huge: this is the Phillies-only gag, not the compact generic
    # notification.  It fills most of a 720p frame vertically while keeping the
    # same bottom-center FFmpeg overlay anchor.
    art.thumbnail((560, 500), Image.Resampling.LANCZOS)
    graphic = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))

    art_x = (CANVAS_WIDTH - art.width) // 2
    art_y = CANVAS_HEIGHT - art.height
    graphic.alpha_composite(art, (art_x, art_y))

    _draw_arc_title(graphic)

    score = (
        f"{_team_abbr(alert.away)} {int(alert.away_score)}"
        f" - {int(alert.home_score)} {_team_abbr(alert.home)}"
    )
    _centered_text(
        graphic,
        score,
        y=115,
        font_size=58,
        fill=PHILLIES_RED,
        outer_width=8,
        inner_width=4,
    )

    channel = f"On channel: {alert.source_channel}"
    channel_font = base.demo._font(27, bold=True)
    draw = ImageDraw.Draw(graphic, "RGBA")
    box = draw.textbbox((0, 0), channel, font=channel_font)
    text_width = box[2] - box[0]
    text_height = box[3] - box[1]
    pad_x = 18
    pad_y = 8
    left = (CANVAS_WIDTH - text_width) // 2 - pad_x
    top = 187
    right = left + text_width + pad_x * 2
    bottom = top + text_height + pad_y * 2
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=16,
        fill=(0, 45, 114, 235),
        outline=(255, 255, 255, 235),
        width=2,
    )
    draw.text(
        ((CANVAS_WIDTH - text_width) // 2 - box[0], top + pad_y - box[1]),
        channel,
        font=channel_font,
        fill=WHITE,
    )

    with _GRAPHIC_LOCK:
        if len(_GRAPHICS) >= 12:
            _GRAPHICS.pop(next(iter(_GRAPHICS)))
        _GRAPHICS[key] = graphic
    return graphic.copy()


def phillies_slide_offset(elapsed: float) -> int:
    value = max(0.0, float(elapsed))
    if value < SLIDE_IN_SECONDS:
        progress = value / SLIDE_IN_SECONDS
        eased = 1.0 - (1.0 - progress) ** 3
        return int(round(CANVAS_HEIGHT * (1.0 - eased)))

    if value < SLIDE_OUT_START_SECONDS:
        return 0

    progress = min(1.0, (value - SLIDE_OUT_START_SECONDS) / SLIDE_OUT_SECONDS)
    eased = progress ** 3
    return int(round(CANVAS_HEIGHT * eased))


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def _expand_standard(payload: bytes) -> bytes:
    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    try:
        with Image.open(io.BytesIO(payload)) as source:
            card = source.convert("RGBA")
    except Exception:
        return _encode_png(canvas)

    canvas.alpha_composite(
        card,
        (
            (CANVAS_WIDTH - card.width) // 2,
            CANVAS_HEIGHT - card.height,
        ),
    )
    return _encode_png(canvas)


def render_alert(alert) -> bytes:
    if not is_phillies_scoring_alert(alert):
        return _expand_standard(base._standard_render_alert(alert))

    elapsed = base._animation_elapsed(alert)
    graphic = _build_graphic(alert)
    if graphic is None:
        return _expand_standard(base._standard_render_alert(alert))

    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), (0, 0, 0, 0))
    offset = phillies_slide_offset(elapsed)
    if offset < CANVAS_HEIGHT:
        canvas.alpha_composite(graphic, (0, offset))
    return _encode_png(canvas)
