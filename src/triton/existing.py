"""The quay already on site where a section is built, for the 3D views, the construction sequence and
the clash checks. Never a design input.

Two systems (Section.existing):

* Combi wall: a capping beam on king piles, tie rods from the wall to the last row of a slab on piles
  inland (the anchor). The new combi wall is driven just in front of it: by default the land-side
  edge of the new steel pipes is the sea face of the existing capping beam, which is the working
  platform while the new wall is built.
* Gravity block wall: blocks on a founding level with quarry run behind them. Only the front beam on
  top is demolished; new piles bored through the quarry run need a temporary casing.

Warehouses may stand behind either. Positions are taken on the berth frame (triton/furniture.py
``berth_frame``): s along the berth from its start, d inland from the new quay face. Every size is an
input with a stated default; the ones not given by Ahmed are assumed (Construction sequence tab).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .design import sheet_piles
from .project import CombiWallInput, PileInput, Project, Section, SheetPileInput

KING_SPACING = 2.8  # m, existing king piles along the wall (every second tie rod): assumed
TIE_BELOW_COPE = 0.5  # m, tie rods below the existing cope when no level is given (Ahmed)
ANCHOR_BEAM = 1.5  # m, width and depth of the anchor beam over the anchor piles: assumed
E_STEEL = 205e6  # kPa
YEARS = 10  # the existing structure's remaining life (Ahmed, 2026-09-25)


def _extent(g: dict[str, Any]) -> dict[str, list[float]] | None:
    if g.get("box"):
        return g["box"]
    if g.get("lines"):
        xs = [ln[0] for ln in g["lines"]]
        ys = [ln[1] for ln in g["lines"]]
        return {
            "X": [min(xs), max(xs)],
            "Y": [min(ys), max(ys)],
            "Z": [min(ln[3] for ln in g["lines"]), max(ln[2] for ln in g["lines"])],
        }
    return None


class Frame:
    """Plan positions on the berth: (s, d) <-> model X, Y."""

    def __init__(self, frame: dict[str, Any]):
        self.along, self.across, self.inland = frame["along"], frame["across"], frame["inland"]
        self.face, self.start = frame["face"], frame["start"]
        self.cope = frame["cope_m"]

    def at(self, s: float, d: float, z: float) -> list[float]:
        p = {self.along: self.start + s, self.across: self.face + self.inland * d}
        return [round(p["X"], 3), round(p["Y"], 3), round(z, 3)]

    def sd(self, x: float, y: float) -> tuple[float, float]:
        p = {"X": x, "Y": y}
        return p[self.along] - self.start, (p[self.across] - self.face) * self.inland

    def s_range(self, box: dict[str, list[float]]) -> tuple[float, float]:
        return box[self.along][0] - self.start, box[self.along][1] - self.start

    def d_range(self, box: dict[str, list[float]]) -> tuple[float, float]:
        a, b = ((v - self.face) * self.inland for v in box[self.across])
        return min(a, b), max(a, b)


def _members(section: Section, geometry: list[dict[str, Any]], fr: Frame) -> list[dict[str, Any]]:
    """The new structure's upright members (piles and king piles) at their plan positions."""
    out = []
    for g in geometry:
        el = section.elements.get(g["element"])
        if not isinstance(el, PileInput | CombiWallInput) or not g.get("lines"):
            continue
        dia = (el.diameter if isinstance(el, PileInput) else el.tube_diameter) / 1000
        for x, y, top, bottom in g["lines"]:
            s, d = fr.sd(x, y)
            out.append(
                {
                    "element": g["element"],
                    "kind": el.kind,
                    "x": x,
                    "y": y,
                    "s": s,
                    "d": d,
                    "top": top,
                    "bottom": bottom,
                    "dia": dia,
                }
            )
    return out


