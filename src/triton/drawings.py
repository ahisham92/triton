"""Reinforcement drawings as plain 2D detail lines, for AutoCAD (DXF) and Revit (a script draws them).

Triton works out every line here, so the AutoCAD file and the Revit script only copy it:

* a view is one drawing (a pile cage section, a pile elevation, a beam section, a slab plan of one face
  and direction, a slab cut); its items are in mm in the view's own plane;
* each item sits on a layer key: ``bar-<Ø>`` for bars and links, ``concrete``, ``zones`` (zone outlines
  and level marks) or ``text``; the project's drawing names (Project tab) turn a key into an AutoCAD
  layer, a Revit line style and, optionally, a Revit detail family type;
* items: ``line`` (a to b), ``circle`` (centre c, radius r), ``bar`` (a cut bar: centre c, diameter d,
  drawn filled), ``rect`` (corners a and b) and ``text`` (at, height h in mm on the view, left aligned).

Slab plans draw every bar at its spacing: the mesh across the slab, each added bar only in its zone.
Bars of a second or third layer in a zone are drawn 2Ø beside where they sit so they do not hide the
layer under them (the zone's text gives the layer). Anchorage beyond a zone is not drawn.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection
from typing import Any

from .design.export import pile_cages
from .project import DrawingSettings

FORMAT = "triton.drawings/1"
TEXT_MM = 2.5  # text height on paper


def _f(v: float | None, n: int = 2) -> str:
    return "" if v is None else f"{v:+.{n}f}"


def _mm(v: float) -> str:
    return f"{v:.0f}"


class View:
    def __init__(self, name: str, title: str, scale: int, element: str) -> None:
        self.name, self.title, self.scale, self.element = name, title, scale, element
        self.items: list[dict[str, Any]] = []

    def line(self, layer: str, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.items.append({"type": "line", "layer": layer, "a": _pt(a), "b": _pt(b)})

    def rect(self, layer: str, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.items.append({"type": "rect", "layer": layer, "a": _pt(a), "b": _pt(b)})

    def circle(self, layer: str, c: tuple[float, float], r: float) -> None:
        self.items.append({"type": "circle", "layer": layer, "c": _pt(c), "r": round(r, 1)})

    def bar(self, d: float, c: tuple[float, float]) -> None:
        self.items.append({"type": "bar", "layer": bar_key(d), "c": _pt(c), "d": d})

    def text(self, at: tuple[float, float], text: str, size: float = 1.0) -> None:
        h = round(TEXT_MM * size * self.scale, 1)
        self.items.append({"type": "text", "layer": "text", "at": _pt(at), "text": text, "h": h})

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "element": self.element,
            "scale": self.scale,
            "box": box(self.items),
            "items": self.items,
        }


def _pt(p: tuple[float, float]) -> list[float]:
    return [round(p[0], 1), round(p[1], 1)]


def bar_key(d: float) -> str:
    return f"bar-{int(round(d))}"


def box(items: list[dict[str, Any]]) -> list[float]:
    """[x0, y0, x1, y1] around the items (text counted by an estimate of its length)."""
    xs: list[float] = []
    ys: list[float] = []
    for it in items:
        t = it["type"]
        if t in ("line", "rect"):
            xs += [it["a"][0], it["b"][0]]
            ys += [it["a"][1], it["b"][1]]
        elif t in ("circle", "bar"):
            r = it.get("r", it.get("d", 0) / 2)
            xs += [it["c"][0] - r, it["c"][0] + r]
            ys += [it["c"][1] - r, it["c"][1] + r]
        elif t == "text":
            xs += [it["at"][0], it["at"][0] + 0.8 * it["h"] * len(it["text"])]
            ys += [it["at"][1], it["at"][1] + it["h"]]
    if not xs:
        return [0.0, 0.0, 0.0, 0.0]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


# --- piles -------------------------------------------------------------------------------------------


def _pile_views(p: dict[str, Any]) -> list[View]:
    el = p["element"] + (" infill" if p["part"] == "infill" else "")
    D, cover, link = p["diameter_mm"], p["cover_mm"], p["link_diameter_mm"]
    views = []
    for i, run in enumerate(p["runs"], 1):
        v = View(
            f"{el} - cage {i} section",
            f"{el}: cage {i}, {_f(run['top_m'])} to {_f(run['bottom_m'])}",
            20,
            p["element"],
        )
        v.circle("concrete", (0, 0), D / 2)
        v.circle(bar_key(link), (0, 0), D / 2 - cover - link / 2)
        for hoop in run["inner_link_hoops_mm"]:
            v.circle(bar_key(link), (0, 0), hoop / 2)
        for row in run["rows"]:
            n = row["count"]
            for k in range(n):
                a = math.radians(row["first_bar_angle_deg"] + 360.0 * k / n)
                v.bar(row["diameter_mm"], (row["radius_mm"] * math.cos(a), row["radius_mm"] * math.sin(a)))
        y = -D / 2 - 12 * v.scale
        v.text((-D / 2, D / 2 + 6 * v.scale), f"{el} cage {i}  1:{v.scale}", 1.4)
        v.text((-D / 2, y), f"Ø{_mm(D)} pile, {run['label']}")
        v.text((-D / 2, y - 5 * v.scale), f"Links Ø{_mm(link)}, cover {_mm(cover)}")
        v.text(
            (-D / 2, y - 10 * v.scale),
            f"Bars from {_f(run['top_m'])} to {_f(run['bottom_m'])} m, x axis = model X",
        )
        views.append(v)
    views.append(_pile_elevation(p, el))
    return views


def _pile_elevation(p: dict[str, Any], el: str) -> View:
    """Pile along its length at true levels (y = level in mm): the bars of each cage with their laps."""
    D = p["diameter_mm"]
    v = View(f"{el} - elevation", f"{el}: elevation", 50, p["element"])
    head, toe = p["head_level_m"], p["toe_level_m"]
    tops = [r["top_m"] for r in p["runs"]] + [head]
    top = max(t for t in tops if t is not None)
    bottom = toe if toe is not None else min(r["bottom_m"] for r in p["runs"])
    v.rect("concrete", (-D / 2, bottom * 1000), (D / 2, top * 1000))
    marks = {head: "head", bottom: "toe"}
    for j, run in enumerate(p["runs"]):
        shift = (j % 2) * 2 * max(r["diameter_mm"] for r in run["rows"])  # laps side by side
        for row in run["rows"]:
            x = row["radius_mm"] - shift
            for s in (-1, 1):
                v.line(
                    bar_key(row["diameter_mm"]),
                    (s * x, row["bar_bottom_m"] * 1000),
                    (s * x, row["bar_top_m"] * 1000),
                )
        mid = (run["top_m"] + run["bottom_m"]) / 2 * 1000
        v.text((D / 2 + 4 * v.scale, mid), f"Cage {j + 1}: {run['label']}")
        marks.setdefault(run["bottom_m"], "")
    for level, what in sorted(marks.items(), reverse=True):
        if level is None:
            continue
        y = level * 1000
        v.line("zones", (-D / 2 - 8 * v.scale, y), (-D / 2, y))
        v.text((-D / 2 - 30 * v.scale, y + v.scale), f"{_f(level)} {what}".strip(), 0.9)
    for j in p.get("construction_joints") or []:
        if j.get("level_m") is None:
            continue
        y = j["level_m"] * 1000
        v.line("zones", (-D / 2 - 4 * v.scale, y), (D / 2 + 4 * v.scale, y))
        for x in j["additional"]:
            r, half = x.get("radius_mm"), x.get("length_m", 0) * 500
            if r:
                for s in (-1, 1):
                    v.line(bar_key(x["diameter_mm"]), (s * r, y - half), (s * r, y + half))
        words = "; ".join(x["label"] for x in j["additional"]) or "no additional bars"
        v.text((D / 2 + 4 * v.scale, y + v.scale), f"Construction joint {_f(j['level_m'])}: {words}", 0.8)
    n = p.get("count") or 1
    highest = max([top] + [r["bar_top_m"] for run in p["runs"] for r in run["rows"]])
    v.text((-D / 2, highest * 1000 + 6 * v.scale), f"{el} elevation, {n} No., Ø{_mm(D)}  1:{v.scale}", 1.4)
    v.text((-D / 2, bottom * 1000 - 8 * v.scale), f"Links Ø{_mm(p['link_diameter_mm'])} (pitch per design)")
    return v


# --- beams -------------------------------------------------------------------------------------------


def _beam_views(b: dict[str, Any]) -> list[View]:
    W, H, cover = b["width_mm"], b["depth_mm"], b["cover_mm"] or 0
    v = View(f"{b['element']} - section", f"{b['element']}: section", 20, b["element"])
    v.rect("concrete", (-W / 2, -H / 2), (W / 2, H / 2))
    links = b.get("links") or {}
    phi = links.get("diameter_mm")
    if phi:
        e = cover + phi / 2
        v.rect(bar_key(phi), (-W / 2 + e, -H / 2 + e), (W / 2 - e, H / 2 - e))
    for bar in b["bars"]:
        v.bar(bar["diameter_mm"], (bar["y_mm"], bar["z_mm"]))
    y = -H / 2 - 12 * v.scale
    v.text((-W / 2, H / 2 + 6 * v.scale), f"{b['element']} section  1:{v.scale}", 1.4)
    v.text((-W / 2, y), f"{_mm(W)} x {_mm(H)}, {b.get('label') or ''}")
    if phi:
        v.text(
            (-W / 2, y - 5 * v.scale),
            f"Links Ø{_mm(phi)}, {links.get('legs')} legs @ {_mm(links.get('spacing_mm') or 0)}",
        )
    tr = [
        f"{face} Ø{t['diameter_mm']} @ {_mm(t['spacing_mm'] or 0)}"
        + (f" in {t['layers']} layers" if t.get("layers", 1) > 1 else "")
        for face, t in (b.get("transverse") or {}).items()
        if t.get("diameter_mm")
    ]
    if tr:
        v.text((-W / 2, y - 10 * v.scale), "Transverse bars: " + ", ".join(tr))
    along = b.get("along") or ""
    v.text(
        (-W / 2, y - 15 * v.scale),
        f"Along {along} from {_f(b.get('start_m'))} to {_f(b.get('end_m'))} m, top at {_f(b.get('level_m'))}",
    )
    k = 0
    for j in b.get("construction_joints") or []:
        h = j.get("height_above_soffit_mm")
        if h is not None and 0 < h < H:
            v.line("zones", (-W / 2 - 4 * v.scale, -H / 2 + h), (W / 2 + 4 * v.scale, -H / 2 + h))
        for words in [x["label"] for x in j["additional"]] or ["no additional bars"]:
            v.text((-W / 2, y - (20 + 5 * k) * v.scale), f"Construction joint, {j['where']}: {words}", 0.8)
            k += 1
    return [v]


# --- slabs -------------------------------------------------------------------------------------------


def _grid(lo: float, hi: float, spacing: float, offset: float) -> list[float]:
    """Bar positions k*s + offset (mm, from the slab edge ``lo``) that fall inside [lo, hi]."""
    if spacing <= 0:
        return []
    out = []
    k = math.ceil((lo - offset) / spacing - 1e-9)
    while k * spacing + offset <= hi + 1e-6:
        out.append(k * spacing + offset)
        k += 1
    return out


def _slab_views(d: dict[str, Any]) -> list[View]:
    bx = d.get("box_m") or {}
    if not bx.get("X") or not bx.get("Y"):
        return []
    X0, X1 = (v * 1000 for v in bx["X"])
    Y0, Y1 = (v * 1000 for v in bx["Y"])
    views = []
    for f in d["faces"]:
        along = f["bars_along"]  # the direction the bars run
        s_mesh = f["mesh"].get("spacing_mm") or 150.0
        name = f"{d['element']} - {f['face']} bars along {along}"
        v = View(name, f"{d['element']}: {f['face']} face, bars along {along}", 100, d["element"])
        v.rect("concrete", (X0, Y0), (X1, Y1))
        # Bars along X sit at Y positions (and the other way round), the first half a spacing in.
        lo, hi = (Y0, Y1) if along == "X" else (X0, X1)
        a0, a1 = (X0, X1) if along == "X" else (Y0, Y1)

        def draw(
            phi: float,
            spacing: float,
            offset: float,
            span: tuple[float, float],
            across: tuple[float, float],
            v: View = v,
            lo: float = lo,
            along: str = along,
        ) -> None:
            for c in _grid(across[0], across[1], spacing, lo + offset):
                p, q = (span[0], c), (span[1], c)
                v.line(bar_key(phi), p if along == "X" else p[::-1], q if along == "X" else q[::-1])

        mesh_text = []
        for lay in f["mesh"]["layers"]:
            for b in lay["bars"]:
                draw(
                    b["diameter_mm"],
                    b["spacing_mm"],
                    s_mesh / 2 + (0 if b["kind"] == "mesh" else s_mesh / 2),
                    (a0, a1),
                    (lo, hi),
                )
            mesh_text.append(f"L{lay['layer']} {lay['text']}")
        for z in f["zones"]:
            zx0, zx1 = (x * 1000 for x in z["x_m"])
            zy0, zy1 = (y * 1000 for y in z["y_m"])
            v.rect("zones", (zx0, zy0), (zx1, zy1))
            span = (zx0, zx1) if along == "X" else (zy0, zy1)
            across = (zy0, zy1) if along == "X" else (zx0, zx1)
            parts = []
            for lay in z["layers"]:
                for b in lay["bars"]:
                    if lay["layer"] == 1 and b["kind"] == "mesh":
                        continue  # drawn with the mesh
                    off = s_mesh / 2 + (0 if b["kind"] == "mesh" else s_mesh / 2)
                    if lay["layer"] > 1:
                        off += 2 * b["diameter_mm"]  # beside the layer under it, to be seen
                    draw(b["diameter_mm"], b["spacing_mm"], off, span, across)
                    parts.append(f"L{lay['layer']} Ø{b['diameter_mm']} @ {_mm(b['spacing_mm'])}")
            if parts:
                v.text((zx0 + 2 * v.scale, zy0 + 2 * v.scale), " + ".join(parts), 0.8)
        for j in d.get("construction_joints") or []:
            ln = j.get("line") or {}
            if ln.get("along") != along or ln.get("at_m") is None or not ln.get("range_m"):
                continue
            c = ln["at_m"] * 1000
            r0, r1 = (t * 1000 for t in ln["range_m"])
            p, q = (c, r0), (c, r1)
            v.line("zones", p if along == "X" else p[::-1], q if along == "X" else q[::-1])
            for x in j["additional"]:
                half = x.get("length_m", 0) * 500
                s0 = (x["from_m"] if x.get("from_m") is not None else ln["range_m"][0]) * 1000
                s1 = (x["to_m"] if x.get("to_m") is not None else ln["range_m"][1]) * 1000
                draw(x["diameter_mm"], x["spacing_mm"], x["spacing_mm"] / 4, (c - half, c + half), (s0, s1))
            words = "; ".join(x["label"] for x in j["additional"]) or "no additional bars"
            at = (c + 2 * v.scale, r0 + 2 * v.scale) if along == "X" else (r0 + 2 * v.scale, c + 2 * v.scale)
            v.text(at, f"Construction joint: {words}", 0.8)
        v.text(
            (X0, Y1 + 12 * v.scale), f"{d['element']} {f['face']} face, bars along {along}  1:{v.scale}", 1.4
        )
        v.text(
            (X0, Y1 + 6 * v.scale), "Mesh: " + ", ".join(mesh_text) + f", cover {_mm(f.get('cover_mm') or 0)}"
        )
        v.text(
            (X0, Y0 - 8 * v.scale),
            "Model X to the right, Y up. Added bars in each zone; anchorage beyond the zone not drawn.",
        )
        views.append(v)
    views += _slab_cuts(d)
    if d.get("links"):
        v = View(f"{d['element']} - shear links", f"{d['element']}: shear link zones", 100, d["element"])
        v.rect("concrete", (X0, Y0), (X1, Y1))
        for z in d["links"]:
            zx0, zx1 = (x * 1000 for x in z["x_m"])
            zy0, zy1 = (y * 1000 for y in z["y_m"])
            v.rect(bar_key(z["diameter_mm"] or 10), (zx0, zy0), (zx1, zy1))
            v.text(
                ((zx0 + zx1) / 2 - 20 * v.scale, (zy0 + zy1) / 2),
                f"Links Ø{z['diameter_mm']} @ {_mm(z['sx_mm'] or 0)} x {_mm(z['sy_mm'] or 0)}",
            )
        v.text((X0, Y1 + 6 * v.scale), f"{d['element']} shear links  1:{v.scale}", 1.4)
        views.append(v)
    return views


def _slab_cuts(d: dict[str, Any]) -> list[View]:
    """A 1 m cut of the basic mesh, across X and across Y: the bars cut as dots, the others as lines."""
    h = d.get("thickness_mm") or 0
    views = []
    for cut in ("X", "Y"):
        v = View(
            f"{d['element']} - mesh cut across {cut}",
            f"{d['element']}: 1 m cut across {cut}, basic mesh",
            20,
            d["element"],
        )
        v.rect("concrete", (0, 0), (1000, h))
        labels: list[tuple[str, str]] = []
        for f in d["faces"]:
            s = f["mesh"].get("spacing_mm") or 150.0
            for lay in f["mesh"]["layers"]:
                z = lay.get("above_soffit_mm")
                if z is None:
                    continue
                for b in lay["bars"]:
                    if f["bars_along"] == cut:  # runs through the cut: dots
                        off = s / 2 + (0 if b["kind"] == "mesh" else s / 2)
                        for c in _grid(0, 1000, b["spacing_mm"], off):
                            v.bar(b["diameter_mm"], (c, z))
                    else:
                        v.line(bar_key(b["diameter_mm"]), (0, z), (1000, z))
                labels.append((f["face"], f"{f['face']} {f['bars_along']}: {lay['text']}"))
        for face, step in (("top", -1), ("bottom", 1)):
            rows = [t for fc, t in labels if fc == face]
            z = h - 3 * v.scale if face == "top" else 0
            for k, t in enumerate(rows if face == "top" else rows[::-1]):
                v.text((1000 + 5 * v.scale, z + step * 4 * v.scale * k), t, 0.8)
        other = "Y" if cut == "X" else "X"
        v.text((0, h + 6 * v.scale), f"{d['element']} 1 m cut across {cut}  1:{v.scale}", 1.4)
        v.text(
            (0, -8 * v.scale),
            f"{_mm(h)} thick; horizontal = model {other}. Added bars in zones: see the plans.",
        )
        views.append(v)
    return views


# --- the file ----------------------------------------------------------------------------------------


def layer_names(settings: DrawingSettings, keys: set[str]) -> dict[str, dict[str, str]]:
    """What each layer key is called in AutoCAD and Revit."""
    by_d = {b.diameter: b for b in settings.bars}
    out: dict[str, dict[str, str]] = {}
    for k in sorted(keys):
        if k.startswith("bar-"):
            d = int(k[4:])
            b = by_d.get(d)
            out[k] = {
                "diameter_mm": d,
                "cad_layer": (b.cad_layer if b else "") or f"REBAR-{d}",
                "revit_line_style": (b.revit_line_style if b else "") or f"REBAR-{d}",
                "revit_section_type": b.revit_section_type if b else "",
                "revit_line_type": b.revit_line_type if b else "",
            }
        elif k == "concrete":
            out[k] = {
                "cad_layer": settings.concrete_cad_layer,
                "revit_line_style": settings.concrete_revit_line_style,
            }
        elif k == "zones":
            out[k] = {
                "cad_layer": settings.zones_cad_layer,
                "revit_line_style": settings.zones_revit_line_style,
            }
        elif k == "text":
            out[k] = {"cad_layer": settings.text_cad_layer, "revit_text_type": settings.revit_text_type}
    return out


def drawings(
    project_name: str,
    results: dict[str, Any],
    settings: DrawingSettings,
    section: str = "",
    element: str | Collection[str] | None = None,
) -> dict[str, Any]:
    """Every drawing of a designed section (or of the elements named), with the names to draw them on."""
    return from_cages(pile_cages(project_name, results, section), settings, element)


def from_cages(
    data: dict[str, Any], settings: DrawingSettings, element: str | Collection[str] | None = None
) -> dict[str, Any]:
    """The drawings of the bars in a Revit bar file (``design.export.pile_cages``); ``element``: only
    these elements (one name or several; empty: all)."""
    views: list[View] = []
    for p in data["piles"]:
        views += _pile_views(p)
    for b in data["beams"]:
        views += _beam_views(b)
    for d in data["slabs"]:
        views += _slab_views(d)
    if element:
        wanted = {element} if isinstance(element, str) else set(element)
        views = [v for v in views if v.element in wanted]
    out = [v.as_dict() for v in views]
    keys = {it["layer"] for v in out for it in v["items"]}
    return {
        "format": FORMAT,
        "project": data.get("project"),
        "section": data.get("section"),
        "run_at": data.get("run_at"),
        "units": "mm",
        "view_prefix": settings.revit_view_prefix,
        "layers": layer_names(settings, keys),
        "views": out,
    }


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", text).strip() or "drawing"
