"""The fender protrusion: a block cast on the front beam's sea face at each fender, and the stand-off
it gives the ship against the STS crane.

The block (projection a from the beam's sea face, depth D from the cope, length L along the berth)
is flush with the cope. It is joined to the beam over the beam's depth h (or D, if less); any depth
below the beam's soffit hangs as a downstand with nothing behind it.

Loads (ULS, per block): the fender's rated reaction R × its load factor, pressing the block onto the
beam at the fender centre z below the cope; the panel's friction μR at the block's sea face, down,
up or along the quay; the panel weight (× the fender load factor) at the sea face; the block's own
weight (γG 1.35, or 1.0 where it helps) at a/2. A case without the reaction (self weight and panel
only) is checked too, since friction comes only with the reaction. With a bollard on the block (as on
drawing SC-502-1) its pull F = factor × capacity × g is checked on its own (mooring, not with
berthing): off the quay level (the joint in tension, the top pulled open by F at the line height
above the cope), off the quay at the steepest line angle (with its uplift) and along the quay. A
joint pulled open has no cohesion and σn < 0 (6.2.5(1)); dowels are added across it until it passes.

Section at the joint (EN 1992-1-1 6.1, the rectangular section of ``rect``): b = L, h = the joined
depth, under N = R (compression), Mv = −(ΣV·lever) − R·(z − h/2) about the joint's centre (the
top in tension) and Mh = μR·a from friction along the quay, biaxially (5.8.9(4)). The top ties are
U-bars anchored into the beam; the side and bottom bars run through the joint too.

Short cantilever (Annex J.3, a/h < 1): tie Ftd = V·ac / z0 with z0 = 0.8d (the reaction's
compression, which helps, is left out); the top ties take the larger of this and the section's need.
Links: closed horizontal links ≥ 0.25 As,main where ac ≤ 0.5h (J.3(3)), else vertical links for
0.5·V (J.3(4)). V ≤ 0.5·b·d·ν·fcd (6.2.2(6)).

Joint shear (6.2.5), unless cast with the beam: vRdi = c·fctd + μ·σn + ρ·fyd·μ ≤ 0.5·ν·fcd, with c
halved for the fender's dynamic load (6.2.5(5)), σn from the characteristic reaction (≤ 0.6 fcd),
and ρ from every bar crossing the joint. vEdi is the vertical and along-the-quay shear over the
joint, plus the twist of the along-the-quay friction about the joint's centre (T·r / Ip).

Downstand: the part of the fender reaction below the beam's soffit (the flange taken as a uniform
pressure over its circle) bends the downstand about the soffit, with its own weight hanging on it;
As = M / (0.9·d·fyd) + N / fyd on the land face.

Face mesh on every face (7.3.2): As = k·fct,eff·Act / fyk per face, k 0.65, Act = 2.5(c + φ/2).
The fender flange bears on the block's face (6.7) within the block.

Into the front beam: the block's loads give the beam a torque T = ΣV·(B/2 + lever) + R·(z − h/2)
about its centre and a shear ΣV. The whole T is taken each way (a block beside a joint sends it all
one way) to the pile heads: TRd,max = 2ν·fcd·Ak·tef·sinθ·cosθ (6.30, θ 45°), with extra closed links
Asw/s = T / (2·Ak·fyd) per leg and extra longitudinal bars ΣAsl = T·uk / (2·Ak·fyd) (6.28), on top of
the beam's own design, from the block to the nearest pile heads either side.

STS crane stand-off: the ship's side is s = a + the fender height + the panel from the quay face,
or a + (1 − deflection)·the fender height + the panel with the fender at its rated reaction. The
crane must reach its far row: rail from the face + s (not compressed) + ship beam − far row inside ≤
the outreach. The ship's flare must clear the crane's seaside legs: rail from the face − the legs'
reach seaward of the rail + s (compressed) ≥ flare + clearance. Neither has a code value: both come
from the crane and the design ship.

The flare, unless typed in, is worked out from the design ship: the lightest ship (ballast draft) at
high water stands highest, its deck edge at HW + depth − ballast draft; over the height H from the
fender contact line (cope − fender centre) to the deck edge its side leans out by the hull's flare
and the list towards the quay: flare = H·(tan flare angle + sin list). Deck cargo is taken inside
the hull line. Each section ticks whether it has the blocks and STS cranes.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..furniture_inputs import Bollards, Fenders
from ..materials import concrete
from ..protrusion_inputs import FenderProtrusion, StsCrane
from .rect import Bars, RectSection, rect_laws

GAMMA_G, GAMMA_G_FAV = 1.35, 1.0
FYK = 500.0
STEEL_KG_M3 = 7850.0
LAP = 45  # bar diameters, the project's lap and anchorage length

# EN 1992-1-1 6.2.5(2): c and μ for each kind of joint.
JOINTS = {"indented": (0.5, 0.9), "rough": (0.45, 0.7), "smooth": (0.35, 0.6), "monolithic": (None, None)}


def _bar(phi: float) -> float:
    return math.pi * phi * phi / 4


def _count(span: float, spacing: float) -> int:
    """Bars over ``span`` (mm, centre to centre of the end bars) at no more than ``spacing``."""
    return max(2, math.ceil(span / spacing - 1e-9) + 1)


def _flange_below(fenders: Fenders | None, z: float, level: float) -> tuple[float, float]:
    """Share of the flange's area below ``level`` (mm under the cope) and the depth of its centroid."""
    if fenders is None:
        return 0.0, level
    r = fenders.flange / 2
    ys = np.linspace(z - r, z + r, 401)
    w = 2 * np.sqrt(np.maximum(r * r - (ys - z) ** 2, 0.0))
    below = ys > level
    total = np.trapezoid(w, ys)
    if not below.any() or total <= 0:
        return 0.0, level
    part = np.trapezoid(w[below], ys[below])
    cz = float(np.trapezoid(w[below] * ys[below], ys[below]) / part) if part > 0 else level
    return float(part / total), cz


