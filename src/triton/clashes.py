"""Reinforcement clashes: where the bars of a pile cage run up into a slab or beam and meet its bars.

Everything is found from Triton's own designed bars, placed as the drawing export places them
(``design/export.py``): each pile's rows of bars on their circles, the slab's mesh, zones and layers,
its punching links, each beam's longitudinal bars, links and transverse bars. Nothing here changes a
design: every solution and every "what if" is a check of the element as it would be.

Geometry
--------
* A Plaxis plate sits at the element's mid-depth, unless the clash settings say it is its top, or give
  an element its own top level. Slabs are ``thickness`` deep, beams ``depth``.
* The pile bars come up through the soffit and run on for the anchorage the pile design gave them
  (``head_anchorage_factor`` × Ø above the pile's top level). Into a beam deep enough they stay
  straight; where they would run past the top bars (a slab) they turn horizontal, outwards, just under
  the top bars: an L bar. The legs are checked against the punching links.
* A slab's bars along X sit at Y = the slab's edge + cover + Ø/2 + k × spacing (the clash settings can
  move where the mesh starts); added bars "between the mesh bars" sit half a spacing over. A beam's
  links start 75 mm from its end at their spacing, their legs evenly across the width; its transverse
  bars sit just inside the longitudinal bars of their face, half a link pitch from the links.
* Slab shear links are left out over a pile head (the pile is the support there); the punching links
  are drawn on their perimeters round the pile (EN 1992-1-1 6.4.5 and 9.4.3): the smallest bar whose
  legs carry Asw per perimeter at no more than 1.5d apart round it (2d beyond 2d from the face).

A clash is two bars closer than the rule allows: bars that would overlap (with a fixing tolerance),
or, with the EN 1992-1-1 8.2(2) rule, closer than max(Ø, dg + 5, 20 mm).

Solutions for the pile bars, each designed again with the bars as changed:

* ``rotate``: turn the pile cage about its axis so its bars fall between the bars above. The pile's
  N–M check is the same whichever way the cage faces (it is checked all round), so it needs no new
  design.
* ``shift``: move each clashing slab or beam bar sideways to the nearest clear place (a local crank
  round the pile bars). Bars per metre stay the same; the widest gap it leaves is checked against the
  spacing limit and, for slabs, the crack width at that spacing; beam bars moved sideways are checked
  in N + M in their new places, links and transverse bars at the wider pitch.
* ``rotate_shift``: the best turn of the cage, then the bars still clashing moved.
* ``crank``: crank the pile bars inwards (1 in 6) over the connection so the cage is narrower where it
  meets the bars; the pile head is designed again in N + M with the smaller circle.
* ``cut_trim``: cut the clashing bars at the pile cage and add trimmer bars of the same area beside it,
  bundled with the first bar left each side and lapped past the cage; clashing links are moved.
* ``cut``: cut the clashing bars and add nothing; the strip or beam is designed again without them.

Punching links that clash with the slab bars or the pile's L legs are set out on site in the mesh
openings next to where they were drawn (``snap``): round their perimeter, and in or out by up to 0.15d
where the spacings still allow (the first perimeter 0.3d to 0.5d from the face, perimeters at most 0.75d
apart, legs at most 1.5d apart round one); the layout is checked again against 9.4.3.

A "what if" takes bars out of any connection (a slab or beam bar, a pile bar) and checks the slab
strip, the beam, the pile head and the punching resistance without them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple

import numpy as np

from .design.circular import CircularSection, ConcreteLaw, Ring, SteelLaw
from .design.export import pile_cages
from .design.rect import Bars, RectSection
from .design.slabs import crack_widths, strip_mrd
from .materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from .project import ClashSettings, DesignSettings, Project, Section, WhatIf, with_project_grades

ROTATE_STEP = 0.25  # degrees
SHIFT_STEP = 5.0  # mm
LINK_START = 75.0  # mm from the beam's end to its first link
CRANK_SLOPE = 6.0  # 1 in 6
CRANK_MAX = 150.0  # mm
PUNCH_GAP = 50.0  # mm, least clear gap between two punching legs


# --- Bars ------------------------------------------------------------------------------------------


@dataclass
class HBar:
    """A horizontal bar near a pile: it runs along ``along`` at ``at`` (m, the other plan coordinate)."""

    id: str
    along: str  # "X" or "Y"
    at: float  # m
    z: float  # m
    phi: float  # mm
    group: str  # e.g. "Deck bottom bars along X, layer 1 (Ø25 @ 150, mesh)"
    layer: str  # bars laid together: "slab|bottom_x|1", "beam|long|bottom", "beam|links", "beam|tr_bottom|1"
    kind: str  # mesh, between, added, longitudinal, link leg, transverse
    spacing: float | None = None  # mm between bars of its kind (None: not evenly spaced)
    lo: float = -math.inf  # m, extent along ``along``
    hi: float = math.inf
    link: int | None = None  # the beam link it belongs to


@dataclass
class Leg:
    """A vertical link leg: a point in plan through the element's depth."""

    id: str
    x: float
    y: float
    phi: float
    group: str
    link: int  # beam link number, or punching perimeter


class Hit(NamedTuple):
    a: str  # "pile" (a pile bar) or "punch" (a punching link leg)
    i: int
    b: str  # "h" (horizontal bar), "l" (beam link leg), "leg" (a pile bar's L leg)
    j: int
    gap: float  # mm, clear
    kind: str  # clash or tight


@dataclass
class Host:
    element: str
    kind: str  # slab or beam
    soffit: float  # m
    top: float  # m
    data: dict  # the element's drawing export
    result: dict  # the element's design result


@dataclass
class Head:
    """One pile head in one element."""

    pile: str
    part: str  # pile or infill
    index: int
    x: float
    y: float
    diameter: float  # mm
    rings: list[dict]  # count, diameter_mm, radius_mm, bar_top_m
    host: Host
    z0: float  # m, where the pile bars enter the element
    top_zone: float = 0.0  # mm from the top to the underside of the top bars
    z1: list[float] = field(default_factory=list)  # m, top of the straight bars, per row
    legs_m: list[float] = field(default_factory=list)  # m, horizontal L leg per row (0: straight)
    hbars: list[HBar] = field(default_factory=list)
    legs: list[Leg] = field(default_factory=list)  # beam link legs
    punch: list[Leg] = field(default_factory=list)  # punching link legs
    punch_info: dict | None = None


