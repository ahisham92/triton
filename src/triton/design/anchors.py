"""Cast-in anchor groups to EN 1992-4 (headed bolts with a nut and anchor plate, cracked concrete).

A fixture (fender flange, bollard base, crane stopper, rail clip, ladder bracket) is held by a group
of bolts cast into the beam. The loads on the fixture, a pull N out of the concrete, shears Vu (along
the beam) and Vv (towards the member's edges) and moments Mu, Mv about the group's centre, are shared
between the bolts as a rigid base plate on elastic bolts: each bolt takes N/n plus M·y/Σy² (the
concrete under the plate is left out, which puts more tension in the bolts than EN 1992-4 6.2.2
needs), and the shear equally.

Tension (7.2.1): steel NRk,s = As·fuk; concrete cone NRk,c = N0·(Ac,N/A0c,N)·ψs,N·ψre,N·ψec,N with
N0 = k1·√fck·hef^1.5 (k1 = 8.9 cracked, 12.7 uncracked), scr,N = 3hef, ccr,N = 1.5hef, over the
tensioned bolts; pull-out NRk,p = k2·Ah·fck (k2 = 7.5 cracked, 10.5 uncracked, Ah the bearing area of
the head); blow-out, where a bolt is closer than 0.5hef to an edge, NRk,cb = k5·c1·√Ah·√fck
(k5 = 8.7 cracked, 12.2 uncracked) with the group and edge factors. Splitting is not checked: the
concrete is taken as cracked and the beam's bars keep crack widths to 0.3 mm (7.2.1.7(2)b); the
splitting bars As,re = 0.5·ΣNEd/(fyk/γMs,re) are given instead.

Shear (7.2.2): steel VRk,s = k6·As·fuk (k6 = 0.6 for fuk ≤ 500 MPa, else 0.5; no lever arm: the base
plate sits on the concrete or on grout thinner than half the bolt); pry-out VRk,cp = k8·NRk,c (k8 = 2);
concrete edge failure towards each edge, from the row nearest it, VRk,c = V0·(Ac,V/A0c,V)·ψs,V·ψh,V·
ψα,V·ψre,V with V0 = k9·d^α·lf^β·√fck·c1^1.5 (k9 = 1.7 cracked, 2.4 uncracked).

Partial factors (Table 4.1): steel γMs = 1.2·fuk/fyk ≥ 1.4 in tension, fuk/fyk ≥ 1.25 in shear (1.5
for fuk > 800 MPa or fyk/fuk > 0.8); concrete, pull-out and blow-out γMc = γc = 1.5 (cast-in, γinst = 1).
Tension and shear together (7.2.3): steel (βN)² + (βV)² ≤ 1, concrete (βN)^1.5 + (βV)^1.5 ≤ 1.

Where the concrete cone or the edge fails, anchor reinforcement takes the load instead (7.2.1.9,
7.2.2.6): legs next to each tensioned bolt NRd,re = As,re·fyk/1.15, anchored in the cone over l1 at
fbd (EN 1992-1-1 8.4.2, the U-bar bends as hooks), and hairpins round the bolts VRd,re =
As,re·fyk/1.15 (k10 = 1), which then carry the shear in place of the edge and pry-out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..materials import concrete

# Stress area (mm²) of metric threads (ISO 898-1; M56 and up with a 4 or 6 mm pitch).
STRESS_AREA = {
    12: 84.3,
    16: 157.0,
    20: 245.0,
    24: 353.0,
    27: 459.0,
    30: 561.0,
    33: 694.0,
    36: 817.0,
    39: 976.0,
    42: 1120.0,
    45: 1310.0,
    48: 1470.0,
    52: 1760.0,
    56: 2030.0,
    60: 2360.0,
    64: 2680.0,
    72: 3460.0,
    80: 4340.0,
    90: 5590.0,
    100: 7000.0,
}
# (fyk, fuk) MPa. A4 stainless to ISO 3506; property classes to ISO 898-1.
BOLT_GRADES = {
    "4.6": (240.0, 400.0),
    "5.6": (300.0, 500.0),
    "8.8": (640.0, 800.0),
    "10.9": (900.0, 1000.0),
    "A4-70": (450.0, 700.0),
    "A4-80": (600.0, 800.0),
}
GAMMA_C = 1.5
GAMMA_RE = 1.15
BARS = (16, 20, 25, 32)


def stress_area(d: float) -> float:
    """Stress area of a metric thread; sizes not in the table from As = π/4·(d − 0.9382·p)²."""
    if int(d) in STRESS_AREA:
        return STRESS_AREA[int(d)]
    p = 6.0 if d > 64 else 4.0 if d > 39 else 3.5
    return math.pi / 4 * (d - 0.9382 * p) ** 2


def gamma_ms(fyk: float, fuk: float) -> tuple[float, float]:
    """EN 1992-4 Table 4.1: steel partial factors in tension and in shear."""
    t = max(1.2 * fuk / fyk, 1.4)
    v = max(fuk / fyk, 1.25) if fuk <= 800 and fyk / fuk <= 0.8 else 1.5
    return t, v


@dataclass
class Group:
    """Bolt positions (mm) in the face they are cast into: u along the beam, v across the face (up the
    front face, or inland on the top), with the edges of that face at v = v_min − c_neg and v_max + c_pos.
    Along the beam the edges are far (ends or joints further than 1.5 hef)."""

    bolts: list[tuple[float, float]]
    diameter: float
    grade: str
    hef: float
    head: float  # bearing diameter of the head or anchor plate per bolt, mm
    c_pos: float  # edge beyond the bolts at the largest v, mm
    c_neg: float  # edge beyond the bolts at the smallest v, mm
    thickness: float  # member thickness in the direction of the bolts, mm
    concrete_grade: str
    cracked: bool = True
    edge_bars: float = 1.0  # ψre,V: 1.0 none, 1.2 straight edge bars ≥ Ø12, 1.4 closed links ≤ 100 mm
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.bolts)


def grid(rows: int, cols: int, su: float, sv: float) -> list[tuple[float, float]]:
    """rows across the face × cols along it, centred on 0."""
    return [((j - (cols - 1) / 2) * su, (i - (rows - 1) / 2) * sv) for i in range(rows) for j in range(cols)]


def circle(count: int, diameter: float, start: float = 0.0) -> list[tuple[float, float]]:
    """Bolts on a circle, the first at ``start`` degrees from the u axis (22.5° puts none on an axis
    for 8 bolts)."""
    r = diameter / 2
    return [
        (
            r * math.cos(math.radians(start + 360 * k / count)),
            r * math.sin(math.radians(start + 360 * k / count)),
        )
        for k in range(count)
    ]


def bolt_forces(g: Group, n: float, mu: float, mv: float) -> list[float]:
    """Tension in each bolt (kN, ≥ 0): n (kN, + pulls out) shared, mu (kNm, about the u axis, tension
    at +v) and mv (kNm, about the v axis, tension at +u) by the elastic bolt group."""
    iu = sum(v * v for _, v in g.bolts)
    iv = sum(u * u for u, _ in g.bolts)
    out = []
    for u, v in g.bolts:
        t = n / g.n
        if iu > 0:
            t += mu * 1e3 * v / iu
        if iv > 0:
            t += mv * 1e3 * u / iv
        out.append(max(t, 0.0))
    return out


def _span(values: list[float], cap: float) -> float:
    """Length of a row of bolts at ``values`` with gaps capped at ``cap`` (spacings beyond scr count
    as scr)."""
    xs = sorted(set(round(x, 3) for x in values))
    return sum(min(b - a, cap) for a, b in zip(xs, xs[1:], strict=False))


def cone(g: Group, tensioned: list[int], forces: list[float]) -> dict[str, Any]:
    """Concrete cone resistance NRk,c of the tensioned bolts (7.2.1.4)."""
    conc = concrete(g.concrete_grade)
    k1 = 8.9 if g.cracked else 12.7
    hef = g.hef
    scr, ccr = 3 * hef, 1.5 * hef
    n0 = k1 * math.sqrt(conc.fck) * hef**1.5 / 1e3  # kN
    us = [g.bolts[i][0] for i in tensioned]
    vs = [g.bolts[i][1] for i in tensioned]
    vmin, vmax = min(v for _, v in g.bolts), max(v for _, v in g.bolts)
    c_hi = g.c_pos + (vmax - max(vs))
    c_lo = g.c_neg + (min(vs) - vmin)
    width_u = _span(us, scr) + 2 * ccr
    width_v = _span(vs, scr) + min(c_hi, ccr) + min(c_lo, ccr)
    ac = width_u * width_v
    a0 = scr * scr
    c = min(c_hi, c_lo)
    psi_s = min(1.0, 0.7 + 0.3 * c / ccr)
    psi_re = min(1.0, 0.5 + hef / 200)
    total = sum(forces[i] for i in tensioned)
    if total > 0:
        eu = abs(sum(forces[i] * g.bolts[i][0] for i in tensioned) / total - sum(us) / len(us))
        ev = abs(sum(forces[i] * g.bolts[i][1] for i in tensioned) / total - sum(vs) / len(vs))
    else:
        eu = ev = 0.0
    psi_ec = 1 / (1 + 2 * eu / scr) * 1 / (1 + 2 * ev / scr)
    nrk = n0 * ac / a0 * psi_s * psi_re * psi_ec
    return {
        "N0Rk_kN": round(n0, 1),
        "Ac_N_m2": round(ac / 1e6, 3),
        "A0c_N_m2": round(a0 / 1e6, 3),
        "psi_s": round(psi_s, 3),
        "psi_re": round(psi_re, 3),
        "psi_ec": round(psi_ec, 3),
        "c_min_mm": round(c),
        "NRk_kN": round(nrk, 1),
        "NRd_kN": round(nrk / GAMMA_C, 1),
    }


def _edge_rows(g: Group, side: int) -> tuple[list[int], float]:
    """Bolts in the row nearest the edge on ``side`` (+1: at +v) and that edge's distance c1."""
    vs = [v for _, v in g.bolts]
    edge = max(vs) if side > 0 else min(vs)
    row = [i for i, (_, v) in enumerate(g.bolts) if abs(v - edge) < 1.0]
    c1 = g.c_pos if side > 0 else g.c_neg
    return row, c1


