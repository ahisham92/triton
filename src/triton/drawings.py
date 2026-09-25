"""Reinforcement drawings as plain 2D detail items, for AutoCAD (DXF) and Revit (a script draws them).

Triton works out every line here, so the AutoCAD file and the Revit code only copy it:

* a view is one drawing (a pile sheet: elevation and cage sections, a beam section, a slab plan of one face,
  a slab section at a pile); its items are in mm in the view's own plane. Every view is framed, with a
  caption under the frame saying what it holds and a base point (a circled cross) to copy it from;
  ``at`` places it in the one drafting view (or DXF model space) that holds a whole run, a row per
  element;
* each item sits on a layer key: ``bar-<Ø>`` for bars and links, ``concrete``, ``zones`` (zone outlines,
  frames, base points and level marks), ``dims`` or ``text``; the project's drawing names (Project tab)
  turn a key into an AutoCAD layer, a Revit line style and, for cut bars, a Revit detail family;
* items: ``line`` (a to b), ``circle`` (centre c, radius r), ``bar`` (a cut bar: centre c, diameter d),
  ``rect`` (corners a and b), ``text`` (at, height h in mm on the view, left aligned), ``dim`` (a
  dimension from a to b, its line ``off`` mm to the left of a->b) and ``family`` (an office detail
  family placed at ``at`` with its ``params`` set). ``dim`` and ``family`` carry ``fallback``: the
  same thing as plain items, drawn by AutoCAD, and by Revit when the family or dimension cannot be made.

Slab plans are one per face (bottom, top), as the office draws them: the mesh is in the caption only
(drawn by hand to suit the plan), the additional bars of each zone are the office's RFT_ADD family (bars
along X and along Y) with L, spacing, diameter and distribution length set.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection
from typing import Any

from .design.export import pile_cages
from .design.slabs import layer_name
from .project import OFFICE_LINE_STYLE, DrawingSettings

FORMAT = "triton.drawings/2"
TEXT_MM = 2.5  # text height on paper
GAP_MM = 3000.0  # between views in the drafting view
ROW_WIDTH_MM = 120_000.0  # start a new row of views past this width
CLEAR_OVER_PILE = 25.0  # mm, bottom bars over a pile head that sits in the slab
CRANK_SLOPE = 6.0  # cranked bars rise 1 in this
BAR_PARAM = "DAR_BAR DIAMETER|Bar Diameter|Diameter"  # the cut bar family's size


def _f(v: float | None, n: int = 2) -> str:
    return "" if v is None else f"{v:+.{n}f}"


def _mm(v: float) -> str:
    return f"{v:.0f}"


def P(
    names: str, *, mm: float | None = None, n: int | None = None, text: str | None = None
) -> dict[str, Any]:
    """A family parameter to set: the first of ``names`` ("A|B") the family has, to a length (mm), a
    count or a text."""
    out: dict[str, Any] = {"names": names}
    if mm is not None:
        out["mm"] = round(mm, 1)
    if n is not None:
        out["n"] = int(n)
    if text is not None:
        out["text"] = text
    return out


class View:
    def __init__(
        self,
        name: str,
        title: str,
        scale: int,
        element: str,
        base: tuple[float, float] | None = (0.0, 0.0),
        base_text: str = "",
    ) -> None:
        self.name, self.heading, self.scale, self.element = name, title, scale, element
        self.items: list[dict[str, Any]] = []
        self.caption: list[str] = []
        self.base, self.base_text = base, base_text
        self._done = False

    def sub(self) -> View:
        """A scratch view at the same scale, for a family's fallback items."""
        return View(self.name, "", self.scale, self.element, None)

    def line(self, layer: str, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.items.append({"type": "line", "layer": layer, "a": _pt(a), "b": _pt(b)})

    def rect(self, layer: str, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.items.append({"type": "rect", "layer": layer, "a": _pt(a), "b": _pt(b)})

    def circle(self, layer: str, c: tuple[float, float], r: float) -> None:
        self.items.append({"type": "circle", "layer": layer, "c": _pt(c), "r": round(r, 1)})

    def bar(self, d: float, c: tuple[float, float]) -> None:
        self.items.append({"type": "bar", "layer": bar_key(d), "c": _pt(c), "d": d})

    def text(self, at: tuple[float, float], text: str, size: float = 1.0, rot: float = 0.0) -> None:
        """Text from ``at`` (its bottom left), ``rot`` degrees anticlockwise (90: reading upward)."""
        h = round(TEXT_MM * size * self.scale, 1)
        it = {"type": "text", "layer": "text", "at": _pt(at), "text": text, "h": h}
        if rot:
            it["rot"] = rot
        self.items.append(it)

    def title(self, at: tuple[float, float], words: str, scale: int | None = None) -> None:
        """An office title: the words underlined, "SCALE: 1 : n" under the line."""
        s = self.scale
        self.text(at, words, 1.6)
        length = 0.8 * TEXT_MM * 1.6 * s * len(words)
        self.line("text", (at[0], at[1] - 0.8 * s), (at[0] + length, at[1] - 0.8 * s))
        self.text((at[0], at[1] - 3.2 * s), f"SCALE: 1 : {scale or s}", 0.7)

    def leader(
        self, tip: tuple[float, float], at: tuple[float, float], words: str, size: float = 0.9
    ) -> None:
        """A label at ``at`` (its bottom left) with a line under it and on to ``tip``, what it names."""
        s = self.scale
        width = 0.8 * TEXT_MM * size * s * len(words)
        y = at[1] - 0.4 * s
        near = (at[0] + width, y) if tip[0] > at[0] + width / 2 else (at[0], y)
        self.line("text", (at[0], y), (at[0] + width, y))
        self.line("text", near, tip)
        self.text(at, words, size)

    def merge(self, other: View, dx: float, dy: float) -> None:
        """Another drawing's items, moved by (dx, dy), into this one."""
        self.items += _moved(other.items, dx, dy)

    def family(
        self,
        family: str,
        at: tuple[float, float],
        params: list[dict[str, Any]],
        fallback: View,
        align: str = "origin",
        rot: float = 0.0,
    ) -> None:
        """An office detail family at ``at`` (its origin there, or with align "center" its extents centred
        there), ``params`` set; ``fallback`` holds the same as plain items. No family named: the items."""
        if not family:
            self.items += fallback.items
            return
        self.items.append(
            {
                "type": "family",
                "layer": "zones",
                "family": family,
                "at": _pt(at),
                "rot": rot,
                "align": align,
                "params": params,
                "fallback": fallback.items,
            }
        )

    def dim(self, a: tuple[float, float], b: tuple[float, float], off: float, text: str = "") -> None:
        """A linear dimension from a to b, its line ``off`` mm to the left of a->b (negative: right)."""
        length = math.dist(a, b)
        if length < 1.0:
            return
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        nx, ny = -uy, ux
        a2 = (a[0] + nx * off, a[1] + ny * off)
        b2 = (b[0] + nx * off, b[1] + ny * off)
        fb = self.sub()
        sgn = 1 if off >= 0 else -1
        gap, ext = 1.0 * self.scale * sgn, 1.5 * self.scale * sgn
        for p, q in ((a, a2), (b, b2)):
            fb.line("dims", (p[0] + nx * gap, p[1] + ny * gap), (q[0] + nx * ext, q[1] + ny * ext))
        fb.line("dims", a2, b2)
        t = 1.0 * self.scale  # 45° ticks
        for q in (a2, b2):
            fb.line(
                "dims",
                (q[0] - t * (ux + nx), q[1] - t * (uy + ny)),
                (q[0] + t * (ux + nx), q[1] + t * (uy + ny)),
            )
        words = text or _mm(length)
        h = TEXT_MM * 0.9 * self.scale
        mx, my = (a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2
        if abs(uy) > abs(ux):  # upright: the text beside the line, away from the drawing
            outward = nx * sgn
            at = (
                (mx + 0.8 * self.scale, my)
                if outward > 0
                else (mx - 0.8 * self.scale - 0.8 * h * len(words), my)
            )
        else:
            at = (mx - 0.4 * h * len(words), my + 0.6 * self.scale)
        fb.text(at, words, 0.9)
        self.items.append(
            {
                "type": "dim",
                "layer": "dims",
                "a": _pt(a),
                "b": _pt(b),
                "off": round(off, 1),
                "text": words,
                "fallback": fb.items,
            }
        )

    def finish(self) -> None:
        """The base point, the frame round the drawing and the caption under it."""
        if self._done:
            return
        self._done = True
        s = self.scale
        if self.base is not None:
            bx, by = self.base
            r = 1.5 * s
            self.circle("zones", (bx, by), r)
            self.line("zones", (bx - 2 * r, by), (bx + 2 * r, by))
            self.line("zones", (bx, by - 2 * r), (bx, by + 2 * r))
            self.text((bx + 2 * r, by + r), "BP", 0.8)
        x0, y0, x1, y1 = box(self.items)
        m = 5 * s
        x0, y0, x1, y1 = x0 - m, y0 - m, x1 + m, y1 + m
        self.rect("zones", (x0, y0), (x1, y1))
        self.title((x0, y0 - 5 * s), self.heading.upper().replace(": ", " - "))
        lines = [(c, 0.9) for c in self.caption]
        if self.base is not None:
            lines.append(
                (f"BP = base point to copy from: {self.base_text or 'the origin of this drawing'}", 0.8)
            )
        y = y0 - 11 * s
        for words, size in lines:
            y -= TEXT_MM * size * s
            self.text((x0, y), words, size)
            y -= 0.8 * TEXT_MM * s

    def as_dict(self) -> dict[str, Any]:
        self.finish()
        return {
            "name": self.name,
            "title": self.heading,
            "element": self.element,
            "scale": self.scale,
            "base": _pt(self.base) if self.base is not None else None,
            "caption": self.caption,
            "box": box(self.items),
            "items": self.items,
        }


def _moved(items: list[dict[str, Any]], dx: float, dy: float) -> list[dict[str, Any]]:
    out = []
    for it in items:
        it = dict(it)
        for k in ("a", "b", "c", "at"):
            if k in it:
                it[k] = [round(it[k][0] + dx, 1), round(it[k][1] + dy, 1)]
        if "fallback" in it:
            it["fallback"] = _moved(it["fallback"], dx, dy)
        out.append(it)
    return out


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
        if t in ("family", "dim"):
            b = box(it["fallback"])
            if b != [0.0, 0.0, 0.0, 0.0]:
                xs += [b[0], b[2]]
                ys += [b[1], b[3]]
        elif t in ("line", "rect"):
            xs += [it["a"][0], it["b"][0]]
            ys += [it["a"][1], it["b"][1]]
        elif t in ("circle", "bar"):
            r = it.get("r", it.get("d", 0) / 2)
            xs += [it["c"][0] - r, it["c"][0] + r]
            ys += [it["c"][1] - r, it["c"][1] + r]
        elif t == "text":
            w = 0.8 * it["h"] * len(it["text"])
            if abs(it.get("rot", 0) - 90) < 1:  # reading upward
                xs += [it["at"][0] - it["h"], it["at"][0]]
                ys += [it["at"][1], it["at"][1] + w]
            else:
                xs += [it["at"][0], it["at"][0] + w]
                ys += [it["at"][1], it["at"][1] + it["h"]]
    if not xs:
        return [0.0, 0.0, 0.0, 0.0]
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def layout(views: list[dict[str, Any]]) -> None:
    """Where each view goes in the one drawing of a run (``at``: added to its points, mm): side by
    side, a new row for each element and past ROW_WIDTH_MM."""
    x = y = row_h = 0.0
    last = None
    for v in views:
        x0, y0, x1, y1 = v["box"]
        w, h = x1 - x0, y1 - y0
        if x > 0 and (x + w > ROW_WIDTH_MM or v["element"] != last):
            x, y, row_h = 0.0, y - row_h - 2 * GAP_MM, 0.0
        v["at"] = [round(x - x0, 1), round(y - y1, 1)]
        x += w + GAP_MM
        row_h = max(row_h, h)
        last = v["element"]


# --- piles -------------------------------------------------------------------------------------------


def _letter(i: int) -> str:
    return chr(ord("A") + i) if i < 26 else f"A{i - 25}"


def _links_in(p: dict[str, Any], top: float | None, bottom: float | None) -> dict[str, Any] | None:
    """The tightest link zone over a cage's length (from top to bottom, m)."""
    zones = [
        z
        for z in p.get("link_zones") or []
        if z.get("spacing_mm")
        and (top is None or z["bottom_m"] is None or z["bottom_m"] < top)
        and (bottom is None or z["top_m"] is None or z["top_m"] > bottom)
    ]
    return min(zones, key=lambda z: z["spacing_mm"]) if zones else None


def _ring_label(rows: list[dict[str, Any]]) -> str:
    """The office's bar label: "26ø32+26ø25"."""
    return "+".join(f"{r['count']}ø{r['diameter_mm']}" for r in rows)


def _spiral_label(link: float, pitch: float | None) -> str:
    return f"SPIRAL ø{_mm(link)}" + (f" PITCH {_mm(pitch)}" if pitch else "")


def _pile_section(
    v: View, p: dict[str, Any], run: dict[str, Any], c: tuple[float, float], st: DrawingSettings
) -> None:
    """A cage's section centred at ``c``: the office Round Col family (pile, spiral, up to four rings of
    bars and its own labels), inner hoops and any further rings as items, the labels by leaders."""
    D, cover, link = p["diameter_mm"], p["cover_mm"], p["link_diameter_mm"]
    cx, cy = c
    s = v.scale
    z = _links_in(p, run["top_m"], run["bottom_m"])
    pitch = z["spacing_mm"] if z else None
    rows = run["rows"]
    r_link = D / 2 - cover - link / 2

    def ring(w: View, row: dict[str, Any]) -> None:
        n = row["count"]
        for k in range(n):
            a = math.radians(row["first_bar_angle_deg"] + 360.0 * k / n)
            w.bar(
                row["diameter_mm"], (cx + row["radius_mm"] * math.cos(a), cy + row["radius_mm"] * math.sin(a))
            )

    in_family = rows[:4]
    fb = v.sub()
    fb.circle("concrete", c, D / 2)
    fb.circle(bar_key(link), c, r_link)
    for row in in_family:
        ring(fb, row)
    # The family writes these itself; drawn with lines, leaders say them.
    a = math.radians(135)
    fb.leader(
        (cx + r_link * math.cos(a), cy + r_link * math.sin(a)),
        (cx - D / 2 - 2 * s, cy + D / 2 + 3 * s),
        _spiral_label(link, pitch),
        0.8,
    )
    if rows:
        r0 = rows[0]["radius_mm"]
        a = math.radians(20)
        fb.leader(
            (cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
            (cx + D / 2 + 3 * s, cy + D / 2 * 0.55),
            _ring_label(in_family),
            0.8,
        )
    a = math.radians(25)
    diam = fb.sub()  # the diameter across, drawn as lines under the family's own
    diam.dim(
        (cx - D / 2 * math.cos(a), cy - D / 2 * math.sin(a)),
        (cx + D / 2 * math.cos(a), cy + D / 2 * math.sin(a)),
        0.0,
        f"ø {_mm(D)}",
    )
    fb.items += diam.items[0]["fallback"] if diam.items else []
    if rows:
        r0 = rows[0]
        params = [
            P("DAR_WIDTH|DAR_DIAMETER|Diameter", mm=D),
            P("DAR_COVER", mm=cover),
            P("DAR_NO OF BARS", n=r0["count"]),
            P("DAR_BAR DIAMETER", mm=r0["diameter_mm"]),
            P("DAR_STIRRUP DIAMETER", mm=link),
            P("DAR_MAIN RFT", text=_ring_label(in_family)),
            P("DAR_STIRRUPS RFT", text=_spiral_label(link, pitch)),
        ]
        if pitch:
            params.append(P("DAR_STIRRUPS SPACING", mm=pitch))
        for k in (2, 3, 4):
            row = rows[k - 1] if len(rows) >= k else None
            # LAYER<k>: the ring's bar count (or, if the family has it as Yes/No, whether it is there).
            params.append(P(f"LAYER{k}", n=row["count"] if row else 0))
            if row:
                params.append(P(f"Layer{k}_BarDiameter", mm=row["diameter_mm"]))
        v.family(st.pile_section_family, c, params, fb)
    else:
        v.items += fb.items
    for hoop in run["inner_link_hoops_mm"]:
        v.circle(bar_key(link), c, hoop / 2)
    for row in rows[4:]:
        ring(v, row)
    if len(rows) > 4:
        r = rows[4]["radius_mm"]
        v.leader((cx + r, cy), (cx + D / 2 + 3 * s, cy - D / 4), _ring_label(rows[4:]) + " (INNER)", 0.8)


def _pile_views(p: dict[str, Any], st: DrawingSettings) -> list[View]:
    """One sheet per pile, as the office draws it: the elevation at true levels (the cap, the bars of each
    cage with their laps and lengths, the spiral at its pitch, section marks and levels) and, to its
    right at the level of its mark, the section of each cage."""
    el = p["element"] + (" infill" if p["part"] == "infill" else "")
    EL = el.upper()
    code = re.sub(r"[^A-Z0-9]+", "", EL)[:6] or "P"
    D, cover, link = p["diameter_mm"], p["cover_mm"], p["link_diameter_mm"]
    head, toe = p["head_level_m"], p["toe_level_m"]
    runs = p["runs"]
    bar_top = max((r["bar_top_m"] for run in runs for r in run["rows"]), default=head)
    tops = [r["top_m"] for r in runs] + [head, bar_top]
    top = max(t for t in tops if t is not None)
    bottom = toe if toe is not None else min(r["bottom_m"] for r in runs)
    y_head = (head if head is not None else top) * 1000
    v = View(
        f"{el} - pile sheet",
        f"{el} - reinforcement details",
        50,
        p["element"],
        (0.0, y_head),
        f"the pile's axis at its head ({_f(head if head is not None else top)})",
    )
    s = v.scale
    # The cap the pile is built into, broken off at the sides.
    soffit = y_head - st.pile_into_slab
    cap_top = max(bar_top * 1000 + 150, soffit + 1500)
    w = D / 2 + 900
    v.line("concrete", (-w, soffit), (-D / 2, soffit))
    v.line("concrete", (D / 2, soffit), (w, soffit))
    v.line("concrete", (-w, cap_top), (w, cap_top))
    for sx in (-1, 1):  # break lines
        x = sx * w
        ym = (soffit + cap_top) / 2
        v.line("concrete", (x, soffit), (x, ym - 150))
        v.line("concrete", (x, ym - 150), (x + sx * 120, ym - 50))
        v.line("concrete", (x + sx * 120, ym - 50), (x - sx * 120, ym + 50))
        v.line("concrete", (x - sx * 120, ym + 50), (x, ym + 150))
        v.line("concrete", (x, ym + 150), (x, cap_top))
    v.text((-w, cap_top + 1.0 * s), "CAPPING BEAM / DECK (SEE ITS SECTION)", 0.7)
    # The pile.
    v.line("concrete", (-D / 2, bottom * 1000), (-D / 2, soffit))
    v.line("concrete", (D / 2, bottom * 1000), (D / 2, soffit))
    v.line("concrete", (-D / 2, bottom * 1000), (D / 2, bottom * 1000))
    # Bars as seen: every bar of a ring projected on the plane of the drawing; alternate cages set in by
    # two bar sizes so the laps show side by side.
    marks_x = D / 2 + 4 * s
    for j, run in enumerate(runs):
        shift = (j % 2) * 2 * max((r["diameter_mm"] for r in run["rows"]), default=0)
        for row in run["rows"]:
            xs = sorted(
                {
                    round(
                        (row["radius_mm"] - shift)
                        * math.cos(math.radians(row["first_bar_angle_deg"] + 360.0 * i / row["count"]))
                    )
                    for i in range(row["count"])
                }
            )
            for x in xs:
                v.line(
                    bar_key(row["diameter_mm"]), (x, row["bar_bottom_m"] * 1000), (x, row["bar_top_m"] * 1000)
                )
        # The run of each size of bar, as the office marks it: a line over its length, "26ø32 L= 8000".
        by_d: dict[float, list[dict[str, Any]]] = {}
        for row in run["rows"]:
            by_d.setdefault(row["diameter_mm"], []).append(row)
        for k, (d, rs) in enumerate(sorted(by_d.items(), reverse=True)):
            x = marks_x + ((j % 2) * len(by_d) + k) * 7 * s
            y0, y1 = min(r["bar_bottom_m"] for r in rs) * 1000, max(r["bar_top_m"] for r in rs) * 1000
            v.line(bar_key(d), (x, y0), (x, y1))
            n = sum(r["count"] for r in rs)
            length = max(r["bar_length_m"] for r in rs) * 1000
            v.text((x - 0.4 * s, (y0 + y1) / 2 - 8 * s), f"{n}ø{_mm(d)}", 0.7, 90)
            v.text((x + 2.2 * s, (y0 + y1) / 2 - 8 * s), f"L= {_mm(length)}", 0.7, 90)
    # The spiral: a zigzag across the cage at the pitch of each link zone.
    r = D / 2 - cover - link / 2
    lines = 0
    for zn in p.get("link_zones") or []:
        sp, zt, zb = zn.get("spacing_mm"), zn.get("top_m"), zn.get("bottom_m")
        if not sp or zt is None or zb is None or zt <= zb:
            continue
        y, yb = zt * 1000, zb * 1000
        while y - sp / 2 >= yb - 1e-6 and lines < 4000:
            v.line(bar_key(link), (-r, y), (r, y - sp / 2))
            v.line(bar_key(link), (r, y - sp / 2), (-r, y - sp))
            y -= sp
            lines += 2
        v.leader(
            (-r, (zt + zb) / 2 * 1000),
            (-D / 2 - 45 * s, (zt + zb) / 2 * 1000 - 6 * s),
            _spiral_label(link, sp),
            0.7,
        )
    # Levels on the left.
    levels = {head: "PILE HEAD", bottom: "PILE TOE"}
    for j in p.get("construction_joints") or []:
        if j.get("level_m") is not None:
            levels.setdefault(j["level_m"], "CONSTRUCTION JOINT")
    for level, what in levels.items():
        if level is None:
            continue
        y = level * 1000
        x = -D / 2 - 3 * s
        v.line("zones", (x - 14 * s, y), (-D / 2, y))
        v.line("zones", (x, y), (x - 0.8 * s, y + 1.2 * s))
        v.line("zones", (x - 0.8 * s, y + 1.2 * s), (x + 0.8 * s, y + 1.2 * s))
        v.line("zones", (x + 0.8 * s, y + 1.2 * s), (x, y))
        v.text((x - 14 * s, y + 0.5 * s), f"{level:+.2f}", 0.8)
        v.text((x - 14 * s, y - 1.8 * s), what, 0.6)
    for j in p.get("construction_joints") or []:
        if j.get("level_m") is None:
            continue
        y = j["level_m"] * 1000
        v.line("zones", (-D / 2, y), (D / 2, y))
        for x in j["additional"]:
            rr, half = x.get("radius_mm"), x.get("length_m", 0) * 500
            if rr:
                for sx in (-1, 1):
                    v.line(bar_key(x["diameter_mm"]), (sx * rr, y - half), (sx * rr, y + half))
        words = "; ".join(x["label"] for x in j["additional"]) or "no additional bars"
        v.text((-D / 2 - 17 * s, y - 4 * s), f"CONSTRUCTION JOINT {j['level_m']:+.2f}: {words}", 0.6)
    # Sections to the right, each at the level of its mark (moved down where they would meet).
    x_sec = (
        marks_x
        + 2 * max(len({r["diameter_mm"] for r in run["rows"]}) for run in runs) * 7 * s
        + 20 * s
        + D / 2
    )
    x_sec = max(x_sec, D / 2 + 30 * s + D / 2)
    room = D + 22 * s
    last = None
    for j, run in enumerate(runs):
        cut = _letter(j)
        y_mark = min(run["top_m"] * 1000 - 0.25 * (run["top_m"] - run["bottom_m"]) * 1000, soffit - 3 * s)
        y_c = y_mark if last is None else min(y_mark, last - room)
        last = y_c
        # The mark: its name on the left, a line across the pile, a thick end on the right.
        x0 = -D / 2 - 18 * s
        v.line("zones", (x0, y_mark), (marks_x - 1 * s, y_mark))
        v.rect("zones", (marks_x - 1 * s, y_mark - 0.4 * s), (marks_x + 2 * s, y_mark + 0.4 * s))
        v.line("zones", (x0 + 6 * s, y_mark), (x0 + 6 * s, y_mark + 2.5 * s))
        v.line("zones", (x0 + 5.3 * s, y_mark + 1.5 * s), (x0 + 6 * s, y_mark + 2.5 * s))
        v.line("zones", (x0 + 6.7 * s, y_mark + 1.5 * s), (x0 + 6 * s, y_mark + 2.5 * s))
        v.text((x0, y_mark + 0.5 * s), f"{code}-{cut}", 1.0)
        _pile_section(v, p, run, (x_sec, y_c), st)
        v.title((x_sec - D / 2 - 6 * s, y_c - D / 2 - 6 * s), f"{EL} - SECTION {cut}")
        v.text(
            (x_sec - D / 2 - 6 * s, y_c - D / 2 - 11 * s),
            f"{run['label']}; BARS {_f(run['top_m'])} TO {_f(run['bottom_m'])}",
            0.6,
        )
    # Lengths: the pile on the left, each cage on the right of the marks.
    v.dim((-D / 2, y_head), (-D / 2, bottom * 1000), -20 * s)
    v.dim((-D / 2, bottom * 1000), (D / 2, bottom * 1000), -4 * s, f"ø {_mm(D)}")
    v.title((-D / 2 - 18 * s, bottom * 1000 - 10 * s), f"{EL} - ELEVATION")
    n = p.get("count") or 1
    v.caption += [
        f"{n} No. ø{_mm(D)} piles, head {_f(head)}, toe {_f(toe)}, cover {_mm(cover)}",
        "Bars drawn at their levels, laps side by side; sections are at their marks on the elevation",
    ]
    return [v]


# --- beams -------------------------------------------------------------------------------------------


def _beam_views(b: dict[str, Any], st: DrawingSettings) -> list[View]:
    W, H, cover = b["width_mm"], b["depth_mm"], b["cover_mm"] or 0
    v = View(
        f"{b['element']} - section",
        f"{b['element']}: section",
        20,
        b["element"],
        (0.0, H / 2),
        "the top of the beam on its centre line",
    )
    v.rect("concrete", (-W / 2, -H / 2), (W / 2, H / 2))
    links = b.get("links") or {}
    phi = links.get("diameter_mm")
    if phi:
        e = cover + phi / 2
        fb = v.sub()
        fb.rect(bar_key(phi), (-W / 2 + e, -H / 2 + e), (W / 2 - e, H / 2 - e))
        params = [
            P("DAR_A", mm=W - 2 * cover),
            P("DAR_B", mm=H - 2 * cover),
            P("DAR_BAR DIAMETER", mm=phi),
        ]
        v.family(st.stirrup_family, (0.0, 0.0), params, fb, align="center")
    for bar in b["bars"]:
        v.bar(bar["diameter_mm"], (bar["y_mm"], bar["z_mm"]))
    sc = v.scale
    if phi:
        # The inner legs as single ties across the depth, evenly between the outer link's legs.
        e = cover + phi / 2
        legs = int(links.get("legs") or 2)
        for k in range(1, max(legs - 1, 1)):
            x = -W / 2 + e + k * (W - 2 * e) / (legs - 1)
            v.line(bar_key(phi), (x, -H / 2 + e), (x, H / 2 - e))
        words = f"ø{_mm(phi)} @ {_mm(links.get('spacing_mm') or 0)} ({legs} LEGS)"
        v.leader(
            (-W / 2 + e, -H / 2 + H * 0.3),
            (-W / 2 - 8 * sc - 0.8 * TEXT_MM * 0.8 * sc * len(words), -H / 2 + H * 0.3 - 4 * sc),
            words,
            0.8,
        )
    # Each group of bars named by a leader, as the office labels them: "19ø25".
    groups: dict[str, list[dict[str, Any]]] = {"top": [], "bottom": [], "sides": []}
    if b["bars"]:
        zt, zb = max(x["z_mm"] for x in b["bars"]), min(x["z_mm"] for x in b["bars"])
        for bar in b["bars"]:
            z = bar["z_mm"]
            groups["top" if z > zt - 100 else "bottom" if z < zb + 100 else "sides"].append(bar)
    for g, bars in groups.items():
        if not bars:
            continue
        by: dict[float, int] = {}
        for bar in bars:
            by[bar["diameter_mm"]] = by.get(bar["diameter_mm"], 0) + 1
        words = "+".join(f"{n}ø{_mm(d)}" for d, n in sorted(by.items(), reverse=True))
        if g == "sides":
            words += " (SIDES)"
        tip = max(bars, key=lambda x: (x["y_mm"], x["z_mm"] * (1 if g == "top" else -1)))
        dy = {"top": 5, "bottom": -6, "sides": 2}[g] * sc
        v.leader((tip["y_mm"], tip["z_mm"]), (W / 2 + 6 * sc, tip["z_mm"] + dy), words, 0.8)
    v.dim((-W / 2, -H / 2), (W / 2, -H / 2), -6 * v.scale)
    v.dim((-W / 2, H / 2), (-W / 2, -H / 2), -6 * v.scale)
    v.caption.append(f"{_mm(W)} x {_mm(H)}, {b.get('label') or ''}")
    if phi:
        v.caption.append(
            f"Links Ø{_mm(phi)}, {links.get('legs')} legs @ {_mm(links.get('spacing_mm') or 0)}"
            f" (outer link {_mm(W - 2 * cover)} x {_mm(H - 2 * cover)} outside)"
        )
    tr = [
        f"{face} Ø{t['diameter_mm']} @ {_mm(t['spacing_mm'] or 0)}"
        + (f" in {t['layers']} layers" if t.get("layers", 1) > 1 else "")
        for face, t in (b.get("transverse") or {}).items()
        if t.get("diameter_mm")
    ]
    if tr:
        v.caption.append("Transverse bars: " + ", ".join(tr))
    along = b.get("along") or ""
    v.caption.append(
        f"Along {along} from {_f(b.get('start_m'))} to {_f(b.get('end_m'))} m, top at {_f(b.get('level_m'))}"
    )
    for j in b.get("construction_joints") or []:
        h = j.get("height_above_soffit_mm")
        if h is not None and 0 < h < H:
            v.line("zones", (-W / 2 - 4 * v.scale, -H / 2 + h), (W / 2 + 4 * v.scale, -H / 2 + h))
        for words in [x["label"] for x in j["additional"]] or ["no additional bars"]:
            v.caption.append(f"Construction joint, {j['where']}: {words}")
    return [v] + [_room_view(b, rm) for rm in b.get("rooms") or []]


def _room_view(b: dict[str, Any], rm: dict[str, Any]) -> View:
    """A section through a room cut into the beam: the concrete left, the room and its bars."""
    W, H, cover = b["width_mm"], b["depth_mm"], b["cover_mm"] or 0
    u0, u1, v0, v1 = rm["void_mm"]
    v = View(
        f"{b['element']} - {rm['name']}",
        f"{b['element']}: section through {rm['name']}",
        20,
        b["element"],
        (0.0, H / 2),
        "the top of the beam on its centre line",
    )
    v.dim((-W / 2, -H / 2), (W / 2, -H / 2), -6 * v.scale)
    v.dim((-W / 2, H / 2), (-W / 2, -H / 2), -6 * v.scale)
    if v1 >= H / 2 - 1e-6:  # open at the top: the outline goes down round the room
        pts = [
            (-W / 2, H / 2),
            (-W / 2, -H / 2),
            (W / 2, -H / 2),
            (W / 2, H / 2),
            (u1, H / 2),
            (u1, v0),
            (u0, v0),
            (u0, H / 2),
            (-W / 2, H / 2),
        ]
        for a, c in zip(pts[:-1], pts[1:], strict=True):
            v.line("concrete", a, c)
    else:
        v.rect("concrete", (-W / 2, -H / 2), (W / 2, H / 2))
        v.rect("concrete", (u0, v0), (u1, v1))
    plus, minus = (u1, W / 2), (-W / 2, u0)
    walls = {"land": plus, "sea": minus} if rm.get("land_side") == "+" else {"land": minus, "sea": plus}
    for name, lk in (rm.get("wall_links") or {}).items():
        phi = lk.get("diameter_mm")
        if phi and name in walls:
            e = cover + phi / 2
            lo, hi = walls[name]
            v.rect(bar_key(phi), (lo + e, -H / 2 + e), (hi - e, H / 2 - e))
    for bar in rm["bars"]:
        v.bar(bar["diameter_mm"], (bar["y_mm"], bar["z_mm"]))
    v.caption.append(f"{_mm(u1 - u0)} x {_mm(v1 - v0)} room from {rm['start_m']:g} to {rm['end_m']:g} m")
    links = [
        f"{k} wall Ø{_mm(lk['diameter_mm'])} @ {_mm(lk['spacing_mm'] or 0)}"
        for k, lk in sorted((rm.get("wall_links") or {}).items())
        if lk.get("diameter_mm")
    ]
    if links:
        v.caption.append("Closed links: " + ", ".join(links))
    fl = rm.get("floor_per_metre") or {}
    wl = rm.get("walls_per_metre") or {}
    parts = [
        f"floor {f} Ø{_mm(t['diameter_mm'])} @ {_mm(t['spacing_mm'] or 0)}"
        for f, t in fl.items()
        if t.get("diameter_mm")
    ]
    if wl.get("diameter_mm"):
        parts.append(f"walls Ø{_mm(wl['diameter_mm'])} @ {_mm(wl['spacing_mm'] or 0)} each face")
    if parts:
        v.caption.append("Across: " + ", ".join(parts))
    if rm.get("diagonals"):
        v.caption.append(
            f"Wall top bars {_mm(rm.get('wall_top_bars_past_ends_mm') or 0)} past "
            f"each end; {rm['diagonals']}",
        )
    return v


# --- slabs -------------------------------------------------------------------------------------------


def _manhole_view(d: dict[str, Any], m: dict[str, Any]) -> View:
    """A manhole in plan (mm from its centre): the opening, the trimmer bars each side and the corner
    diagonals. Top and bottom trimmers are the same lines; the text gives both."""
    v = View(
        f"{d['element']} - {m['name']}",
        f"{d['element']}: {m['name']} in plan",
        20,
        d["element"],
        base_text="the opening's centre",
    )
    a, b = m["size_x_mm"] / 2, m["size_y_mm"] / 2
    v.rect("concrete", (-a, -b), (a, b))
    v.dim((-a, -b), (a, -b), -3 * v.scale)
    v.dim((-a, b), (-a, -b), -3 * v.scale)
    lines = []
    for along, faces in m["trimmers"].items():
        t = faces["top"] if faces["top"]["count"] >= faces["bottom"]["count"] else faces["bottom"]
        half = t["length_mm"] / 2
        gap = max(2.5 * t["phi"], 50.0)
        for side in (1, -1):
            for k in range(t["count"]):
                off = side * ((b if along == "X" else a) + 75 + k * gap)
                if along == "X":
                    v.line(bar_key(t["phi"]), (-half, off), (half, off))
                else:
                    v.line(bar_key(t["phi"]), (off, -half), (off, half))
        lines.append(
            f"Along {along}: top {faces['top']['count']}Ø{faces['top']['phi']}, bottom "
            f"{faces['bottom']['count']}Ø{faces['bottom']['phi']} each side, {_mm(t['length_mm'])} long"
        )
    dg = m.get("diagonals") or {}
    if dg.get("phi"):
        r = (dg.get("length_mm") or 0) / 2 / math.sqrt(2)
        for sx in (1, -1):
            for sy in (1, -1):
                cx, cy = sx * (a + 100), sy * (b + 100)
                # Square to the line from the opening's centre through the corner.
                v.line(bar_key(dg["phi"]), (cx - r, cy + r * sx * sy), (cx + r, cy - r * sx * sy))
    v.caption.append(f"Opening {_mm(2 * a)} x {_mm(2 * b)} at X {m.get('x_m', 0):g}, Y {m.get('y_m', 0):g} m")
    v.caption += lines + ([dg["diagonals"]] if dg.get("diagonals") else [])
    return v


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


def _add_length(span: float, phi: float, st: DrawingSettings) -> float:
    """An additional bar's length: its zone plus the lap factor x Ø past each end, up to 100 mm."""
    return math.ceil((span + 2 * st.lap_factor * phi) / 100.0 - 1e-9) * 100.0


def _plan_context(
    v: View,
    box: tuple[float, float, float, float],
    piles: list[dict[str, Any]],
    beams: list[dict[str, Any]],
) -> None:
    """What the office shows under the bars of a slab plan: the piles with their grid lines (dash-dot in
    the office; the zones layer here) and the beams at the slab's edges."""
    X0, Y0, X1, Y1 = box
    m = 1500.0
    xs: set[float] = set()
    ys: set[float] = set()
    for p in piles:
        if p.get("part") != "pile":
            continue
        for q in p.get("positions") or []:
            x, y = q["x"] * 1000, q["y"] * 1000
            if X0 - m <= x <= X1 + m and Y0 - m <= y <= Y1 + m:
                v.circle("concrete", (x, y), p["diameter_mm"] / 2)
                xs.add(round(x))
                ys.add(round(y))
    for x in sorted(xs):
        v.line("zones", (x, Y0 - m), (x, Y1 + m))
    for y in sorted(ys):
        v.line("zones", (X0 - m, y), (X1 + m, y))
    for b in beams:
        c, w = (b.get("centre_m") or 0.0) * 1000, b.get("width_mm") or 0.0
        s0, s1 = (b.get("start_m") or 0.0) * 1000, (b.get("end_m") or 0.0) * 1000
        if b.get("along") == "Y" and X0 - m <= c <= X1 + m:
            v.rect("concrete", (c - w / 2, max(s0, Y0)), (c + w / 2, min(s1, Y1)))
            v.text((c - w / 2 + v.scale, (Y0 + Y1) / 2), b["element"].upper(), 0.8, 90)
        elif b.get("along") == "X" and Y0 - m <= c <= Y1 + m:
            v.rect("concrete", (max(s0, X0), c - w / 2), (min(s1, X1), c + w / 2))
            v.text(((X0 + X1) / 2, c - w / 2 + v.scale), b["element"].upper(), 0.8)


def _mesh_symbol(v: View, at: tuple[float, float], phi: float, sp: float, face: str, along: str) -> None:
    """The office's mesh note: a hatched circle (lines the way the bars run) and "ø16 mm @ 150 (BOT)"."""
    r = 5 * v.scale
    cx, cy = at
    v.circle(bar_key(phi), at, r)
    for k in range(-4, 5):
        t = k * r / 5
        h = math.sqrt(max(r * r - t * t, 0.0))
        if along == "X":
            v.line(bar_key(phi), (cx - h, cy + t), (cx + h, cy + t))
        else:
            v.line(bar_key(phi), (cx + t, cy - h), (cx + t, cy + h))
    w1 = f"ø{_mm(phi)} mm @ {_mm(sp)} ({'BOT' if face == 'bottom' else 'TOP'})"
    w2 = f"IN {along} DIRECTION"
    for i, words in enumerate((w1, w2)):
        tw = 0.8 * TEXT_MM * 0.9 * v.scale * len(words)
        v.text((cx - tw / 2, cy - r - (2.8 + 2.8 * i) * v.scale), words, 0.9)


def _slab_views(
    d: dict[str, Any],
    piles: list[dict[str, Any]],
    st: DrawingSettings,
    beams: list[dict[str, Any]] | None = None,
) -> list[View]:
    bx = d.get("box_m") or {}
    if not bx.get("X") or not bx.get("Y"):
        return []
    X0, X1 = (v * 1000 for v in bx["X"])
    Y0, Y1 = (v * 1000 for v in bx["Y"])
    inside = X0 <= 0 <= X1 and Y0 <= 0 <= Y1
    base = (0.0, 0.0) if inside else (X0, Y0)
    base_text = (
        "the model origin (X 0, Y 0)" if inside else f"the slab corner X {X0 / 1000:g}, Y {Y0 / 1000:g}"
    )
    views = []
    for face in ("bottom", "top"):
        faces = [f for f in d["faces"] if f["face"] == face]
        if not faces:
            continue
        v = View(
            f"{d['element']} - {face} plan",
            f"{d['element']}: {face} reinforcement plan",
            100,
            d["element"],
            base,
            base_text,
        )
        v.rect("concrete", (X0, Y0), (X1, Y1))
        _plan_context(v, (X0, Y0, X1, Y1), piles, beams or [])
        mesh_text = []
        for i, f in enumerate(faces):
            along = f["bars_along"]  # the direction the bars run
            s_mesh = f["mesh"].get("spacing_mm") or 150.0
            lo = Y0 if along == "X" else X0

            def draw(
                w: View,
                phi: float,
                spacing: float,
                offset: float,
                span: tuple[float, float],
                across: tuple[float, float],
                lo: float = lo,
                along: str = along,
            ) -> None:
                for c in _grid(across[0], across[1], spacing, lo + offset):
                    p, q = (span[0], c), (span[1], c)
                    w.line(bar_key(phi), p if along == "X" else p[::-1], q if along == "X" else q[::-1])

            layers = " + ".join(f"{layer_name(lay['layer'])} {lay['text']}" for lay in f["mesh"]["layers"])
            mesh_text.append(f"Mesh along {along}: {layers}, cover {_mm(f.get('cover_mm') or 0)}")
            fam = st.additional_bars_x_family if along == "X" else st.additional_bars_y_family
            _mesh_symbol(
                v,
                (X0 + (X1 - X0) * (0.3 + 0.4 * i), Y0 + (Y1 - Y0) * (0.62 - 0.24 * i)),
                f["mesh"].get("diameter_mm") or 0,
                s_mesh,
                face,
                along,
            )
            for z in f["zones"]:
                zx0, zx1 = (x * 1000 for x in z["x_m"])
                zy0, zy1 = (y * 1000 for y in z["y_m"])
                span = (zx0, zx1) if along == "X" else (zy0, zy1)
                across = (zy0, zy1) if along == "X" else (zx0, zx1)
                k = 0
                for lay in z["layers"]:
                    for b in lay["bars"]:
                        if lay["layer"] == 1 and b["kind"] == "mesh":
                            continue  # the mesh: drawn by hand
                        off = s_mesh / 2 + (0 if b["kind"] == "mesh" else s_mesh / 2)
                        if lay["layer"] > 1:
                            off += 2 * b["diameter_mm"]  # beside the layer under it, to be seen
                        phi, sp = b["diameter_mm"], b["spacing_mm"]
                        count = len(_grid(across[0], across[1], sp, lo + off))
                        length = _add_length(span[1] - span[0], phi, st)
                        name = layer_name(lay["layer"])
                        # As the office's RFT_ADD family draws it: one bar of its length through the
                        # zone, the width it is spread over with ticks, "ø25 @150 L=4000 (ADD.)".
                        fb = v.sub()
                        cx, cy = (zx0 + zx1) / 2, (zy0 + zy1) / 2
                        k_off = (k - (len(z["layers"]) - 1) / 2) * 3 * v.scale
                        w1 = f"ø{_mm(phi)} @{_mm(sp)}" + ("" if lay["layer"] == 1 else f" {name}")
                        w2 = f"L={_mm(length)} (ADD.)"
                        tw = 0.8 * TEXT_MM * 0.7 * v.scale * max(len(w1), len(w2))
                        th = TEXT_MM * 0.7 * v.scale
                        if along == "X":
                            yb = cy + k_off
                            fb.line(bar_key(phi), (cx - length / 2, yb), (cx + length / 2, yb))
                            xd = cx + k_off
                            fb.line("zones", (xd, zy0), (xd, zy1))
                            for yy in (zy0, zy1):
                                fb.line("zones", (xd - 0.8 * v.scale, yy), (xd + 0.8 * v.scale, yy))
                            fb.text((cx - tw / 2, yb + 0.5 * v.scale), w1, 0.7)
                            fb.text((cx - tw / 2, yb - 0.5 * v.scale - th), w2, 0.7)
                        else:
                            xb = cx + k_off
                            fb.line(bar_key(phi), (xb, cy - length / 2), (xb, cy + length / 2))
                            yd = cy + k_off
                            fb.line("zones", (zx0, yd), (zx1, yd))
                            for xx in (zx0, zx1):
                                fb.line("zones", (xx, yd - 0.8 * v.scale), (xx, yd + 0.8 * v.scale))
                            fb.text((xb - 0.5 * v.scale, cy - tw / 2), w1, 0.7, 90)
                            fb.text((xb + 0.5 * v.scale + th, cy - tw / 2), w2, 0.7, 90)
                        k += 1
                        params = [
                            P("L", mm=length),
                            P("Spacing", mm=sp),
                            P("Diameter", mm=phi),
                            P("Distribution|Distribution Length", mm=across[1] - across[0]),
                            P("Top No.", n=lay["layer"]),
                            P("TOP REINF.", n=1 if face == "top" else 0),
                            P("Comments", text=f"{count} bars, {name}"),
                        ]
                        v.family(fam, ((zx0 + zx1) / 2, (zy0 + zy1) / 2), params, fb, align="center")
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
                    draw(
                        v,
                        x["diameter_mm"],
                        x["spacing_mm"],
                        x["spacing_mm"] / 4,
                        (c - half, c + half),
                        (s0, s1),
                    )
                words = "; ".join(x["label"] for x in j["additional"]) or "no additional bars"
                at = (
                    (c + 2 * v.scale, r0 + 2 * v.scale)
                    if along == "X"
                    else (r0 + 2 * v.scale, c + 2 * v.scale)
                )
                v.text(at, f"Construction joint: {words}", 0.8)
        v.dim((X0, Y0), (X1, Y0), -6 * v.scale)
        v.dim((X0, Y1), (X0, Y0), -6 * v.scale)
        v.caption += mesh_text + [
            "Mesh drawn by hand to suit the plan. Additional bars: one family per zone and direction, "
            f"L = zone + 2 x {st.lap_factor:g}Ø, rounded up to 100 mm. Model X to the right, Y up."
        ]
        views.append(v)
    views += _slab_cuts(d)
    views += _slab_pile_section(d, piles, st)
    if d.get("links"):
        v = View(
            f"{d['element']} - shear links",
            f"{d['element']}: shear link zones",
            100,
            d["element"],
            base,
            base_text,
        )
        v.rect("concrete", (X0, Y0), (X1, Y1))
        for z in d["links"]:
            zx0, zx1 = (x * 1000 for x in z["x_m"])
            zy0, zy1 = (y * 1000 for y in z["y_m"])
            v.rect(bar_key(z["diameter_mm"] or 10), (zx0, zy0), (zx1, zy1))
            v.text(
                ((zx0 + zx1) / 2 - 20 * v.scale, (zy0 + zy1) / 2),
                f"Links Ø{z['diameter_mm']} @ {_mm(z['sx_mm'] or 0)} x {_mm(z['sy_mm'] or 0)}",
            )
        v.caption.append("Shear link zones in plan; the links are drawn as lines in the section at a pile.")
        views.append(v)
    return views


def _slab_pile_section(d: dict[str, Any], piles: list[dict[str, Any]], st: DrawingSettings) -> list[View]:
    """A section across X through the head of the largest pile under the slab (x from the pile's axis,
    y up from the soffit): bottom bars cranked over the pile head where it sits above them, top bars,
    bars along Y cut as dots, the slab's shear links as lines."""
    h = d.get("thickness_mm") or 0
    under = [p for p in piles if p.get("part") == "pile"]
    if not h or not under:
        return []
    pile = max(under, key=lambda p: p["diameter_mm"])
    D = pile["diameter_mm"]
    into = st.pile_into_slab
    half = D / 2 + 1500
    v = View(
        f"{d['element']} - section at {pile['element']}",
        f"{d['element']}: section across X at a {pile['element']} head",
        20,
        d["element"],
        (0.0, 0.0),
        "the pile's axis at the slab soffit",
    )
    v.rect("concrete", (-half, 0), (half, h))
    v.line("concrete", (-D / 2, -1200), (-D / 2, into))
    v.line("concrete", (D / 2, -1200), (D / 2, into))
    v.line("concrete", (-D / 2, into), (D / 2, into))
    v.dim((-D / 2, -1200), (D / 2, -1200), -4 * v.scale, f"Ø{_mm(D)}")
    v.dim((-half, h), (-half, 0), -6 * v.scale)
    if into > 0:
        v.dim((D / 2, 0), (D / 2, into), -3 * v.scale)

    def face(name: str) -> list[tuple[str, dict[str, Any], float]]:
        out = []
        for f in d["faces"]:
            if f["face"] != name:
                continue
            for lay in f["mesh"]["layers"]:
                z = lay.get("above_soffit_mm")
                if z is not None:
                    out.append((f["bars_along"], lay, z))
        return out

    bottom, top = face("bottom"), face("top")
    lowest = min(
        (z - max(b["diameter_mm"] for b in lay["bars"]) / 2 for _, lay, z in bottom),
        default=None,
    )
    shift = max(0.0, into + CLEAR_OVER_PILE - lowest) if lowest is not None else 0.0
    x_crank = D / 2 + 50  # the bars are up over the pile and 50 mm past its face
    run = CRANK_SLOPE * shift  # the crank at 1:6

    def raised(x: float) -> float:
        """How much a bottom bar is lifted at x: all of it over the pile, sloping down along the crank."""
        if run <= 0:
            return 0.0
        return shift * min(1.0, max(0.0, (x_crank + run - abs(x)) / run))

    for rows, lift in ((bottom, shift), (top, 0.0)):
        for along, lay, z in rows:
            for b in lay["bars"]:
                phi = b["diameter_mm"]
                if along == "X":  # along the cut: a line, cranked over the pile
                    if lift > 0:
                        k = bar_key(phi)
                        pts = [
                            (-half + 50, z),
                            (-x_crank - run, z),
                            (-x_crank, z + lift),
                            (x_crank, z + lift),
                            (x_crank + run, z),
                            (half - 50, z),
                        ]
                        for a, c in zip(pts[:-1], pts[1:], strict=True):
                            v.line(k, a, c)
                    else:
                        v.line(bar_key(phi), (-half + 50, z), (half - 50, z))
                else:  # across the cut: dots at their spacing
                    sp = b["spacing_mm"] or 150.0
                    for c in _grid(-half + 50, half - 50, sp, sp / 2):
                        v.bar(phi, (c, z + (raised(c) if lift > 0 else 0.0)))
    links = d.get("links") or []
    if links and bottom and top:
        z0 = min(z for _, _, z in bottom)
        z1 = max(z for _, _, z in top)
        lk = links[0]
        phi, sx = lk.get("diameter_mm") or 12, lk.get("sx_mm") or 300.0
        hook = 6 * phi
        for sgn in (-1, 1):
            k = 1
            while x_crank + run + (k - 0.5) * sx < half - 100 and k <= 6:
                x = sgn * (x_crank + run + (k - 0.5) * sx)  # past the crank, round the bottom bars
                k += 1
                v.line(bar_key(phi), (x, z0), (x, z1))
                v.line(bar_key(phi), (x, z1), (x + sgn * hook, z1))
                v.line(bar_key(phi), (x, z0), (x - sgn * hook, z0))
        v.caption.append(f"Shear links Ø{phi} @ {_mm(sx)} drawn as lines round the pile head")
    if shift > 0:
        v.caption.append(
            f"Pile head {_mm(into)} into the slab: bottom bars cranked up {_mm(shift)} over it "
            f"({_mm(CLEAR_OVER_PILE)} clear), cranks at 1:{CRANK_SLOPE:g} from 50 mm past the pile face"
        )
    else:
        v.caption.append(f"Pile head {_mm(into)} into the slab: the bottom bars clear it, not cranked")
    v.caption.append(f"{_mm(h)} slab, basic mesh only (added bars in zones: see the plans)")
    return [v]


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
            (0.0, 0.0),
            "the soffit at the start of the metre",
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
        v.dim((0, h), (0, 0), -4 * v.scale)
        v.caption.append(f"{_mm(h)} thick; horizontal = model {other}. Added bars in zones: see the plans.")
        views.append(v)
    return views


# --- approach slab and ledge -------------------------------------------------------------------------


def _bar_of(text: str | None) -> tuple[int, float] | None:
    m = re.search(r"Ø(\d+) @ ([\d.]+)", text or "")
    return (int(m.group(1)), float(m.group(2))) if m else None


def _approach_views(a: dict[str, Any]) -> list[View]:
    """A section along the approach slab through the rear beam's ledge: slab and ledge outlines, the
    joint and bearing strip, main bars as lines, distribution bars cut, the ledge's U tie and hangers."""
    led = a.get("ledge") or {}
    v = View(
        f"{a['element']} - section",
        f"{a['element']}: section on the ledge",
        25,
        a["element"],
        (0.0, 0.0),
        "the top of the rear beam at its face",
    )
    h, L = a["thickness_mm"], a["length_m"] * 1000
    joint = a.get("joint_mm") or 0
    P, D = led.get("projection_mm") or 0, led.get("depth_mm") or 0
    drop = led.get("top_below_beam_top_mm") or h
    beam = 800  # mm of the rear beam drawn
    # Rear beam face at x = 0; beam top at y = 0; the slab's top is level with it.
    v.line("concrete", (-beam, 0), (0, 0))
    v.line("concrete", (0, 0), (0, -drop))
    v.rect("concrete", (0, -drop - D), (P, -drop))
    v.line("concrete", (-beam, -drop - D - 400), (0, -drop - D - 400))
    v.line("concrete", (0, -drop - D), (0, -drop - D - 400))
    bt = led.get("bearing_thickness_mm") or 0
    bw = led.get("bearing_width_mm") or 0
    bx = P - (led.get("edge_distance_mm") or 0) - bw
    v.rect("zones", (bx, -drop), (bx + bw, -drop + bt))
    slab_bottom = -drop + bt
    x0 = joint
    v.rect("concrete", (x0, slab_bottom), (x0 + L, slab_bottom + h))
    # Main bars along the slab.
    for face, y, cover in (
        ("bottom", slab_bottom, a.get("cover_bottom_mm") or 75),
        ("top", slab_bottom + h, -(a.get("cover_top_mm") or 50)),
    ):
        bar = _bar_of((a.get(face) or {}).get("bars"))
        if not bar:
            continue
        yb = y + cover + (bar[0] / 2 if face == "bottom" else -bar[0] / 2)
        v.line(bar_key(bar[0]), (x0 + 50, yb), (x0 + L - 50, yb))
        dist = _bar_of((a.get("distribution") or {}).get(face))
        if dist:
            yd = yb + (1 if face == "bottom" else -1) * (bar[0] + dist[0]) / 2
            for xk in _grid(x0 + 50, x0 + L - 50, dist[1], dist[1] / 2):
                v.bar(dist[0], (xk, yd))
        v.text(
            (x0 + L + 100, yb - 40),
            f"{face.capitalize()} Ø{bar[0]} @ {bar[1]:.0f} (main), "
            f"{(a.get('distribution') or {}).get(face) or ''} across",
        )
    # Ledge tie: a U-bar from the beam, round the tip, back into the beam.
    tie = _bar_of(led.get("tie"))
    c = led.get("cover_mm") or 50
    if tie:
        k = bar_key(tie[0])
        yt, yl = -drop - c - tie[0] / 2, -drop - D + c + tie[0] / 2
        anchor = 45 * tie[0]
        v.line(k, (-anchor, yt), (P - c, yt))
        v.line(k, (P - c, yt), (P - c, yl))
        v.line(k, (P - c, yl), (-anchor / 2, yl))
        v.text((P + 100, -drop - D / 2), f"Ledge tie U-bars {led['tie']}, 45Ø into the beam")
    links = led.get("links")
    if links:
        v.text((P + 100, -drop - D / 2 - 6 * v.scale), f"Ledge links: {links}")
    hang = _bar_of(led.get("hanger"))
    if hang:
        kh = bar_key(hang[0])
        xh = -(led.get("cover_mm") or 50) - hang[0] / 2 - 60
        v.line(kh, (xh, -60), (xh, -drop - D - 300))
        v.text((-beam, -drop - D - 400 - 12 * v.scale), f"Hanger bars in the rear beam: {led['hanger']}")
    v.dim((x0 + L, slab_bottom + h), (x0 + L, slab_bottom), 6 * v.scale)
    if P and D:
        v.dim((0, -drop - D), (P, -drop - D), -3 * v.scale)
        v.dim((P, -drop), (P, -drop - D), 3 * v.scale)
    v.caption.append(
        f"Slab {_mm(h)} thick, {a['length_m']:g} m to the slab on grade; "
        f"joint {_mm(joint)} at the rear beam; "
        f"ledge {_mm(P)} x {_mm(D)}"
        + (
            f"; shear links {a['links']:.0f} mm²/m² over {a.get('links_zone_m') or 0:g} m"
            if a.get("links")
            else ""
        ),
    )
    return [v]


def layer_names(settings: DrawingSettings, keys: set[str]) -> dict[str, dict[str, Any]]:
    """What each layer key is called in AutoCAD and Revit."""
    by_d = {b.diameter: b for b in settings.bars}
    out: dict[str, dict[str, Any]] = {}
    for k in sorted(keys):
        if k.startswith("bar-"):
            d = int(k[4:])
            b = by_d.get(d)
            office = OFFICE_LINE_STYLE.format(d=d)
            out[k] = {
                "diameter_mm": d,
                "cad_layer": (b.cad_layer if b else "") or office,
                "revit_line_style": (b.revit_line_style if b else "") or office,
                "revit_section_type": (b.revit_section_type if b else "") or settings.cut_bar_family,
                "revit_line_type": b.revit_line_type if b else "",
            }
        elif k == "concrete":
            out[k] = {
                "cad_layer": settings.concrete_cad_layer,
                "revit_line_style": settings.concrete_revit_line_style,
            }
        elif k in ("zones", "dims"):
            out[k] = {
                "cad_layer": settings.zones_cad_layer if k == "zones" else settings.text_cad_layer,
                "revit_line_style": settings.zones_revit_line_style,
            }
            if k == "dims":
                out[k]["revit_dimension_type"] = settings.revit_dimension_type
        elif k == "text":
            out[k] = {"cad_layer": settings.text_cad_layer, "revit_text_type": settings.revit_text_type}
    return out


def _layers_of(items: list[dict[str, Any]]) -> set[str]:
    out = set()
    for it in items:
        out.add(it["layer"])
        out |= _layers_of(it.get("fallback") or [])
    return out


def drawings(
    project_name: str,
    results: dict[str, Any],
    settings: DrawingSettings,
    section: str = "",
    element: str | Collection[str] | None = None,
) -> dict[str, Any]:
    """Every drawing of a designed section (or of the elements named), with the names to draw them on."""
    from .furniture_report import bollard_views

    return from_cages(
        pile_cages(project_name, results, section), settings, element, extra=bollard_views(results)
    )


def from_cages(
    data: dict[str, Any],
    settings: DrawingSettings,
    element: str | Collection[str] | None = None,
    extra: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The drawings of the bars in a Revit bar file (``design.export.pile_cages``); ``element``: only
    these elements (one name or several; empty: all). ``extra``: finished views to add (the bollards)."""
    views: list[View] = []
    for p in data["piles"]:
        views += _pile_views(p, settings)
    for b in data["beams"]:
        views += _beam_views(b, settings)
    for d in data["slabs"]:
        views += _slab_views(d, data["piles"], settings, data["beams"])
        views += [_manhole_view(d, m) for m in d.get("manholes") or []]
        views += [
            _room_view(
                {
                    "element": d["element"],
                    "width_mm": c["strip_mm"],
                    "depth_mm": c["depth_mm"],
                    "cover_mm": c["cover_mm"],
                },
                c,
            )
            for c in d.get("channels") or []
        ]
    for a in data.get("approach") or []:
        views += _approach_views(a)
    out = [v.as_dict() for v in views]
    # The bollard sections go after their beam's section.
    for x in extra or []:
        at = max((i for i, v in enumerate(out) if v["element"] == x["element"]), default=len(out) - 1)
        out.insert(at + 1, x)
    if element:
        wanted = {element} if isinstance(element, str) else set(element)
        out = [v for v in out if v["element"] in wanted]
    layout(out)
    keys = set().union(*(_layers_of(v["items"]) for v in out)) if out else set()
    return {
        "format": FORMAT,
        "project": data.get("project"),
        "section": data.get("section"),
        "run_at": data.get("run_at"),
        "units": "mm",
        "view_prefix": settings.revit_view_prefix,
        "view_scale": settings.revit_view_scale,
        "bar_param": BAR_PARAM,
        "families": {
            "cut_bar": settings.cut_bar_family,
            "pile_section": settings.pile_section_family,
            "stirrup": settings.stirrup_family,
            "additional_x": settings.additional_bars_x_family,
            "additional_y": settings.additional_bars_y_family,
        },
        "layers": layer_names(settings, keys),
        "views": out,
    }


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", text).strip() or "drawing"
