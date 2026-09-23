"""Figures for the calculation reports, drawn as PNG with Pillow (installed with reportlab)."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

INK = (40, 44, 52)
MUTED = (120, 126, 136)
COLUMN = (205, 222, 240)
FIELD = (232, 240, 226)
STATION = (196, 90, 40)


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def slab_stations(sd: dict[str, Any], box: dict[str, list[float]]) -> bytes:
    """Plan of the slab: column and field strips, pile rows and the stations (as the office's
    Figure 5-1). Along the quay across the page; distance from the front beam down the page."""
    along = sd["along"]
    across = "Y" if along == "X" else "X"
    a0, a1 = box[across]
    depth = max(sd["stations"][-1] if sd["stations"] else 0.0, 1.0)
    w, h, left, top, right, bottom = 1600, 900, 110, 90, 60, 90
    sx = (w - left - right) / (a1 - a0)
    sy = (h - top - bottom) / depth
    scale = min(sx, sy)
    pw = (a1 - a0) * scale
    ox, oy = left + ((w - left - right) - pw) / 2, top

    def px(t: float) -> float:
        return ox + (t - a0) * scale

    def py(s: float) -> float:
        return oy + s * scale

    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f, small = _font(22), _font(18)
    lines = sd["lines"]
    half_c, half_f = sd["column_width_m"] / 2, sd["field_width_m"] / 2
    for a, b in zip(lines, lines[1:], strict=False):
        m = (a + b) / 2
        d.rectangle([px(max(a0, m - half_f)), py(0), px(min(a1, m + half_f)), py(depth)], fill=FIELD)
    for c in lines:
        d.rectangle([px(max(a0, c - half_c)), py(0), px(min(a1, c + half_c)), py(depth)], fill=COLUMN)
    d.rectangle([px(a0), py(0), px(a1), py(depth)], outline=INK, width=3)
    for s in sd["stations"]:
        y = py(s)
        for x in range(int(px(a0)), int(px(a1)), 18):
            d.line([x, y, min(x + 10, px(a1)), y], fill=STATION, width=2)
        d.text((px(a0) - 12, y), f"{s:g}", fill=STATION, font=f, anchor="rm")
    r = max(6.0, 0.6 * scale)
    for s in sd.get("pile_rows_m") or []:
        for c in lines:
            d.ellipse([px(c) - r, py(s) - r, px(c) + r, py(s) + r], outline=INK, width=2, fill="white")
    for a, b in zip(lines, lines[1:], strict=False):
        d.text((px((a + b) / 2), py(depth) + 12), "field", fill=MUTED, font=small, anchor="mt")
    for c in lines:
        d.text((px(c), py(depth) + 12), "column", fill=INK, font=small, anchor="mt")
    d.text(
        (px((a0 + a1) / 2), py(0) - 14),
        f"{sd['from'].capitalize()} (station 0)",
        fill=INK,
        font=f,
        anchor="mb",
    )
    d.text((px(a0) - 12, py(0) - 30), "Station (m)", fill=STATION, font=small, anchor="rb")
    d.text(
        (w / 2, h - 18),
        f"Column strips {sd['column_width_m']:g} m on the pile lines, field strips {sd['field_width_m']:g} m "
        f"between; across the page: global {across} ({a0:g} to {a1:g} m)",
        fill=MUTED,
        font=small,
        anchor="mb",
    )
    box_ = ImageOps.invert(img.convert("L")).getbbox()
    if box_:
        pad = 16
        img = img.crop(
            (max(0, box_[0] - pad), max(0, box_[1] - pad), min(w, box_[2] + pad), min(h, box_[3] + pad))
        )
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