def front_wall(
    section: Section, geometry: list[dict[str, Any]], fr: Frame
) -> tuple[float, float, str | None]:
    """The new front wall's centre line (d), its pipe radius (m) and its element: the combi wall or sheet
    pile wall nearest the quay face."""
    best = None
    for g in geometry:
        el = section.elements.get(g["element"])
        if not isinstance(el, CombiWallInput | SheetPileInput) or not (b := _extent(g)):
            continue
        d0, d1 = fr.d_range(b)
        d = (d0 + d1) / 2
        if isinstance(el, CombiWallInput):
            r = el.tube_diameter / 2000
        else:
            try:
                r = sheet_piles.section(el.section_name).h / 2000
            except ValueError:
                r = 0.225
        combi = isinstance(el, CombiWallInput)
        # The king piles over the sheets between them at the same line.
        rank = (round(abs(d), 1), not combi)
        if d < 5.0 and (best is None or rank < best[0]):
            best = (rank, (d, r, g["element"]))
    return best[1] if best else (0.0, 0.0, None)


def layout(project: Project, section: Section, geometry: list[dict[str, Any]], frame: dict[str, Any]) -> dict:
    """Where every part of the existing structure is: levels, rows, tie rods, and the objects drawn."""
    ex = section.existing
    fr = Frame(frame)
    boxes = [b for g in geometry if (b := _extent(g))]
    s0 = min(fr.s_range(b)[0] for b in boxes)
    s1 = max(fr.s_range(b)[1] for b in boxes)
    wall_d, wall_r, wall_name = front_wall(section, geometry, fr)
    notes: list[str] = []
    edge = ex.edge if ex.edge is not None else wall_d + wall_r
    edge_from = (
        "as set"
        if ex.edge is not None
        else f"the land-side edge of the new pipes ({wall_name or 'no front wall: the quay face'})"
    )
    cope = ex.cope_level if ex.cope_level is not None else fr.cope
    objects: list[dict[str, Any]] = []
    out: dict[str, Any] = {
        "system": ex.system,
        "edge_d": round(edge, 3),
        "edge_from": edge_from,
        "cope": cope,
        "dredge_level": ex.dredge_level,
        "s": [round(s0, 3), round(s1, 3)],
        "objects": objects,
        "notes": notes,
    }

    def box(part: str, label: str, s: tuple, d: tuple, z: tuple, removed: str | None = None) -> None:
        a, b = fr.at(s[0], d[0], z[0]), fr.at(s[1], d[1], z[1])
        objects.append(
            {
                "part": part,
                "label": label,
                "removed_by": removed,
                "shape": "box",
                "box": {k: sorted([a[i], b[i]]) for i, k in enumerate("XYZ")},
            }
        )

    def prism(part: str, label: str, poly: list[tuple[float, float]], removed: str | None = None) -> None:
        """A (d, z) outline run along the whole model."""
        faces = []
        n = len(poly)
        for i in range(n):
            (da, za), (db, zb) = poly[i], poly[(i + 1) % n]
            faces.append([fr.at(s0, da, za), fr.at(s1, da, za), fr.at(s1, db, zb), fr.at(s0, db, zb)])
        faces.append([fr.at(s0, d, z) for d, z in poly])
        faces.append([fr.at(s1, d, z) for d, z in poly])
        objects.append(
            {"part": part, "label": label, "removed_by": removed, "shape": "faces", "faces": faces}
        )

    def line(part: str, label: str, a: list, b: list, size: float) -> None:
        objects.append(
            {"part": part, "label": label, "removed_by": None, "shape": "line", "a": a, "b": b, "size": size}
        )

    cap_bottom = cope - ex.capping_depth
    beam_word = "capping beam" if ex.system == "combi_wall" else "front beam"
    box(
        "capping_beam",
        f"Existing {beam_word} {ex.capping_width:g} × {ex.capping_depth:g} m, top {cope:g} m (demolished)",
        (s0, s1),
        (edge, edge + ex.capping_width),
        (cap_bottom, cope),
        "demolition",
    )
    if ex.system == "combi_wall":
        wd = edge + ex.capping_width / 2
        out["wall_d"] = round(wd, 3)
        king = []
        s = s0 + KING_SPACING / 2
        while s <= s1 + 1e-6:
            king.append(s)
            line(
                "combi_wall",
                f"Existing king pile Ø{ex.wall_diameter:g} mm at {s:.1f} m, toe {ex.wall_toe:g} m",
                fr.at(s, wd, cap_bottom),
                fr.at(s, wd, ex.wall_toe),
                ex.wall_diameter / 1000,
            )
            s += KING_SPACING
        box(
            "combi_wall",
            "Existing sheet piles between the king piles",
            (s0, s1),
            (wd - 0.2, wd + 0.2),
            (ex.wall_toe + 6.0, cap_bottom),
        )
        out["king_piles"] = [round(v, 3) for v in king]
        notes.append(
            f"Existing king piles drawn every {KING_SPACING:g} m under the middle of the capping "
            "beam, with sheets "
            "between them to 6 m above their toe (assumed; they are drawn only)."
        )
        tie = ex.tie_rod_level if ex.tie_rod_level is not None else cope - TIE_BELOW_COPE
        out["tie_level"] = tie
        rows = [edge + ex.first_row + i * ex.row_spacing for i in range(ex.rows)] if ex.piles else []
        anchor = wd + ex.tie_rod_length if ex.anchor_row else None
        out["rows_d"] = [round(v, 3) for v in rows]
        out["anchor_d"] = round(anchor, 3) if anchor is not None else None
        piles = []
        if anchor is not None:
            box(
                "anchor",
                f"Existing anchor beam at the tie rods' end, {ex.tie_rod_length:g} m from the wall",
                (s0, s1),
                (anchor - ANCHOR_BEAM / 2, anchor + ANCHOR_BEAM / 2),
                (tie - ANCHOR_BEAM / 2, tie + ANCHOR_BEAM / 2),
            )
        rows_all = rows + ([anchor] if anchor is not None else [])
        slab_top = cope
        slab_bottom = cope - ex.slab_thickness / 1000
        if rows:
            box(
                "slab",
                f"Existing slab {ex.slab_thickness:g} mm on piles (demolished)",
                (s0, s1),
                (edge + ex.capping_width, rows[-1] + 1.0),
                (slab_bottom, slab_top),
                "demolition",
            )
        for d in rows_all:
            s = s0 + ex.pile_offset
            while s <= s1 + 1e-6:
                piles.append((s, d))
                line(
                    "piles",
                    f"Existing {'anchor ' if d == anchor else ''}pile Ø{ex.pile_diameter:g} mm, row "
                    f"{d - edge:.1f} m from the existing edge, at {s:.1f} m along",
                    fr.at(s, d, slab_bottom if d != anchor else tie - ANCHOR_BEAM / 2),
                    fr.at(s, d, ex.pile_toe),
                    ex.pile_diameter / 1000,
                )
                s += ex.pile_spacing
        out["piles"] = [[round(s, 3), round(d, 3)] for s, d in piles]
        rods = []
        if ex.tie_rods:
            s = s0 + ex.tie_rod_spacing / 2
            while s <= s1 + 1e-6:
                rods.append(s)
                line(
                    "tie_rods",
                    f"Existing tie rod Ø{ex.tie_rod_diameter:g} mm, {ex.tie_rod_length:g} m at {tie:g} m, "
                    f"{s:.1f} m along",
                    fr.at(s, wd, tie),
                    fr.at(s, wd + ex.tie_rod_length, tie),
                    ex.tie_rod_diameter / 1000,
                )
                s += ex.tie_rod_spacing
        out["tie_rods_s"] = [round(v, 3) for v in rods]
    else:
        top = cap_bottom
        base, crest = ex.block_base_width, ex.block_top_width
        blocks = [
            (edge, top),
            (edge + crest, top),
            (edge + base, ex.block_founding),
            (edge, ex.block_founding),
        ]
        prism("blocks", f"Existing gravity block wall, {base:g} m wide at {ex.block_founding:g} m", blocks)
        heel = edge + base
        reach = heel + (top - ex.block_founding) * ex.quarry_slope
        quarry = [(edge + crest, top), (reach, top), (heel, ex.block_founding)]
        prism("quarry_run", f"Quarry run behind the blocks, 1:{ex.quarry_slope:g}", quarry)
        out["blocks"] = [[round(d, 3), round(z, 3)] for d, z in blocks]
        out["quarry"] = [[round(d, 3), round(z, 3)] for d, z in quarry]
    if ex.warehouse:
        w0 = edge + ex.warehouse_distance
        box(
            "warehouse",
            f"Warehouse {ex.warehouse_distance:g} m from the existing edge, {ex.warehouse_height:g} m high",
            (s0, s1),
            (w0, w0 + ex.warehouse_depth),
            (cope, cope + ex.warehouse_height),
        )
        out["warehouse_d"] = [round(w0, 3), round(w0 + ex.warehouse_depth, 3)]
    notes.append(
        f"Existing edge {edge:.2f} m inland of the new quay face ({edge_from}); existing cope {cope:g} m; "
        f"existing dredge level {ex.dredge_level:g} m."
    )
    return out