def cases(
    p: FenderProtrusion, fenders: Fenders | None, bollards: Bollards | None = None
) -> tuple[list[dict[str, float]], float]:
    """ULS loads on one block (kN, m). R: the fender reaction pressing the block on; W: its weight at
    a/2; P: panel weight and friction down at the sea face; H: friction along the quay at the sea
    face. With a bollard on the block: Bh its pull off the quay, Bu its uplift and Ba its pull along
    the quay, at eb from the beam face and the line height above the cope (berthing and mooring are
    not taken together)."""
    a = p.projection / 1000
    w = p.density * a * p.depth / 1000 * p.length / 1000
    r = fenders.load_factor * fenders.reaction if fenders else 0.0
    fr = fenders.friction * r if fenders else 0.0
    wp = fenders.load_factor * fenders.panel_weight if fenders else 0.0
    out = [
        {"case": "Friction down", "R": r, "W": GAMMA_G * w, "P": wp + fr, "H": 0.0},
        {"case": "Friction up", "R": r, "W": GAMMA_G_FAV * w, "P": wp - fr, "H": 0.0},
        {"case": "Friction along the quay", "R": r, "W": GAMMA_G * w, "P": wp, "H": fr},
        {"case": "No reaction (own weight)", "R": 0.0, "W": GAMMA_G * w, "P": wp, "H": 0.0},
    ]
    if p.bollard_on_block and bollards is not None:
        f = bollards.load_factor * bollards.capacity * 9.81
        steep = math.radians(bollards.max_vertical_angle)
        out += [
            {"case": "Bollard pull off the quay", "W": GAMMA_G * w, "Bh": f},
            {
                "case": f"Bollard pull off the quay at {bollards.max_vertical_angle:g}°",
                "W": GAMMA_G_FAV * w,
                "Bh": f * math.cos(steep),
                "Bu": f * math.sin(steep),
            },
            {"case": "Bollard pull along the quay", "W": GAMMA_G * w, "Ba": f},
        ]
    eb = max(a - bollards.centre_from_face, 0.0) if bollards else 0.0
    lh = bollards.line_height if bollards else 0.0
    for c in out:
        for k in ("R", "P", "H", "Bh", "Bu", "Ba"):
            c.setdefault(k, 0.0)
        c["eb"], c["lh"] = eb, lh
        c["V"] = c["W"] + c["P"] - c["Bu"]
        # Moment of the vertical loads about the beam face (kNm): weight at a/2, panel at a, uplift at eb.
        c["Mv_face"] = c["W"] * a / 2 + c["P"] * a - c["Bu"] * eb
    return out, w


