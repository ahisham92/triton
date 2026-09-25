"""Quay furniture: the fixing of each item into the beam, and the items made of steel.

Fenders: the flange is bolted to the front face. Under the rated reaction R (× the load factor) the
panel's friction μR acts in the panel face at the fender height H in front of the flange, along the
quay or up and down, with the panel weight. The flange's bolts take that shear and the moment
μR·H (and W·H), less the reaction pressing the flange on (friction only comes with it); the panel
weight is also checked on its own. The reaction itself bears on the face (EN 1992-1-1 6.7) with bursting bars behind the flange.

Bollards: the line pull F = factor × rated capacity × g at any plan angle towards the sea and up to
the steepest line angle; its horizontal part at the line height over the base is a moment on the
bolts, its vertical part pulls them out. Every angle in 15° steps is checked and the worst kept.

Crane stoppers: the buffer force along the rail at the buffer height, as shear and moment on the
base bolts.

Storm pins: the pin's horizontal force bears on the socket wall (EN 1992-1-1 6.7) and is carried
back into the beam by loops round the socket, As = F / fyd.

Crane rails: the rail on a continuous resilient pad is a beam on an elastic foundation
(β = (k / 4EI)^¼): each wheel of a corner gives M = P/(4β)·e^(−βx)(cos βx − sin βx) and a settlement
w = Pβ/(2k)·e^(−βx)(cos βx + sin βx), added over the wheels. The rail's bending stress is checked
against its yield, the pad pressure k·w / b on the concrete (6.7), and the lateral force (a share of
the wheel load) against the clips over the length π/β, with the clip bolt to EN 1992-4.

Ladders: rungs as simply supported bars under 1.5 kN per person (EN ISO 14122-4) × 1.5, stringers
and bracket bolts under the people on one bracket span at the rung's standoff, all with the
corrosion loss taken off.

Tie rods (EN 1993-5 7.2.3): Ft,Rd = min(Ftg,Rd = fy·Ag / γM0, Ftt,Rd = kt·0.9·fua·As / γM2), with
γM0 = 1.0 and γM2 = 1.25, the shaft and thread reduced by the corrosion loss on each face; in
service Ft,ser ≤ fy·As / γM,ser (1.1). The anchor plate bears on the beam (6.7).

Anchor bolts are checked by ``anchors`` (EN 1992-4). Items from a catalogue (fender units, bollard
castings, clips, pins) are the supplier's; only their fixing and the concrete round it are designed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..furniture_inputs import (
    Anchors,
    Bollards,
    CraneRails,
    CraneStoppers,
    Fenders,
    Ladders,
    StormPins,
    TieDowns,
    TieRods,
)
from ..materials import concrete
from .anchors import BOLT_GRADES, Group, check_group, circle, grid, local_bearing, stress_area

G = 9.81
E_STEEL = 210_000.0

# Rail catalogue (DIN 536-1 A rails; MRS and CR rails from common catalogues, approximate):
# height, head width, foot width (mm), area (cm²), Iy (cm⁴), mass (kg/m).
RAILS: dict[str, dict[str, float]] = {
    "A100": {"h": 95, "head": 100, "foot": 200, "area": 94.9, "I": 856, "mass": 74.3},
    "A120": {"h": 105, "head": 120, "foot": 220, "area": 127.4, "I": 1361, "mass": 100.0},
    "A150": {"h": 150, "head": 150, "foot": 220, "area": 191.4, "I": 4373, "mass": 150.3},
    "MRS 87A": {"h": 152, "head": 102, "foot": 152, "area": 110.6, "I": 2010, "mass": 86.8},
    "MRS 125": {"h": 180, "head": 120, "foot": 180, "area": 159.2, "I": 4260, "mass": 125.0},
    "175 lb CR": {"h": 152, "head": 108, "foot": 152, "area": 110.6, "I": 2130, "mass": 86.8},
}
TIE_GRADES = {"S355J2": (355.0, 470.0), "ASDO 500": (500.0, 660.0), "ASDO 700": (700.0, 860.0)}


@dataclass
class Beam:
    """The beam an item sits in: width across the quay, depth, concrete (mm)."""

    width: float
    depth: float
    concrete: str
    name: str = "Front Beam"


def _positions(a: Anchors) -> list[tuple[float, float]]:
    if a.pattern == "circle":
        # Start half a pitch off the axes, so no bolt sits alone nearest an edge.
        return circle(a.count, a.circle_diameter, 180.0 / a.count)
    return grid(a.rows, a.columns, a.spacing_along, a.spacing_across)


def group(a: Anchors, c_pos: float, c_neg: float, thickness: float, conc: str) -> Group:
    return Group(
        bolts=_positions(a),
        diameter=float(a.diameter),
        grade=a.grade,
        hef=a.embedment,
        head=a.head_diameter or 3.0 * a.diameter,
        c_pos=c_pos,
        c_neg=c_neg,
        thickness=thickness,
        concrete_grade=conc,
    )


def _spread(a: Anchors) -> tuple[float, float]:
    ps = _positions(a)
    return min(v for _, v in ps), max(v for _, v in ps)


def _worst(cases: list[tuple[str, dict]]) -> tuple[str, dict]:
    return max(cases, key=lambda c: c[1]["utilisation"] if c[1]["utilisation"] is not None else math.inf)


def _result(item: str, title: str, parts: list[tuple[str, float | None, bool]], **extra) -> dict[str, Any]:
    us = [u for _, u, _ in parts]
    u = max((x for x in us if x is not None), default=0.0) if all(x is not None for x in us) else None
    return {
        "item": item,
        "title": title,
        "utilisation": round(u, 3) if u is not None else None,
        "passed": bool(u is not None and u <= 1.0),
        "parts": [{"part": p, "utilisation": x, "passed": ok} for p, x, ok in parts],
        **extra,
    }


def fender(f: Fenders, beam: Beam) -> dict[str, Any]:
    vmin, vmax = _spread(f.anchors)
    centre = f.centre_below_cope * 1000
    c_top = centre - vmax
    c_bottom = beam.depth - centre + vmin
    g = group(f.anchors, c_top, c_bottom, beam.width, beam.concrete)
    r = f.load_factor * f.reaction
    w = f.load_factor * f.panel_weight
    fr = f.friction * r
    h = f.height
    # +v is up the face (towards the cope); mu > 0 puts the top bolts in tension.
    # Friction comes only with the reaction pressing the flange on (N = -R); the weight also acts alone.
    cases = [
        ("Friction along the quay", check_group(g, -r, fr, -w, w * h, fr * h)),
        ("Friction upwards", check_group(g, -r, 0.0, fr - w, -(fr - w) * h, 0.0)),
        ("Friction downwards", check_group(g, -r, 0.0, -fr - w, (fr + w) * h, 0.0)),
    ]
    if w > 0:
        cases.append(("Panel weight, no reaction", check_group(g, 0.0, 0.0, -w, w * h, 0.0)))
    name, worst = _worst(cases)
    flange = f.flange
    side = flange * math.sqrt(math.pi) / 2  # the square of the flange's area
    bearing = local_bearing(
        r, side, side, min(3 * side, 2 * side + 1000.0), min(3 * side, beam.depth), beam.concrete
    )
    spacing = None
    if f.smallest_ship:
        limit = 0.15 * f.smallest_ship
        spacing = {
            "spacing_m": f.spacing,
            "limit_m": round(limit, 1),
            "utilisation": round(f.spacing / limit, 3),
            "passed": f.spacing <= limit + 1e-9,
            "rule": "BS 6349-4: fenders no further apart than 0.15 × the smallest ship's length",
        }
    fits = c_top > 0 and c_bottom > 0
    notes = []
    if not fits:
        notes.append(
            f"The bolts do not fit in the {beam.depth:.0f} mm deep face at {f.centre_below_cope:g} m below the cope: "
            "a deeper cope at the fenders, or a smaller fender."
        )
    parts = [
        (f"Anchor bolts ({name.lower()})", worst["utilisation"], worst["passed"]),
        ("Bearing of the flange on the face", bearing["utilisation"], bearing["passed"]),
    ]
    if spacing:
        parts.append(("Spacing for the smallest ship", spacing["utilisation"], spacing["passed"]))
    return _result(
        "fenders",
        f"Fenders: {f.name}",
        parts,
        loads={"R_Ed_kN": round(r, 1), "friction_kN": round(fr, 1), "weight_kN": round(w, 1), "lever_m": h},
        anchors=worst,
        anchor_cases=[{"case": n, "utilisation": c["utilisation"]} for n, c in cases],
        governing_case=name,
        bearing=bearing,
        spacing=spacing,
        edges={"to_cope_mm": round(c_top), "to_soffit_mm": round(c_bottom)},
        notes=notes,
    )


def bollard(b: Bollards, beam: Beam) -> dict[str, Any]:
    vmin, vmax = _spread(b.anchors)
    centre = b.centre_from_face * 1000
    c_sea = centre + vmin  # +v inland
    c_land = beam.width - centre - vmax
    g = group(b.anchors, c_land, c_sea, beam.depth, beam.concrete)
    f = b.load_factor * b.capacity * G
    h = b.line_height
    cases = []
    steps = [t for t in range(0, int(b.max_vertical_angle) + 1, 15)]
    if steps[-1] != b.max_vertical_angle:
        steps.append(b.max_vertical_angle)
    for theta in steps:
        for phi in range(-90, 91, 15):
            du, dv = math.sin(math.radians(phi)), -math.cos(math.radians(phi))
            hor = f * math.cos(math.radians(theta))
            up = f * math.sin(math.radians(theta))
            res = check_group(g, up, hor * du, hor * dv, -hor * h * dv, -hor * h * du)
            cases.append(
                (
                    f"line {theta:g}° up, {abs(phi)}° {'along the quay' if phi else 'square to the face'}".replace(
                        " 0° square", " square"
                    ),
                    res,
                    theta,
                    phi,
                )
            )
    name, worst, theta, phi = max(
        cases, key=lambda c: c[1]["utilisation"] if c[1]["utilisation"] is not None else math.inf
    )
    notes = []
    if c_sea <= 0 or c_land <= 0:
        notes.append("The bolts do not fit across the beam at this position.")
    if b.anchors.embedment > beam.depth - 100:
        notes.append(f"The embedment {b.anchors.embedment:g} mm is more than the beam depth less 100 mm.")
    return _result(
        "bollards",
        f"Bollards: {b.capacity:g} t",
        [(f"Anchor bolts ({name})", worst["utilisation"], worst["passed"])],
        loads={"F_Ed_kN": round(f, 1), "line_height_m": h},
        anchors=worst,
        governing_case=name,
        angles_checked=len(cases),
        edges={"to_face_mm": round(c_sea), "to_back_mm": round(c_land)},
        notes=notes,
    )


def stopper(s: CraneStoppers, beam: Beam, rail_from_face: float) -> dict[str, Any]:
    vmin, vmax = _spread(s.anchors)
    centre = rail_from_face * 1000
    c_sea = centre + vmin
    c_land = beam.width - centre - vmax
    g = group(s.anchors, c_land, c_sea, beam.depth, beam.concrete)
    f = s.load_factor * s.force
    res = check_group(g, 0.0, f, 0.0, 0.0, f * s.buffer_height)
    notes = []
    if c_sea <= 0 or c_land <= 0:
        notes.append(f"The base bolts do not fit across the {beam.name} at the rail.")
    return _result(
        "crane_stoppers",
        "Crane stoppers",
        [("Anchor bolts", res["utilisation"], res["passed"])],
        loads={"F_Ed_kN": round(f, 1), "lever_m": s.buffer_height},
        anchors=res,
        edges={"to_face_mm": round(c_sea), "to_back_mm": round(c_land)},
        notes=notes,
    )


def tie_down(t: TieDowns, beam: Beam, rail_from_face: float) -> dict[str, Any]:
    """One plate of a set: its share of the uplift, off its centre by the link's eccentricity, in
    tension on the anchor group (EN 1992-4). The seaward plate of the set is the one checked, as it
    is nearer the beam's edge."""
    vmin, vmax = _spread(t.anchors)
    centre = (rail_from_face - t.offset_from_rail) * 1000 if t.plates > 1 else rail_from_face * 1000
    c_sea = centre + vmin
    c_land = beam.width - centre - vmax
    g = group(t.anchors, c_land, c_sea, beam.depth, beam.concrete)
    n = t.load_factor * t.force / t.plates
    res = check_group(g, n, 0.0, 0.0, 0.0, n * t.eccentricity / 1000)
    notes = []
    if c_sea <= 0 or c_land <= 0:
        notes.append(f"The plate's bolts do not fit across the {beam.name} at the rail.")
    return _result(
        "tie_downs",
        "Crane tie-downs",
        [("Anchor bolts (one plate)", res["utilisation"], res["passed"])],
        loads={"N_Ed_kN": round(n, 1), "e_mm": t.eccentricity, "plates_per_set": t.plates},
        anchors=res,
        edges={"to_face_mm": round(c_sea), "to_back_mm": round(c_land)},
        notes=notes,
    )


