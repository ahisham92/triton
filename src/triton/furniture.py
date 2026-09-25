"""Quay furniture along a section's berth: where each item goes, and its design.

The items are the project's (Project.furniture); the berth is the section's. Positions are distances
along the berth from its start (the front beam's end in the model) and across it from the quay face.

Arrangement. Fenders, bollards and ladders are spaced evenly along the berth, at no more than their
spacing, from the end distance at each end; ladders sit midway between fenders. The cranes are stowed
side by side from the berth start (or at the section's stow positions), each with a storm pin by the
front and the rear rail; crane stoppers close both ends of both rails. Each item is then moved (up
to the largest move allowed, the smallest move first) until it is clear of the expansion joints,
of the other items on the same face (the front face or the beam top), of the crane rails, and, where
its anchors reach below the beam's top bars, of the pile heads and their starter bars in plan. An
item that cannot be cleared stays at its spacing and is listed with what it clashes with.

The model covers a length of the berth; its piles are repeated along the whole berth at the spacing
of each pile line in the model. A berth that turns a corner is laid out along its developed length.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from .design import furniture as fd
from .design import protrusion
from .furniture_inputs import QuayFurniture, SectionFurniture
from .project import BeamInput, CombiWallInput, PileInput, Project, Section, with_project_grades

TOP_BARS = 150.0
RAIL_CLASH = "the crane rail (move the rail or the item across the beam)"  # mm: anchors shallower than this stay above the beam's top bars

LABEL = {
    "fenders": "Fender",
    "bollards": "Bollard",
    "ladders": "Ladder",
    "storm_pins": "Storm pin",
    "crane_stoppers": "Crane stopper",
    "tie_downs": "Crane tie-down",
    "tie_rods": "Tie rod",
    "crane_rails": "Crane rail",
    "fender_blocks": "Fender protrusion",
}
PLURAL = {
    "fenders": "Fenders",
    "bollards": "Bollards",
    "ladders": "Ladders",
    "storm_pins": "Storm pins",
    "crane_stoppers": "Crane stoppers",
    "tie_downs": "Crane tie-downs",
    "tie_rods": "Tie rods",
    "crane_rails": "Crane rails",
    "fender_blocks": "Fender protrusions",
}


# --- The berth in the model ----------------------------------------------------------------------------


def _beam(section: Section, kind: str) -> tuple[str, BeamInput] | None:
    return next(
        ((n, e) for n, e in section.elements.items() if isinstance(e, BeamInput) and e.kind == kind), None
    )


def berth_frame(project: Project, section: Section, geometry: list[dict[str, Any]]) -> dict[str, Any]:
    """The berth in model coordinates: which axis runs along it, where the quay face is and which way
    is inland, the model's start and length, the beams, the cope level and the pile heads."""
    notes: list[str] = []
    fb = _beam(section, "front_beam")
    rb = _beam(section, "rear_beam")
    by = {g["element"]: g for g in geometry}

    def extent(g: dict[str, Any]) -> dict[str, list[float]] | None:
        if g.get("box"):
            return g["box"]
        if g.get("lines"):
            xs = [ln[0] for ln in g["lines"]]
            ys = [ln[1] for ln in g["lines"]]
            zs = [ln[2] for ln in g["lines"]]
            return {"X": [min(xs), max(xs)], "Y": [min(ys), max(ys)], "Z": [min(zs), max(zs)]}
        return None

    fbox = extent(by[fb[0]]) if fb and fb[0] in by else None
    if fbox is None:
        # No front beam in the workbook: take the whole model.
        boxes = [b for g in geometry if (b := extent(g))]
        if not boxes:
            raise ValueError("The workbook has no element positions to lay the furniture out on.")
        fbox = {a: [min(b[a][0] for b in boxes), max(b[a][1] for b in boxes)] for a in "XYZ"}
        notes.append("No front beam in the workbook: the berth is taken along the whole model.")
    along = "Y" if fbox["Y"][1] - fbox["Y"][0] >= fbox["X"][1] - fbox["X"][0] else "X"
    across = "X" if along == "Y" else "Y"
    # Inland is towards the rest of the structure (the deck, the rear beam).
    others = [extent(g) for g in geometry if g["element"] != (fb[0] if fb else None)]
    mids = [(b[across][0] + b[across][1]) / 2 for b in others if b]
    fmid = (fbox[across][0] + fbox[across][1]) / 2
    inland = -1.0 if (median(mids) if mids else fmid - 1) < fmid else 1.0
    face = fbox[across][1] if inland < 0 else fbox[across][0]
    start = fbox[along][0]
    model_length = fbox[along][1] - fbox[along][0]

    def d_of(v: float) -> float:
        return (v - face) * inland

    fb_el = with_project_grades(fb[1], project.design.materials, project.design.durability) if fb else None
    fwidth = (fb_el.width if fb_el and fb_el.width else (fbox[across][1] - fbox[across][0]) * 1000) or 2000.0
    fdepth = fb_el.depth if fb_el else 1600.0
    concrete_grade = (fb_el.concrete if fb_el else None) or project.design.materials.concrete
    plate = fbox["Z"][1]
    cope = plate if section.clashes.plate_level == "top" else plate + fdepth / 2e3
    if fb and fb[0] in section.clashes.top_levels:
        cope = float(section.clashes.top_levels[fb[0]])
    rear = None
    if rb and rb[0] in by and (rbox := extent(by[rb[0]])):
        rb_el = with_project_grades(rb[1], project.design.materials, project.design.durability)
        rmid = d_of((rbox[across][0] + rbox[across][1]) / 2)
        rw = rb_el.width or abs(rbox[across][1] - rbox[across][0]) * 1000 or 2000.0
        rear = {
            "name": rb[0],
            "centre_m": round(rmid, 3),
            "width_mm": rw,
            "depth_mm": rb_el.depth,
            "concrete": rb_el.concrete or project.design.materials.concrete,
        }
    piles = []
    for g in geometry:
        el = section.elements.get(g["element"])
        if not g.get("lines") or not isinstance(el, (PileInput, CombiWallInput)):
            continue
        dia = el.diameter if isinstance(el, PileInput) else el.tube_diameter
        heads: dict[tuple[float, float], float] = {}
        for x, y, zhi, *_ in g["lines"]:
            key = (round(x, 2), round(y, 2))
            heads[key] = max(heads.get(key, -1e9), zhi)
        pos = sorted({round((x if along == "X" else y) - start, 3) for x, y in heads})
        acr = [d_of(x if across == "X" else y) for x, y in heads]
        piles.append(
            {
                "element": g["element"],
                "diameter_mm": dia,
                "across_m": round(median(acr), 3),
                "along_m": pos,
                "spacing_m": round(median([b - a for a, b in zip(pos, pos[1:], strict=False)]), 3)
                if len(pos) > 1
                else None,
                "top_m": round(max(heads.values()), 3),
            }
        )
    return {
        "along": along,
        "across": across,
        "inland": inland,
        "face": round(face, 3),
        "start": round(start, 3),
        "model_length_m": round(model_length, 3),
        "front_beam": {
            "name": fb[0] if fb else "Front Beam",
            "width_mm": fwidth,
            "depth_mm": fdepth,
            "concrete": concrete_grade,
            "cover_mm": (fb_el.cover if fb_el and fb_el.cover else project.design.durability.covers.beams),
            "joint_spacing_m": fb_el.joint_spacing if fb_el else 58.0,
        },
        "rear_beam": rear,
        "cope_m": round(cope, 3),
        "piles": piles,
        "notes": notes,
    }