def block(
    p: FenderProtrusion,
    fenders: Fenders | None,
    beam_width: float,
    beam_depth: float,
    beam_concrete: str,
    beam_cover: float,
    gamma_c: float = 1.5,
    gamma_s: float = 1.15,
    alpha_cc: float = 1.0,
    bollards: Bollards | None = None,
) -> dict[str, Any]:
    """Design of one fender block and its joint to the front beam (all mm, kN). ``bollards``: the
    project's bollard, pulling on the block when ``p.bollard_on_block``."""
    grade = p.concrete or beam_concrete
    conc = concrete(grade)
    fcd = alpha_cc * conc.fck / gamma_c
    fyd = FYK / gamma_s
    nu = 0.6 * (1 - conc.fck / 250)
    cover = p.cover or beam_cover
    a, D, L = p.projection, p.depth, p.length
    h = min(D, beam_depth)  # joined depth
    z = (p.fender_centre_below_cope * 1000) if p.fender_centre_below_cope else D / 2
    loads, w_block = cases(p, fenders, bollards)
    notes: list[str] = []
    if fenders is None:
        notes.append("No fenders in the Furniture items: the block carries only its own weight.")
    if z > D:
        notes.append(f"The fender centre ({z:.0f} mm) is below the block ({D:.0f} mm deep).")

    # --- Section at the joint: b = L (u along the quay), h (v up).
    phl, phs = p.link_bar, p.side_bar
    edge = cover + phl
    v_bot = -h / 2 + edge + phs / 2
    u_side = L / 2 - edge - phs / 2
    n_bot = _count(L - 2 * edge - phs, p.max_spacing)
    laws = rect_laws(conc.fck, gamma_c, gamma_s, alpha_cc, FYK)

    def section(pht: int, n_top: int, layers: int) -> RectSection:
        v_top = h / 2 - edge - pht / 2
        span_u = L - 2 * edge - pht
        groups = [Bars.row(n_top, pht, v_top - i * (pht + 25), span_u / 2) for i in range(layers)]
        groups.append(Bars.row(n_bot, phs, v_bot, (L - 2 * edge - phs) / 2))
        n_side = max(0, math.ceil((v_top - v_bot) / p.max_spacing - 1e-9) - 1)
        for s in (-1, 1) if n_side else ():
            g = Bars.column(n_side, phs, s * u_side, (v_top - v_bot) / 2)
            # column() spreads the bars about 0: move them to between the top and bottom bars.
            groups.append(Bars(g.u, g.v + (v_top + v_bot) / 2, g.area))
        return RectSection(float(L), float(h), Bars.join(*groups), *laws, strips=100)

    N = np.array([c["R"] - c["Bh"] for c in loads])
    lever_R = (z - h / 2) / 1000  # m, below the joint's centre
    # The bollard's pull off the quay acts at the line height above the cope: the top in tension.
    Mv = np.array([-(c["Mv_face"]) - c["R"] * lever_R - c["Bh"] * (h / 2000 + c["lh"]) for c in loads])
    Mh = np.array([c["H"] * a / 1000 + c["Ba"] * c["eb"] for c in loads])
    V = np.array([c["V"] for c in loads])

    # Annex J.3 tie (the worst downward case), z0 = 0.8d.
    ac_list = [c["Mv_face"] / c["V"] if c["V"] > 1e-9 else 0.0 for c in loads]

    def tie(pht: int) -> tuple[float, float, float]:
        d = h - edge - pht / 2
        z0 = 0.8 * d
        ftd = max(max(c["V"], 0.0) * ac / (z0 / 1000) for c, ac in zip(loads, ac_list, strict=True))
        return d, z0, ftd

    # The smallest top tie bar (up to the one given) and the fewest bars at no more than the largest
    # spacing that carry both the section and the tie.
    sizes = [x for x in (16, 20, 25, 32, 40) if x <= p.tie_bar] or [p.tie_bar]
    chosen = None
    for layers in (1, 2):
        for pht in sizes:
            span_u = L - 2 * edge - pht
            n_min = _count(span_u, p.max_spacing)
            n_fit = max(n_min, int(span_u // (pht + 25)) + 1)
            as_tie = tie(pht)[2] * 1e3 / fyd
            for n in range(n_min, n_fit + 1):
                if n * layers * _bar(pht) < as_tie:
                    continue
                sec = section(pht, n, layers)
                u = sec.utilisation(N, Mv, Mh)
                if float(u.max()) <= 1.0:
                    chosen = (pht, n, layers, sec, u)
                    break
            if chosen:
                break
        if chosen:
            break
    if chosen is None:
        pht = sizes[-1]
        n = max(_count(L - 2 * edge - pht, p.max_spacing), int((L - 2 * edge - pht) // (pht + 25)) + 1)
        sec = section(pht, n, 2)
        chosen = (pht, n, 2, sec, sec.utilisation(N, Mv, Mh))
        notes.append(
            f"Two layers of {n} Ø{pht} top ties do not carry the block: a bigger tie bar or a deeper block."
        )
    pht, n_top, layers, sec, u_sec = chosen
    d, z0, ftd = tie(pht)
    as_tie = ftd * 1e3 / fyd
    as_top = n_top * layers * _bar(pht)
    n_side = max(0, math.ceil((h - 2 * edge - pht / 2 - phs / 2) / p.max_spacing - 1e-9) - 1)
    worst = int(np.argmax(u_sec))

    # Shear of the short cantilever (6.2.2(6)) and its links (J.3).
    v_max = float(np.abs(V).max())
    vrd_max = 0.5 * L * d * nu * fcd / 1e3
    ac = max(ac_list)
    if ac * 1000 <= 0.5 * h:
        link_rule = "closed horizontal links ≥ 0.25·As,main (EN 1992-1-1 J.3(3), ac ≤ 0.5·hc)"
        as_links = 0.25 * as_top
    else:
        link_rule = "closed vertical links for 0.5·V (EN 1992-1-1 J.3(4), ac > 0.5·hc)"
        as_links = 0.5 * v_max * 1e3 / fyd
    legs = max(2, math.ceil(L / p.max_spacing))  # legs across the block's length
    link_sets = max(
        _count(a - 2 * edge, p.max_spacing), math.ceil(as_links / (legs * _bar(phl)))
    )  # along the projection

    # --- Joint shear (6.2.5).
    crossing = sec.bars.total
    c_coef, mu = JOINTS[p.joint]
    joint = None
    dowels = 0
    if c_coef is not None:
        area = L * h
        fctd = 1.0 * 0.7 * conc.fctm / gamma_c
        ip = L * h * (L * L + h * h) / 12
        r_max = math.hypot(L, h) / 2
        load_factor = fenders.load_factor if fenders else 1.0

        def joint_rows(crossing_mm2: float) -> list[dict[str, Any]]:
            rows = []
            for c in loads:
                # Nmm: along friction at the fender centre, the bollard's pull along at its line height.
                twist = c["H"] * 1e3 * (z - h / 2) + c["Ba"] * 1e3 * (h / 2 + c["lh"] * 1000)
                along = c["H"] + c["Ba"]
                v_ed = math.hypot(c["V"], along) * 1e3 / area + abs(twist) * r_max / ip
                if c["Bh"] > 0:
                    # The joint pulled open: tension across it (σn < 0) and no cohesion (6.2.5(1)).
                    sig_n, c_eff = -c["Bh"] * 1e3 / area, 0.0
                else:
                    sig_n, c_eff = min(c["R"] / load_factor * 1e3 / area, 0.6 * fcd), 0.5 * c_coef
                rho = crossing_mm2 / area
                v_rd = max(min(c_eff * fctd + mu * sig_n + rho * fyd * mu, 0.5 * nu * fcd), 1e-6)
                rows.append({"case": c["case"], "v_Edi": v_ed, "v_Rdi": v_rd, "sigma_n": sig_n, "c": c_eff})
            return rows

        # Dowels (the side bar size) across the joint, added until it passes.
        while True:
            rows = joint_rows(crossing + dowels * _bar(phs))
            jw = max(rows, key=lambda r: r["v_Edi"] / r["v_Rdi"])
            u_joint = jw["v_Edi"] / jw["v_Rdi"]
            if u_joint <= 1.0 or dowels >= 400:
                break
            dowels += 1
        joint = {
            "surface": p.joint,
            "c": c_coef,
            "c_used": jw["c"],
            "mu": mu,
            "area_m2": round(area / 1e6, 3),
            "crossing_mm2": round(crossing + dowels * _bar(phs)),
            "governing_case": jw["case"],
            "v_Edi_MPa": round(jw["v_Edi"], 3),
            "v_Rdi_MPa": round(jw["v_Rdi"], 3),
            "sigma_n_MPa": round(jw["sigma_n"], 3),
            "utilisation": round(u_joint, 3),
            "passed": bool(u_joint <= 1.0),
            "extra_dowels": f"{dowels} Ø{phs}" if dowels else "",
            "clause": "EN 1992-1-1 6.2.5 (c halved for the dynamic load, 6.2.5(5))",
        }
        if u_joint > 1.0:
            notes.append(
                "The joint cannot pass on dowels alone (6.2.5 upper limit): an indented surface, or cast with the beam."
            )

    # --- Downstand below the beam's soffit.
    down = None
    if D > beam_depth + 1e-6:
        hd = D - beam_depth
        share, cz = _flange_below(fenders, z, beam_depth)
        r_d = loads[0]["R"]
        f_low = share * r_d
        m_low = f_low * (cz - beam_depth) / 1000
        n_hang = GAMMA_G * p.density * a / 1000 * hd / 1000 * L / 1000
        dd = a - edge - p.face_bar / 2
        as_req = m_low * 1e6 / (0.9 * dd * fyd) + n_hang * 1e3 / fyd
        down = {
            "depth_mm": round(hd),
            "reaction_share": round(share, 3),
            "F_kN": round(f_low, 1),
            "M_kNm": round(m_low, 1),
            "hanging_kN": round(n_hang, 1),
            "As_req_mm2": round(as_req),
        }

    # --- Face mesh (7.3.2) on every face.
    phf = p.face_bar
    hc = min(2.5 * (cover + phl + phf / 2), min(a, L, D) / 2)
    as_face = 0.65 * conc.fctm * hc * 1000 / FYK  # mm²/m per face
    s_face = int(min(p.max_spacing, math.floor(_bar(phf) * 1000 / as_face / 25) * 25))
    if down is not None:
        per_m = _bar(phf) * 1000 / s_face
        land = per_m * L / 1000
        down["As_prov_mm2"] = round(land)
        down["utilisation"] = round(down["As_req_mm2"] / land, 3) if land else None
        down["passed"] = bool(land >= down["As_req_mm2"])
        if not down["passed"]:
            need = math.ceil(down["As_req_mm2"] / _bar(phf))
            s_new = max(75, math.floor(L / need / 25) * 25)
            down["note"] = f"Land-face vertical bars of the downstand: Ø{phf} at {s_new} mm."

    # --- Fender flange bearing on the block's face (6.7), spread within the block.
    bearing = None
    if fenders:
        side = fenders.flange * math.sqrt(math.pi) / 2
        from .anchors import local_bearing

        r_d = loads[0]["R"]
        bearing = local_bearing(
            r_d, side, side, min(3 * side, L), min(3 * side, D, 2 * min(z, D - z) + side), grade
        )

    # --- Into the front beam: torque and shear.
    B = beam_width
    tors = []
    for c in loads:
        t = (
            c["W"] * (B / 2 + a / 2) / 1000
            + c["P"] * (B / 2 + a) / 1000
            + c["R"] * (z - beam_depth / 2) / 1000
            + c["Bh"] * (beam_depth / 2000 + c["lh"])
            - c["Bu"] * (B / 2000 + c["eb"])
        )
        tors.append(t)
    t_ed = max(abs(t) for t in tors)
    area_b = B * beam_depth
    tef = area_b / (2 * (B + beam_depth))
    ak = (B - tef) * (beam_depth - tef)
    uk = 2 * ((B - tef) + (beam_depth - tef))
    trd_max = 2 * nu * fcd * ak * tef * 0.5 / 1e6  # kNm, θ = 45°
    asw_s = t_ed * 1e6 / (2 * ak * fyd) * 1000  # mm²/m per leg
    asl = t_ed * 1e6 * uk / (2 * ak * fyd)
    beam = {
        "T_Ed_kNm": round(t_ed, 1),
        "V_Ed_kN": round(float(np.abs(V).max()), 1),
        "t_ef_mm": round(tef),
        "Ak_m2": round(ak / 1e6, 3),
        "T_Rd_max_kNm": round(trd_max, 1),
        "utilisation": round(t_ed / trd_max, 3),
        "passed": bool(t_ed <= trd_max),
        "extra_links_mm2_per_m": round(asw_s),
        "extra_links": _links_text(asw_s, phl),
        "extra_longitudinal_mm2": round(asl),
        "extra_longitudinal": f"{math.ceil(asl / _bar(20))} Ø20 round the section",
    }

    # --- Quantities.
    vol = a * D * L / 1e9
    tie_len = 2 * (a - cover) + 2 * LAP * pht + (h - 2 * edge)  # U-bar: top leg, into the beam, lap each end
    steel_mm3 = (
        n_top * layers * _bar(pht) * tie_len
        + n_bot * _bar(phs) * (a - cover + LAP * phs)
        + 2 * n_side * _bar(phs) * (a - cover + LAP * phs)
        + link_sets * legs * _bar(phl) * 2 * (h + a)
        + dowels * _bar(phs) * 2 * LAP * phs
        + 2 * (a * D + a * L + D * L) / 1e6 * (as_face * 2)  # face mesh both ways on every face, per m²
    )
    kg = steel_mm3 / 1e9 * STEEL_KG_M3

    u_parts = [
        ("Section at the joint (N with biaxial bending)", float(u_sec[worst]), bool(u_sec[worst] <= 1.0)),
        ("Short cantilever shear (6.2.2(6))", v_max / vrd_max, v_max <= vrd_max),
    ]
    if joint:
        u_parts.append(("Joint to the beam (6.2.5)", joint["utilisation"], joint["passed"]))
    if down and down.get("utilisation") is not None:
        u_parts.append(("Downstand below the beam", down["utilisation"], down["passed"]))
    if bearing:
        u_parts.append(
            ("Fender flange bearing on the block (6.7)", bearing["utilisation"], bearing["passed"])
        )
    u_parts.append(("Torsion in the front beam (6.3.2)", beam["utilisation"], beam["passed"]))
    us = [u for _, u, _ in u_parts]
    u = max(us)
    lc = loads[worst]
    return {
        "item": "fender_blocks",
        "title": f"Fender protrusion {a / 1000:g} × {D / 1000:g} × {L / 1000:g} m (out × deep × long)",
        "utilisation": round(u, 3),
        "passed": bool(u <= 1.0),
        "governing_case": lc["case"],
        "parts": [{"part": n, "utilisation": round(x, 3), "passed": bool(ok)} for n, x, ok in u_parts],
        "loads": {
            "R_Ed_kN": round(loads[0]["R"], 1),
            "friction_kN": round(fenders.friction * loads[0]["R"], 1) if fenders else 0.0,
            "block_weight_kN": round(w_block, 1),
            "bollard_pull_kN": round(max((c["Bh"] + c["Ba"] for c in loads), default=0.0), 1),
            "fender_centre_below_cope_m": round(z / 1000, 3),
        },
        "geometry": {
            "projection_mm": a,
            "depth_mm": D,
            "length_mm": L,
            "joined_depth_mm": h,
            "beam_width_mm": B,
            "beam_depth_mm": beam_depth,
            "concrete": grade,
            "cover_mm": cover,
        },
        "section": {
            "b_mm": L,
            "h_mm": h,
            "d_mm": round(d),
            "N_kN": round(float(N[worst]), 1),
            "Mv_kNm": round(float(Mv[worst]), 1),
            "Mh_kNm": round(float(Mh[worst]), 1),
            "utilisation": round(float(u_sec[worst]), 3),
            "tie_Ftd_kN": round(ftd, 1),
            "tie_As_mm2": round(as_tie),
            "z0_mm": round(z0),
            "ac_m": round(ac, 3),
        },
        "shear": {"V_Ed_kN": round(v_max, 1), "V_Rd_max_kN": round(vrd_max, 1)},
        "joint": joint,
        "downstand": down,
        "bearing": bearing,
        "beam": beam,
        "bars": {
            "top_ties": f"{n_top} Ø{pht} U-bars"
            + (" in 2 layers of that" if layers == 2 else "")
            + f", {LAP}Ø into the beam",
            "top_As_mm2": round(as_top),
            "bottom": f"{n_bot} Ø{phs}",
            "sides": f"{n_side} Ø{phs} each side" if n_side else "none",
            "links": f"{link_sets} sets of {legs}-leg Ø{phl} ({link_rule})",
            "dowels": f"{dowels} Ø{phs} across the joint, {LAP}Ø each side" if dowels else "none",
            "face_mesh": f"Ø{phf} at {s_face} mm both ways on every face ({as_face:.0f} mm²/m, EN 1992-1-1 7.3.2)",
        },
        "quantities": {
            "concrete_m3": round(vol, 2),
            "steel_kg": round(kg),
            "kg_per_m3": round(kg / vol) if vol else 0,
        },
        "load_cases": [
            {
                "case": c["case"],
                "N_kN": round(float(N[i]), 1),
                "V_kN": round(float(V[i]), 1),
                "Mv_kNm": round(float(Mv[i]), 1),
                "Mh_kNm": round(float(Mh[i]), 1),
                "T_beam_kNm": round(tors[i], 1),
                "utilisation": round(float(u_sec[i]), 3),
            }
            for i, c in enumerate(loads)
        ],
        "details": _details(a, D, L, h, z, loads, N, Mv, Mh, V, tors, u_sec),
        "notes": notes,
    }


def _links_text(asw_s: float, phi: float) -> str:
    s = math.floor(_bar(phi) * 1000 / asw_s / 25) * 25 if asw_s > 0 else 0
    if s >= 75:
        return f"Ø{phi} closed links at {min(s, 300)} mm (one leg each face)"
    return f"{asw_s:.0f} mm²/m per leg: Ø{phi} is too small, use bigger closed links"


def _details(a, D, L, h, z, loads, N, Mv, Mh, V, tors, u_sec) -> list[dict[str, Any]]:
    return [
        {
            "title": "Load cases at the joint",
            "headers": ["Case", "N (kN)", "V (kN)", "Mv (kNm)", "Mh (kNm)", "T on beam (kNm)", "Utilisation"],
            "rows": [
                [
                    c["case"],
                    round(float(N[i]), 1),
                    round(float(V[i]), 1),
                    round(float(Mv[i]), 1),
                    round(float(Mh[i]), 1),
                    round(tors[i], 1),
                    round(float(u_sec[i]), 3),
                ]
                for i, c in enumerate(loads)
            ],
        }
    ]


def clearance(
    sts: StsCrane,
    fenders: Fenders | None,
    protrusion: FenderProtrusion | None,
    rail_from_face: float,
    cope: float = 3.5,
    contact_below_cope: float | None = None,
) -> dict[str, Any]:
    """The ship's stand-off from the quay face against the STS crane's outreach and legs (m). ``cope``:
    the cope level (m CD); ``contact_below_cope``: the fender centre below it (empty: the block's or the
    fender's)."""
    if contact_below_cope is None:
        if protrusion:
            contact_below_cope = protrusion.fender_centre_below_cope or protrusion.depth / 2000
        else:
            contact_below_cope = fenders.centre_below_cope if fenders else 0.0
    fl = flare(sts, cope - contact_below_cope)
    sts = sts.model_copy(update={"flare_overhang": fl["overhang_m"]})
    a = protrusion.projection / 1000 if protrusion else 0.0
    fh = fenders.height if fenders else 0.0
    panel = sts.panel_thickness if fenders else 0.0
    s_max = a + fh + panel
    s_min = a + fh * (1 - sts.rated_deflection) + panel
    need = rail_from_face + s_max + sts.ship_beam - sts.far_row_inside
    u_reach = need / sts.outreach
    allowed_standoff = sts.outreach - rail_from_face - sts.ship_beam + sts.far_row_inside
    max_projection = allowed_standoff - fh - panel
    avail = rail_from_face - sts.leg_seaward_of_rail + s_min
    gap = sts.flare_overhang + sts.min_clearance
    u_legs = gap / avail if avail > 0 else math.inf
    clear = avail - sts.flare_overhang
    min_projection = max(
        0.0, gap + sts.leg_seaward_of_rail - rail_from_face - fh * (1 - sts.rated_deflection) - panel
    )
    parts = [
        ("Crane reaches the far row (stand-off not more than allowed)", u_reach, u_reach <= 1.0),
        ("Ship's flare clears the crane's seaside legs", u_legs, u_legs <= 1.0),
    ]
    notes = []
    if protrusion is None:
        if u_legs > 1.0:
            notes.append(
                "Without a protrusion the ship's flare reaches the crane's seaside legs: tick \"Front beam "
                f"protrusion at each fender\" in this section's furniture settings, at least {min_projection:.2f} m "
                "projection."
            )
        else:
            notes.append("No fender protrusion: the stand-off is the fender and panel only.")
    if min_projection > max_projection + 1e-9:
        notes.append("No projection satisfies both: the ship, crane or fender values need another look.")
    elif protrusion is not None and not (min_projection - 1e-9 <= a <= max_projection + 1e-9):
        notes.append(
            f"The {a:g} m projection is outside the range the crane allows ({min_projection:.2f} to {max_projection:.2f} m)."
        )
    us = [x for _, x, _ in parts]
    u = max(us)
    return {
        "item": "sts_clearance",
        "title": "STS crane: the ship's stand-off from the quay face",
        "utilisation": round(u, 3) if math.isfinite(u) else None,
        "passed": bool(u <= 1.0),
        "parts": [
            {"part": n, "utilisation": round(x, 3) if math.isfinite(x) else None, "passed": bool(ok)}
            for n, x, ok in parts
        ],
        "loads": {},
        "standoff": {
            "projection_m": a,
            "fender_m": fh,
            "panel_m": panel,
            "free_m": round(s_max, 3),
            "compressed_m": round(s_min, 3),
            "rail_from_face_m": round(rail_from_face, 3),
        },
        "reach": {
            "needed_m": round(need, 2),
            "outreach_m": sts.outreach,
            "largest_standoff_m": round(allowed_standoff, 2),
        },
        "flare": fl,
        "legs": {
            "available_m": round(avail, 3),
            "flare_m": sts.flare_overhang,
            "clearance_m": round(clear, 3),
            "min_clearance_m": sts.min_clearance,
        },
        "projection_range_m": [round(min_projection, 2), round(max_projection, 2)],
        "details": [
            {
                "title": "Stand-off and crane",
                "headers": ["Quantity", "Value (m)"],
                "rows": [
                    ["Protrusion from the beam face", round(a, 3)],
                    ["Fender height + panel", round(fh + panel, 3)],
                    ["Stand-off, fender free", round(s_max, 3)],
                    ["Stand-off, fender at rated reaction", round(s_min, 3)],
                    ["Front rail from the quay face", round(rail_from_face, 3)],
                    [
                        f"Reach needed: rail + free stand-off + ship beam {sts.ship_beam:g} − {sts.far_row_inside:g}",
                        round(need, 2),
                    ],
                    ["Crane outreach", sts.outreach],
                    ["Largest stand-off the outreach allows", round(allowed_standoff, 2)],
                    [
                        f"Ship to the legs: rail − {sts.leg_seaward_of_rail:g} + compressed stand-off − flare {sts.flare_overhang:g}",
                        round(clear, 3),
                    ],
                    ["Clearance kept", sts.min_clearance],
                    ["Projection the crane allows, from", round(min_projection, 2)],
                    ["to", round(max_projection, 2)],
                ],
            },
            {
                "title": "The ship's flare towards the crane",
                "headers": ["Quantity", "Value"],
                "rows": fl["rows"],
            },
        ],
        "notes": notes,
    }


def flare(sts: StsCrane, contact_level: float) -> dict[str, Any]:
    """How far the ship's hull stands out beyond its fender contact line at the deck edge (m): the
    lightest ship (ballast draft) at high water stands highest; its side leans out by the flare and by
    the list towards the quay. Deck cargo is taken inside the hull line."""
    deck = sts.high_water + sts.ship_depth - sts.ballast_draft
    height = max(0.0, deck - contact_level)
    lean = math.tan(math.radians(sts.flare_angle)) + math.sin(math.radians(sts.list_angle))
    derived = height * lean
    given = sts.flare_overhang is not None
    overhang = sts.flare_overhang if given else derived
    rows = [
        ["Deck edge: high water + depth − ballast draft (m CD)", round(deck, 3)],
        ["Fender contact line (m CD)", round(contact_level, 3)],
        ["Height of the deck edge above the contact line (m)", round(height, 3)],
        [f"tan {sts.flare_angle:g}° flare + sin {sts.list_angle:g}° list", round(lean, 4)],
        ["Overhang worked out (m)", round(derived, 3)],
    ]
    if given:
        rows.append(["Overhang typed in, used (m)", overhang])
    return {
        "deck_level_m": round(deck, 3),
        "contact_level_m": round(contact_level, 3),
        "height_m": round(height, 3),
        "overhang_m": round(overhang, 3),
        "from": "typed in" if given else "the ship",
        "rows": rows,
    }
