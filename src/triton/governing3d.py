"""Where the design of one element is governed, for its 3D view on the Design tab (display only).

Every point a design result names as governing a check (N–M, shear, crack width, punching, each
face's bars, the AdSec sets) is placed in plan, from the node it names, the station along a beam,
or its X, Y in the part's turned frame turned back. Points at one place are gathered into one pin.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .alignment import Part, rotate_points

KINDS = ("piles", "combi_walls", "beams", "slabs", "sheet_pile_walls", "diaphragm_walls")
FACES = {
    "bottom_x": ("Bottom bars along X", "x", "max"),
    "bottom_y": ("Bottom bars along Y", "y", "max"),
    "top_x": ("Top bars along X", "x", "min"),
    "top_y": ("Top bars along Y", "y", "min"),
}


def _f(v: Any, nd: int = 0) -> str:
    return "–" if v is None else f"{v:,.{nd}f}"


def _part(d: dict) -> Part | None:
    p = d.get("part")
    if not isinstance(p, dict) or not p.get("rotation_deg"):
        return None
    return Part(
        index=p["index"],
        name=p["name"],
        start=tuple(p["start"]),
        end=tuple(p["end"]),
        rotation=p["rotation_deg"],
        pivot=tuple(p["pivot"]),
        own_axes=p.get("own_axes", False),
    )


def _plan(part: Part | None, x: float, y: float) -> tuple[float, float]:
    if part is None:
        return x, y
    px, py = rotate_points(part, np.array([x], float), np.array([y], float), back=True)
    return float(px[0]), float(py[0])


class _Nodes:
    """Plan position of a node in a combination, and the node nearest a level matching a value."""

    def __init__(self, sheets: dict[str, Any]):
        self.frames = {
            c: s.frame for c, s in (sheets or {}).items() if {"X", "Y", "Z"} <= set(s.frame.columns)
        }

    def node(self, combination: str, node: Any) -> list[float] | None:
        f = self.frames.get(combination)
        if f is None or node is None or "Node" not in f.columns:
            return None
        r = f[f["Node"] == node]
        if r.empty:
            return None
        r = r.iloc[0]
        return [float(r["X"]), float(r["Y"]), float(r["Z"])]

    def match(
        self, combination: str, z: float, cols: tuple[str, ...], value: float | None
    ) -> list[float] | None:
        """The node at level ``z`` whose resultant of ``cols`` is closest to ``value`` (a pile element
        holds several piles; the result names only the level)."""
        f = self.frames.get(combination)
        if f is None or value is None or not set(cols) <= set(f.columns):
            return None
        near = f[(f["Z"] - z).abs() <= 0.3]
        if near.empty:
            return None
        res = np.hypot(*(near[c].to_numpy(float) for c in cols)) if len(cols) > 1 else near[cols[0]].abs()
        r = near.iloc[int(np.argmin(np.abs(np.asarray(res) - abs(value))))]
        return [float(r["X"]), float(r["Y"]), float(z)]


def element_points(results: dict[str, Any], element: str, sheets: dict[str, Any]) -> list[dict[str, Any]]:
    """The governing points of ``element``: [{at: [x, y, z], part, lines: [{title, text}]}], one per
    place, the most utilised first."""
    nodes = _Nodes(sheets)
    raw: list[tuple[list[float], str | None, str, str, float | None]] = []

    def add(at, part_name, title, text, util=None):
        if at is not None and all(v is not None and math.isfinite(v) for v in at):
            raw.append(([round(v, 2) for v in at], part_name, title, text, util))

    for kind in KINDS:
        for d in results.get(kind) or []:
            if d.get("element") != element:
                continue
            part = _part(d)
            pname = (d.get("part") or {}).get("name") if (d.get("parts") or 1) > 1 else None
            if kind in ("piles", "combi_walls"):
                _pile_like(
                    d if kind == "piles" else d.get("infill") or {},
                    nodes,
                    pname,
                    add,
                    "" if kind == "piles" else "Concrete infill: ",
                )
                if kind == "combi_walls":
                    _tube(d, nodes, pname, add)
            elif kind == "beams":
                _beam(d, part, pname, add)
            elif kind == "slabs":
                _slab(d, part, pname, add)
            elif kind == "sheet_pile_walls":
                _spw(d, nodes, pname, add)
            elif kind == "diaphragm_walls":
                _dwall(d, nodes, pname, add)
    pins: dict[tuple, dict[str, Any]] = {}
    for at, pname, title, text, util in raw:
        k = tuple(round(v, 1) for v in at)
        pin = pins.setdefault(k, {"at": at, "part": pname, "lines": [], "utilisation": None})
        pin["lines"].append({"title": title, "text": text})
        if util is not None:
            pin["utilisation"] = max(pin["utilisation"] or 0.0, util)
    out = sorted(pins.values(), key=lambda p: -(p["utilisation"] or 0.0))
    for n, p in enumerate(out, 1):
        p["n"] = n
    return out


def _pile_like(d: dict, nodes: _Nodes, pname, add, prefix: str) -> None:
    g = d.get("governing")
    if isinstance(g, dict) and g.get("combination"):
        at = nodes.node(g["combination"], g.get("node")) or nodes.match(
            g["combination"], g.get("z", 0.0), ("M_2", "M_3"), g.get("M_kNm")
        )
        u = d.get("utilisation")
        add(
            at,
            pname,
            f"{prefix}Bending with N (N–M), utilisation {_f(u, 2)}",
            f"{g['combination']}: N {_f(g.get('N_kN'))} kN, "
            f"M {_f(g.get('M_kNm'))} kNm (MRd {_f(g.get('M_Rd_kNm'))}), z {_f(g.get('z'), 2)} m",
            u,
        )
    sh = (d.get("shear") or {}).get("governing")
    if isinstance(sh, dict) and sh.get("combination"):
        at = nodes.match(sh["combination"], sh.get("z", 0.0), ("Q_12", "Q_13"), sh.get("V_kN"))
        u = (d.get("shear") or {}).get("utilisation")
        add(
            at,
            pname,
            f"{prefix}Shear, utilisation {_f(u, 2)}",
            f"{sh['combination']}: V {_f(sh.get('V_kN'))} kN, "
            f"VRd,c {_f(sh.get('VRd_c_kN'))} kN, z {_f(sh.get('z'), 2)} m",
            u,
        )
    cr = (d.get("cracks") or {}).get("governing")
    if isinstance(cr, dict) and cr.get("combination"):
        at = nodes.match(cr["combination"], cr.get("z", 0.0), ("M_2", "M_3"), cr.get("M_kNm"))
        c = d.get("cracks") or {}
        u = c["wk_mm"] / c["limit_mm"] if c.get("wk_mm") is not None and c.get("limit_mm") else None
        add(
            at,
            pname,
            f"{prefix}Crack width (QP), {_f(c.get('wk_mm'), 2)} of {_f(c.get('limit_mm'), 2)} mm",
            f"{cr['combination']}: N {_f(cr.get('N_kN'))} kN, "
            f"M {_f(cr.get('M_kNm'))} kNm, z {_f(cr.get('z'), 2)} m",
            u,
        )
    for st in d.get("governing_sets") or []:
        for state in ("uls", "qp"):
            for r in st.get(state) or []:
                if not r.get("combination"):
                    continue
                at = nodes.node(r["combination"], r.get("node")) or nodes.match(
                    r["combination"],
                    r.get("z", 0.0),
                    ("M_2", "M_3"),
                    math.hypot(r.get("M2_kNm") or 0, r.get("M3_kNm") or 0),
                )
                add(
                    at,
                    pname,
                    f"{prefix}AdSec set {state.upper()} {r['case']}",
                    f"{r['combination']}: N {_f(r.get('N_kN'))} kN, M2 {_f(r.get('M2_kNm'))}, "
                    f"M3 {_f(r.get('M3_kNm'))} kNm, z {_f(r.get('z'), 2)} m",
                )


def _tube(d: dict, nodes: _Nodes, pname, add) -> None:
    t = d.get("tube") or {}
    g = t.get("governing")
    if isinstance(g, dict) and g.get("combination") and g.get("z") is not None:
        at = nodes.node(g["combination"], g.get("node")) or nodes.match(
            g["combination"], g["z"], ("M_2", "M_3"), g.get("M_kNm") or 0.0
        )
        u = t.get("utilisation")
        add(
            at,
            pname,
            f"Steel tube: {g.get('check') or 'governing check'}, utilisation {_f(u, 2)}",
            f"{g['combination']}: z {_f(g.get('z'), 2)} m",
            u,
        )


def _beam(d: dict, part: Part | None, pname, add) -> None:
    along, centre, level = d.get("along") or "Y", d.get("centre_m") or 0.0, d.get("level_m") or 0.0

    def at(s):
        if s is None:
            return None
        x, y = (centre, s) if along == "Y" else (s, centre)
        return [*_plan(part, x, y), level]

    for key, title in (
        ("bending", "Bending with N"),
        ("shear", "Shear and torsion"),
        ("transverse", "Transverse bars (M11 across the beam)"),
    ):
        c = d.get(key) or {}
        g = c.get("governing")
        if isinstance(g, dict) and g.get("combination"):
            u = c.get("utilisation")
            vals = ", ".join(
                f"{k.split('_')[0]} {_f(v)}"
                for k, v in g.items()
                if k.endswith(("_kN", "_kNm", "_kNm_per_m")) and v is not None
            )
            add(
                at(g.get("s")),
                pname,
                f"{title}, utilisation {_f(u, 2)}",
                f"{g['combination']}: {vals}, at {_f(g.get('s'), 1)} m along",
                u,
            )
    for face, c in (d.get("cracks") or {}).items():
        if isinstance(c, dict) and c.get("s") is not None:
            u = c["wk"] / c["limit"] if c.get("wk") is not None and c.get("limit") else None
            add(
                at(c["s"]),
                pname,
                f"Crack width, {face} face: {_f(c.get('wk'), 2)} of {_f(c.get('limit'), 2)} mm",
                f"{c.get('combination')}: N {_f(c.get('N_kN'))} kN, "
                f"M {_f(c.get('M_kNm'))} kNm, at {_f(c['s'], 1)} m along",
                u,
            )
    for st in d.get("governing_sets") or []:
        for state in ("uls", "qp"):
            for r in st.get(state) or []:
                if r.get("combination"):
                    add(
                        at(r.get("z")),
                        pname,
                        f"AdSec set {state.upper()} {r['case']}",
                        f"{r['combination']}: N {_f(r.get('N_kN'))} kN, M2 {_f(r.get('M2_kNm'))}, "
                        f"M3 {_f(r.get('M3_kNm'))} kNm, at {_f(r.get('z'), 1)} m along",
                    )


def _slab(d: dict, part: Part | None, pname, add) -> None:
    level = d.get("level_m") or 0.0
    mc = d.get("moment_cells") or {}
    size, x0, y0 = mc.get("size"), mc.get("x0"), mc.get("y0")
    cells = [c for c in mc.get("cells") or [] if len(c) == 6]  # squares with a result of their own
    names = mc.get("names") or {"x": "M11", "y": "M22"}
    a = np.array(cells, float) if cells and size else None
    for key, (title, axis, which) in FACES.items():
        lay = (d.get("layers") or {}).get(key) or {}
        u = lay.get("utilisation")
        g = lay.get("governing_cell")
        if isinstance(g, dict) and g.get("x") is not None:
            # Where these bars are most used: the square whose steel needed over steel given is the
            # layer's utilisation (so it is the reddest square of that face).
            m = None
            if a is not None:
                hit = (np.floor((g["x"] - x0) / size) == a[:, 0]) & (
                    np.floor((g["y"] - y0) / size) == a[:, 1]
                )
                if hit.any():
                    c = a[np.argmax(hit)][{"x": 2, "y": 4}[axis] :][:2]
                    m = f"{_f(min(c))} to {_f(max(c))}"
            add(
                [*_plan(part, g["x"], g["y"]), level],
                pname,
                f"{title}, utilisation {_f(g.get('utilisation', u), 2)}",
                "where these bars are most used (bending steel needed over the steel given)"
                + (f", ULS {names[axis]} {m} kNm/m here" if m is not None else ""),
                g.get("utilisation", u),
            )
            continue
        if a is None:
            continue
        # A design from before the governing square was kept: the largest moment only, which is not
        # always where the bars are most used (more bars may be given there).
        k = {("x", "max"): 2, ("x", "min"): 3, ("y", "max"): 4, ("y", "min"): 5}[(axis, which)]
        i = int(np.argmax(a[:, k]) if which == "max" else np.argmin(a[:, k]))
        m = a[i, k]
        if (which == "max" and m <= 0) or (which == "min" and m >= 0):
            continue
        add(
            [*_plan(part, x0 + (a[i, 0] + 0.5) * size, y0 + (a[i, 1] + 0.5) * size), level],
            pname,
            f"{title}: largest {names[axis]}",
            f"{names[axis]} {_f(m)} kNm/m (the bars' utilisation {_f(u, 2)} is where they are most "
            "used, which may be elsewhere: design again to pin it)",
            None,
        )
    sh = d.get("shear") or {}
    g = sh.get("governing")
    if isinstance(g, dict) and g.get("x") is not None:
        xy = (g["plan_x"], g["plan_y"]) if "plan_x" in g else _plan(part, g["x"], g["y"])
        add(
            [*xy, level],
            pname,
            f"Shear, utilisation {_f(sh.get('utilisation'), 2)}",
            f"{g.get('combination')}: V {_f(g.get('V_kN_per_m'))} kN/m, "
            f"VRd,c {_f(g.get('VRd_c_kN_per_m'))} kN/m",
            sh.get("utilisation"),
        )
    for t in d.get("punching_types") or []:
        if t.get("governing_x") is None:
            continue
        u = t.get("utilisation")
        add(
            [*_plan(part, t["governing_x"], t["governing_y"]), level],
            pname,
            f"Punching, governing head of {t.get('pile')}, utilisation {_f(u, 2)}",
            f"{t.get('combination')}: V {_f(t.get('V_kN'))} kN, {t.get('heads')} heads of this type",
            u,
        )


def _spw(d: dict, nodes: _Nodes, pname, add) -> None:
    des = d.get("design") or {}
    g = ((des.get("designed") or des.get("as_plaxis")) or {}).get("governing") or {}
    if g.get("combination") and g.get("z") is not None:
        at = nodes.match(g["combination"], g["z"], ("M_11",), g.get("M_kNm_per_m") or g.get("M") or 0.0)
        add(
            at,
            pname,
            f"{des.get('section', 'Sheet pile')}: Uf {_f(des.get('uf'), 2)}",
            f"{g['combination']}: z {_f(g['z'], 2)} m",
            des.get("uf"),
        )


def _dwall(d: dict, nodes: _Nodes, pname, add) -> None:
    des = d.get("design") or {}
    g = des.get("governing") or {}
    zone = g.get("zone")
    if g.get("combination") and zone:
        at = nodes.match(g["combination"], (zone[0] + zone[1]) / 2, ("M_11",), 1e12)
        add(
            at,
            pname,
            f"Diaphragm wall: {g.get('governs')}, Uf {_f(des.get('uf'), 2)}",
            f"{g['combination']}: zone {_f(zone[0], 2)} to {_f(zone[1], 2)} m",
            des.get("uf"),
        )