# --- Arrangement -------------------------------------------------------------------------------------


@dataclass
class Item:
    kind: str
    s: float  # along the berth, m
    half: float  # half its length along the berth, m
    d0: float  # across: from, m from the face
    d1: float  # across: to
    plane: str  # "face" or "top"
    deep: bool  # anchors below the top bars
    nominal: float = 0.0
    tag: str = ""
    clashes: list[str] = field(default_factory=list)
    joint_half: float = 0.0  # half the length kept clear of joints if over ``half`` (fender block)

    def to_dict(self, n: int) -> dict[str, Any]:
        moved = round(self.s - self.nominal, 2)
        return {
            "id": f"{self.kind}-{n}",
            "kind": self.kind,
            "label": f"{LABEL[self.kind]} {n}",
            "tag": self.tag,
            "s_m": round(self.s, 2),
            "nominal_m": round(self.nominal, 2),
            "moved_m": moved,
            "from_m": round(self.s - self.half, 3),
            "to_m": round(self.s + self.half, 3),
            "across_m": [round(self.d0, 3), round(self.d1, 3)],
            "plane": self.plane,
            "status": "clash" if self.clashes else "moved" if abs(moved) >= 0.01 else "ok",
            "clashes": self.clashes,
        }


def evenly(length: float, end: float, spacing: float) -> list[float]:
    """Positions from ``end`` to ``length − end`` at no more than ``spacing`` apart."""
    run = length - 2 * end
    if run <= 0:
        return [length / 2]
    n = math.ceil(run / spacing - 1e-9) + 1
    return [end + i * run / (n - 1) for i in range(n)]