def _poly_z(poly: list[list[float]], d: float) -> tuple[float, float] | None:
    """The levels a (d, z) outline covers at d."""
    zs = []
    n = len(poly)
    for i in range(n):
        (da, za), (db, zb) = poly[i], poly[(i + 1) % n]
        if min(da, db) - 1e-9 <= d <= max(da, db) + 1e-9:
            if abs(db - da) < 1e-9:
                zs += [za, zb]
            else:
                zs.append(za + (zb - za) * (d - da) / (db - da))
    return (min(zs), max(zs)) if len(zs) >= 2 else None


def clashes(
    project: Project, section: Section, geometry: list[dict[str, Any]], frame: dict[str, Any]
) -> dict:
    """The new piles and walls against the existing structure: piles, tie rods, the existing wall,
    blocks and quarry run, and the warehouses. ``found``: one row per new member or element."""
    ex = section.existing
    fr = Frame(frame)
    lay = layout(project, section, geometry, frame)
    gap = ex.clearance
    found: list[dict[str, Any]] = []

    def add(level: str, element: str, what: str, at: list | None = None, **kw) -> None:
        found.append({"level": level, "element": element, "what": what, "at": at, **kw})

    members = _members(section, geometry, fr)
    wall_d, wall_r, wall_name = front_wall(section, geometry, fr)
    # The new front wall against the existing capping beam it is built from.
    if wall_name:
        into = wall_d + wall_r - lay["edge_d"]
        if into > 0.005:
            add(
                "clash",
                wall_name,
                f"The new wall cuts {into * 1000:.0f} mm into the existing "
                f"{'capping' if ex.system == 'combi_wall' else 'front'} beam",
            )
        elif into < -0.5:
            add(
                "note",
                wall_name,
                f"The new wall is {-into:.2f} m clear of the existing edge: the existing beam is "
                "not directly its "
                "working platform",
            )
    for m in members:
        if m["kind"] == "combi_wall":
            continue  # the new front wall: checked against the capping beam above
        where = f"X {m['x']:g}, Y {m['y']:g}"
        at = [m["x"], m["y"], m["top"]]
        if ex.system == "combi_wall":
            wd = lay["wall_d"]
            clear = abs(m["d"] - wd) - (m["dia"] + ex.wall_diameter / 1000) / 2
            if clear < gap and m["bottom"] < lay["cope"] - ex.capping_depth:
                add(
                    "clash",
                    m["element"],
                    f"Pile at {where} hits the existing combi wall ({clear * 1000:.0f} mm clear)",
                    at,
                )
            if lay["piles"]:
                pts = np.array(lay["piles"])
                dist = np.hypot(pts[:, 0] - m["s"], pts[:, 1] - m["d"])
                i = int(dist.argmin())
                clear = float(dist[i]) - (m["dia"] + ex.pile_diameter / 1000) / 2
                if clear < gap:
                    add(
                        "clash",
                        m["element"],
                        f"Pile at {where} is {clear * 1000:.0f} mm clear of the existing pile at "
                        f"{pts[i, 0]:.1f} m along, row {pts[i, 1] - lay['edge_d']:.1f} m (needs "
                        f"{gap * 1000:.0f} mm)",
                        at,
                        clear_mm=round(clear * 1000),
                    )
            # Bored from the working platform: every tie rod above the toe is in the way, whatever the
            # pile's cut-off level.
            tie = lay.get("tie_level")
            rods = lay.get("tie_rods_s") or []
            if rods and m["bottom"] <= tie and wd <= m["d"] <= wd + ex.tie_rod_length:
                ds = np.abs(np.array(rods) - m["s"])
                i = int(ds.argmin())
                clear = float(ds[i]) - m["dia"] / 2 - ex.tie_rod_diameter / 2000
                if clear < gap:
                    add(
                        "clash",
                        m["element"],
                        f"Pile at {where} is {clear * 1000:.0f} mm clear of the tie rod at "
                        f"{rods[i]:.1f} m along "
                        f"(tie rods every {ex.tie_rod_spacing:g} m at {tie:g} m)",
                        [m["x"], m["y"], tie],
                        clear_mm=round(clear * 1000),
                    )
        else:
            zb = _poly_z(lay["blocks"], m["d"])
            if zb and m["bottom"] < zb[1] and m["top"] > zb[0]:
                add(
                    "clash",
                    m["element"],
                    f"Pile at {where} runs into the existing blocks ({zb[0]:.1f} to {zb[1]:.1f} m)",
                    at,
                )
            zq = _poly_z(lay["quarry"], m["d"])
            if zq and m["bottom"] < zq[1] and m["top"] > zq[0]:
                lo, hi = max(zq[0], m["bottom"]), min(zq[1], m["top"])
                add(
                    "warning",
                    m["element"],
                    f"Pile at {where} is bored through quarry run from {hi:.1f} to {lo:.1f} m: a "
                    "temporary casing "
                    "through it",
                    [m["x"], m["y"], hi],
                    through_m=round(hi - lo, 2),
                )
    if ex.system == "combi_wall" and lay.get("tie_rods_s"):
        fits = ex.tie_rod_spacing - ex.tie_rod_diameter / 1000 - 2 * gap
        big = max((m["dia"] for m in members if m["kind"] == "pile"), default=0.0)
        if big > fits:
            add(
                "note",
                "Tie rods",
                f"With tie rods every {ex.tie_rod_spacing:g} m only piles up to "
                f"Ø{max(fits, 0) * 1000:.0f} mm fit "
                f"between them with {gap * 1000:.0f} mm clear each side: Ø{big * 1000:.0f} mm piles "
                "need tie rods "
                "cut or moved where they are bored",
            )
    # Plates (sheet pile walls) across the tie rods and existing piles.
    for g in geometry:
        el = section.elements.get(g["element"])
        if not isinstance(el, SheetPileInput) or not g.get("box"):
            continue
        d0, d1 = fr.d_range(g["box"])
        a0, a1 = fr.s_range(g["box"])
        z0, z1 = g["box"]["Z"]
        if d1 - d0 > 1.0:
            continue  # not a wall across the quay
        dm = (d0 + d1) / 2
        if ex.system == "combi_wall":
            tie = lay.get("tie_level")
            rods = [s for s in lay.get("tie_rods_s") or [] if a0 - 1e-6 <= s <= a1 + 1e-6]
            if rods and z0 <= tie <= z1 and lay["wall_d"] <= dm <= lay["wall_d"] + ex.tie_rod_length:
                add(
                    "clash",
                    g["element"],
                    f"The sheet pile wall crosses {len(rods)} tie rods at {tie:g} m, "
                    f"{dm - lay['edge_d']:.1f} m "
                    "from the existing edge: they are cut or the wall is driven below them",
                    fr.at((a0 + a1) / 2, dm, tie),
                    count=len(rods),
                )
            near = [
                p for p in lay["piles"] if abs(p[1] - dm) < ex.pile_diameter / 2000 + gap and a0 <= p[0] <= a1
            ]
            if near:
                add(
                    "clash",
                    g["element"],
                    f"The sheet pile wall runs through {len(near)} existing piles (row "
                    f"{near[0][1] - lay['edge_d']:.1f} m from the existing edge)",
                    fr.at(near[0][0], dm, z1),
                    count=len(near),
                )
        else:
            zq = _poly_z(lay["quarry"], dm)
            if zq and z0 < zq[1] and z1 > zq[0]:
                add(
                    "warning",
                    g["element"],
                    f"The sheet pile wall is driven through quarry run ({zq[0]:.1f} to {zq[1]:.1f} m)",
                )
    if lay.get("warehouse_d"):
        w0, w1 = lay["warehouse_d"]
        for g in geometry:
            b = _extent(g)
            if not b:
                continue
            d0, d1 = fr.d_range(b)
            if d1 > w0 and d0 < w1:
                add(
                    "clash",
                    g["element"],
                    f"{g['element']} runs under the warehouse ({w0:.1f} to {w1:.1f} m inland)",
                )
            elif w0 - d1 < ex.warehouse_clearance:
                add(
                    "warning",
                    g["element"],
                    f"{g['element']} is {w0 - d1:.1f} m from the warehouse: less than the "
                    f"{ex.warehouse_clearance:g} m working room kept for the piling rig",
                )
    counts = {k: sum(1 for f in found if f["level"] == k) for k in ("clash", "warning", "note")}
    return {"found": found, "counts": counts, "layout": lay}