def edge_shear(g: Group, side: int, alpha: float) -> dict[str, Any]:
    """Concrete edge resistance VRk,c towards the edge on ``side``, the shear at ``alpha`` (°) from
    square to the edge (7.2.2.5), carried by the row nearest the edge."""
    conc = concrete(g.concrete_grade)
    row, c1 = _edge_rows(g, side)
    d = g.diameter
    lf = min(g.hef, 12 * d) if d <= 24 else min(g.hef, max(8 * d, 300.0))
    c1 = max(c1, 1.0)
    a = 0.1 * (lf / c1) ** 0.5
    b = 0.1 * (d / c1) ** 0.2
    k9 = 1.7 if g.cracked else 2.4
    v0 = k9 * d**a * lf**b * math.sqrt(conc.fck) * c1**1.5 / 1e3
    s = _span([g.bolts[i][0] for i in row], 3 * c1)
    other = g.c_neg if side > 0 else g.c_pos
    h_eff = min(g.thickness, 1.5 * c1)
    ac = (3 * c1 + s) * h_eff
    a0 = 4.5 * c1 * c1
    psi_s = 1.0  # along the beam the ends are far
    psi_h = max(1.0, (1.5 * c1 / g.thickness) ** 0.5)
    ar = math.radians(min(max(alpha, 0.0), 90.0))
    psi_a = math.sqrt(1 / (math.cos(ar) ** 2 + (0.5 * math.sin(ar)) ** 2))
    vrk = v0 * ac / a0 * psi_s * psi_h * psi_a * g.edge_bars
    return {
        "edge": "+v" if side > 0 else "-v",
        "c1_mm": round(c1),
        "c2_other_side_mm": round(other),
        "bolts_in_row": len(row),
        "V0Rk_kN": round(v0, 1),
        "Ac_V_m2": round(ac / 1e6, 3),
        "A0c_V_m2": round(a0 / 1e6, 3),
        "psi_h": round(psi_h, 3),
        "psi_alpha": round(psi_a, 3),
        "psi_re": g.edge_bars,
        "VRk_kN": round(vrk, 1),
        "VRd_kN": round(vrk / GAMMA_C, 1),
        "row": row,
    }


