"""The site round a section's structure for the 3D views: seabed, water, soil, quay furniture and the
STS crane, in model coordinates.

Nothing here is a design input. The seabed, water and soil levels are the section's (Sections tab,
"Site in the 3D views"); the furniture is the Furniture tab's arrangement along the berth, drawn where
it falls inside the model's length; the crane is a simple outline from the project's STS crane values
with drawing-only sizes (lift height, leg spacing) that are assumed.

The berth frame (triton/furniture.py ``berth_frame``) gives the quay's axis: positions along the berth
(s, from the front beam's end) and across it (d, from the quay face, inland +). A point (s, d) is at
along = start + s and across = face + inland × d. A corner berth is drawn along its first straight.
"""

from __future__ import annotations

import math
from typing import Any

from . import existing, furniture
from .design import sheet_piles
from .project import BeamInput, CombiWallInput, PileInput, Project, Section, SheetPileInput, SlabInput

SEA = 20.0  # m of sea bed drawn in front of the wall
LAND = 5.0  # m of soil drawn beyond the model's landward end
BELOW = 3.0  # m of soil drawn below the deepest toe
# Drawing-only sizes of the STS crane (not in its design values): assumed.
HEIGHT = 35.0  # m, the drawn crane overall above the rail (Ahmed 09-25: 30 to 40 m, for show)
LIFT = 26.0  # m, portal girder above the rail
BACKREACH = 25.0  # m, boom landward of the land-side rail
LEG_SPACING = 18.0  # m, between the legs along the quay (clear between the sill beams' legs)
GAUGE = 30.48  # m, rail gauge when there is no rear rail


class _Path:
    """The quay face in plan as a polyline: a point s along it (m from its start) and d across it
    (inland +). A straight berth is one straight run; a corner berth follows its front beam round
    each corner. Past the ends it runs straight on."""

    def __init__(self, verts: list[list[float]], inland: list[float]):
        self.v = [list(map(float, p)) for p in verts]
        self.t, self.n, self.s = [], [], [0.0]
        for a, b in zip(self.v, self.v[1:], strict=False):
            dx, dy = b[0] - a[0], b[1] - a[1]
            ln = math.hypot(dx, dy) or 1e-9
            self.t.append((dx / ln, dy / ln))
            self.s.append(self.s[-1] + ln)
        # Inland: the same side of every run, the side the berth frame says overall.
        side = sum(
            (self.s[i + 1] - self.s[i]) * (-t[1] * inland[0] + t[0] * inland[1]) for i, t in enumerate(self.t)
        )
        k = 1.0 if side >= 0 else -1.0
        self.n = [(-t[1] * k, t[0] * k) for t in self.t]

    @property
    def length(self) -> float:
        return self.s[-1]

    def _seg(self, s: float) -> int:
        return next((i for i in range(len(self.t)) if s <= self.s[i + 1] + 1e-9), len(self.t) - 1)

    def frame(self, s: float) -> tuple[list[float], tuple[float, float], tuple[float, float]]:
        """The face point at s, the direction along the face and inland there."""
        i = self._seg(s)
        tx, ty = self.t[i]
        a = self.v[i]
        return [a[0] + (s - self.s[i]) * tx, a[1] + (s - self.s[i]) * ty], self.t[i], self.n[i]

    def at(self, s: float, d: float, z: float) -> list[float]:
        p, _, n = self.frame(s)
        # At a corner: on the line that halves it, so the pieces either side meet.
        for i in range(1, len(self.v) - 1):
            if abs(s - self.s[i]) < 1e-6:
                mx, my = self.n[i - 1][0] + self.n[i][0], self.n[i - 1][1] + self.n[i][1]
                m = math.hypot(mx, my) or 1e-9
                cos = (mx * self.n[i][0] + my * self.n[i][1]) / m
                n = (mx / m / max(cos, 0.2), my / m / max(cos, 0.2))
        return [round(p[0] + d * n[0], 3), round(p[1] + d * n[1], 3), round(z, 3)]

    def near(self, s: float):
        """Positions round s on the straight through it: for a thing drawn whole (a crane, a block)."""
        p, t, n = self.frame(s)

        def at(s2: float, d: float, z: float) -> list[float]:
            q = s2 - s
            return [round(p[0] + q * t[0] + d * n[0], 3), round(p[1] + q * t[1] + d * n[1], 3), round(z, 3)]

        return at

    def lines(self, s0: float, s1: float, d: float, z: float) -> list[list[list[float]]]:
        """A line along the face from s0 to s1 at d, in one piece per straight."""
        cuts = [s0] + [s for s in self.s[1:-1] if s0 < s < s1] + [s1]
        return [[self.at(a, d, z), self.at(b, d, z)] for a, b in zip(cuts, cuts[1:], strict=False)]