def storm_pin(p: StormPins, beam: Beam) -> dict[str, Any]:
    f = p.load_factor * p.force
    fyd = 500.0 / 1.15
    along = local_bearing(
        f,
        p.socket_width,
        p.socket_depth,
        min(3 * p.socket_width, beam.width),
        min(3 * p.socket_depth, beam.depth),
        beam.concrete,
        p.bar,
    )
    across = local_bearing(
        f,
        p.socket_length,
        p.socket_depth,
        3 * p.socket_length,
        min(3 * p.socket_depth, beam.depth),
        beam.concrete,
        p.bar,
    )
    a_bar = math.pi * p.bar**2 / 4
    legs = max(4, math.ceil(f * 1e3 / (a_bar * fyd)))
    legs += legs % 2
    loops = {
        "F_Ed_kN": round(f, 1),
        "As_req_mm2": round(f * 1e3 / fyd),
        "bars": f"{legs // 2} loops Ø{p.bar} ({legs} legs) round the socket, each way",
        "As_prov_mm2": round(legs * a_bar),
        "utilisation": round(f * 1e3 / fyd / (legs * a_bar), 3),
    }
    notes = []
    if p.socket_depth > beam.depth - 150:
        notes.append("The socket is deeper than the beam less 150 mm.")
    return _result(
        "storm_pins",
        "Storm pins",
        [
            ("Socket bearing, force along the rail", along["utilisation"], along["passed"]),
            ("Socket bearing, force across the rail", across["utilisation"], across["passed"]),
            ("Loops round the socket", loops["utilisation"], loops["utilisation"] <= 1.0),
        ],
        loads={"F_Ed_kN": round(f, 1)},
        bearing_along=along,
        bearing_across=across,
        loops=loops,
        notes=notes,
    )