def _limit(rule: ClashSettings, dg: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Least clear gap (mm) between bars of Ø a and Ø b."""
    if rule.rule == "ec2":
        return np.maximum(np.maximum(np.maximum(a, b), dg + 5), 20.0)
    return np.full(np.broadcast(a, b).shape, rule.fixing_tolerance, float)


def _ec2_gap(dg: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.maximum(np.maximum(np.maximum(a, b), dg + 5), 20.0)


def pile_bars(head: Head, turn: float = 0.0, inward: float = 0.0, drop: set[str] | None = None) -> np.ndarray:
    """Pile bars as rows [x, y, Ø, row, bar, top] (m, m, mm, -, -, m), the cage turned ``turn`` degrees
    anticlockwise and cranked ``inward`` mm; ``drop``: 'row:bar' ids left out."""
    out = []
    for k, r in enumerate(head.rings):
        n = int(r["count"])
        rad = (r["radius_mm"] - inward) / 1e3
        a = np.radians(turn) + 2 * np.pi * np.arange(n) / n
        for i in range(n):
            if drop and f"{k}:{i}" in drop:
                continue
            out.append(
                [
                    head.x + rad * math.cos(a[i]),
                    head.y + rad * math.sin(a[i]),
                    r["diameter_mm"],
                    k,
                    i,
                    head.z1[k],
                ]
            )
    return np.array(out, float).reshape(-1, 6)


def pile_legs(head: Head, bars: np.ndarray) -> np.ndarray:
    """The L legs of the pile bars that turn under the top bars: rows [x0, y0, x1, y1, Ø, z, bar row]."""
    out = []
    for n, (x, y, phi, k, _i, top) in enumerate(bars):
        length = head.legs_m[int(k)]
        if length <= 0:
            continue
        a = math.atan2(y - head.y, x - head.x)
        out.append([x, y, x + length * math.cos(a), y + length * math.sin(a), phi, top, n])
    return np.array(out, float).reshape(-1, 7)


def _harr(hbars: list[HBar]) -> dict[str, np.ndarray]:
    return {
        "x": np.array([h.along == "X" for h in hbars], bool),
        "at": np.array([h.at for h in hbars], float),
        "z": np.array([h.z for h in hbars], float),
        "phi": np.array([h.phi for h in hbars], float),
        "lo": np.array([h.lo for h in hbars], float),
        "hi": np.array([h.hi for h in hbars], float),
    }


def _hits(mask: np.ndarray, gap: np.ndarray, a: str, b: str, rows: np.ndarray | None = None) -> list[Hit]:
    out = []
    for i, j in zip(*np.nonzero(mask), strict=True):
        g = float(gap[i, j])
        out.append(
            Hit(a, int(i if rows is None else rows[i]), b, int(j), round(g, 1), "clash" if g < 0 else "tight")
        )
    return out


def conflicts(
    head: Head,
    rule: ClashSettings,
    dg: float,
    bars: np.ndarray,
    hbars: list[HBar] | None = None,
    legs: list[Leg] | None = None,
    punch: list[Leg] | None = None,
    with_punch: bool = True,
) -> list[Hit]:
    """Every pair of bars closer than the rule: pile bars with the element's bars and link legs, and the
    punching legs with the slab bars and the pile bars' L legs."""
    hbars = head.hbars if hbars is None else hbars
    legs = head.legs if legs is None else legs
    punch = head.punch if punch is None else punch
    out: list[Hit] = []
    if len(bars) and hbars:
        H = _harr(hbars)
        px, py, pphi, top = (bars[:, k][:, None] for k in (0, 1, 2, 5))
        across = np.where(H["x"][None, :], py, px)
        along = np.where(H["x"][None, :], px, py)
        gap = np.abs(across - H["at"][None, :]) * 1e3 - (pphi + H["phi"][None, :]) / 2
        inside = (H["z"][None, :] >= head.z0 - H["phi"][None, :] / 2e3) & (
            H["z"][None, :] <= top + pphi / 2e3
        )
        within = (along >= H["lo"][None, :]) & (along <= H["hi"][None, :])
        hit = inside & within & (gap < _limit(rule, dg, pphi, H["phi"][None, :]))
        out += _hits(hit, gap, "pile", "h")
    if len(bars) and legs:
        lx = np.array([g.x for g in legs])[None, :]
        ly = np.array([g.y for g in legs])[None, :]
        lp = np.array([g.phi for g in legs])[None, :]
        gap = np.hypot(bars[:, 0:1] - lx, bars[:, 1:2] - ly) * 1e3 - (bars[:, 2:3] + lp) / 2
        out += _hits(gap < _limit(rule, dg, bars[:, 2:3], lp), gap, "pile", "l")
    if with_punch and punch:
        qx = np.array([g.x for g in punch])[:, None]
        qy = np.array([g.y for g in punch])[:, None]
        qp = np.array([g.phi for g in punch])[:, None]
        if hbars:
            H = _harr(hbars)
            across = np.where(H["x"][None, :], qy, qx)
            gap = np.abs(across - H["at"][None, :]) * 1e3 - (qp + H["phi"][None, :]) / 2
            out += _hits(gap < _limit(rule, dg, qp, H["phi"][None, :]), gap, "punch", "h")
        pl = pile_legs(head, bars)
        if len(pl):
            x0, y0, x1, y1 = (pl[:, k][None, :] for k in range(4))
            dx, dy = x1 - x0, y1 - y0
            t = np.clip(((qx - x0) * dx + (qy - y0) * dy) / np.maximum(dx * dx + dy * dy, 1e-12), 0, 1)
            gap = np.hypot(qx - (x0 + t * dx), qy - (y0 + t * dy)) * 1e3 - (qp + pl[:, 4][None, :]) / 2
            out += _hits(gap < _limit(rule, dg, qp, pl[:, 4][None, :]), gap, "punch", "leg")
    return out


def pile_hits(hits: list[Hit]) -> list[Hit]:
    return [h for h in hits if h.a == "pile"]


def punch_hits(hits: list[Hit]) -> list[Hit]:
    return [h for h in hits if h.a == "punch"]


# --- Where the elements are ---------------------------------------------------------------------------


def _top_of(rule: ClashSettings, name: str, level: float, depth_mm: float) -> float:
    if name in rule.top_levels:
        return float(rule.top_levels[name])
    return level if rule.plate_level == "top" else level + depth_mm / 2e3


def _beam_axes(d: dict) -> tuple[str, str]:
    along = d.get("along") or "Y"
    return along, ("X" if along == "Y" else "Y")


def _in_host(host: Host, x: float, y: float) -> bool:
    d = host.data
    if host.kind == "beam":
        along, _ = _beam_axes(d)
        s, t = (y, x) if along == "Y" else (x, y)
        lo, hi = sorted((d["start_m"], d["end_m"]))
        return lo <= s <= hi and abs(t - d["centre_m"]) <= d["width_mm"] / 2e3
    b = d.get("box_m") or {}
    return bool(b) and b["X"][0] <= x <= b["X"][1] and b["Y"][0] <= y <= b["Y"][1]


def _zone_at(face: dict, x: float, y: float) -> dict | None:
    for z in face.get("zones") or []:
        if z["x_m"][0] - 1e-6 <= x <= z["x_m"][1] + 1e-6 and z["y_m"][0] - 1e-6 <= y <= z["y_m"][1] + 1e-6:
            return z
    return None


def slab_layers_at(d: dict, x: float, y: float) -> list[dict]:
    """The slab's bar layers at a point: per face and direction, the zone's layers there or the mesh's."""
    out = []
    for f in d.get("faces") or []:
        zone = _zone_at(f, x, y)
        layers = (zone or {}).get("layers") or f["mesh"]["layers"]
        out.append(
            {
                "face": f["face"],
                "along": f["bars_along"],
                "cover_mm": f.get("cover_mm") or 0,
                "mesh": f["mesh"],
                "zone": zone,
                "layers": layers,
            }
        )
    return out


KIND_WORDS = {"mesh": "mesh", "between the mesh bars": "between the mesh bars"}


def _slab_bars(host: Host, rule: ClashSettings, x: float, y: float, reach: float) -> list[HBar]:
    d = host.data
    box = d["box_m"]
    out: list[HBar] = []
    for lay in slab_layers_at(d, x, y):
        along = lay["along"]
        perp = "Y" if along == "X" else "X"
        c = y if along == "X" else x
        mesh_phi = lay["mesh"]["diameter_mm"] or 0
        s_mesh = lay["mesh"]["spacing_mm"] or 0
        shift = rule.mesh_start.get(f"{d['element']}|{along}", 0.0)
        o = box[perp][0] + (lay["cover_mm"] + mesh_phi / 2 + shift) / 1e3
        key = f"{lay['face']}_{along.lower()}"
        for layer in lay["layers"]:
            z = host.soffit + layer["above_soffit_mm"] / 1e3
            for b in layer["bars"]:
                s = b["spacing_mm"] / 1e3
                if s <= 0:
                    continue
                first = o + (s_mesh / 2e3 if b["kind"] == "between the mesh bars" else 0.0)
                kind = {"mesh": "mesh", "between the mesh bars": "between"}.get(b["kind"], "added")
                for k in range(
                    math.ceil((c - reach - first) / s - 1e-9), math.floor((c + reach - first) / s + 1e-9) + 1
                ):
                    at = first + k * s
                    if not box[perp][0] - 1e-6 <= at <= box[perp][1] + 1e-6:
                        continue
                    out.append(
                        HBar(
                            f"s|{key}|{layer['layer']}|{kind}|{k}",
                            along,
                            round(at, 4),
                            round(z, 4),
                            b["diameter_mm"],
                            f"{d['element']} {lay['face']} bars along {along}, layer {layer['layer']} "
                            f"(Ø{b['diameter_mm']:g} @ {b['spacing_mm']:g}, {KIND_WORDS.get(b['kind'], b['kind'])})",
                            f"slab|{key}|{layer['layer']}",
                            kind,
                            b["spacing_mm"],
                        )
                    )
    return out


def _slab_top_zone(d: dict, x: float, y: float) -> float:
    """mm from the slab's top to the underside of its top bars at a point."""
    deep = 0.0
    h = d["thickness_mm"]
    for lay in slab_layers_at(d, x, y):
        if lay["face"] != "top":
            continue
        for layer in lay["layers"]:
            big = max((b["diameter_mm"] for b in layer["bars"]), default=0)
            deep = max(deep, h - layer["above_soffit_mm"] + big / 2)
    return deep


def _beam_groups(d: dict) -> dict[str, Any]:
    """A beam's bottom and top bars (within two bars and 60 mm of the outermost)."""
    bars = d.get("bars") or []
    if not bars:
        return {}
    lo, hi = min(b["z_mm"] for b in bars), max(b["z_mm"] for b in bars)
    band = 2 * max(b["diameter_mm"] for b in bars) + 60
    return {
        "bottom": [b for b in bars if b["z_mm"] <= lo + band],
        "top": [b for b in bars if b["z_mm"] >= hi - band],
        "lo": lo,
        "hi": hi,
        "lo_phi": max(b["diameter_mm"] for b in bars if b["z_mm"] == lo),
        "hi_phi": max(b["diameter_mm"] for b in bars if b["z_mm"] == hi),
    }


def _face_of(g: dict, b: dict) -> str:
    return "bottom" if b in g["bottom"] else "top" if b in g["top"] else "side"


def _beam_bars(host: Host, x: float, y: float, reach: float) -> tuple[list[HBar], list[Leg]]:
    d = host.data
    along, across = _beam_axes(d)
    mid = host.top - d["depth_mm"] / 2e3
    g = _beam_groups(d)
    if not g:
        return [], []
    s_here, t_here = (y, x) if along == "Y" else (x, y)
    lo, hi = sorted((d["start_m"], d["end_m"]))
    out: list[HBar] = []
    for n, b in enumerate(d["bars"]):
        at = d["centre_m"] + b["y_mm"] / 1e3
        if abs(at - t_here) > reach:
            continue
        face = _face_of(g, b)
        out.append(
            HBar(
                f"b|long|{n}",
                along,
                round(at, 4),
                round(mid + b["z_mm"] / 1e3, 4),
                b["diameter_mm"],
                f"{d['element']} {face} bars (Ø{b['diameter_mm']:g} along the beam)",
                f"beam|long|{face}",
                "longitudinal",
                None,
                lo,
                hi,
            )
        )
    half = d["width_mm"] / 2e3
    t0, t1 = d["centre_m"] - half, d["centre_m"] + half
    links = d.get("links") or {}
    legs: list[Leg] = []
    lphi = links.get("diameter_mm") or 0
    ls = (links.get("spacing_mm") or 0) / 1e3
    if lphi and ls > 0:
        k0 = max(0, math.ceil((s_here - reach - lo - LINK_START / 1e3) / ls - 1e-9))
        k1 = math.floor((s_here + reach - lo - LINK_START / 1e3) / ls + 1e-9)
        n_legs = max(2, int(links.get("legs") or 2))
        a = half - (d["cover_mm"] + lphi / 2) / 1e3
        spots = np.linspace(-a, a, n_legs)
        z_bot = mid + (g["lo"] - g["lo_phi"] / 2 - lphi / 2) / 1e3
        words = f"{d['element']} links (Ø{lphi:g}, {n_legs} legs @ {links['spacing_mm']:g})"
        for k in range(k0, k1 + 1):
            s = lo + LINK_START / 1e3 + k * ls
            if s > hi:
                break
            out.append(
                HBar(
                    f"b|link|{k}",
                    across,
                    round(s, 4),
                    round(z_bot, 4),
                    lphi,
                    words + ", bottom leg",
                    "beam|links",
                    "link leg",
                    links["spacing_mm"],
                    t0,
                    t1,
                    link=k,
                )
            )
            for m, u in enumerate(spots):
                t = d["centre_m"] + u
                px, py = (t, s) if along == "Y" else (s, t)
                legs.append(
                    Leg(f"b|linkleg|{k}|{m}", round(px, 4), round(py, 4), lphi, words + ", vertical legs", k)
                )
    for face, t in (d.get("transverse") or {}).items():
        phi, sp = t.get("diameter_mm"), (t.get("spacing_mm") or 0) / 1e3
        if not phi or sp <= 0 or face not in ("top", "bottom"):
            continue
        rows = g[face]
        big = max(b["diameter_mm"] for b in rows)
        if face == "bottom":
            z, step = mid + (max(b["z_mm"] for b in rows) + big / 2 + phi / 2) / 1e3, 1
        else:
            z, step = mid + (min(b["z_mm"] for b in rows) - big / 2 - phi / 2) / 1e3, -1
        first = lo + LINK_START / 1e3 + (ls / 2 if ls > 0 else sp / 2)
        k0 = max(0, math.ceil((s_here - reach - first) / sp - 1e-9))
        k1 = math.floor((s_here + reach - first) / sp + 1e-9)
        for layer in range(int(t.get("layers") or 1)):
            zl = z + step * layer * (phi + max(25.0, phi)) / 1e3
            for k in range(k0, k1 + 1):
                s = first + k * sp
                if s > hi:
                    break
                out.append(
                    HBar(
                        f"b|tr_{face}|{layer + 1}|{k}",
                        across,
                        round(s, 4),
                        round(zl, 4),
                        phi,
                        f"{d['element']} {face} transverse bars (Ø{phi:g} @ {t['spacing_mm']:g})",
                        f"beam|tr_{face}|{layer + 1}",
                        "transverse",
                        t["spacing_mm"],
                        t0,
                        t1,
                    )
                )
    return out, legs


def _beam_top_zone(d: dict) -> float:
    g = _beam_groups(d)
    if not g:
        return 0.0
    deep = d["depth_mm"] / 2 - (
        min(b["z_mm"] for b in g["top"]) - max(b["diameter_mm"] for b in g["top"]) / 2
    )
    t = (d.get("transverse") or {}).get("top") or {}
    if t.get("diameter_mm"):
        deep += int(t.get("layers") or 1) * (t["diameter_mm"] + 25)
    return deep


def _hosts(rule: ClashSettings, drawing: dict, results: dict) -> list[Host]:
    by = {(k, e["element"]): e for k in ("slabs", "beams") for e in results.get(k) or []}
    out = []
    for b in drawing.get("beams") or []:
        top = _top_of(rule, b["element"], b["level_m"], b["depth_mm"])
        out.append(
            Host(b["element"], "beam", top - b["depth_mm"] / 1e3, top, b, by.get(("beams", b["element"]), {}))
        )
    for s in drawing.get("slabs") or []:
        h = s["thickness_mm"] or 0
        top = _top_of(rule, s["element"], s["level_m"], h)
        out.append(Host(s["element"], "slab", top - h / 1e3, top, s, by.get(("slabs", s["element"]), {})))
    return out


def _punching(settings: DesignSettings, head: Head) -> tuple[list[Leg], dict | None]:
    """The punching links round a pile head in the slab, on their perimeters (6.4.5, 9.4.3)."""
    res = head.host.result
    p = next(
        (q for q in res.get("punching") or [] if abs(q["x"] - head.x) < 0.05 and abs(q["y"] - head.y) < 0.05),
        None,
    )
    if p is None or not p.get("link_radii_mm"):
        return [], None
    d = p["d_mm"]
    asw = p.get("asw_mm2_per_perimeter") or 0
    bars = [b for b in settings.reinforcement.bar_diameters if b >= 10] or [12]
    pick = None
    radii = p["link_radii_mm"]
    for phi in bars:
        counts = []
        for r in radii:
            reach = r - head.diameter / 2
            s_t = (1.5 if reach <= 2 * d else 2.0) * d
            counts.append(
                max(math.ceil(asw / (math.pi * phi * phi / 4)), math.ceil(2 * math.pi * r / s_t), 4)
            )
        if all(2 * math.pi * r / n >= 100 for r, n in zip(radii, counts, strict=True)):
            pick = (phi, counts)
            break
    if pick is None:
        phi = bars[-1]
        pick = (phi, [max(math.ceil(asw / (math.pi * phi * phi / 4)), 4) for _ in radii])
    phi, counts = pick
    legs = []
    for k, (r, n) in enumerate(zip(radii, counts, strict=True)):
        off = math.pi / n if k % 2 else 0.0  # perimeters staggered
        for i in range(n):
            a = off + 2 * math.pi * i / n
            legs.append(
                Leg(
                    f"p|{k}|{i}",
                    round(head.x + r / 1e3 * math.cos(a), 4),
                    round(head.y + r / 1e3 * math.sin(a), 4),
                    phi,
                    f"{head.host.element} punching links round {head.pile} (Ø{phi:g})",
                    k,
                )
            )
    info = {
        "d_mm": d,
        "phi": phi,
        "counts": counts,
        "radii_mm": radii,
        "asw_mm2_per_perimeter": asw,
        "radial_spacing_mm": p.get("radial_spacing_mm"),
        "first_from_face_mm": round(radii[0] - head.diameter / 2),
        "utilisation": p.get("utilisation"),
        "utilisation_with_links": p.get("utilisation_with_links"),
    }
    return legs, info


def heads(
    rule: ClashSettings, settings: DesignSettings, drawing: dict, results: dict
) -> tuple[list[Head], list[str]]:
    """Every pile head that runs up into a slab or beam, with the bars round it."""
    notes: list[str] = []
    hosts = _hosts(rule, drawing, results)
    out: list[Head] = []
    for p in drawing.get("piles") or []:
        runs = p.get("runs") or []
        if not runs:
            continue
        rows = runs[0]["rows"]
        r_out = max(r["radius_mm"] + r["diameter_mm"] / 2 for r in rows) / 1e3
        for i, pos in enumerate(p.get("positions") or []):
            x, y = pos["x"], pos["y"]
            host = next((h for h in hosts if h.kind == "beam" and _in_host(h, x, y)), None)
            host = host or next((h for h in hosts if h.kind == "slab" and _in_host(h, x, y)), None)
            if host is None:
                continue
            level = p.get("head_level_m")
            if level is not None and level > host.top:
                notes.append(
                    f"{p['element']} at X {x:g}, Y {y:g}: its top level {level:g} m is above the top of "
                    f"{host.element} ({host.top:.2f} m), so its bars are not checked there."
                )
                continue
            h = Head(
                p["element"],
                p["part"],
                i,
                x,
                y,
                p["diameter_mm"],
                [
                    {
                        "count": r["count"],
                        "diameter_mm": r["diameter_mm"],
                        "radius_mm": r["radius_mm"],
                        "bar_top_m": r["bar_top_m"],
                    }
                    for r in rows
                ],
                host,
                host.soffit,
            )
            legs_reach = max(r["bar_top_m"] or 0 for r in rows) - host.soffit
            reach = r_out + max(0.3, legs_reach if host.kind == "slab" else 0.3)
            if host.kind == "slab":
                h.top_zone = _slab_top_zone(host.data, x, y)
                h.hbars = _slab_bars(host, rule, x, y, reach)
            else:
                h.top_zone = _beam_top_zone(host.data)
                h.hbars, h.legs = _beam_bars(host, x, y, r_out + 0.3)
            for r in h.rings:
                bend = host.top - (h.top_zone + r["diameter_mm"] / 2) / 1e3
                top = r["bar_top_m"] if r["bar_top_m"] is not None else bend
                h.z1.append(round(min(top, bend), 3))
                h.legs_m.append(round(max(top - bend, 0.0), 3))
            if host.kind == "slab":
                h.punch, h.punch_info = _punching(settings, h)
            out.append(h)
    return out, notes


def connection(head: Head) -> dict:
    """How the pile bars end in the element: straight or as an L under the top bars, row by row."""
    rows = []
    for k, r in enumerate(head.rings):
        up = head.z1[k] - head.z0
        leg = head.legs_m[k]
        rows.append(
            {
                "row": k + 1,
                "bars": f"{r['count']}Ø{r['diameter_mm']:g}",
                "up_m": round(up, 3),
                "leg_m": leg,
                "shape": "L" if leg > 0 else "straight",
            }
        )
    shape = "L bars" if any(r["shape"] == "L" for r in rows) else "straight bars"
    text = f"{head.pile} bars into {head.host.element}: " + "; ".join(
        f"row {r['row']} {r['bars']} "
        + (
            f"up {r['up_m']:.2f} m, then an L leg of {r['leg_m']:.2f} m outwards under the top bars"
            if r["shape"] == "L"
            else f"straight, {r['up_m']:.2f} m into it"
        )
        for r in rows
    )
    return {"shape": shape, "rows": rows, "text": text}


# --- Design checks again -------------------------------------------------------------------------------


@dataclass
class Ctx:
    project: Project
    section: Section
    settings: DesignSettings
    results: dict
    rule: ClashSettings

    @property
    def dg(self) -> float:
        return self.settings.piles.aggregate_size

    @property
    def lap(self) -> float:
        return self.settings.piles.lap_factor

    @property
    def fyd(self) -> float:
        return REINFORCEMENT_GRADES[self.settings.reinforcement.grade] / self.settings.partial_factors.gamma_s


def _weight(phi: float, length_m: float) -> float:
    return math.pi * phi * phi / 4 / 1e6 * length_m * STEEL_DENSITY


def _num(v: float | None, digits: int = 3) -> float | None:
    if v is None or not math.isfinite(v):
        return None
    return round(v, digits)


def _strip_row(head: Head, layer_key: str) -> dict | None:
    """The slab's design row (column strip and station, or zone) that sets ``layer_key`` at the pile."""
    sd = head.host.result.get("strip_design") or {}
    rows = [r for r in sd.get("rows") or [] if r.get("layer") == layer_key]
    if not rows:
        return None
    s = sd.get("origin", 0.0) + sd.get("sign", 1.0) * (head.x if sd.get("along", "X") == "X" else head.y)
    for r in rows:
        st = r.get("station") or [0, 0]
        if r.get("strip") == "column" and st[0] - 1e-6 <= s <= st[1] + 1e-6:
            return r
    for r in rows:
        key = r.get("key", "")
        if "|zone|" in key:
            parts = key.split("|")
            x0, x1, y0, y1 = (float(v) for v in parts[-4:])
            if x0 - 1e-6 <= head.x <= x1 + 1e-6 and y0 - 1e-6 <= head.y <= y1 + 1e-6:
                return r
    return next((r for r in rows if r.get("key", "").endswith("|mesh")), rows[0])


def _row_depth(row: dict, h: float) -> tuple[float, float, float]:
    """(d, equivalent Ø, bar spacing in layer 1) of a strip row's bars."""
    area = moment = 0.0
    phis, counts, gap = [], [], 150.0
    for layer in row.get("bar_layers") or []:
        a = layer["as_mm2_per_m"]
        area += a
        moment += a * layer["from_face_mm"]
        for b in layer["bars"]:
            phis.append(b["diameter_mm"])
            counts.append(1000 / b["spacing_mm"])
        if layer["layer"] == 1:
            gap = min(b["spacing_mm"] for b in layer["bars"]) / (2 if len(layer["bars"]) > 1 else 1)
    d = h - (moment / area if area else 60.0)
    phi_eq = sum(c * f * f for c, f in zip(counts, phis, strict=True)) / max(
        sum(c * f for c, f in zip(counts, phis, strict=True)), 1e-9
    )
    return d, phi_eq, gap


def _drawn_layers(head: Head, layer_key: str) -> tuple[float, float, float, float] | None:
    """(d, equivalent Ø, bar spacing in layer 1, mm²/m) of a slab face and direction at the pile, from the
    bars drawn there."""
    face, direction = layer_key.split("_")
    h = head.host.data["thickness_mm"]
    for lay in slab_layers_at(head.host.data, head.x, head.y):
        if lay["face"] != face or lay["along"].lower() != direction:
            continue
        area = moment = 0.0
        phis, counts, gap = [], [], 150.0
        for layer in lay["layers"]:
            a = layer["as_mm2_per_m"]
            from_face = layer["above_soffit_mm"] if face == "bottom" else h - layer["above_soffit_mm"]
            area += a
            moment += a * from_face
            for b in layer["bars"]:
                phis.append(b["diameter_mm"])
                counts.append(1000 / b["spacing_mm"])
            if layer["layer"] == 1:
                gap = min(b["spacing_mm"] for b in layer["bars"]) / (2 if len(layer["bars"]) > 1 else 1)
        if not area:
            return None
        phi = sum(c * f * f for c, f in zip(counts, phis, strict=True)) / sum(
            c * f for c, f in zip(counts, phis, strict=True)
        )
        return h - moment / area, phi, gap, area
    return None


def _layer_words(key: str) -> str:
    face, direction = key.split("_")
    return f"{face} bars along {direction.upper()}"


def _row_label(row: dict) -> str:
    st = row.get("station") or []
    if row.get("strip") in ("column", "field") and len(st) == 2:
        return f"{row['strip']} strip, station {st[0]:g} to {st[1]:g} m"
    return (
        "zone " + row["key"].split("|zone|")[1].replace("|", " ")
        if "|zone|" in row.get("key", "")
        else "basic mesh"
    )


def band_width(head: Head) -> float:
    """mm over which a slab bar lost at the pile counts: the column strip."""
    sd = head.host.result.get("strip_design") or {}
    return (sd.get("column_width_m") or 2.2) * 1e3


def slab_check(
    ctx: Ctx, head: Head, layer_key: str, as_change: float, spacing: float | None = None
) -> dict | None:
    """The slab's strip (or zone) row that sets ``layer_key`` at the pile, designed again with its bars
    per metre changed by ``as_change`` (mm²/m) and, if given, the bars ``spacing`` apart (mm) at the pile:
    MRd with N (strip_mrd) and the QP crack width (7.3.4) as in the slab design."""
    row = _strip_row(head, layer_key)
    if row is None:
        return None
    res = head.host.result
    h = res.get("thickness_mm") or head.host.data["thickness_mm"]
    conc = concrete(res.get("concrete") or ctx.settings.materials.concrete)
    pf = ctx.settings.partial_factors
    fcd = pf.alpha_cc * conc.fck / pf.gamma_c
    e_eff = conc.ecm / (1 + ctx.settings.cracking.creep_coefficient)
    face = layer_key.split("_")[0]
    cover = res.get(f"cover_{face}_mm") or 50.0
    d, phi, gap = _row_depth(row, h)
    as0 = row["as_mm2_per_m"]
    drawn = _drawn_layers(head, layer_key)
    differs = drawn is not None and abs(drawn[3] - as0) > 0.05 * as0
    as1 = max(as0 + as_change, 0.0)
    n = row.get("N_kN_per_m") or 0.0
    m = abs(row.get("M_kNm_per_m") or 0.0)
    qp = row.get("qp") or {}
    limit = row.get("wk_limit_mm") or 0.2

    def state(area: float, s: float) -> dict:
        mrd = strip_mrd(area, d, h, n, fcd, ctx.fyd) if area > 0 else 0.0
        wk = None
        if qp and area > 0 and abs(qp.get("M_kNm_per_m") or 0.0) > 0.5:
            wk = float(
                crack_widths(
                    np.array([abs(qp.get("M_kNm_per_m") or 0.0)]),
                    np.array([qp.get("N_kN_per_m") or 0.0]),
                    area,
                    phi,
                    s,
                    h,
                    d,
                    cover,
                    conc,
                    e_eff,
                )[0]
            )
        ratio = m / mrd if mrd > 0 else (0.0 if m < 0.5 else None)
        return {
            "as_mm2_per_m": round(area),
            "MRd_kNm_per_m": round(mrd, 1),
            "utilisation": _num(ratio),
            "wk_mm": _num(wk),
            "spacing_mm": round(s),
        }

    before, after = state(as0, gap), state(as1, spacing or gap)
    ok = (
        after["utilisation"] is not None
        and after["utilisation"] <= 1.0 + 1e-9
        and (after["wk_mm"] is None or after["wk_mm"] <= limit + 1e-9)
    )
    return {
        "element": head.host.element,
        "what": f"{head.host.element} {_layer_words(layer_key)} at the pile ({_row_label(row)})",
        "M_kNm_per_m": round(m, 1),
        "N_kN_per_m": round(n, 1),
        "combination": row.get("combination"),
        "qp": qp,
        "wk_limit_mm": limit,
        "d_mm": round(d),
        "before": before,
        "after": after,
        "passes": ok,
        "note": (
            f"The bars drawn at the pile give {drawn[3]:.0f} mm²/m, the design row {as0:.0f} mm²/m: checked with the "
            "design row's."
            if differs
            else None
        ),
    }


def _beam_station(head: Head) -> float:
    along, _ = _beam_axes(head.host.data)
    return head.y if along == "Y" else head.x


def _beam_loads(head: Head) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """N, Mv, Mh (kN, kNm) at the pile's station: the largest and smallest vertical moment there with the
    governing axial force and the largest horizontal moment of the beam."""
    res = head.host.result
    s = _beam_station(head)
    prof = res.get("profile") or []
    near = min(prof, key=lambda p: abs(p["s"] - s)) if prof else {}
    bend = res.get("bending") or {}
    n = (bend.get("governing") or {}).get("N_kN", 0.0)
    ext = (bend.get("extremes") or {}).get("Mh") or {}
    mh = max(abs(ext.get("max", 0.0)), abs(ext.get("min", 0.0)))
    return np.full(2, n), np.array([near.get("Mv_max", 0.0), near.get("Mv_min", 0.0)]), np.full(2, mh)


def _beam_section(ctx: Ctx, head: Head, bars: list[dict]) -> RectSection:
    d = head.host.data
    conc = concrete(head.host.result.get("concrete") or ctx.settings.materials.concrete)
    pf = ctx.settings.partial_factors
    fyk = REINFORCEMENT_GRADES[ctx.settings.reinforcement.grade]
    return RectSection(
        d["width_mm"],
        d["depth_mm"],
        Bars(
            np.array([b["y_mm"] for b in bars], float),
            np.array([b["z_mm"] for b in bars], float),
            np.array([math.pi * b["diameter_mm"] ** 2 / 4 for b in bars], float),
        ),
        ConcreteLaw(conc.fck, pf.gamma_c, pf.alpha_cc),
        SteelLaw(fyk, pf.gamma_s),
        deduct=pf.deduct_bar_area,
    )


def beam_check(ctx: Ctx, head: Head, bars_after: list[dict], words: str = "") -> dict:
    """The beam at the pile in N + M (EC2 5.8.9(4)) with its longitudinal bars as designed and as changed."""
    n, mv, mh = _beam_loads(head)
    before = float(_beam_section(ctx, head, head.host.data["bars"]).utilisation(n, mv, mh).max())
    after = (
        float(_beam_section(ctx, head, bars_after).utilisation(n, mv, mh).max()) if bars_after else math.inf
    )
    return {
        "element": head.host.element,
        "what": f"{head.host.element} in N + M at {_beam_station(head):g} m, over the pile"
        + (f"; {words}" if words else ""),
        "N_kN": round(float(n[0]), 1),
        "Mv_kNm": [round(float(v), 1) for v in mv],
        "Mh_kNm": round(float(mh[0]), 1),
        "before": {"utilisation": _num(before), "bars": len(head.host.data["bars"])},
        "after": {"utilisation": _num(after), "bars": len(bars_after)},
        "passes": after <= 1.0 + 1e-9,
    }


def _pile_result(ctx: Ctx, head: Head) -> dict | None:
    if head.part == "infill":
        wall = next((w for w in ctx.results.get("combi_walls") or [] if w["element"] == head.pile), None)
        return (wall or {}).get("infill")
    return next((p for p in ctx.results.get("piles") or [] if p["element"] == head.pile), None)


class _BarSection(CircularSection):
    """A circular section with bars at their own places (some taken out), checked in every direction."""

    def __init__(self, diameter, bars_xy, concrete_law, steel_law, deduct):
        super().__init__(diameter, (Ring(1, 1.0, 0.0),), concrete_law, steel_law, deduct=deduct)
        object.__setattr__(self, "_xy", bars_xy)

    @property
    def area_steel(self) -> float:
        return float(self._xy[:, 2].sum())

    @property
    def rotations(self) -> tuple[float, ...]:
        return tuple(np.radians(np.arange(0, 360, 15)))

    def _bars(self, rotation: float):
        u = self._xy[:, 0] * math.cos(rotation) + self._xy[:, 1] * math.sin(rotation)
        return self.diameter / 2 - u, self._xy[:, 2]


def pile_check(
    ctx: Ctx, head: Head, inward: float = 0.0, drop: set[str] | None = None, words: str = ""
) -> dict | None:
    """The pile head in N + M (the head zone's governing sets) with its bars ``inward`` mm further in, or
    with the ``drop`` bars taken out."""
    pr = _pile_result(ctx, head)
    el = ctx.section.elements.get(head.pile)
    if pr is None or el is None:
        return None
    el = with_project_grades(el, ctx.settings.materials, ctx.settings.durability)
    uls = ((pr.get("governing_sets") or [{}])[0]).get("uls") or []
    if not uls:
        return None
    nn = np.array([s["N_kN"] for s in uls], float)
    mm = np.array([math.hypot(s.get("M2_kNm") or 0.0, s.get("M3_kNm") or 0.0) for s in uls], float)
    pf = ctx.settings.partial_factors
    laws = (
        ConcreteLaw(concrete(el.concrete).fck, pf.gamma_c, pf.alpha_cc),
        SteelLaw(REINFORCEMENT_GRADES[ctx.settings.reinforcement.grade], pf.gamma_s),
    )

    def util(e: float, gone: set[str] | None) -> float:
        if gone:
            xy = []
            for k, r in enumerate(head.rings):
                for i in range(int(r["count"])):
                    if f"{k}:{i}" in gone:
                        continue
                    a = 2 * math.pi * i / r["count"]
                    xy.append(
                        [
                            (r["radius_mm"] - e) * math.cos(a),
                            (r["radius_mm"] - e) * math.sin(a),
                            math.pi * r["diameter_mm"] ** 2 / 4,
                        ]
                    )
            sec = _BarSection(head.diameter, np.array(xy).reshape(-1, 3), *laws, pf.deduct_bar_area)
        else:
            sec = CircularSection(
                head.diameter,
                tuple(Ring(r["count"], r["diameter_mm"], r["radius_mm"] - e) for r in head.rings),
                *laws,
                deduct=pf.deduct_bar_area,
            )
        return float(sec.utilisation(nn, mm).max())

    before, after = util(0.0, None), util(inward, drop)
    return {
        "element": head.pile,
        "what": f"{head.pile} head in N + M (the head zone's governing sets)"
        + (f"; {words}" if words else ""),
        "before": {"utilisation": _num(before)},
        "after": {"utilisation": _num(after)},
        "passes": after <= 1.0 + 1e-9,
    }


# --- Solutions --------------------------------------------------------------------------------------


def _count(c: list[Hit]) -> dict:
    return {
        "clash": sum(1 for x in c if x.kind == "clash"),
        "tight": sum(1 for x in c if x.kind == "tight"),
        "pairs": len(c),
        "bars": len({(x.b, x.j) for x in c}),
    }


def _pitch(head: Head) -> float:
    n = max(int(r["count"]) for r in head.rings)
    pitch = 360.0 / n
    if any(int(r["count"]) * 2 == n for r in head.rings):  # a half row: the pattern repeats every two bars
        pitch *= 2
    return pitch


def best_turn(
    ctx: Ctx, head: Head, inward: float = 0.0, step: float = ROTATE_STEP
) -> tuple[float, list[Hit]]:
    """The turn of the cage (degrees, within ± half the bar pitch) with the fewest pile bar clashes."""
    pitch = _pitch(head)
    best: tuple[float, float, list[Hit]] | None = None
    for t in np.arange(0.0, pitch, step):
        c = pile_hits(conflicts(head, ctx.rule, ctx.dg, pile_bars(head, float(t), inward), with_punch=False))
        score = sum(2 if x.kind == "clash" else 1 for x in c)
        turn = float(t) if t <= pitch / 2 else float(t - pitch)
        if best is None or score < best[0] or (score == best[0] and abs(turn) < abs(best[1])):
            best = (score, turn, c)
    return round(best[1], 2), best[2]


def _moved_leg(head: Head, leg: Leg, ds: float) -> Leg:
    along, _ = _beam_axes(head.host.data)
    return replace(leg, y=leg.y + ds) if along == "Y" else replace(leg, x=leg.x + ds)


def shift_bars(
    ctx: Ctx, head: Head, bars: np.ndarray, hit: list[Hit], only: set[str] | None = None
) -> tuple[dict[int, float], list[int]]:
    """Move each clashing horizontal bar (and each beam link with a clashing leg) sideways to the nearest
    clear place: {bar: shift mm}, and the ones that cannot move far enough."""
    moved: dict[int, float] = {}
    stuck: list[int] = []
    at = {j: h.at for j, h in enumerate(head.hbars)}
    targets = sorted({x.j for x in hit if x.b == "h"}, key=lambda j: head.hbars[j].at)
    links = {head.legs[x.j].link for x in hit if x.b == "l"}
    targets += [
        j for j, h in enumerate(head.hbars) if h.link is not None and h.link in links and j not in targets
    ]
    if only is not None:
        targets = [j for j in targets if head.hbars[j].kind in only]
    for j in targets:
        h = head.hbars[j]
        room = 150.0 if h.kind == "longitudinal" else (h.spacing or 200.0) / 2
        near = [
            k
            for k, o in enumerate(head.hbars)
            if k != j and o.along == h.along and abs(o.z - h.z) < max(o.phi, h.phi) / 1e3
        ]
        found = None
        for step in np.arange(SHIFT_STEP, room + 1e-9, SHIFT_STEP):
            for sign in (1, -1):
                pos = h.at + sign * step / 1e3
                if conflicts(head, ctx.rule, ctx.dg, bars, [replace(h, at=pos)], [], [], with_punch=False):
                    continue
                if h.link is not None:
                    legs = [_moved_leg(head, g, pos - h.at) for g in head.legs if g.link == h.link]
                    if conflicts(head, ctx.rule, ctx.dg, bars, [], legs, [], with_punch=False):
                        continue
                if any(
                    abs(at[k] - pos) * 1e3 - (head.hbars[k].phi + h.phi) / 2
                    < float(_ec2_gap(ctx.dg, np.array(h.phi), np.array(head.hbars[k].phi)))
                    for k in near
                ):
                    continue
                if h.kind == "longitudinal":
                    half = (
                        head.host.data["width_mm"] / 2
                        - head.host.data["cover_mm"]
                        - h.phi / 2
                        - ((head.host.data.get("links") or {}).get("diameter_mm") or 0)
                    )
                    if abs(pos - head.host.data["centre_m"]) * 1e3 > half:
                        continue
                found = sign * float(step)
                break
            if found is not None:
                break
        if found is None:
            stuck.append(j)
        else:
            moved[j] = found
            at[j] = h.at + found / 1e3
    return moved, stuck


def _after_shift(head: Head, moved: dict[int, float]) -> tuple[list[HBar], list[Leg]]:
    hb = [replace(h, at=h.at + moved.get(j, 0.0) / 1e3) for j, h in enumerate(head.hbars)]
    by_link = {h.link: moved[j] / 1e3 for j, h in enumerate(head.hbars) if j in moved and h.link is not None}
    return hb, [_moved_leg(head, g, by_link.get(g.link, 0.0)) for g in head.legs]


def _widest(hb: list[HBar], j: int) -> float:
    """Widest centre-to-centre gap (mm) either side of bar j among the bars of its layer."""
    same = sorted(
        {o.at for o in hb if o.layer == hb[j].layer and o.along == hb[j].along and abs(o.z - hb[j].z) < 1e-3}
    )
    i = same.index(hb[j].at)
    return max(
        (same[i] - same[i - 1]) * 1e3 if i else 0.0,
        (same[i + 1] - same[i]) * 1e3 if i < len(same) - 1 else 0.0,
    )


def _slab_spacing_limit(head: Head, key: str) -> float:
    """EN 1992-1-1 9.3.1.1(3): 2h ≤ 250 mm for the main bars (along the strips) where the moments are
    largest, 3h ≤ 400 mm for the others."""
    t = head.host.data["thickness_mm"]
    sd = head.host.result.get("strip_design") or {}
    main = key.endswith("_" + (sd.get("along") or "X").lower())
    return min(2 * t, 250.0) if main else min(3 * t, 400.0)


def _shift_checks(ctx: Ctx, head: Head, moved: dict[int, float], hb: list[HBar]) -> list[dict]:
    checks: list[dict] = []
    by: dict[str, list[int]] = {}
    for j in moved:
        h = head.hbars[j]
        key = h.layer.split("|")[1] if head.host.kind == "slab" else h.layer
        by.setdefault(key, []).append(j)
    most = lambda js: max(abs(moved[j]) for j in js)  # noqa: E731
    for key, js in by.items():
        h = head.hbars[js[0]]
        wide = max(_widest(hb, j) for j in js)
        if head.host.kind == "slab":
            c = slab_check(ctx, head, key, 0.0, spacing=wide)
            if c is not None:
                lim = _slab_spacing_limit(head, key)
                c["what"] += (
                    f"; {len(js)} bar(s) moved up to {most(js):g} mm, widest gap {wide:.0f} mm (limit {lim:g} mm, 9.3.1.1)"
                )
                c["spacing_limit_mm"] = lim
                c["passes"] = c["passes"] and wide <= lim + 1e-6
                checks.append(c)
            continue
        if h.kind == "longitudinal":
            continue
        base = h.spacing or wide
        if h.kind == "link leg":
            u0 = (head.host.result.get("shear") or {}).get("utilisation") or 0.0
            lim = (head.host.result.get("shear") or {}).get("max_spacing_mm")
            what = f"{head.host.element} shear links at the pile: {len(js)} moved up to {most(js):g} mm"
        else:
            u0 = (head.host.result.get("transverse") or {}).get("utilisation") or 0.0
            lim = None
            what = f"{head.host.element} transverse bars at the pile: {len(js)} moved up to {most(js):g} mm"
        u1 = u0 * max(wide, base) / base
        checks.append(
            {
                "element": head.host.element,
                "what": what
                + f", widest pitch {wide:.0f} mm (designed {base:g} mm; resistance per metre in proportion)",
                "before": {"utilisation": _num(u0), "pitch_mm": base},
                "after": {"utilisation": _num(u1), "pitch_mm": round(wide)},
                "spacing_limit_mm": lim,
                "passes": u1 <= 1.0 + 1e-9 and (lim is None or wide <= lim + 1e-6),
            }
        )
    longs = [j for j in moved if head.hbars[j].kind == "longitudinal"]
    if longs:
        bars = [dict(b) for b in head.host.data["bars"]]
        for j in longs:
            n = int(head.hbars[j].id.split("|")[2])
            bars[n]["y_mm"] = round(bars[n]["y_mm"] + moved[j], 1)
        checks.append(
            beam_check(ctx, head, bars, f"{len(longs)} bar(s) moved sideways up to {most(longs):g} mm")
        )
    return checks


def _cage_r(head: Head) -> float:
    return max(r["radius_mm"] + r["diameter_mm"] / 2 for r in head.rings)


def _cut_len(head: Head, h: HBar) -> float:
    """Length (m) of bar ``h`` inside the pile cage, where it is cut out."""
    r = _cage_r(head) / 1e3 + 0.05
    off = abs(h.at - (head.y if h.along == "X" else head.x))
    return 2 * math.sqrt(max(r * r - off * off, 0.0)) if off < r else 0.1


def removal_checks(
    ctx: Ctx, head: Head, gone: list[int], added: list[dict] | None = None, words: str = "cut"
) -> list[dict]:
    """The slab strip, the beam and its links and transverse bars at the pile designed again without the
    bars ``gone`` (and with the ``added`` trimmers)."""
    added = added or []
    checks: list[dict] = []
    if head.host.kind == "slab":
        by: dict[str, list[int]] = {}
        for j in gone:
            by.setdefault(head.hbars[j].layer.split("|")[1], []).append(j)
        band = band_width(head)
        for key, js in by.items():
            lost = sum(math.pi * head.hbars[j].phi ** 2 / 4 for j in js)
            back = sum(math.pi * t["phi"] ** 2 / 4 for t in added if t["layer"].split("|")[1] == key)
            c = slab_check(ctx, head, key, (back - lost) * 1000 / band)
            if c is not None:
                c["what"] += (
                    f"; {len(js)} bar(s) {words}"
                    + (
                        f", {sum(1 for t in added if t['layer'].split('|')[1] == key)} trimmer(s) added"
                        if added
                        else ""
                    )
                    + f", counted over the {band / 1e3:g} m column strip"
                )
                checks.append(c)
        punch = punching_check(ctx, head, gone, added)
        if punch:
            checks.append(punch)
        return checks
    longs = [j for j in gone if head.hbars[j].kind == "longitudinal"]
    if longs or any(t["layer"].startswith("beam|long") for t in added):
        drop = {int(head.hbars[j].id.split("|")[2]) for j in longs}
        bars = [b for n, b in enumerate(head.host.data["bars"]) if n not in drop]
        mid = head.host.top - head.host.data["depth_mm"] / 2e3
        for t in added:
            if t["layer"].startswith("beam|long"):
                bars.append(
                    {
                        "y_mm": round((t["at"] - head.host.data["centre_m"]) * 1e3, 1),
                        "z_mm": round((t["z"] - mid) * 1e3, 1),
                        "diameter_mm": t["phi"],
                    }
                )
        checks.append(
            beam_check(
                ctx, head, bars, f"{len(longs)} bar(s) {words}" + (" and trimmers added" if added else "")
            )
        )
    for kind, part in (("transverse", "transverse"), ("link leg", "shear")):
        js = [j for j in gone if head.hbars[j].kind == kind]
        if not js:
            continue
        back = sum(1 for t in added if head.hbars[js[0]].layer == t["layer"])
        sp = head.hbars[js[0]].spacing or 200.0
        n_band = max(2 * _cage_r(head) / sp, 1.0)
        u0 = (head.host.result.get(part) or {}).get("utilisation") or 0.0
        left = n_band - len(js) + back
        u1 = u0 * n_band / left if left > 0.05 else None
        name = "transverse bars" if kind == "transverse" else "shear links"
        checks.append(
            {
                "element": head.host.element,
                "what": f"{head.host.element} {name} over the pile: {len(js)} {words}"
                + (f", {back} trimmer(s) beside the cage" if back else "")
                + f" (resistance per metre in proportion to the bars left over the {2 * _cage_r(head):.0f} mm cage)",
                "before": {"utilisation": _num(u0)},
                "after": {"utilisation": _num(u1)},
                "passes": u1 is not None and u1 <= 1.0 + 1e-9,
            }
        )
    return checks


def punching_check(ctx: Ctx, head: Head, gone: list[int], added: list[dict] | None = None) -> dict | None:
    """Punching at the pile with the slab bars taken out: vRd,c ∝ (ρl)^(1/3) (6.47), ρl = √(ρx·ρy) over the
    pile width + 3d each side (6.4.4(1))."""
    res = head.host.result
    p = next(
        (q for q in res.get("punching") or [] if abs(q["x"] - head.x) < 0.05 and abs(q["y"] - head.y) < 0.05),
        None,
    )
    if p is None or not p.get("rho_l"):
        return None
    face = "top" if p.get("direction") == "pile pulls down" else "bottom"
    d = p["d_mm"]
    width = head.diameter + 6 * d
    lost = {"x": 0.0, "y": 0.0}
    for j in gone:
        h = head.hbars[j]
        if h.layer.split("|")[1].startswith(face):
            lost[h.along.lower()] += math.pi * h.phi**2 / 4
    for t in added or []:
        if t["layer"].split("|")[1].startswith(face):
            lost[t["along"].lower()] -= math.pi * t["phi"] ** 2 / 4
    if not any(abs(v) > 1e-6 for v in lost.values()):
        return None
    rho = p["rho_l"]
    drawn = {k: _drawn_layers(head, f"{face}_{k}") for k in ("x", "y")}
    if not all(drawn.values()):
        return None
    # 6.4.4(1): ρl = √(ρx·ρy) ≤ 0.02 from the bars at the pile, before and after.
    r0 = {k: v[3] / (1000 * d) for k, v in drawn.items()}
    r1 = {k: max(r0[k] - lost[k] / (width * d), 1e-6) for k in r0}
    rho_a = min(math.sqrt(r0["x"] * r0["y"]), 0.02)
    rho_b = min(math.sqrt(r1["x"] * r1["y"]), 0.02)
    rho1 = rho * rho_b / rho_a
    u0 = p.get("utilisation_with_links") or p["utilisation"]
    u1 = u0 * (rho_a / rho_b) ** (1 / 3)
    return {
        "element": head.host.element,
        "what": f"Punching at {head.pile} ({face} bars in tension): ρl of the bars there {rho_a * 100:.2f}% → {rho_b * 100:.2f}% "
        f"with {sum(1 for v in lost.values() if v)} direction(s) losing bars over {width / 1e3:.2f} m (pile + 3d each side); "
        "vRd,c ∝ ρl^(1/3), capped at 2%",
        "before": {"utilisation": _num(u0), "rho_l_pct": round(rho * 100, 3)},
        "after": {"utilisation": _num(u1), "rho_l_pct": round(rho1 * 100, 3)},
        "passes": u1 <= 1.0 + 1e-9,
    }


def cut_solution(ctx: Ctx, head: Head, hit: list[Hit], trim: bool) -> dict:
    """Cut the clashing bars (never links: they are moved) and, with ``trim``, add trimmers beside the cage."""
    cut = sorted({x.j for x in hit if x.b == "h" and head.hbars[x.j].kind != "link leg"})
    trimmers: list[dict] = []
    removed = sum(_weight(head.hbars[j].phi, _cut_len(head, head.hbars[j])) for j in cut)
    added_kg = 0.0
    if trim:
        by: dict[tuple, list[int]] = {}
        for j in cut:
            by.setdefault((head.hbars[j].layer, head.hbars[j].along, round(head.hbars[j].z, 3)), []).append(j)
        for (layer, along, z), js in by.items():
            c_perp = head.y if along == "X" else head.x
            keep = [
                o
                for k, o in enumerate(head.hbars)
                if o.layer == layer and o.along == along and abs(o.z - z) < 1e-3 and k not in cut
            ]
            phi = max(head.hbars[j].phi for j in js)
            length = 2 * _cage_r(head) / 1e3 + 2 * ctx.lap * phi / 1e3
            n = len(js)
            for sign, count in ((-1, n - n // 2), (1, n // 2)):
                # Beside the cage, never inside it: the bars left that pass inside the cage are not neighbours.
                side = [o for o in keep if (o.at - c_perp) * sign > (_cage_r(head) + phi) / 1e3]
                nb = min(side, key=lambda o: abs(o.at - c_perp)) if side else None
                base = nb.at if nb else c_perp + sign * (_cage_r(head) + phi) / 1e3
                step = ((nb.phi if nb else phi) + phi) / 2e3
                for k in range(count):
                    # Bundled against the bar left, the next one against that: pairs and threes (8.9.1).
                    at = (
                        base + sign * (step + k * phi / 1e3)
                        if k < 2
                        else (nb.at if nb else base) - sign * step
                    )
                    trimmers.append(
                        {
                            "id": f"t|{layer}|{along}|{sign}|{k}",
                            "along": along,
                            "at": round(at, 4),
                            "z": z,
                            "phi": phi,
                            "layer": layer,
                            "length_m": round(length, 2),
                            "lo": round(((head.x if along == "X" else head.y) - length / 2), 4),
                            "hi": round(((head.x if along == "X" else head.y) + length / 2), 4),
                        }
                    )
                    added_kg += _weight(phi, length)
    checks = removal_checks(ctx, head, cut, trimmers)
    bars = pile_bars(head)
    link_hits = [x for x in hit if x.b == "l" or (x.b == "h" and head.hbars[x.j].kind == "link leg")]
    moved: dict[int, float] = {}
    left: list[Hit] = []
    if link_hits:
        moved, _stuck = shift_bars(ctx, head, bars, link_hits, only={"link leg"})
        hb, legs = _after_shift(head, moved)
        left = [
            x
            for x in pile_hits(conflicts(head, ctx.rule, ctx.dg, bars, hb, legs, with_punch=False))
            if x.b == "l" or hb[x.j].kind == "link leg"
        ]
        checks += _shift_checks(ctx, head, moved, hb)
    return {
        "cut": cut,
        "trimmers": trimmers,
        "checks": checks,
        "delta_kg": added_kg - removed,
        "left": left,
        "moved": moved,
    }


def _volume(ctx: Ctx, head: Head, where: str) -> float | None:
    if where == "host":
        st = head.host.result.get("steel") or {}
        kg = st.get("total_kg") or ((st.get("total_t") or 0) * 1000)
        return kg / st["kg_per_m3"] if kg and st.get("kg_per_m3") else None
    pr = _pile_result(ctx, head)
    v = ((pr or {}).get("steel") or {}).get("concrete_m3")
    return v * ((pr or {}).get("count") or 1) if v else None


def solutions(ctx: Ctx, head: Head, hit: list[Hit]) -> list[dict]:
    """Every way out of the pile bar clashes at a head, each designed again."""
    out: list[dict] = []
    base = pile_bars(head)

    def pack(
        sid: str,
        title: str,
        how: str,
        left: list[Hit],
        checks: list[dict],
        delta: float,
        change: dict,
        where: str = "host",
    ) -> dict:
        checks = [c for c in checks if c]
        vol = _volume(ctx, head, where)
        return {
            "id": sid,
            "title": title,
            "how": how,
            "passes": not left and all(c["passes"] for c in checks),
            "left": _count(left),
            "left_pairs": [list(x) for x in left[:300]],
            "checks": checks,
            "delta_kg": round(delta, 1),
            "delta_kg_m3_per_head": None if not vol else round(delta / vol, 3),
            "change": change,
            "element": head.host.element if where == "host" else head.pile,
        }

    turn, left = best_turn(ctx, head)
    words = f"{abs(turn):g}° {'anticlockwise' if turn >= 0 else 'clockwise'}"
    out.append(
        pack(
            "rotate",
            f"Turn the pile cage {words}",
            "Turn the whole cage about the pile's axis so its bars fall between the bars above. The pile's N–M "
            "check is the same whichever way the cage faces (checked all round), so it needs no new design.",
            left,
            [],
            0.0,
            {"rotate_deg": turn},
            "pile",
        )
    )
    moved, stuck = shift_bars(ctx, head, base, hit)
    hb, legs = _after_shift(head, moved)
    out.append(
        pack(
            "shift",
            "Move the clashing bars sideways",
            f"Move {len(moved)} bar(s) or link(s) sideways round the pile bars (a local crank, or set out so on the drawing)."
            + (f" {len(stuck)} cannot move far enough." if stuck else ""),
            pile_hits(conflicts(head, ctx.rule, ctx.dg, base, hb, legs, with_punch=False)),
            _shift_checks(ctx, head, moved, hb),
            0.0,
            {"shift_mm": {str(j): v for j, v in moved.items()}},
        )
    )
    if left and turn:
        turned = pile_bars(head, turn)
        moved2, _ = shift_bars(ctx, head, turned, left)
        hb2, legs2 = _after_shift(head, moved2)
        out.append(
            pack(
                "rotate_shift",
                f"Turn the cage {words} and move what still clashes",
                f"Turn the pile cage first, then move the {len(moved2)} bar(s) or link(s) that still clash sideways.",
                pile_hits(conflicts(head, ctx.rule, ctx.dg, turned, hb2, legs2, with_punch=False)),
                _shift_checks(ctx, head, moved2, hb2),
                0.0,
                {"rotate_deg": turn, "shift_mm": {str(j): v for j, v in moved2.items()}},
            )
        )
    best = None
    for e in np.arange(25.0, CRANK_MAX + 1e-9, 25.0):
        t, lft = best_turn(ctx, head, float(e), step=1.0)
        if best is None or len(lft) < len(best[2]):
            best = (float(e), t, lft)
        if not lft:
            break
    if best is not None:
        e, t, lft = best
        crank_len = e * CRANK_SLOPE / 1e3
        extra = sum(
            _weight(r["diameter_mm"], crank_len * (math.sqrt(1 + 1 / CRANK_SLOPE**2) - 1)) * r["count"]
            for r in head.rings
        )
        out.append(
            pack(
                "crank",
                f"Crank the pile bars {e:g} mm inwards" + (f" and turn the cage {abs(t):g}°" if t else ""),
                f"Crank every pile bar {e:g} mm towards the centre (1 in {CRANK_SLOPE:g}, over {crank_len:.2f} m below the "
                f"soffit) so the cage is narrower inside {head.host.element}; the pile head is designed again with the smaller circle.",
                lft,
                [pile_check(ctx, head, e, words=f"every bar {e:g} mm further in")],
                extra,
                {"crank_mm": e, "rotate_deg": t},
                "pile",
            )
        )
    for trim in (True, False):
        c = cut_solution(ctx, head, hit, trim)
        n = len(c["cut"])
        if not n:
            continue
        how = (
            f"Cut {n} bar(s) at the pile cage and add {len(c['trimmers'])} trimmer(s) of the same size beside it, bundled "
            f"with the first bar left each side (EN 1992-1-1 8.9) and lapped {ctx.lap:g}Ø past the cage."
            if trim
            else f"Cut {n} bar(s) at the pile cage and add nothing."
        )
        if c["moved"]:
            how += f" Move {len(c['moved'])} link(s) with clashing legs."
        out.append(
            pack(
                "cut_trim" if trim else "cut",
                "Cut the clashing bars and add trimmers" if trim else "Cut the clashing bars",
                how,
                c["left"],
                c["checks"],
                c["delta_kg"],
                {
                    "cut": c["cut"],
                    "trimmers": c["trimmers"],
                    "shift_mm": {str(j): v for j, v in c["moved"].items()},
                },
            )
        )
    order = ["rotate", "shift", "rotate_shift", "crank", "cut_trim", "cut"]
    passing = [s for s in out if s["passes"]]
    rec = (
        min(passing, key=lambda s: (max(s["delta_kg"], 0.0), order.index(s["id"])))
        if passing
        else min(out, key=lambda s: (s["left"]["pairs"], order.index(s["id"])))
    )
    for s in out:
        s["recommended"] = s is rec
    return out


def _radius_from_face(head: Head, g: Leg) -> float:
    return math.hypot(g.x - head.x, g.y - head.y) * 1e3 - head.diameter / 2


def punching_layout(ctx: Ctx, head: Head, legs: list[Leg]) -> dict:
    """EN 1992-1-1 9.4.3 for a layout of punching legs: the first perimeter 0.3d to 0.5d from the pile face,
    perimeters at most 0.75d apart, legs at most 1.5d apart round a perimeter inside 2d (2d beyond), and
    Asw on each perimeter."""
    info = head.punch_info or {}
    d = info.get("d_mm") or 600
    per: dict[int, list[Leg]] = {}
    for g in legs:
        per.setdefault(g.link, []).append(g)
    rows = []
    ok = True
    prev = None
    for k in sorted(per):
        gs = sorted(per[k], key=lambda g: math.atan2(g.y - head.y, g.x - head.x))
        a = [_radius_from_face(head, g) for g in gs]
        ang = [math.atan2(g.y - head.y, g.x - head.x) for g in gs]
        r_mid = sum(a) / len(a) + head.diameter / 2
        tang = (
            max(((ang[(i + 1) % len(ang)] - ang[i]) % (2 * math.pi)) * r_mid for i in range(len(ang)))
            if len(ang) > 1
            else 0
        )
        t_lim = (1.5 if min(a) <= 2 * d else 2.0) * d
        radial = None
        if prev is not None:
            # Each leg against the nearest leg (by angle) of the perimeter inside it.
            radial = max(
                a[n] - prev[1][int(np.argmin(np.abs(np.angle(np.exp(1j * (np.array(prev[0]) - ang[n]))))))]
                for n in range(len(a))
            )
        area = len(gs) * math.pi * info.get("phi", 12) ** 2 / 4
        good = tang <= t_lim + 1 and area >= (info.get("asw_mm2_per_perimeter") or 0) - 1
        if k == 0:
            good = good and 0.3 * d - 1 <= min(a) and max(a) <= 0.5 * d + 2
        if radial is not None:
            good = good and radial <= 0.75 * d + 2  # the design's radii are rounded to 1 mm
        ok = ok and good
        rows.append(
            {
                "perimeter": k + 1,
                "legs": len(gs),
                "from_face_mm": [round(min(a)), round(max(a))],
                "tangential_mm": round(tang),
                "tangential_limit_mm": round(t_lim),
                "radial_mm": None if radial is None else round(radial),
                "radial_limit_mm": round(0.75 * d),
                "asw_mm2": round(area),
                "asw_needed_mm2": info.get("asw_mm2_per_perimeter"),
                "passes": good,
            }
        )
        prev = (ang, a)
    return {"what": f"Punching links round {head.pile} (EN 1992-1-1 9.4.3)", "rows": rows, "passes": ok}


def _clear_points(
    ctx: Ctx, head: Head, bars: np.ndarray, px: np.ndarray, py: np.ndarray, phi: float
) -> np.ndarray:
    """Which of the points (m) a punching leg of Ø ``phi`` can stand on: clear of the slab bars and the pile
    bars' L legs."""
    ok = np.ones(len(px), bool)
    if head.hbars:
        H = _harr(head.hbars)
        across = np.where(H["x"][None, :], py[:, None], px[:, None])
        gap = np.abs(across - H["at"][None, :]) * 1e3 - (phi + H["phi"][None, :]) / 2
        ok &= ~(gap < _limit(ctx.rule, ctx.dg, np.array(phi), H["phi"][None, :])).any(axis=1)
    pl = pile_legs(head, bars)
    if len(pl):
        x0, y0, x1, y1 = (pl[:, k][None, :] for k in range(4))
        dx, dy = x1 - x0, y1 - y0
        qx, qy = px[:, None], py[:, None]
        t = np.clip(((qx - x0) * dx + (qy - y0) * dy) / np.maximum(dx * dx + dy * dy, 1e-12), 0, 1)
        gap = np.hypot(qx - (x0 + t * dx), qy - (y0 + t * dy)) * 1e3 - (phi + pl[:, 4][None, :]) / 2
        ok &= ~(gap < _limit(ctx.rule, ctx.dg, np.array(phi), pl[:, 4][None, :])).any(axis=1)
    return ok


def _ang_gap(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(np.angle(np.exp(1j * (a - b))))


def _snap_pass(
    ctx: Ctx,
    head: Head,
    bars: np.ndarray,
    legs: list[Leg],
    todo: list[int],
    moved: dict,
    dr: np.ndarray,
    dt: np.ndarray,
    d: float,
    R: float,
    polar,
) -> None:
    for i in todo:
        g = legs[i]
        f0, a0 = polar(g)
        r0 = f0 + R
        f = f0 + dr
        a = a0 + dt / r0
        px, py = head.x + (f + R) / 1e3 * np.cos(a), head.y + (f + R) / 1e3 * np.sin(a)
        ok = _clear_points(ctx, head, bars, px, py, g.phi)
        others = [(k, o) for k, o in enumerate(legs) if k != i]
        if others:
            ox = np.array([o.x for _, o in others])[None, :]
            oy = np.array([o.y for _, o in others])[None, :]
            op = np.array([o.phi for _, o in others])[None, :]
            ok &= ~((np.hypot(px[:, None] - ox, py[:, None] - oy) * 1e3 - (op + g.phi) / 2) < PUNCH_GAP).any(
                axis=1
            )
        if g.link == 0:
            ok &= (f >= 0.3 * d - 1) & (f <= 0.5 * d + 2)
        for side in (-1, 1):
            ring = [polar(o) for _, o in others if o.link == g.link + side]
            if ring:
                rf, ra = np.array([x[0] for x in ring]), np.array([x[1] for x in ring])
                near = rf[np.argmin(_ang_gap(a[:, None], ra[None, :]), axis=1)]
                ok &= (near - f if side > 0 else f - near) <= 0.75 * d + 2
        same = [polar(o) for _, o in others if o.link == g.link]
        if same:
            sa = np.array([x[1] for x in same])
            t_lim = (1.5 if f0 <= 2 * d else 2.0) * d
            ahead = np.min((sa[None, :] - a[:, None]) % (2 * math.pi), axis=1)
            behind = np.min((a[:, None] - sa[None, :]) % (2 * math.pi), axis=1)
            ok &= (ahead * (f + R) <= t_lim) & (behind * (f + R) <= t_lim)
        pick = np.nonzero(ok)[0]
        if len(pick):
            k = int(pick[0])
            legs[i] = replace(g, x=float(px[k]), y=float(py[k]))
            m = moved.get(i, [0, 0])
            moved[i] = [m[0] + round(float(dr[k])), m[1] + round(float(dt[k]))]


def snap_punching(ctx: Ctx, head: Head, bars: np.ndarray) -> dict:
    """Set the clashing punching legs out in the nearest clear mesh opening (as fixed on site), then check
    the layout again against 9.4.3. A leg may move round its perimeter and in or out a little, as long as
    it stays within 0.75d of the perimeters either side, 1.5d (2d) of its neighbours round the perimeter
    and, on the first perimeter, 0.3d to 0.5d from the pile's face."""
    legs = list(head.punch)
    d = (head.punch_info or {}).get("d_mm") or 600
    R = head.diameter / 2
    moved: dict[int, list[float]] = {}
    dr, dt = np.meshgrid(np.arange(-0.15 * d, 0.15 * d + 1e-9, 5.0), np.arange(-0.5 * d, 0.5 * d + 1e-9, 5.0))
    dr, dt = dr.ravel(), dt.ravel()
    order = np.argsort(np.abs(dt) + 2 * np.abs(dr), kind="stable")
    dr, dt = dr[order], dt[order]

    def polar(g: Leg) -> tuple[float, float]:
        return math.hypot(g.x - head.x, g.y - head.y) * 1e3 - R, math.atan2(g.y - head.y, g.x - head.x)

    for _ in range(3):  # a leg set out may free the place another one needs
        todo = sorted(
            {x.i for x in punch_hits(conflicts(head, ctx.rule, ctx.dg, bars, head.hbars, [], legs))}
        )
        if not todo:
            break
        _snap_pass(ctx, head, bars, legs, todo, moved, dr, dt, d, R, polar)
    left = punch_hits(conflicts(head, ctx.rule, ctx.dg, bars, head.hbars, [], legs))
    layout = punching_layout(ctx, head, legs)
    return {
        "id": "snap",
        "title": "Set the punching links out in the mesh openings",
        "how": f"Place {len(moved)} punching leg(s) in the nearest clear opening of the mesh (round the perimeter, and in or "
        f"out by up to 0.15d where the spacings allow), hooked over the outer bars; the layout is checked again (9.4.3).",
        "passes": not left and layout["passes"],
        "left": _count(left),
        "left_pairs": [list(x) for x in left[:300]],
        "moved": {str(i): v for i, v in moved.items()},
        "legs_after": [[round((g.x - head.x) * 1e3, 1), round((g.y - head.y) * 1e3, 1)] for g in legs],
        "checks": [],
        "layout": layout,
        "delta_kg": 0.0,
        "recommended": True,
    }


# --- What if bars are taken out -----------------------------------------------------------------------


def whatif_check(ctx: Ctx, head: Head, w: WhatIf) -> dict:
    """The connection checked again with the chosen bars taken out: the slab strip or the beam, its links
    and transverse bars, the punching resistance, and the pile head."""
    ids = {h.id: j for j, h in enumerate(head.hbars)}
    gone = [ids[b] for b in w.bars if b in ids]
    unknown = [b for b in w.bars if b not in ids] + [b for b in w.pile_bars if not _pile_id_ok(head, b)]
    checks = removal_checks(ctx, head, gone, words="taken out")
    drop = {b for b in w.pile_bars if _pile_id_ok(head, b)}
    if drop:
        c = pile_check(ctx, head, 0.0, drop, words=f"{len(drop)} pile bar(s) taken out at the head")
        if c:
            checks.append(c)
    return {
        "id": w.id,
        "group": w.group,
        "head": w.head,
        "bars": w.bars,
        "pile_bars": w.pile_bars,
        "note": w.note,
        "bars_found": len(gone),
        "unknown": unknown,
        "checks": checks,
        "passes": bool(checks) and all(c["passes"] for c in checks),
        "delta_kg": round(-sum(_weight(head.hbars[j].phi, _cut_len(head, head.hbars[j])) for j in gone), 1),
    }


def _pile_id_ok(head: Head, pid: str) -> bool:
    try:
        k, i = (int(v) for v in pid.split(":"))
    except ValueError:
        return False
    return 0 <= k < len(head.rings) and 0 <= i < int(head.rings[k]["count"])


# --- The whole section --------------------------------------------------------------------------------


def _local(head: Head, h: HBar) -> dict:
    ox, oy = (head.y, head.x) if h.along == "X" else (head.x, head.y)
    return {
        "id": h.id,
        "along": h.along,
        "at": round((h.at - ox) * 1e3, 1),
        "z_m": h.z,
        "phi": h.phi,
        "group": h.group,
        "layer": h.layer,
        "kind": h.kind,
        "link": h.link,
        "lo": None if h.lo == -math.inf else round((h.lo - oy) * 1e3, 1),
        "hi": None if h.hi == math.inf else round((h.hi - oy) * 1e3, 1),
    }


def scene(head: Head, hit: list[Hit]) -> dict:
    """What the Clashes tab draws for a head: the pile, the bars round it and the element's levels; plan
    coordinates in mm from the pile's centre, levels in m."""
    return {
        "pile": {
            "element": head.pile,
            "x": head.x,
            "y": head.y,
            "diameter_mm": head.diameter,
            "rings": head.rings,
            "enters_m": round(head.z0, 3),
            "tops_m": head.z1,
            "legs_m": head.legs_m,
        },
        "host": {
            "element": head.host.element,
            "kind": head.host.kind,
            "soffit_m": round(head.host.soffit, 3),
            "top_m": round(head.host.top, 3),
            "top_zone_mm": round(head.top_zone),
            "along": _beam_axes(head.host.data)[0] if head.host.kind == "beam" else None,
            "width_mm": head.host.data.get("width_mm"),
            "centre_mm": None
            if head.host.kind != "beam"
            else round(
                (head.host.data["centre_m"] - (head.x if _beam_axes(head.host.data)[0] == "Y" else head.y))
                * 1e3,
                1,
            ),
        },
        "pile_bars": [
            {
                "id": f"{int(b[3])}:{int(b[4])}",
                "u": round((b[0] - head.x) * 1e3, 1),
                "v": round((b[1] - head.y) * 1e3, 1),
                "phi": b[2],
                "row": int(b[3]),
                "top_m": round(float(b[5]), 3),
            }
            for b in pile_bars(head)
        ],
        "hbars": [_local(head, h) for h in head.hbars],
        "legs": [
            {
                "id": g.id,
                "u": round((g.x - head.x) * 1e3, 1),
                "v": round((g.y - head.y) * 1e3, 1),
                "phi": g.phi,
                "link": g.link,
            }
            for g in head.legs
        ],
        "punch": [
            {
                "id": g.id,
                "u": round((g.x - head.x) * 1e3, 1),
                "v": round((g.y - head.y) * 1e3, 1),
                "phi": g.phi,
                "perimeter": g.link,
            }
            for g in head.punch
        ],
        "punch_info": head.punch_info,
        "conflicts": [list(x) for x in hit],
        "connection": connection(head),
    }


def _what(head: Head, hit: list[Hit]) -> list[dict]:
    """The clashing bars, set by set: which bars, how many pairs, the worst gap."""
    by: dict[str, dict] = {}
    for x in hit:
        if x.a == "punch":
            group = (
                head.punch[x.i].group
                + " against "
                + (head.hbars[x.j].group if x.b == "h" else f"{head.pile} L legs")
            )
        else:
            group = head.hbars[x.j].group if x.b == "h" else head.legs[x.j].group
        e = by.setdefault(
            group, {"bars": group, "pairs": 0, "clash": 0, "worst_gap_mm": x.gap, "levels_m": set()}
        )
        e["pairs"] += 1
        e["clash"] += x.kind == "clash"
        e["worst_gap_mm"] = min(e["worst_gap_mm"], x.gap)
        if x.b == "h":
            e["levels_m"].add(round(head.hbars[x.j].z, 3))
    return [{**e, "levels_m": sorted(e["levels_m"])} for e in by.values()]


def _signature(head: Head) -> tuple:
    """Heads with the same bars round them in the same places get the same answers."""
    hb = tuple(
        (h["along"], h["at"], round(h["z_m"], 3), h["phi"], h["layer"], h["kind"], h["lo"], h["hi"])
        for h in (_local(head, x) for x in head.hbars)
    )
    return (
        head.pile,
        head.host.element,
        round(head.z0, 3),
        tuple(head.z1),
        tuple(head.legs_m),
        hb,
        tuple((round((g.x - head.x) * 1e3, 1), round((g.y - head.y) * 1e3, 1), g.phi) for g in head.legs),
        tuple((round((g.x - head.x) * 1e3, 1), round((g.y - head.y) * 1e3, 1), g.phi) for g in head.punch),
    )


class Clashes:
    """The heads of a section and what it takes to check them; built once per request."""

    def __init__(self, project: Project, section: Section, results: dict, drawing: dict | None = None):
        self.ctx = Ctx(project, section, project.design, results, section.clashes)
        self.drawing = drawing or pile_cages(project.info.name, results, section=section.name)
        self.heads, self.notes = heads(self.ctx.rule, project.design, self.drawing, results)
        self._memo: dict[tuple, dict] = {}

    def head(self, group: str, index: int) -> Head | None:
        pile, _, host = group.partition("|")
        return next(
            (h for h in self.heads if h.pile == pile and h.host.element == host and h.index == index), None
        )

    def entry(self, head: Head) -> dict:
        """A head: its clashes, drawing, solutions and punching links."""
        ctx = self.ctx
        hits = conflicts(head, ctx.rule, ctx.dg, pile_bars(head))
        ph, qh = pile_hits(hits), punch_hits(hits)
        key = _signature(head)
        if key not in self._memo:
            same: dict[str, Any] = {}
            if ph:
                same["solutions"] = solutions(ctx, head, ph)
            if head.punch:
                same["punching"] = {
                    "info": head.punch_info,
                    "layout": punching_layout(ctx, head, head.punch),
                    "solution": snap_punching(ctx, head, pile_bars(head)) if qh else None,
                }
            self._memo[key] = same
        return {
            "group": f"{head.pile}|{head.host.element}",
            "index": head.index,
            "x": head.x,
            "y": head.y,
            "count": _count(ph),
            "punch_count": _count(qh),
            "scene": scene(head, hits),
            "what": _what(head, hits),
            **self._memo[key],
        }

    def whatif(self, w: WhatIf) -> dict:
        head = self.head(w.group, w.head)
        if head is None:
            return {
                "id": w.id,
                "group": w.group,
                "head": w.head,
                "note": w.note,
                "bars": w.bars,
                "pile_bars": w.pile_bars,
                "error": "That pile head is not in the design any more.",
                "checks": [],
                "passes": False,
            }
        return _clean(whatif_check(self.ctx, head, w))


def _brief(e: dict) -> dict:
    """A head for the list: counts and each solution's verdict, not the drawing or the checks."""
    out = {k: e[k] for k in ("index", "x", "y", "count", "punch_count")}
    out["solutions"] = [
        {
            k: s[k]
            for k in ("id", "title", "passes", "recommended", "delta_kg", "delta_kg_m3_per_head", "left")
        }
        for s in e.get("solutions") or []
    ]
    p = e.get("punching")
    if p:
        out["punching"] = {
            "layout_passes": p["layout"]["passes"],
            "snap_passes": None if not p["solution"] else p["solution"]["passes"],
        }
    return out


def find_clashes(
    project: Project,
    section: Section,
    results: dict,
    drawing: dict | None = None,
    detail: bool = False,
    c: Clashes | None = None,
) -> dict:
    """Every pile head's clashes with the slab or beam over it, grouped by pile type and element, each
    head with its solutions designed again; the punching links; and the "what if" checks kept. Without
    ``detail`` each head comes without its drawing and checks (``Clashes.entry`` gives them)."""
    c = c or Clashes(project, section, results, drawing)
    rule = c.ctx.rule
    groups: dict[str, dict] = {}
    for head in c.heads:
        e = c.entry(head)
        key = e["group"]
        g = groups.setdefault(
            key,
            {
                "key": key,
                "pile": head.pile,
                "part": head.part,
                "host": head.host.element,
                "host_kind": head.host.kind,
                "heads": [],
                "choice": rule.choices.get(key),
                "connection": connection(head),
            },
        )
        g["heads"].append(e if detail else _brief(e))
    out = []
    for g in groups.values():
        worst = max(
            g["heads"], key=lambda h: (h["count"]["clash"] + h["punch_count"]["clash"], h["count"]["pairs"])
        )
        g["worst"] = worst["index"]
        g["count"] = {
            "heads": len(g["heads"]),
            "with_clashes": sum(1 for h in g["heads"] if h["count"]["pairs"]),
            "pairs": sum(h["count"]["pairs"] for h in g["heads"]),
            "clash": sum(h["count"]["clash"] for h in g["heads"]),
            "punch_pairs": sum(h["punch_count"]["pairs"] for h in g["heads"]),
        }
        g["summary"] = _group_summary(g)
        out.append(g)
    out.sort(key=lambda g: (-g["count"]["clash"], -g["count"]["pairs"], g["key"]))
    return _clean(
        {
            "run_at": c.ctx.results.get("run_at"),
            "settings": rule.model_dump(mode="json"),
            "groups": out,
            "heads_checked": len(c.heads),
            "heads_clear": sum(
                1
                for g in out
                for h in g["heads"]
                if not h["count"]["pairs"] and not h["punch_count"]["pairs"]
            ),
            "whatifs": [c.whatif(w) for w in rule.whatifs],
            "notes": c.notes + assumptions(rule, c.ctx.settings),
        }
    )


def _group_summary(g: dict) -> dict:
    """Each solution over the group's heads: where it passes, where it is the one recommended, the steel
    it adds; the recommended one is the one recommended at most heads."""
    tally: dict[str, dict] = {}
    for h in g["heads"]:
        for s in h.get("solutions") or []:
            t = tally.setdefault(
                s["id"],
                {
                    "id": s["id"],
                    "title": s["title"],
                    "heads": 0,
                    "passes_at": 0,
                    "recommended_at": 0,
                    "delta_kg": 0.0,
                    "delta_kg_m3": None,
                },
            )
            t["heads"] += 1
            t["passes_at"] += s["passes"]
            t["recommended_at"] += s["recommended"]
            t["delta_kg"] = round(t["delta_kg"] + s["delta_kg"], 1)
            if s["delta_kg_m3_per_head"] is not None:
                t["delta_kg_m3"] = round((t["delta_kg_m3"] or 0.0) + s["delta_kg_m3_per_head"], 3)
    rows = sorted(tally.values(), key=lambda r: (-r["recommended_at"], -r["passes_at"]))
    return {"solutions": rows, "recommended": rows[0]["id"] if rows else None}


def assumptions(rule: ClashSettings, settings: DesignSettings) -> list[str]:
    tol = (
        f"closer than EN 1992-1-1 8.2(2) allows, max(Ø, dg + 5 = {settings.piles.aggregate_size + 5:g}, 20 mm)"
        if rule.rule == "ec2"
        else f"that would overlap, or pass less than {rule.fixing_tolerance:g} mm apart (fixing tolerance)"
    )
    return [
        f"A clash is two bars {tol}: 'clash' where they overlap, 'tight' where they only come too close.",
        "Plaxis plates are taken at the element's "
        + ("top" if rule.plate_level == "top" else "mid-depth")
        + (" (top levels set by element in the settings)." if rule.top_levels else "."),
        "Pile bars run up by their anchorage from the pile's top level; where that would pass the top bars they turn "
        "outwards just under them (L bars).",
        "Slab bars start at the slab's edge + cover + Ø/2 and repeat at their spacing; bars between the mesh bars sit half a "
        "spacing over. Beam links start 75 mm from the beam's end; transverse bars sit just inside the longitudinal bars "
        "of their face, half a link pitch from the links.",
        "Punching links: the smallest project bar whose legs give Asw per perimeter at no more than 1.5d apart round it "
        "(2d beyond 2d from the face), perimeters staggered.",
        "Solutions and 'what if' checks never change the design: they show what the element would be.",
    ]


def _clean(v: Any) -> Any:
    """JSON safe: no infinities or NaN."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.generic):
        return _clean(v.item())
    return v