def ladder_spots(fenders: list[float], spacing: float, length: float) -> list[float]:
    """Midway between fenders, in every gap that keeps them no more than ``spacing`` apart."""
    if len(fenders) < 2:
        return evenly(length, min(5.0, length / 4), spacing)
    gaps = [(a + b) / 2 for a, b in zip(fenders, fenders[1:], strict=False)]
    gap = (fenders[-1] - fenders[0]) / (len(fenders) - 1)
    every = max(1, int(spacing / gap + 1e-9))
    picks = gaps[::every]
    if gaps[-1] not in picks and length - picks[-1] > spacing / 2:
        picks.append(gaps[-1])
    return picks


def stow_spots(f: QuayFurniture, sf: SectionFurniture) -> list[float]:
    if sf.stow_positions:
        return list(sf.stow_positions)
    st = f.crane_stoppers
    start = st.end_distance + st.base_length / 1000 if st else 1.0
    return [start + f.storm_pins.crane_width * (i + 0.5) for i in range(f.storm_pins.cranes)]


def tie_down_spots(f: QuayFurniture, sf: SectionFurniture) -> list[float]:
    """A set at each leg of each stowed crane: half the leg spacing each side of its stow position."""
    half = f.tie_downs.leg_spacing / 2
    return [s + k * half for s in stow_spots(f, sf) for k in (-1, 1)]


def nominal(f: QuayFurniture, sf: SectionFurniture, length: float) -> list[dict[str, Any]]:
    """Where the items go at their spacing, before anything moves them: what the expansion joints
    keep clear of."""
    if not sf.use or length <= 0:
        return []
    out: list[tuple[str, float]] = []
    fenders = evenly(length, f.fenders.end_distance, f.fenders.spacing) if f.fenders else []
    out += [("Fender", s) for s in fenders]
    if f.ladders:
        out += [("Ladder", s) for s in ladder_spots(fenders, f.ladders.spacing, length)]
    if f.bollards:
        out += [("Bollard", s) for s in evenly(length, f.bollards.end_distance, f.bollards.spacing)]
    if f.crane_rails and f.crane_stoppers:
        half = f.crane_stoppers.base_length / 2000
        out += [("Crane stopper", f.crane_stoppers.end_distance + half)]
        out += [("Crane stopper", length - f.crane_stoppers.end_distance - half)]
    if f.crane_rails and f.storm_pins and f.storm_pins.cranes:
        out += [("Storm pin", s) for s in stow_spots(f, sf)]
        if f.tie_downs:
            out += [("Crane tie-down", s) for s in tie_down_spots(f, sf)]
    return [{"name": n, "chainage": round(c, 3)} for n, c in out if 0 <= c <= length]