def rail(r: CraneRails, beam: Beam, rail_from_face: float) -> dict[str, Any]:
    prop = RAILS[r.rail]
    inertia = prop["I"] * 1e4
    wr = inertia / (0.55 * prop["h"])  # to the head, the centroid a little below mid-height
    k = r.pad_stiffness
    beta = (k / (4 * E_STEEL * inertia)) ** 0.25  # 1/mm
    p = r.load_factor * r.wheel_load * 1e3  # N
    xs = [i * r.wheel_spacing for i in range(r.wheels)]

    def at(x: float) -> tuple[float, float]:
        m = w = 0.0
        for xi in xs:
            d = abs(x - xi) * beta
            m += p / (4 * beta) * math.exp(-d) * (math.cos(d) - math.sin(d))
            w += p * beta / (2 * k) * math.exp(-d) * (math.cos(d) + math.sin(d))
        return m, w

    samples = [at(x) for x in xs] + [at((a + b) / 2) for a, b in zip(xs, xs[1:], strict=False)]
    m_max = max(m for m, _ in samples)
    m_min = min(m for m, _ in samples)
    w_max = max(w for _, w in samples)
    sigma = max(abs(m_max), abs(m_min)) / wr
    u_rail = sigma / r.rail_fy
    q = k * w_max  # N/mm
    press = q / prop["foot"]
    conc = concrete(beam.concrete)
    fcd = conc.fck / 1.5
    spread = min(3 * prop["foot"], beam.width)
    frdu = fcd * min(math.sqrt(spread / prop["foot"]), 3.0)
    u_pad = press / frdu
    # Lateral: each wheel's share over the clips within π/β.
    reach = math.pi / beta
    clips = max(1.0, reach / r.clip_spacing)
    h_wheel = r.lateral * p / 1e3  # kN
    per_clip = h_wheel / clips
    uplift = per_clip * prop["h"] / prop["foot"]
    a = r.clip_anchors
    edge = rail_from_face * 1000 - prop["foot"] / 2 - 40
    g = group(
        a,
        beam.width - rail_from_face * 1000 - prop["foot"] / 2 - 40,
        max(edge, 1.0),
        beam.depth,
        beam.concrete,
    )
    clip = check_group(g, uplift, 0.0, per_clip, 0.0, 0.0)
    return _result(
        "crane_rails",
        f"Crane rails: {r.rail}",
        [
            ("Rail bending on the pad", round(u_rail, 3), u_rail <= 1.0),
            ("Pad pressure on the concrete", round(u_pad, 3), u_pad <= 1.0),
            ("Clip lateral capacity", round(per_clip / r.clip_capacity, 3), per_clip <= r.clip_capacity),
            ("Clip bolt", clip["utilisation"], clip["passed"]),
        ],
        rail=prop | {"name": r.rail, "W_head_cm3": round(wr / 1e3, 1)},
        loads={"P_Ed_kN": round(p / 1e3, 1), "wheels": r.wheels, "spacing_mm": r.wheel_spacing},
        beta_per_m=round(beta * 1e3, 3),
        M_max_kNm=round(m_max / 1e6, 1),
        M_min_kNm=round(m_min / 1e6, 1),
        sigma_MPa=round(sigma, 1),
        settlement_mm=round(w_max, 3),
        pad_pressure_MPa=round(press, 2),
        pad_limit_MPa=round(frdu, 2),
        lateral={
            "H_wheel_kN": round(h_wheel, 1),
            "clips_sharing": round(clips, 1),
            "per_clip_kN": round(per_clip, 1),
            "uplift_kN": round(uplift, 1),
        },
        anchors=clip,
        notes=["Fatigue of the rail and its welds, and the wheel–rail contact, are the supplier's."],
    )