def _face_line(points: list[list[float]], frame: dict[str, Any]) -> _Path:
    """The corner berth's quay face: the front beam's line (through its nodes' middle) moved half the
    beam's width out to sea, started at the end the straight berth frame starts at."""
    along = 0 if frame["along"] == "X" else 1
    pts = [list(map(float, p)) for p in points]
    if pts[0][along] > pts[-1][along]:
        pts.reverse()
    e = [0.0, 0.0]
    e[1 - along] = frame["inland"]
    mid = _Path(pts, e)
    half = frame["front_beam"]["width_mm"] / 2000
    return _Path([mid.at(s, -half, 0.0)[:2] for s in mid.s], e)


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


def sizes(section: Section, geometry: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each element's real size for drawing it extruded (m): ``round`` for piles and king piles (the
    diameter), else ``t`` (the thickness or depth across its plate), ``w`` (a beam's width, when given),
    ``at`` (the Plaxis plate at mid-depth or at the top) and ``top`` (a top of concrete given on the
    Clashes tab)."""
    top = section.clashes.plate_level
    out: dict[str, dict[str, Any]] = {}
    for g in geometry:
        name = g["element"]
        el = section.elements.get(name)
        if isinstance(el, PileInput):
            out[name] = {"round": el.diameter / 1000}
        elif isinstance(el, CombiWallInput):
            out[name] = {"round": el.tube_diameter / 1000}
        elif isinstance(el, SheetPileInput):
            try:
                h = sheet_piles.section(el.section_name).h / 1000
            except ValueError:
                h = 0.45
            out[name] = {"t": round(h, 3), "at": "mid"}
        elif isinstance(el, SlabInput | BeamInput):
            depth = el.thickness if isinstance(el, SlabInput) else el.depth
            item: dict[str, Any] = {"t": depth / 1000, "at": top}
            if isinstance(el, BeamInput) and el.width:
                item["w"] = el.width / 1000
            if name in section.clashes.top_levels:
                item["top"] = float(section.clashes.top_levels[name])
            out[name] = item
    return out


def _soffit(section: Section, geometry: list[dict[str, Any]]) -> float | None:
    """Underside of the deck: the slab's plate level less its thickness (plates at mid-depth, or at
    the top when the Clashes tab says so)."""
    top = section.clashes.plate_level == "top"
    out = []
    for g in geometry:
        el = section.elements.get(g["element"])
        if isinstance(el, SlabInput) and g.get("box"):
            z = g["box"]["Z"][0]
            out.append(z - el.thickness / 1000 * (1.0 if top else 0.5))
    return min(out) if out else None


def scene(
    project: Project,
    section: Section,
    geometry: list[dict[str, Any]],
    frame: dict[str, Any] | None,
    furniture: dict[str, Any] | None,
    line: list[list[float]] | None = None,
) -> dict[str, Any]:
    """What the 3D view draws round the structure. ``frame``: the berth frame (None when there is no
    element to lay a berth on); ``furniture``: the Furniture tab's result for the section, or None."""
    site = section.site
    notes: list[str] = []
    boxes = [b for g in geometry if (b := _extent(g))]
    if not boxes or frame is None:
        return {
            "site": site.model_dump(mode="json"),
            "sizes": sizes(section, geometry),
            "notes": ["No element positions in the workbook."],
        }
    lo = {a: min(b[a][0] for b in boxes) for a in "XYZ"}
    hi = {a: max(b[a][1] for b in boxes) for a in "XYZ"}
    along, across, inland = frame["along"], frame["across"], frame["inland"]
    face, start = frame["face"], frame["start"]

    def at(s: float, d: float, z: float) -> list[float]:
        p = {along: start + s, across: face + inland * d, "Z": z}
        return [round(p["X"], 3), round(p["Y"], 3), round(z, 3)]

    def d_of(v: float) -> float:
        return (v - face) * inland

    # The front wall: the combi wall or sheet pile wall nearest the quay face, else the face itself.
    wall_d, wall_name = None, None
    for g in geometry:
        el = section.elements.get(g["element"])
        if not isinstance(el, CombiWallInput | SheetPileInput) or not (b := _extent(g)):
            continue
        d = d_of((b[across][0] + b[across][1]) / 2)
        if d < 5.0 and (wall_d is None or abs(d) < abs(wall_d)):
            wall_d, wall_name = d, g["element"]
    if wall_d is None:
        wall_d = 0.0
        notes.append("No combi wall or sheet pile wall at the quay face: the soil steps down at the face.")
    soffit = _soffit(section, geometry)
    ground = site.soil_level
    if ground is None:
        ground = soffit if soffit is not None else frame["cope_m"] - 2.0
        ground_from = "the underside of the deck" if soffit is not None else "2 m below the cope (no slab)"
    else:
        ground_from = "as set for the section"
    bottom = min(lo["Z"], site.seabed_level) - BELOW
    land = max(d_of(lo[across]), d_of(hi[across])) + LAND
    s0, s1 = lo[along] - start, hi[along] - start
    seabed = min(site.seabed_level, ground)
    out: dict[str, Any] = {
        "site": site.model_dump(mode="json"),
        "sizes": sizes(section, geometry),
        "frame": {
            "along": along,
            "across": across,
            "inland": inland,
            "face": face,
            "start": start,
            "s": [round(s0, 3), round(s1, 3)],
        },
        "levels": {
            "seabed": seabed,
            "water": [w.level for w in site.water_levels],
            "ground": round(ground, 3),
            "ground_from": ground_from,
            "bottom": round(bottom, 3),
            "cope": frame["cope_m"],
        },
        "wall": {"element": wall_name, "d": round(wall_d, 3)},
        # Soil as blocks (s0, s1, d0, d1, top, bottom): in front of the wall, and behind it.
        "soil": [
            {
                "part": "sea",
                "s": [s0, s1],
                "d": [wall_d - SEA, wall_d],
                "top": seabed,
                "bottom": bottom,
                "corners": _corners(at, s0, s1, wall_d - SEA, wall_d),
            },
            {
                "part": "land",
                "s": [s0, s1],
                "d": [wall_d, land],
                "top": ground,
                "bottom": bottom,
                "corners": _corners(at, s0, s1, wall_d, land),
            },
        ],
        # Each named level its own plane over the sea bed, highest first.
        "water": [
            {"name": w.name, "level": w.level, "corners": _corners(at, s0, s1, wall_d - SEA, wall_d)}
            for w in sorted(site.water_levels, key=lambda w: -w.level)
            if w.level > seabed
        ],
    }
    low = min((w.level for w in site.water_levels), default=0.0)
    if line and len(line) >= 2:
        # A corner berth: the furniture follows the front beam's face round each corner.
        path = _face_line(line, frame)
        fs0, fs1 = 0.0, path.length
    else:
        e = [0.0, 0.0]
        e[0 if across == "X" else 1] = inland
        a0 = at(0.0, 0.0, 0.0)
        a1 = at(max(s1, 1.0), 0.0, 0.0)
        path = _Path([a0[:2], a1[:2]], e)
        fs0, fs1 = s0, s1
    items, rails, crane = _furniture(project, section, frame, furniture, path, fs0, fs1, low)
    out["furniture"] = items
    out["rails"] = rails
    out["crane"] = crane
    if crane and crane.get("notes"):
        notes += crane.pop("notes")
    if frame.get("notes"):
        notes += frame["notes"]
    waters = ", ".join(f"{w.name} {w.level:g} m" for w in site.water_levels) or "none"
    notes.append(
        f"Seabed {seabed:g} m; water {waters}; soil behind the wall at {ground:g} m ({ground_from})."
    )
    if section.existing.use:
        lay = existing.layout(project, section, geometry, frame)
        out["existing"] = {k: lay[k] for k in ("system", "edge_d", "cope", "dredge_level", "objects")}
        notes += lay["notes"]
    out["notes"] = notes
    return out