def positions_for(project: Project, section: Section):
    """``nominal`` for this section, as the expansion joints ask for it (by berth length)."""
    return lambda length: nominal(project.furniture, section.furniture, length)


def pile_heads(frame: dict[str, Any], length: float) -> list[dict[str, float]]:
    """Every pile head along the berth: the model's piles repeated at each pile line's spacing."""
    out = []
    for p in frame["piles"]:
        sp = p["spacing_m"] or frame["model_length_m"] or length
        if not p["along_m"] or sp <= 0:
            continue
        phase = p["along_m"][0] % sp
        k = 0
        while phase + k * sp <= length + 1e-6:
            out.append(
                {
                    "s": phase + k * sp,
                    "d": p["across_m"],
                    "r": p["diameter_mm"] / 2000,
                    "element": p["element"],
                }
            )
            k += 1
    return out


def joints_of(
    frame: dict[str, Any], joints: list[float] | None, length: float, joints_from: str = ""
) -> tuple[list[float], str]:
    if joints is not None:
        return sorted(j for j in joints if 0 < j < length), joints_from or "the expansion joint layout"
    sp = frame["front_beam"]["joint_spacing_m"]
    return [k * sp for k in range(1, int(length / sp + 1e-9) + 1) if k * sp < length - 1e-6], (
        f"every {sp:g} m (the front beam's length between joints)"
    )


def _circle_hits(it: Item, heads: list[dict[str, float]], clear: float) -> list[str]:
    out = []
    for h in heads:
        ds = max(abs(h["s"] - it.s) - it.half, 0.0)
        dd = max(it.d0 - h["d"], h["d"] - it.d1, 0.0)
        if math.hypot(ds, dd) < h["r"] + clear:
            out.append(f"{h['element']} head at {h['s']:.2f} m")
    return out


def _conflicts(
    it: Item,
    placed: list[Item],
    joints: list[float],
    heads: list[dict],
    rails: list[tuple[float, float]],
    rules,
) -> list[str]:
    out = []
    for j in joints:
        if abs(j - it.s) < max(it.half, it.joint_half) + rules.joint_clearance:
            out.append(f"expansion joint at {j:.2f} m")
    for o in placed:
        if o.plane != it.plane:
            continue
        if (
            abs(o.s - it.s) < o.half + it.half + rules.item_clearance
            and min(o.d1, it.d1) > max(o.d0, it.d0) - 1e-9
        ):
            out.append(f"{LABEL[o.kind].lower()} {o.tag or ''}".strip() + f" at {o.s:.2f} m")
    if it.plane == "top" and it.kind not in ("crane_stoppers", "tie_downs"):
        for r0, r1 in rails:
            if min(r1, it.d1) > max(r0, it.d0):
                out.append(RAIL_CLASH)
    if it.deep:
        out += _circle_hits(it, heads, rules.pile_clearance / 1000)
    return out


def _place(it: Item, placed, joints, heads, rails, rules, length: float) -> Item:
    it.nominal = it.s
    step = 0.05
    n = int(rules.max_shift / step + 1e-9)
    for k in [0] + [s * i for i in range(1, n + 1) for s in (1, -1)]:
        s = it.nominal + k * step
        if s - it.half < -1e-9 or s + it.half > length + 1e-9:
            continue
        it.s = s
        # A rail runs the whole berth: moving along it never clears it, so it is only reported.
        if not [c for c in _conflicts(it, placed, joints, heads, rails, rules) if c != RAIL_CLASH]:
            break
    else:
        it.s = it.nominal
    it.clashes = list(dict.fromkeys(_conflicts(it, placed, joints, heads, rails, rules)))
    return it