def check_group(
    g: Group,
    n: float,
    vu: float,
    vv: float,
    mu: float,
    mv: float,
    bar_fyk: float = 500.0,
) -> dict[str, Any]:
    """Every EN 1992-4 check of the group under design loads: n (kN, + pull-out), shears vu, vv (kN)
    and moments mu (about u, tension at +v), mv (about v, tension at +u) in kNm. Any anchor
    reinforcement needed is the lightest of Ø16 to Ø32 that works."""
    fyk, fuk = BOLT_GRADES[g.grade]
    gt, gv = gamma_ms(fyk, fuk)
    As = stress_area(g.diameter)
    conc = concrete(g.concrete_grade)
    forces = bolt_forces(g, n, mu, mv)
    ten = [i for i, f in enumerate(forces) if f > 1e-6]
    n_max = max(forces) if forces else 0.0
    n_sum = sum(forces)
    v = math.hypot(vu, vv)
    v_bolt = v / g.n

    nrd_s = As * fuk / gt / 1e3
    vrd_s = (0.6 if fuk <= 500 else 0.5) * As * fuk / gv / 1e3
    rows: list[dict[str, Any]] = []

    def add(name: str, clause: str, ed: float, rd: float, what: str, kind: str) -> float:
        u = ed / rd if rd > 0 else (0.0 if ed <= 0 else math.inf)
        rows.append(
            {
                "check": name,
                "clause": clause,
                "Ed_kN": round(ed, 1),
                "Rd_kN": round(rd, 1),
                "utilisation": round(u, 3) if math.isfinite(u) else None,
                "passed": bool(u <= 1.0),
                "on": what,
                "kind": kind,
            }
        )
        return u

    bn_s = add("Steel failure in tension", "EN 1992-4 7.2.1.3", n_max, nrd_s, "most loaded bolt", "steel")
    ah = max(math.pi / 4 * (g.head**2 - g.diameter**2), 1.0)
    k2 = 7.5 if g.cracked else 10.5
    nrd_p = k2 * ah * conc.fck / GAMMA_C / 1e3
    bn_p = add("Pull-out", "EN 1992-4 7.2.1.5", n_max, nrd_p, "most loaded bolt", "concrete")
    cone_d: dict[str, Any] | None = None
    bn_c = 0.0
    if ten:
        cone_d = cone(g, ten, forces)
        bn_c = add(
            "Concrete cone", "EN 1992-4 7.2.1.4", n_sum, cone_d["NRd_kN"], "tensioned bolts", "concrete"
        )
    # Blow-out at an edge closer than 0.5 hef.
    bn_b = 0.0
    blow: list[dict[str, Any]] = []
    for side in (1, -1):
        row, c1 = _edge_rows(g, side)
        if c1 >= 0.5 * g.hef:
            continue
        ed = sum(forces[i] for i in row)
        if ed <= 0:
            continue
        k5 = 8.7 if g.cracked else 12.2
        n0 = k5 * c1 * math.sqrt(ah) * math.sqrt(conc.fck) / 1e3
        s2 = _span([g.bolts[i][0] for i in row], 4 * c1)
        ac, a0 = (4 * c1 + s2) * 4 * c1, 16 * c1 * c1
        nb = len(row)
        psi_g = (
            max(1.0, math.sqrt(nb) + (1 - math.sqrt(nb)) * (s2 / max(nb - 1, 1)) / (4 * c1))
            if nb > 1
            else 1.0
        )
        nrd = n0 * ac / a0 * psi_g / GAMMA_C
        blow.append({"edge": "+v" if side > 0 else "-v", "c1_mm": round(c1), "NRd_kN": round(nrd, 1)})
        bn_b = max(
            bn_b,
            add(
                "Blow-out",
                "EN 1992-4 7.2.1.8",
                ed,
                nrd,
                f"row at the {'+v' if side > 0 else '-v'} edge",
                "concrete",
            ),
        )

    bv_s = add("Steel failure in shear", "EN 1992-4 7.2.2.3", v_bolt, vrd_s, "each bolt", "steel")
    bv_cp = 0.0
    if v > 0:
        all_i = list(range(g.n))
        pry = cone(g, all_i, [1.0] * g.n)
        bv_cp = add("Pry-out", "EN 1992-4 7.2.2.4", v, 2.0 * pry["NRd_kN"], "group", "concrete")
    edges: list[dict[str, Any]] = []
    bv_c = 0.0
    for side, comp in ((1, vv), (-1, -vv)):
        if comp <= 1e-9 and abs(vu) <= 1e-9:
            continue
        if comp < -1e-9:
            continue  # the shear points away from this edge
        alpha = math.degrees(math.atan2(abs(vu), max(comp, 0.0)))
        e = edge_shear(g, side, alpha)
        share = len(e["row"]) / g.n
        # The row nearest the edge takes its share of the shear; conservatively all of it where the
        # far rows sit within 1.5 c1 of the edge row.
        vs = [vv_ for _, vv_ in g.bolts]
        depth = max(vs) - min(vs)
        ed = v if depth < 1.5 * e["c1_mm"] else v * share
        e["V_Ed_kN"] = round(ed, 1)
        edges.append(e)
        bv_c = max(
            bv_c,
            add(
                f"Concrete edge ({'+v' if side > 0 else '-v'} edge)",
                "EN 1992-4 7.2.2.5",
                ed,
                e["VRd_kN"],
                f"{len(e['row'])} bolt(s) nearest the edge",
                "concrete",
            ),
        )

    bn_conc = max(bn_p, bn_c, bn_b)
    bv_conc = max(bv_cp, bv_c)
    steel_i = bn_s**2 + bv_s**2
    conc_i = bn_conc**1.5 + bv_conc**1.5 if math.isfinite(bn_conc) and math.isfinite(bv_conc) else math.inf
    rows.append(
        {
            "check": "Steel, tension with shear",
            "clause": "EN 1992-4 7.2.3 (7.54)",
            "utilisation": round(steel_i, 3),
            "passed": bool(steel_i <= 1.0),
            "kind": "steel",
        }
    )
    rows.append(
        {
            "check": "Concrete, tension with shear",
            "clause": "EN 1992-4 7.2.3 (7.55)",
            "utilisation": round(conc_i, 3) if math.isfinite(conc_i) else None,
            "passed": bool(conc_i <= 1.0),
            "kind": "concrete",
        }
    )

    # Anchor reinforcement where the concrete alone is not enough (cone, blow-out, pry-out or edge).
    fyd = bar_fyk / GAMMA_RE
    reinf: dict[str, Any] = {}
    fbd = 2.25 * 1.0 * 1.0 * 0.7 * conc.fctm / GAMMA_C
    if max(bn_c, bn_b) > 1.0 or conc_i > 1.0 and ten:
        best = None
        for d in BARS:
            a_bar = math.pi * d**2 / 4
            # Each leg is anchored in the cone above its failure surface: the cone rises 1 in 1.5 from
            # the head, the leg sits a = head / 2 + 25 + Ø/2 from the bolt, less the cover (l1 ≥ 4Ø);
            # the U-bars' bends count as hooks (α1 = 0.7).
            a = g.head / 2 + 25 + d / 2
            l1 = g.hef - a / 1.5 - 50.0
            if l1 < 4 * d:
                continue
            for legs in range(2 * len(ten), 4 * len(ten) + 1, 2):
                nrd_re = legs * a_bar * fyd / 1e3
                nrd_a = legs * math.pi * d * l1 * fbd / 0.7 / 1e3
                u = max(n_sum / nrd_re, n_sum / nrd_a)
                if (
                    best is None
                    or (u <= 1.0 and (best[0] > 1.0 or legs * a_bar < best[1]))
                    or (best[0] > 1.0 and u < best[0])
                ):
                    best = (u, legs * a_bar, d, legs, l1, nrd_re, nrd_a)
        if best is not None:
            u, _, d, legs, l1, nrd_re, nrd_a = best
            reinf["tension"] = {
                "legs": legs,
                "bar": d,
                "text": f"{legs} legs Ø{d} (U-bars) next to the tensioned bolts, within 0.75 hef ({0.75 * g.hef:.0f} mm)",
                "NRd_re_kN": round(nrd_re, 1),
                "l1_mm": round(l1),
                "NRd_a_kN": round(nrd_a, 1),
                "utilisation": round(u, 3),
                "clause": "EN 1992-4 7.2.1.9",
            }
        else:
            reinf["tension"] = {
                "text": "The embedment is too short to anchor reinforcement in the cone.",
                "utilisation": None,
            }
    if bv_c > 1.0 or bv_cp > 1.0:
        # Hairpins round the bolts carry the whole shear (k10 = 1), in place of the edge and pry-out.
        best = None
        for d in BARS:
            a_bar = math.pi * d**2 / 4
            loops = max(2, math.ceil(v * 1e3 / (a_bar * fyd)))
            loops += loops % 2
            if best is None or loops * a_bar < best[0] or (best[1] > 4 * g.n and loops <= 4 * g.n):
                if best is None or loops <= 4 * g.n or best[1] > loops:
                    best = (loops * a_bar, loops, d)
        _, loops, d = best
        a_bar = math.pi * d**2 / 4
        reinf["shear"] = {
            "legs": loops,
            "bar": d,
            "text": f"{loops} hairpin legs Ø{d} round the bolts, towards the loaded edge",
            "VRd_re_kN": round(loops * a_bar * fyd / 1e3, 1),
            "utilisation": round(v * 1e3 / (loops * a_bar * fyd), 3),
            "clause": "EN 1992-4 7.2.2.6",
        }
    split = 0.5 * n_sum * 1e3 / fyd
    reinf["splitting_mm2"] = round(split)

    with_reinf = conc_i
    if reinf.get("tension") or reinf.get("shear"):
        t_u = reinf["tension"]["utilisation"] if "tension" in reinf else max(bn_c, bn_b)
        bn2 = max(bn_p, t_u if t_u is not None else math.inf)
        bv2 = reinf["shear"]["utilisation"] if "shear" in reinf else bv_conc
        with_reinf = max(bn2, bv2)
    u_all = max(steel_i, bn_s, bv_s, with_reinf if math.isfinite(with_reinf) else math.inf)
    return {
        "bolts": g.n,
        "bolt": f"M{g.diameter:g} grade {g.grade}",
        "positions": [[round(u, 1), round(v, 1)] for u, v in g.bolts],
        "As_mm2": round(As, 1),
        "fyk_MPa": fyk,
        "fuk_MPa": fuk,
        "gamma_Ms_tension": round(gt, 3),
        "gamma_Ms_shear": round(gv, 3),
        "hef_mm": g.hef,
        "head_mm": g.head,
        "edges_mm": {"+v": round(g.c_pos), "-v": round(g.c_neg)},
        "member_mm": g.thickness,
        "concrete": g.concrete_grade,
        "cracked": g.cracked,
        "loads": {
            "N_kN": round(n, 1),
            "Vu_kN": round(vu, 1),
            "Vv_kN": round(vv, 1),
            "Mu_kNm": round(mu, 1),
            "Mv_kNm": round(mv, 1),
        },
        "bolt_tension_kN": [round(f, 1) for f in forces],
        "N_max_kN": round(n_max, 1),
        "N_group_kN": round(n_sum, 1),
        "V_bolt_kN": round(v_bolt, 1),
        "cone": cone_d,
        "blow_out": blow,
        "edges": [{k: v for k, v in e.items() if k != "row"} for e in edges],
        "checks": rows,
        "concrete_only_utilisation": round(conc_i, 3) if math.isfinite(conc_i) else None,
        "reinforcement": reinf,
        "utilisation": round(u_all, 3) if math.isfinite(u_all) else None,
        "passed": bool(u_all <= 1.0),
    }