def _corners(at, s0: float, s1: float, d0: float, d1: float) -> list[list[float]]:
    """Plan corners (X, Y) of a block, in order round it."""
    return [at(s, d, 0.0)[:2] for s, d in ((s0, d0), (s1, d0), (s1, d1), (s0, d1))]


def _box(
    path: _Path, s: tuple[float, float], d: tuple[float, float], z: tuple[float, float]
) -> dict[str, Any]:
    """A block square to the face at its middle: its plan corners in order round it, and the box round
    them."""
    at = path.near((s[0] + s[1]) / 2)
    plan = [at(a, b, 0.0)[:2] for a, b in ((s[0], d[0]), (s[1], d[0]), (s[1], d[1]), (s[0], d[1]))]
    return {
        "X": [min(p[0] for p in plan), max(p[0] for p in plan)],
        "Y": [min(p[1] for p in plan), max(p[1] for p in plan)],
        "Z": sorted(z),
        "plan": plan,
    }


def _furniture(
    project: Project,
    section: Section,
    frame: dict[str, Any],
    result: dict[str, Any] | None,
    path: _Path,
    s0: float,
    s1: float,
    water: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    f = furniture.for_section(project.furniture, section.furniture)
    if not result or not result.get("use", True) or not result.get("layout"):
        return [], [], None
    lay = result["layout"]
    cope = result["cope_m"]
    items: list[dict[str, Any]] = []

    def inside(s: float) -> bool:
        return s0 - 0.5 <= s <= s1 + 0.5

    pr = f.protrusion
    proj = pr.projection / 1000 if pr else 0.0
    for row in lay["items"].get("fenders", []):
        s = row["s_m"]
        if not inside(s) or not f.fenders:
            continue
        fe = f.fenders
        zc = cope - ((pr.fender_centre_below_cope or pr.depth / 2000) if pr else fe.centre_below_cope)
        half = fe.flange / 2000
        if pr:
            items.append(
                {
                    "kind": "fender_blocks",
                    "label": f"Fender protrusion at {s:.1f} m",
                    "box": _box(
                        path,
                        (s - pr.length / 2000, s + pr.length / 2000),
                        (-proj, 0.0),
                        (cope - pr.depth / 1000, cope),
                    ),
                }
            )
        body = fe.height
        items.append(
            {
                "kind": "fenders",
                "label": f"{row['label']} at {s:.1f} m ({fe.name})",
                "box": _box(
                    path,
                    (s - half * 0.7, s + half * 0.7),
                    (-proj - body, -proj),
                    (zc - half * 0.7, zc + half * 0.7),
                ),
            }
        )
        panel = max(fe.flange / 1000 * 1.4, 1.5)
        items.append(
            {
                "kind": "fenders",
                "label": f"{row['label']} panel",
                "box": _box(
                    path,
                    (s - panel / 2, s + panel / 2),
                    (-proj - body - 0.3, -proj - body),
                    (zc - panel / 2, zc + panel / 2),
                ),
            }
        )
    for row in lay["items"].get("bollards", []):
        s = row["s_m"]
        if not inside(s) or not f.bollards:
            continue
        bo = f.bollards
        c = bo.centre_from_face
        half = bo.base / 2000
        items.append(
            {
                "kind": "bollards",
                "label": f"{row['label']} at {s:.1f} m ({bo.capacity:g} t)",
                "box": _box(path, (s - half, s + half), (c - half, c + half), (cope, cope + 0.08)),
            }
        )
        post = half * 0.55
        items.append(
            {
                "kind": "bollards",
                "label": f"{row['label']} at {s:.1f} m ({bo.capacity:g} t)",
                "box": _box(path, (s - post, s + post), (c - post, c + post), (cope, cope + 0.75)),
            }
        )
    for row in lay["items"].get("ladders", []):
        s = row["s_m"]
        if not inside(s):
            continue
        items.append(
            {
                "kind": "ladders",
                "label": f"{row['label']} at {s:.1f} m",
                "line": [
                    path.at(s, -proj - 0.05, cope),
                    path.at(s, -proj - 0.05, min(water - 1.0, cope - 1.0)),
                ],
            }
        )
    for row in (
        lay["items"].get("crane_stoppers", [])
        + lay["items"].get("storm_pins", [])
        + lay["items"].get("tie_downs", [])
    ):
        s = row["s_m"]
        if not inside(s):
            continue
        d0, d1 = row["across_m"]
        high = 1.2 if row["kind"] == "crane_stoppers" else 0.05
        items.append(
            {
                "kind": row["kind"],
                "label": f"{row['label']} ({row.get('tag') or ''}) at {s:.1f} m".replace(" ()", ""),
                "box": _box(path, (row["from_m"], row["to_m"]), (d0, d1), (cope, cope + high)),
            }
        )
    rails = [
        {"label": f"Crane rail, {r['tag']}", "line": ln}
        for r in lay.get("rails") or []
        for ln in path.lines(max(s0, 0.0), s1, r["across_m"], cope + 0.15)
    ]
    crane = _crane(project, section, lay, path, s0, s1, cope) if f.sts_crane else None
    return items, rails, crane


def _crane(
    project: Project, section: Section, lay: dict[str, Any], path: _Path, s0: float, s1: float, cope: float
):
    """A ship-to-shore crane outline: four legs, the portal girders and the boom from the backreach to
    the outreach, at the first stow position inside the model (else the model's middle)."""
    sts = project.furniture.sts_crane
    notes = []
    rf = lay.get("rail_front_m")
    rr = lay.get("rail_rear_m")
    if rf is None:
        rf = 1.0
        notes.append(
            "No crane rails in the furniture: the STS crane is drawn with its sea legs 1 m from the face."
        )
    if rr is None:
        rr = rf + GAUGE
        notes.append(f"No rear rail: the STS crane is drawn with a {GAUGE:g} m gauge.")
    spots = [s for s in section.furniture.stow_positions if s0 <= s <= s1]
    s = spots[0] if spots else (s0 + s1) / 2
    at = path.near(s)
    half = LEG_SPACING / 2
    top = cope + LIFT
    boom = top + 2.0
    apex = cope + HEIGHT
    legs = [[at(s + k * half, d, cope), at(s + k * half, d, top)] for k in (-1, 1) for d in (rf, rr)]
    lines = [
        *legs,
        # Sill beams along the quay and portal girders across it.
        [at(s - half, rf, cope + 6.0), at(s + half, rf, cope + 6.0)],
        [at(s - half, rr, cope + 6.0), at(s + half, rr, cope + 6.0)],
        [at(s - half, rf, top), at(s - half, rr, top)],
        [at(s + half, rf, top), at(s + half, rr, top)],
        [at(s - half, rf, top), at(s + half, rf, top)],
        [at(s - half, rr, top), at(s + half, rr, top)],
        # Boom: from the backreach to the outreach, measured from the sea-side rail.
        [at(s, rr + BACKREACH, boom), at(s, rf - sts.outreach, boom)],
        # A-frame and its stays.
        [at(s, rf, top), at(s, (rf + rr) / 2, apex)],
        [at(s, rr, top), at(s, (rf + rr) / 2, apex)],
        [at(s, (rf + rr) / 2, apex), at(s, rf - sts.outreach * 0.6, boom)],
        [at(s, (rf + rr) / 2, apex), at(s, rr + BACKREACH, boom)],
    ]
    notes.append(
        f"STS crane drawn at {s:.1f} m along the berth: outreach {sts.outreach:g} m from the sea-side rail "
        f"(Furniture tab); {HEIGHT:g} m high overall, backreach {BACKREACH:g} m and legs {LEG_SPACING:g} m "
        "apart are drawing "
        "sizes only (assumed)."
    )
    return {
        "label": f"STS crane (outreach {sts.outreach:g} m)",
        "lines": lines,
        # The ship it serves: its far row reached by the outreach.
        "reach": at(s, rf - sts.outreach, boom),
        "notes": notes,
    }