def arrange(
    f: QuayFurniture,
    sf: SectionFurniture,
    frame: dict[str, Any],
    length: float,
    joints: list[float] | None = None,
    joints_from: str = "",
) -> dict[str, Any]:
    rules = f.rules
    heads = pile_heads(frame, length)
    joints, joints_from = joints_of(frame, joints, length, joints_from)
    fb = frame["front_beam"]
    rail_f = rail_r = None
    rails: list[tuple[float, float]] = []
    if f.crane_rails:
        prop = fd.RAILS[f.crane_rails.rail]
        rail_f = f.crane_rails.front_rail_from_face or fb["width_mm"] / 2000
        gauge = f.crane_rails.gauge or (
            (frame["rear_beam"]["centre_m"] - rail_f) if frame["rear_beam"] else None
        )
        rail_r = rail_f + gauge if gauge else None
        half = prop["foot"] / 2000 + 0.15  # the clips beside the foot
        rails = [(r - half, r + half) for r in (rail_f, rail_r) if r is not None]
    placed: list[Item] = []
    out: dict[str, list[Item]] = {}

    if f.fenders:
        fe = f.fenders
        a = fe.anchors
        half = max(fe.flange / 2000, a.circle_diameter / 2000 + (a.head_diameter or 3 * a.diameter) / 2000)
        # The whole fender block stays clear of the joints; its bolts alone are checked against the piles.
        block_half = f.protrusion.length / 2000 if f.protrusion else 0.0
        items = []
        # On a protrusion block the bolts stay in the block, seaward of the beam and its pile heads.
        in_beam = f.protrusion is None or a.embedment >= f.protrusion.projection
        for s in evenly(length, fe.end_distance, fe.spacing):
            items.append(
                _place(
                    Item(
                        "fenders",
                        s,
                        half,
                        0.0,
                        a.embedment / 1000 + 0.05,
                        "face",
                        in_beam,
                        joint_half=block_half,
                    ),
                    placed,
                    joints,
                    heads,
                    rails,
                    rules,
                    length,
                )
            )
            placed.append(items[-1])
        out["fenders"] = items
    if f.ladders:
        la = f.ladders
        half = la.recess / 2000
        depth = 0.2 + la.anchors.embedment / 1000
        picks = ladder_spots([i.s for i in out.get("fenders", [])], la.spacing, length)
        items = []
        for s in picks:
            items.append(
                _place(
                    Item("ladders", s, half, 0.0, depth, "face", True),
                    placed,
                    joints,
                    heads,
                    rails,
                    rules,
                    length,
                )
            )
            placed.append(items[-1])
        out["ladders"] = items
    if f.bollards:
        bo = f.bollards
        half = bo.base / 2000
        c = bo.centre_from_face
        items = []
        for s in evenly(length, bo.end_distance, bo.spacing):
            items.append(
                _place(
                    Item("bollards", s, half, c - half, c + half, "top", bo.anchors.embedment > TOP_BARS),
                    placed,
                    joints,
                    heads,
                    rails,
                    rules,
                    length,
                )
            )
            placed.append(items[-1])
        out["bollards"] = items
    if f.crane_stoppers and rail_f is not None:
        st = f.crane_stoppers
        half = st.base_length / 2000
        items = []
        for tag, r in (("front rail", rail_f), ("rear rail", rail_r)):
            if r is None:
                continue
            for s in (st.end_distance + half, length - st.end_distance - half):
                it = Item(
                    "crane_stoppers",
                    s,
                    half,
                    r - st.base_width / 2000,
                    r + st.base_width / 2000,
                    "top",
                    st.anchors.embedment > TOP_BARS,
                    tag=tag,
                )
                items.append(_place(it, placed, joints, heads, rails, rules, length))
                placed.append(items[-1])
        out["crane_stoppers"] = items
    if f.storm_pins and rail_f is not None and f.storm_pins.cranes:
        sp = f.storm_pins
        half = sp.socket_length / 2000
        stops = (
            f.crane_stoppers.end_distance + f.crane_stoppers.base_length / 1000 if f.crane_stoppers else 1.0
        )
        spots = sf.stow_positions or [stops + sp.crane_width * (i + 0.5) for i in range(sp.cranes)]
        items = []
        for s0 in spots:
            for tag, r in (("front rail", rail_f), ("rear rail", rail_r)):
                if r is None:
                    continue
                d = r + sp.offset_from_rail
                it = Item(
                    "storm_pins",
                    s0,
                    half,
                    d - sp.socket_width / 2000,
                    d + sp.socket_width / 2000,
                    "top",
                    sp.socket_depth > TOP_BARS,
                    tag=tag,
                )
                items.append(_place(it, placed, joints, heads, rails, rules, length))
                placed.append(items[-1])
        out["storm_pins"] = items
        if f.tie_downs:
            td = f.tie_downs
            reach = (td.offset_from_rail if td.plates > 1 else 0.0) + td.plate_width / 2000
            items = []
            for s0 in [s + k * td.leg_spacing / 2 for s in spots for k in (-1, 1)]:
                for tag, r in (("front rail", rail_f), ("rear rail", rail_r)):
                    if r is None:
                        continue
                    it = Item(
                        "tie_downs",
                        s0,
                        td.plate_length / 2000,
                        r - reach,
                        r + reach,
                        "top",
                        td.anchors.embedment > TOP_BARS,
                        tag=tag,
                    )
                    items.append(_place(it, placed, joints, heads, rails, rules, length))
                    placed.append(items[-1])
            out["tie_downs"] = items

    counts = {k: len(v) for k, v in out.items()}
    rows = {k: [it.to_dict(i + 1) for i, it in enumerate(v)] for k, v in out.items()}
    lines = []
    if rails:
        counts["crane_rails"] = len(rails)
        lines = [
            {"kind": "crane_rails", "tag": t, "across_m": round(r, 3), "length_m": round(length, 2)}
            for t, r in (("front rail", rail_f), ("rear rail", rail_r))
            if r is not None
        ]
    if f.tie_rods and not sf.no_tie_rods:
        counts["tie_rods"] = math.floor(length / f.tie_rods.spacing + 1e-6) + 1
    return {
        "items": rows,
        "counts": counts,
        "rails": lines,
        "rail_joints_m": [round(j, 2) for j in joints] if rails else [],
        "joints_m": [round(j, 2) for j in joints],
        "joints_from": joints_from,
        "pile_heads": [{**h, "s": round(h["s"], 3)} for h in heads],
        "rail_front_m": rail_f,
        "rail_rear_m": rail_r,
        "clashes": sum(1 for v in rows.values() for r in v if r["status"] == "clash"),
        "moved": sum(1 for v in rows.values() for r in v if r["status"] == "moved"),
    }