def ladder(lad: Ladders, beam: Beam, cope: float) -> dict[str, Any]:
    c = lad.corrosion
    fy = lad.fy
    p = 1.5 * 1.5  # kN: 1.5 kN per person × 1.5
    d = lad.rung_diameter - 2 * c
    span = lad.clear_width + lad.stringer_thickness
    m = p * span / 4  # kNmm × 1e3 below
    w_el = math.pi * d**3 / 32
    u_rung = m * 1e3 / w_el / fy
    i_rung = math.pi * d**4 / 64
    defl = 1.5e3 * span**3 / (48 * E_STEEL * i_rung)
    u_defl = defl / (span / 200)
    v = 1.5 * lad.users * 1.5  # kN on one bracket span
    t = lad.stringer_thickness - 2 * c
    b = lad.stringer_width - 2 * c
    ms = v / 2 * lad.standoff  # kNmm per stringer
    sigma = v / 2 * 1e3 / (t * b) + ms * 1e3 / (t * b * b / 6)
    u_str = sigma / fy
    lever = 150.0  # bracket bearing edge to the bolts
    n_pull = v * lad.standoff / lever
    g = group(lad.anchors, 500.0, 500.0, beam.width, beam.concrete)
    fix = check_group(g, n_pull, 0.0, -v, 0.0, 0.0)
    length = cope - lad.bottom_level
    return _result(
        "ladders",
        "Ladders",
        [
            ("Rung bending", round(u_rung, 3), u_rung <= 1.0),
            ("Rung deflection (span / 200 under 1.5 kN)", round(u_defl, 3), u_defl <= 1.0),
            ("Stringer", round(u_str, 3), u_str <= 1.0),
            ("Bracket bolts", fix["utilisation"], fix["passed"]),
        ],
        length_m=round(length, 2),
        rungs=int(length * 1000 // lad.rung_pitch),
        rung={
            "diameter_after_loss_mm": d,
            "M_Ed_kNm": round(m / 1e3, 3),
            "sigma_MPa": round(m * 1e3 / w_el, 1),
            "deflection_mm": round(defl, 2),
        },
        stringer={"section_after_loss": f"{b:g} × {t:g} mm", "sigma_MPa": round(sigma, 1)},
        anchors=fix,
        notes=[],
    )


def tie_rod(t: TieRods, beam: Beam) -> dict[str, Any]:
    fy, fu = TIE_GRADES[t.grade]
    c = t.corrosion
    f_ed = t.force * t.spacing
    f_ser = (t.force_sls or t.force / 1.35) * t.spacing
    ag = math.pi / 4 * (t.shaft - 2 * c) ** 2
    ds = math.sqrt(4 * stress_area(t.thread) / math.pi)
    as_ = math.pi / 4 * (ds - 2 * c) ** 2
    ftg = fy * ag / 1.0 / 1e3
    ftt = t.kt * 0.9 * fu * as_ / 1.25 / 1e3
    frd = min(ftg, ftt)
    fser = fy * as_ / 1.1 / 1e3
    u = f_ed / frd
    us = f_ser / fser
    bearing = local_bearing(
        f_ed,
        t.plate,
        t.plate,
        min(3 * t.plate, beam.depth),
        min(3 * t.plate, 3 * t.plate),
        beam.concrete,
        t.bar,
    )
    notes = []
    if ftg >= ftt:
        notes.append("The thread fails before the shaft yields: a larger upset thread makes the tie ductile.")
    return _result(
        "tie_rods",
        f"Tie rods: Ø{t.shaft:g} {t.grade}, M{t.thread} upset",
        [
            ("Tension resistance (EN 1993-5 7.2.3)", round(u, 3), u <= 1.0),
            ("In service", round(us, 3), us <= 1.0),
            ("Anchor plate bearing", bearing["utilisation"], bearing["passed"]),
        ],
        loads={"F_Ed_kN": round(f_ed, 1), "F_ser_kN": round(f_ser, 1)},
        Ag_mm2=round(ag),
        As_mm2=round(as_),
        Ftg_Rd_kN=round(ftg, 1),
        Ftt_Rd_kN=round(ftt, 1),
        Ft_ser_Rd_kN=round(fser, 1),
        ductile=ftg < ftt,
        bearing=bearing,
        notes=notes,
    )


__all__ = [
    "Beam",
    "RAILS",
    "BOLT_GRADES",
    "fender",
    "bollard",
    "stopper",
    "storm_pin",
    "tie_down",
    "rail",
    "ladder",
    "tie_rod",
]
