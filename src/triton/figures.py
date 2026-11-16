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
    s0 = sd["stations"][0] if sd["stations"] else 0.0
    depth = max((sd["stations"][-1] if sd["stations"] else 0.0) - s0, 1.0)
    w, h, left, top, right, bottom = 1600, 900, 110, 90, 60, 90
    sx = (w - left - right) / (a1 - a0)
    sy = (h - top - bottom) / depth
    scale = min(sx, sy)
    pw = (a1 - a0) * scale
    ox, oy = left + ((w - left - right) - pw) / 2, top

    def px(t: float) -> float:
        return ox + (t - a0) * scale

    def py(s: float) -> float:
        return oy + (s - s0) * scale

    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f, small = _font(22), _font(18)
    lines = sd["lines"]
    half_c, half_f = sd["column_width_m"] / 2, sd["field_width_m"] / 2
    for a, b in zip(lines, lines[1:], strict=False):
        m = (a + b) / 2
        d.rectangle([px(max(a0, m - half_f)), py(s0), px(min(a1, m + half_f)), py(s0 + depth)], fill=FIELD)
    for c in lines:
        d.rectangle([px(max(a0, c - half_c)), py(s0), px(min(a1, c + half_c)), py(s0 + depth)], fill=COLUMN)
    d.rectangle([px(a0), py(s0), px(a1), py(s0 + depth)], outline=INK, width=3)
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
        d.text((px((a + b) / 2), py(s0 + depth) + 12), "field", fill=MUTED, font=small, anchor="mt")
    for c in lines:
        d.text((px(c), py(s0 + depth) + 12), "column", fill=INK, font=small, anchor="mt")
    d.text(
        (px((a0 + a1) / 2), py(s0) - 14),
        f"Sea side: stations from the {sd['from']} (station 0)",
        fill=INK,
        font=f,
        anchor="mb",
    )
    d.text((px(a0) - 12, py(s0) - 30), "Station (m)", fill=STATION, font=small, anchor="rb")
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


SERIES = [(31, 119, 180), (214, 110, 40)]


def deflected_shape(entry: dict[str, Any]) -> bytes:
    """An element's estimated displacement (mm) against level, one line per direction, with the
    undeformed member as a dashed line at zero."""
    curves = [c for c in entry.get("directions") or [] if c.get("stations")]
    zs = [s for c in curves for s, _ in c["stations"]]
    ws = [w for c in curves for _, w in c["stations"]] + [0.0]
    z0, z1 = min(zs), max(zs)
    span = max(max(ws) - min(ws), 1.0)
    w0, w1 = min(ws) - 0.08 * span, max(ws) + 0.08 * span
    w, h, left, top, right, bottom = 900, 900, 120, 70, 50, 150
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f, small = _font(22), _font(18)

    def px(v: float) -> float:
        return left + (v - w0) / (w1 - w0) * (w - left - right)

    def py(z: float) -> float:
        return top + (z1 - z) / max(z1 - z0, 1e-9) * (h - top - bottom)

    d.rectangle([left, top, w - right, h - bottom], outline=MUTED, width=1)
    for y in range(top, h - bottom, 16):
        d.line([px(0), y, px(0), min(y + 8, h - bottom)], fill=MUTED, width=2)
    for z in (z0, z1):
        d.text((left - 10, py(z)), f"{z:.1f}", fill=INK, font=small, anchor="rm")
    for v in (w0, 0.0, w1):
        d.text((px(v), h - bottom + 10), f"{v:.0f}", fill=INK, font=small, anchor="mt")
    for i, c in enumerate(curves):
        pts = [(px(v), py(z)) for z, v in c["stations"]]
        d.line(pts, fill=SERIES[i % 2], width=4, joint="curve")
        y = h - bottom + 54 + 28 * i
        d.line([left, y, left + 36, y], fill=SERIES[i % 2], width=4)
        d.text((left + 46, y), c["label"], fill=SERIES[i % 2], font=small, anchor="lm")
    d.text(
        (w / 2, top - 20), f"{entry['element']}: estimated displacement (mm)", fill=INK, font=f, anchor="mb"
    )
    d.text((left - 10, top - 20), "Level (m)", fill=INK, font=small, anchor="rb")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