# --- The section's furniture -------------------------------------------------------------------------


def berth_length(section: Section, frame: dict[str, Any]) -> tuple[float, str]:
    sf = section.furniture
    if sf.berth_length:
        return sf.berth_length, "given for this section's furniture"
    if section.costing.berth_length:
        return section.costing.berth_length, "the Costing berth length"
    return frame["model_length_m"], "the model's length (give the berth length on the Costing tab)"


def assumptions(f: QuayFurniture) -> list[str]:
    """What the user should confirm: every value filled in as a common one, not from this project."""
    out = []
    if f.fenders:
        out.append(
            f"Fender: {f.fenders.name}, rated reaction {f.fenders.reaction:g} kN"
            + (f", energy {f.fenders.energy:g} kNm" if f.fenders.energy else "")
            + f", height {f.fenders.height:g} m, "
            f"{f.fenders.anchors.count} × M{f.fenders.anchors.diameter} on a {f.fenders.anchors.circle_diameter:g} mm circle, "
            f"every {f.fenders.spacing:g} m."
        )
    if f.bollards:
        out.append(
            f"Bollard: {f.bollards.capacity:g} t, load factor {f.bollards.load_factor:g} for its fixing, lines up to "
            f"{f.bollards.max_vertical_angle:g}°, every {f.bollards.spacing:g} m."
        )
    if f.storm_pins:
        out.append(
            f"Storm pin force {f.storm_pins.force:g} kN per pin (service, × {f.storm_pins.load_factor:g}), "
            f"{f.storm_pins.cranes} cranes stowed."
        )
    if f.crane_stoppers:
        out.append(
            f"Crane stopper buffer force {f.crane_stoppers.force:g} kN (service, × {f.crane_stoppers.load_factor:g}) "
            f"at {f.crane_stoppers.buffer_height:g} m."
        )
    if f.tie_downs and f.storm_pins and f.storm_pins.cranes:
        t = f.tie_downs
        out.append(
            f"Crane tie-downs {t.force:g} kN uplift per set (service, × {t.load_factor:g}), {t.plates} plates per set "
            f"{t.offset_from_rail:g} m each side of the rail, a set at each leg, legs {t.leg_spacing:g} m apart."
        )
    if f.crane_rails:
        out.append(
            f"Rail {f.crane_rails.rail}, wheel load {f.crane_rails.wheel_load:g} kN, {f.crane_rails.wheels} wheels at "
            f"{f.crane_rails.wheel_spacing:g} mm, pad modulus {f.crane_rails.pad_stiffness:g} N/mm²."
        )
    if f.ladders:
        out.append(f"Ladders every {f.ladders.spacing:g} m at most, down to {f.ladders.bottom_level:g} m.")
    if f.protrusion:
        p = f.protrusion
        out.append(
            f"Fender protrusion {p.projection:g} × {p.depth:g} × {p.length:g} mm at every fender, joined to the beam by a "
            f"{p.joint} joint{' (cast after the beam)' if p.joint != 'monolithic' else ''}; the fender reaction does not "
            "act on the front beam in the Plaxis model, so the block's torque and shear are extra to the beam's design."
        )
    if f.sts_crane:
        c = f.sts_crane
        out.append(
            f"STS crane: outreach {c.outreach:g} m from the seaside rail for a {c.ship_beam:g} m wide ship, far row "
            f"{c.far_row_inside:g} m inside its side; legs {c.leg_seaward_of_rail:g} m seaward of the rail; ship's flare "
            f"{c.flare_overhang:g} m and {c.min_clearance:g} m clearance kept; fender panel {c.panel_thickness:g} m, "
            f"deflection {c.rated_deflection:g} at the rated reaction. No code gives these. The ship's beam, the panel and "
            "the deflection defaults are the design report's (RPT-ST-01, section 7); the outreach, legs, far row, flare "
            "and clearance are assumed until the crane specification gives them."
        )
    if f.tie_rods:
        out.append(
            f"Tie rods: {f.tie_rods.force:g} kN/m design force at {f.tie_rods.spacing:g} m, anchored in the front beam."
        )
    return out


