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
    items, rails, crane = _furniture(project, section, frame, furniture, at, s0, s1, low)
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


def _box(at, s: tuple[float, float], d: tuple[float, float], z: tuple[float, float]) -> dict[str, Any]:
    a, b = at(s[0], d[0], z[0]), at(s[1], d[1], z[1])
    return {
        "X": sorted([a[0], b[0]]),
        "Y": sorted([a[1], b[1]]),
        "Z": sorted([a[2], b[2]]),
    }


def _furniture(
    project: Project,
    section: Section,
    frame: dict[str, Any],
    result: dict[str, Any] | None,
    at,
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
                        at,
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
                    at,
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
                    at,
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
                "box": _box(at, (s - half, s + half), (c - half, c + half), (cope, cope + 0.08)),
            }
        )
        post = half * 0.55
        items.append(
            {
                "kind": "bollards",
                "label": f"{row['label']} at {s:.1f} m ({bo.capacity:g} t)",
                "box": _box(at, (s - post, s + post), (c - post, c + post), (cope, cope + 0.75)),
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
                "line": [at(s, -proj - 0.05, cope), at(s, -proj - 0.05, min(water - 1.0, cope - 1.0))],
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
                "box": _box(at, (row["from_m"], row["to_m"]), (d0, d1), (cope, cope + high)),
            }
        )
    rails = [
        {
            "label": f"Crane rail, {r['tag']}",
            "line": [at(max(s0, 0.0), r["across_m"], cope + 0.15), at(s1, r["across_m"], cope + 0.15)],
        }
        for r in lay.get("rails") or []
    ]
    crane = _crane(project, section, lay, at, s0, s1, cope) if f.sts_crane else None
    return items, rails, crane


def _crane(project: Project, section: Section, lay: dict[str, Any], at, s0: float, s1: float, cope: float):
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