BAR_ADD = (200, 50, 40)
UNICODE_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "DejaVuSans.ttf",
)


def _ufont(size: int) -> tuple[ImageFont.ImageFont, bool]:
    """A font with Ø in it when the system has one (else Pillow's own, and bars read "dia")."""
    for path in UNICODE_FONTS:
        try:
            return ImageFont.truetype(path, size), True
        except OSError:
            continue
    return _font(size), False


def _layer_name(n: int) -> str:
    return "Mesh level" if n == 1 else f"L{n - 1}"


def _layer_lines(layers: list[dict[str, Any]] | None) -> list[str]:
    """Each layer's additional bars (the mesh left out), as the design page names them."""
    out = []
    for q in layers or []:
        bars = [b for b in q.get("bars") or [] if b.get("kind") != "mesh"]
        if bars:
            out.append(
                f"{_layer_name(q['layer'])} "
                + " + ".join(f"Ø{b['diameter_mm']:g} @ {b['spacing_mm']:g}" for b in bars)
            )
    return out


def slab_bars(design: dict[str, Any], view: str = "along") -> bytes | None:
    """The deck's bars along X or along Y, as on the design page: each face's basic mesh over the whole
    length and the additional bars where they are added, one line per layer (column strip next to the
    mesh, field strip beyond along the strips; one group per zone across them)."""
    sd = design.get("strip_design")
    if not sd:
        return None
    along = sd["along"].lower()
    cross = "y" if along == "x" else "x"
    across_axis = "Y" if sd["along"] == "X" else "X"
    across = sd.get("across_profile") or {}
    rows_all = sd.get("rows") or []
    m_along = next((r.get("moment") for r in sd.get("table") or [] if r.get("along_strips")), None) or (
        "M11" if along == "x" else "M22"
    )
    is_along = view == "along"
    direction = along if is_along else cross
    box = design.get("box") or {}
    if is_along:
        lo, hi = sd["start"], sd["end"]
    else:
        rng = across.get("range") or box.get(across_axis) or [0.0, 1.0]
        lo, hi = rng[0], rng[1]
    segs: dict[str, list[dict[str, Any]]] = {"top": [], "bottom": []}
    for f in ("top", "bottom"):
        rows = [r for r in rows_all if r.get("layer") == f"{f}_{direction}" and r.get("additional_bars")]
        if is_along:
            for k, strip in enumerate(("column", "field")):
                merged: list[dict[str, Any]] = []
                for r in sorted((r for r in rows if r.get("strip") == strip), key=lambda r: r["station"][0]):
                    last = merged[-1] if merged else None
                    if (
                        last
                        and last["text"] == r["additional_bars"]
                        and abs(last["b"] - r["station"][0]) < 1e-6
                    ):
                        last["b"] = r["station"][1]
                    else:
                        merged.append(
                            {
                                "a": r["station"][0],
                                "b": r["station"][1],
                                "text": r["additional_bars"],
                                "lane": k,
                                "layers": r.get("bar_layers"),
                            }
                        )
                segs[f] += merged
        else:
            ax = 1 if across_axis == "Y" else 0
            items = [
                {
                    "a": r["zone"][ax][0],
                    "b": r["zone"][ax][1],
                    "text": r["additional_bars"],
                    "layers": r.get("bar_layers"),
                }
                for r in rows
                if r.get("zone")
            ]
            lanes: list[float] = []
            for s in sorted(items, key=lambda s: s["a"]):
                k = next((i for i, end in enumerate(lanes) if end <= s["a"] + 1e-6), None)
                if k is None:
                    k = len(lanes)
                    lanes.append(0.0)
                lanes[k] = s["b"]
                s["lane"] = k
            segs[f] = items
        for s in segs[f]:
            s["lines"] = _layer_lines(s["layers"]) or [str(s["text"])]
    per_lane = max([1] + [len(s["lines"]) for f in segs for s in segs[f]])
    n_top = max([1] + [s["lane"] + 1 for s in segs["top"]])
    n_bot = max([1] + [s["lane"] + 1 for s in segs["bottom"]])
    W, L, R = 1720, 300, 40
    lane = 12 + 30 * per_lane

    def X(s: float) -> float:
        return L + (s - lo) / ((hi - lo) or 1) * (W - L - R)

    y_mesh_t = 90 + n_top * lane + 16
    slab_top, slab_bot = y_mesh_t - 14, y_mesh_t - 14 + 116
    y_mesh_b = slab_bot - 14

    def lane_y(f: str, k: int) -> float:
        return y_mesh_t - 16 - (k + 0.5) * lane if f == "top" else y_mesh_b + 16 + (k + 0.5) * lane

    H = int(y_mesh_b + 16 + n_bot * lane + 90)
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    (f_, ok), (small, _) = _ufont(24), _ufont(19)

    class _D:  # text with Ø where the font has it
        def __getattr__(self, k: str) -> Any:
            return getattr(draw, k)

        def text(self, xy: Any, t: str, **kw: Any) -> None:
            draw.text(xy, t if ok else t.replace("Ø", "dia "), **kw)

    d = _D()
    layers = design.get("layers") or {}

    def mesh(f: str) -> str:
        return ((layers.get(f"{f}_{direction}") or {}).get("basic") or {}).get("label") or "–"

    title = (
        f"Bars along {sd['along']} ({m_along})"
        if is_along
        else f"Bars along {across_axis} ({across.get('moment', '')})"
    )
    d.text((L, 20), title, fill=INK, font=f_)
    d.text(
        (L, 50),
        ("Sea side on the left; stations in m from the " + str(sd.get("from", "")))
        if is_along
        else f"{across_axis} in m along the quay",
        fill=MUTED,
        font=small,
    )
    d.rectangle([X(lo), slab_top, X(hi), slab_bot], fill=(238, 236, 232))
    for y, f in ((y_mesh_t, "top"), (y_mesh_b, "bottom")):
        d.line([X(lo), y, X(hi), y], fill=INK, width=5)
        d.text(
            (L - 14, y), f"{'Top' if f == 'top' else 'Bottom'} {mesh(f)}", fill=INK, font=small, anchor="rm"
        )
    for f in ("top", "bottom"):
        for k in sorted({s["lane"] for s in segs[f]}):
            if is_along:
                name = f"{'Field' if k else 'Column'} strip, {m_along}"
            else:
                name = "" if k else f"Zones, {across.get('moment', '')}"
            if name:
                d.text((L - 14, lane_y(f, k)), name, fill=MUTED, font=small, anchor="rm")
        for s in segs[f]:
            x0, x1 = X(s["a"]), X(s["b"])
            top = lane_y(f, s["lane"]) - lane / 2
            room = int((x1 - x0 - 10) / 10.5)
            for j, t in enumerate(s["lines"]):
                y = top + lane - 10 - j * 30 if f == "top" else top + 20 + j * 30
                d.line([x0, y, x1, y], fill=BAR_ADD, width=4)
                d.line([x0, y - 6, x0, y + 6], fill=BAR_ADD, width=3)
                d.line([x1, y - 6, x1, y + 6], fill=BAR_ADD, width=3)
                label = t if len(t) <= room else (t[: room - 1] + "…" if room > 4 else "")
                if label:
                    d.text(((x0 + x1) / 2, y - 5), label, fill=INK, font=small, anchor="mb")
    axis_y = H - 40
    marks = sd.get("pile_rows_m") or [] if is_along else sd.get("lines") or []
    ticks = sd.get("stations") or [] if is_along else marks
    d.line([X(lo), axis_y, X(hi), axis_y], fill=MUTED, width=2)
    for m in marks:
        x = X(m)
        d.polygon([(x, axis_y - 22), (x - 11, axis_y - 3), (x + 11, axis_y - 3)], fill=MUTED)
    for t in [lo, *ticks, hi]:
        d.line([X(t), axis_y, X(t), axis_y + 7], fill=MUTED, width=2)
        d.text((X(t), axis_y + 10), f"{t:.1f}", fill=INK, font=small, anchor="mt")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