def design(
    project: Project,
    section: Section,
    geometry: list[dict[str, Any]],
    joints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Arrangement and design of the project's furniture on this section's berth. ``joints``: the
    section's expansion joint layout (``joints.section_joints``); without one, a joint at each of the
    front beam's lengths between joints."""
    f: QuayFurniture = project.furniture
    sf: SectionFurniture = section.furniture
    if not sf.use:
        return {"section": section.name, "section_id": section.id, "use": False}
    frame = berth_frame(project, section, geometry)
    length, length_from = berth_length(section, frame)
    fb = frame["front_beam"]
    front = fd.Beam(fb["width_mm"], fb["depth_mm"], fb["concrete"], fb["name"])
    rear = frame["rear_beam"]
    rear_beam = fd.Beam(rear["width_mm"], rear["depth_mm"], rear["concrete"], rear["name"]) if rear else None
    placed = None
    placed_from = ""
    if joints and joints.get("segments"):
        placed = [j["chainage"] for j in joints["joints"]]
        placed_from = "the expansion joint layout (Sections tab)"
        if abs(joints["berth_length_m"] - length) > 0.01:
            frame["notes"].append(
                f"The expansion joints are laid along {joints['berth_length_m']:g} m of berth, the furniture along "
                f"{length:g} m."
            )
    lay = arrange(f, sf, frame, length, placed, placed_from)
    if f.protrusion and lay["counts"].get("fenders"):
        lay["counts"]["fender_blocks"] = lay["counts"]["fenders"]  # one block at every fender
    items: list[dict[str, Any]] = []
    if f.fenders:
        if f.protrusion:
            # The fender is bolted to the block's face: its depth, the fender centre on it.
            p = f.protrusion
            centre = p.fender_centre_below_cope or p.depth / 2000
            on_block = fd.Beam(front.width + p.projection, p.depth, p.concrete or front.concrete, front.name)
            items.append(fd.fender(f.fenders.model_copy(update={"centre_below_cope": centre}), on_block))
            items[-1]["title"] += (
                f" on the protrusion ({p.depth:.0f} mm deep face, centre {centre:g} m below the cope)"
            )
        else:
            items.append(fd.fender(f.fenders, front))
    if f.protrusion:
        items.append(
            protrusion.block(
                f.protrusion,
                f.fenders,
                front.width,
                front.depth,
                front.concrete,
                fb["cover_mm"],
                project.design.partial_factors.gamma_c,
                project.design.partial_factors.gamma_s,
                project.design.partial_factors.alpha_cc,
                f.bollards,
            )
        )
    if f.sts_crane:
        rail = lay["rail_front_m"] if lay["rail_front_m"] is not None else front.width / 2000
        items.append(protrusion.clearance(f.sts_crane, f.fenders, f.protrusion, rail))
        if lay["rail_front_m"] is None:
            items[-1]["notes"].append(
                "No crane rails in the items: the front rail is taken over the beam's centre."
            )
    if f.bollards:
        items.append(fd.bollard(f.bollards, front))
        own = next(
            (e.bollard for e in section.elements.values() if isinstance(e, BeamInput) and e.bollard), None
        )
        if own is not None and abs(own.capacity - f.bollards.capacity) > 1e-6:
            items[-1]["notes"].append(
                f"The front beam's tie bar check uses a {own.capacity:g} t bollard; this is {f.bollards.capacity:g} t."
            )
    if f.ladders:
        items.append(fd.ladder(f.ladders, front, frame["cope_m"]))
    if f.crane_rails and lay["rail_front_m"] is not None:
        items.append(fd.rail(f.crane_rails, front, lay["rail_front_m"]))
        if rear_beam and lay["rail_rear_m"] is not None:
            r = fd.rail(f.crane_rails, rear_beam, rear_beam.width / 2000)
            r["title"] += f" on the {rear_beam.name}"
            r["item"] = "crane_rails_rear"
            items.append(r)
    if f.crane_stoppers and lay["rail_front_m"] is not None:
        items.append(fd.stopper(f.crane_stoppers, front, lay["rail_front_m"]))
    if f.storm_pins and f.storm_pins.cranes:
        items.append(fd.storm_pin(f.storm_pins, front))
        if f.tie_downs and lay["rail_front_m"] is not None:
            items.append(fd.tie_down(f.tie_downs, front, lay["rail_front_m"]))
    if f.tie_rods and not sf.no_tie_rods:
        items.append(fd.tie_rod(f.tie_rods, front))
    notes = list(frame["notes"])
    if lay["clashes"]:
        notes.append(f"{lay['clashes']} item(s) could not be moved clear: see the arrangement.")
    return {
        "section": section.name,
        "section_id": section.id,
        "use": sf.use,
        "berth_length_m": round(length, 2),
        "berth_length_from": length_from,
        "cope_m": frame["cope_m"],
        "frame": {k: v for k, v in frame.items() if k != "piles"},
        "piles": frame["piles"],
        "layout": lay,
        "items": items,
        "unsafe": [i["title"] for i in items if not i["passed"]],
        "protrusion": f.protrusion.model_dump(mode="json") if f.protrusion else None,
        "assumptions": assumptions(f),
        "notes": notes,
    }


def counts_for_costing(result: dict[str, Any] | None) -> dict[str, int]:
    """Numbers along the berth by the Costing item names they fill in (Fenders, Bollards, ...)."""
    if not result or not result.get("use", True):
        return {}
    return {PLURAL[k]: v for k, v in (result.get("layout") or {}).get("counts", {}).items() if k in PLURAL}
