"""Triton drawings as an AutoCAD DXF (R12, ASCII): opens in any AutoCAD, and in Revit with Import CAD.

Each view is placed in model space in a row, left to right, at 1:1 in mm, with a gap between views.
Every bar size has its own layer (named on the Project tab); cut bars are filled dots (a donut), bars
and links along the view are lines, circles or closed polylines.
"""

from __future__ import annotations

from typing import Any

GAP_MM = 3000.0
ROW_WIDTH_MM = 120_000.0  # start a new row of views past this width

# AutoCAD colour per bar size, so the sizes read apart at a glance.
_COLOURS = {8: 8, 10: 30, 12: 40, 14: 50, 16: 3, 20: 4, 25: 5, 28: 6, 32: 1, 40: 200}


def _pairs(out: list[str], *codes: tuple[int, Any]) -> None:
    for code, value in codes:
        if isinstance(value, float):
            value = f"{value:.3f}"
        out.append(f"{code:>3}")
        out.append(str(value))


def _cad_text(text: str) -> str:
    """Text as AutoCAD R12 reads it: Ø as %%c, other non-ASCII spelled out."""
    t = text.replace("Ø", "%%c").replace("×", "x").replace("·", "-").replace("–", "-").replace("°", "%%d")
    return t.encode("ascii", "replace").decode("ascii")


def _cad_name(name: str) -> str:
    bad = '<>/\\":;?*|=`'
    return "".join("_" if c in bad or ord(c) > 126 else c for c in name).strip() or "0"


def placements(views: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Where each view's own origin goes in model space: views side by side, rows downward."""
    out = []
    x = 0.0
    y = 0.0
    row_h = 0.0
    for v in views:
        x0, y0, x1, y1 = v["box"]
        w, h = x1 - x0, y1 - y0
        if x > 0 and x + w > ROW_WIDTH_MM:
            x, y, row_h = 0.0, y - row_h - GAP_MM * 2, 0.0
        out.append((x - x0, y - y1))  # the view's top left at (x, y)
        x += w + GAP_MM
        row_h = max(row_h, h)
    return out


def to_dxf(data: dict[str, Any]) -> str:
    layers = {k: _cad_name(v.get("cad_layer") or k) for k, v in data["layers"].items()}
    colours: dict[str, int] = {}
    for k, v in data["layers"].items():
        if k.startswith("bar-"):
            colours[layers[k]] = _COLOURS.get(v.get("diameter_mm"), 2)
        elif k == "concrete":
            colours[layers[k]] = 7
        elif k == "zones":
            colours[layers[k]] = 9
        else:
            colours.setdefault(layers[k], 7)
    out: list[str] = []
    _pairs(out, (0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1009"), (9, "$INSUNITS"), (70, 4))
    _pairs(out, (0, "ENDSEC"))
    _pairs(out, (0, "SECTION"), (2, "TABLES"))
    _pairs(out, (0, "TABLE"), (2, "LTYPE"), (70, 1))
    _pairs(out, (0, "LTYPE"), (2, "CONTINUOUS"), (70, 0), (3, "Solid line"), (72, 65), (73, 0), (40, 0.0))
    _pairs(out, (0, "ENDTAB"))
    _pairs(out, (0, "TABLE"), (2, "LAYER"), (70, len(colours) + 1))
    _pairs(out, (0, "LAYER"), (2, "0"), (70, 0), (62, 7), (6, "CONTINUOUS"))
    for name, colour in colours.items():
        if name != "0":
            _pairs(out, (0, "LAYER"), (2, name), (70, 0), (62, colour), (6, "CONTINUOUS"))
    _pairs(out, (0, "ENDTAB"))
    _pairs(out, (0, "TABLE"), (2, "STYLE"), (70, 1))
    _pairs(
        out,
        (0, "STYLE"),
        (2, "STANDARD"),
        (70, 0),
        (40, 0.0),
        (41, 1.0),
        (50, 0.0),
        (71, 0),
        (42, 2.5),
        (3, "txt"),
        (4, ""),
    )
    _pairs(out, (0, "ENDTAB"))
    _pairs(out, (0, "ENDSEC"))
    _pairs(out, (0, "SECTION"), (2, "ENTITIES"))
    for v, (dx, dy) in zip(data["views"], placements(data["views"]), strict=True):
        for it in v["items"]:
            _entity(out, it, layers.get(it["layer"], "0"), dx, dy)
    _pairs(out, (0, "ENDSEC"), (0, "EOF"))
    return "\r\n".join(out) + "\r\n"


def _entity(out: list[str], it: dict[str, Any], layer: str, dx: float, dy: float) -> None:
    t = it["type"]
    if t == "line":
        (ax, ay), (bx, by) = it["a"], it["b"]
        _pairs(
            out,
            (0, "LINE"),
            (8, layer),
            (10, ax + dx),
            (20, ay + dy),
            (30, 0.0),
            (11, bx + dx),
            (21, by + dy),
            (31, 0.0),
        )
    elif t == "circle":
        (cx, cy), r = it["c"], it["r"]
        _pairs(out, (0, "CIRCLE"), (8, layer), (10, cx + dx), (20, cy + dy), (30, 0.0), (40, float(r)))
    elif t == "rect":
        (ax, ay), (bx, by) = it["a"], it["b"]
        _polyline(
            out,
            layer,
            [(ax + dx, ay + dy), (bx + dx, ay + dy), (bx + dx, by + dy), (ax + dx, by + dy)],
            0.0,
            0.0,
        )
    elif t == "bar":
        # A filled dot as AutoCAD's DONUT draws it: two half circles of width r about radius r/2.
        (cx, cy), d = it["c"], float(it["d"])
        r = d / 2
        _polyline(out, layer, [(cx + dx - r / 2, cy + dy), (cx + dx + r / 2, cy + dy)], r, 1.0)
    elif t == "text":
        (x, y) = it["at"]
        _pairs(
            out,
            (0, "TEXT"),
            (8, layer),
            (10, x + dx),
            (20, y + dy),
            (30, 0.0),
            (40, float(it["h"])),
            (1, _cad_text(it["text"])),
        )


def _polyline(out: list[str], layer: str, pts: list[tuple[float, float]], width: float, bulge: float) -> None:
    _pairs(out, (0, "POLYLINE"), (8, layer), (66, 1), (10, 0.0), (20, 0.0), (30, 0.0), (70, 1))
    if width:
        _pairs(out, (40, width), (41, width))
    for x, y in pts:
        _pairs(out, (0, "VERTEX"), (8, layer), (10, x), (20, y), (30, 0.0))
        if bulge:
            _pairs(out, (42, bulge))
    _pairs(out, (0, "SEQEND"), (8, layer))