def local_bearing(
    force_kN: float, a1: float, b1: float, a2: float, b2: float, concrete_grade: str, bar: int = 16
) -> dict[str, Any]:
    """Partially loaded area (EN 1992-1-1 6.7): a1 × b1 loaded, spreading to a2 × b2 (mm) inside the
    member, FRdu = Ac0·fcd·√(Ac1/Ac0) ≤ 3·fcd·Ac0; and the bursting bars for T = 0.25·F·(1 − a/h)
    across the spread (6.5, EN 1992-1-1 Figure 6.25 / 8.10.3)."""
    conc = concrete(concrete_grade)
    fcd = 1.0 * conc.fck / GAMMA_C
    a2, b2 = max(a2, a1), max(b2, b1)
    ac0, ac1 = a1 * b1, a2 * b2
    frdu = min(ac0 * fcd * math.sqrt(ac1 / ac0), 3.0 * fcd * ac0) / 1e3
    t = max(0.25 * force_kN * (1 - a1 / a2), 0.25 * force_kN * (1 - b1 / b2))
    as_req = t * 1e3 / (500.0 / GAMMA_RE)
    a_bar = math.pi * bar**2 / 4
    n = max(2, math.ceil(as_req / a_bar))
    u = force_kN / frdu if frdu > 0 else math.inf
    return {
        "F_Ed_kN": round(force_kN, 1),
        "loaded_mm": [round(a1), round(b1)],
        "spread_mm": [round(a2), round(b2)],
        "fcd_MPa": round(fcd, 2),
        "FRdu_kN": round(frdu, 1),
        "utilisation": round(u, 3),
        "passed": bool(u <= 1.0),
        "bursting_T_kN": round(t, 1),
        "bursting_As_mm2": round(as_req),
        "bursting_bars": f"{n} legs Ø{bar}",
        "clause": "EN 1992-1-1 6.7 and 6.5.3",
    }