def tie_rods(
    project: Project,
    section: Section,
    geometry: list[dict[str, Any]],
    frame: dict[str, Any],
    deformed: dict[str, Any] | None,
) -> dict[str, Any]:
    """What the existing tie rods do to the new wall's movement: a spring at the tie level, in parallel
    with the new king pile as a cantilever from its fixity. Only while the existing structure lasts
    (10 years): the long-term case has no tie rods, so the design stays as the Plaxis model."""
    ex = section.existing
    if not ex.use or ex.system != "combi_wall" or not ex.tie_rods:
        return {"use": False}
    fr = Frame(frame)
    lay = layout(project, section, geometry, frame)
    wall_d, _, wall_name = front_wall(section, geometry, fr)
    wall = section.elements.get(wall_name) if wall_name else None
    if not isinstance(wall, CombiWallInput):
        return {"use": False, "notes": ["No new combi wall in front of the tie rods."]}
    from .design.combi import combi_section
    from .materials import concrete
    from .project import with_project_grades

    w = with_project_grades(wall, project.design.materials, project.design.durability)
    sec = combi_section(w)
    ei = sec.e_steel * sec.i_steel + concrete(w.concrete).ecm * 1e3 * sec.i_concrete  # kN·m², short term
    kings = sorted({round(m["s"], 3) for m in _members(section, geometry, fr) if m["element"] == wall_name})
    king_gap = float(np.median(np.diff(kings))) if len(kings) > 1 else 3.2
    tie = lay["tie_level"]
    firm = (
        wall.firm_soil_level
        if wall.firm_soil_level is not None
        else section.deflection.firm_soil_level
        if section.deflection.firm_soil_level is not None
        else section.site.seabed_level - 3.0
    )
    lc = max(tie - firm, 1.0)
    k_wall = 3 * ei / lc**3  # kN/m per king pile, a load at the tie level
    area = math.pi * (ex.tie_rod_diameter / 1000) ** 2 / 4
    k_rod = E_STEEL * area / ex.tie_rod_length * ex.tie_rod_share  # kN/m per rod
    per_king = king_gap / ex.tie_rod_spacing
    k_tie = k_rod * per_king
    keep = k_wall / (k_wall + k_tie)
    cap = area * ex.tie_rod_fy * 1e3  # kN, fy·A (γM0 1.0)
    out: dict[str, Any] = {
        "use": True,
        "wall": wall_name,
        "tie_level": tie,
        "fixity": firm,
        "EI_kNm2": round(ei),
        "king_spacing_m": round(king_gap, 3),
        "k_wall_kN_per_m": round(k_wall),
        "k_rod_kN_per_m": round(k_rod),
        "k_tie_kN_per_m": round(k_tie),
        "factor": round(keep, 3),
        "reduction_pct": round((1 - keep) * 100, 1),
        "capacity_kN": round(cap),
        "years": YEARS,
    }
    words = [
        f"New king pile EI {ei:,.0f} kN·m² (tube and infill, short term), held as a cantilever from "
        f"{firm:g} m up to the tie rods at {tie:g} m (L {lc:.1f} m): k = 3EI/L³ = {k_wall:,.0f} "
        "kN/m per king pile.",
        f"Tie rods Ø{ex.tie_rod_diameter:g} mm every {ex.tie_rod_spacing:g} m, {ex.tie_rod_length:g} m long: "
        f"EA/L × {ex.tie_rod_share:g} (share holding the new wall) = {k_rod:,.0f} kN/m per rod, "
        f"{per_king:.2f} rods per king pile ({king_gap:.2f} m): {k_tie:,.0f} kN/m.",
        f"The movement at the tie level is multiplied by k_wall / (k_wall + k_tie) = {keep:.3f}, "
        f"{(1 - keep) * 100:.0f}% less, as long as the tie rods hold the new wall.",
        "Assumed: the new front beam is cast round the existing wall head and the tie rods' ends after the "
        "demolition, so they act together; the Plaxis model has no tie rods.",
        f"The existing structure ends in {YEARS} years. After that the tie rods (and the wall they hold) are "
        "gone or corroded, so the long-term movement is the full value without them: the design, the "
        "displacement limits and the Plaxis model stay as they are, and the reduction is only for the "
        f"construction stages and the first {YEARS} years.",
    ]
    if deformed and deformed.get("members"):
        axis = 1 if fr.across == "X" else 2
        heads = []
        for mb in deformed["members"]:
            if mb["element"] != wall_name:
                continue
            pts = sorted(mb["points"])
            zs = [p[0] for p in pts]
            if tie < zs[0]:
                continue  # above the top the wall head's movement is taken
            heads.append(float(np.interp(tie, zs, [p[axis] for p in pts])))
        if heads:
            u = float(np.mean(np.abs(heads)))
            with_rods = u * keep
            force = k_rod * with_rods / 1000  # kN per rod
            out.update(
                {
                    "combination": deformed.get("combination"),
                    "u_mm": round(u, 1),
                    "u_with_mm": round(with_rods, 1),
                    "rod_force_kN": round(force, 1),
                    "rod_utilisation": round(force / cap, 3),
                }
            )
            words.append(
                f"{deformed.get('combination')}: the new wall moves {u:.1f} mm across the quay at {tie:g} m "
                f"(the Design tab's estimate); with the tie rods about {with_rods:.1f} mm. Each rod "
                "then carries "
                f"{force:.0f} kN of {cap:,.0f} kN (fy·A, {ex.tie_rod_fy:g} MPa, no corrosion loss): "
                f"{force / cap:.2f}, on top of what it already carries for the existing wall (not "
                "known here)."
            )
    out["notes"] = words
    return out
