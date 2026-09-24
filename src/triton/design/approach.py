"""The approach slab behind the quay and the ledge on the rear beam that it rests on.

The approach slab links the quay to the existing slab on grade. At the quay end it sits on an
elastomeric bearing strip on a ledge (nib) protruding from the rear beam, with an expansion joint
between the slab and the beam; at the far end it rests on the slab on grade or the ground. Plaxis
does not model it, so its size and loads are entered for the whole project (Elements tab).

Approach slab, per metre width, EN 1992-1-1:

* Model: a beam on the bearing line (vertical support only, free to rotate and slide) and, over
  the length the fill may settle away ("length with no ground support"), no support below; beyond
  it compression-only ground springs k·Δx (Winkler, modulus of subgrade reaction). With no ground
  support at all the far end rests on the slab on grade (vertical support), so the slab spans from
  the ledge to its far end.
* Loads: self weight and surfacing (permanent), surcharge on the whole slab, on the unsupported
  length only and on the rest only (patterns), and one wheel moved along the slab every 0.25 m.
  The wheel spreads over an effective width b_eff = a + 2h + 2.4·x·(1 − x/L) per metre (a the contact
  width spread at 45° through the slab, x the distance to the nearer support of the span L,
  BS 8110 3.5.2.2), so its load per
  metre is P / b_eff.
* ULS = γG·G + γQ·(surcharge + wheel); QP = G + ψ2·surcharge (the wheel is traffic, ψ2 = 0).
* Bars per metre for the largest sagging (bottom) and hogging (top) moments, 1 m strip, with the
  minimum 9.3.1.1 (max(0.26·fctm/fyk, 0.0013)·b·d) on both faces; distribution bars across,
  at least 20% of the main bars (9.3.1.1(2)) and the minimum. One layer, the slab mesh spacings.
* QP crack width 7.3.4 on each face against its own limit, σs from the cracked elastic section
  (αe with the creep coefficient).
* Shear 6.2.2 without links, taken at d from the bearing; where VEd > VRd,c the links needed are
  given (6.2.3, cot θ = 2.5), or a thicker slab.

Ledge (nib), EN 1992-1-1 6.5 and Annex J.3, per metre along the rear beam:

* The load F is the slab's bearing reaction plus the ledge's own weight, at a_c from the beam face
  to the bearing centre (projection − edge distance − bearing width / 2); a horizontal force
  H = ratio·F (at least 0.2 F, J.3) acts at the bearing.
* Strut and tie: tie at the top, d = depth − cover − φ/2; the strut runs from the bearing down to
  a compression node at the bottom of the beam face whose depth x = (Ft − H)/(b·σRd,max),
  σRd,max = ν'·fcd (CCC node, 6.5.4), ν' = 1 − fck/250; z = d − x/2 and
  Ft = F·a_c/z + H·(z + aH)/z with aH from the tie up to the bearing top. The strut angle
  1 ≤ tan θ = z/a_c ≤ 2.5 (J.3(1)); where a_c is shorter a_c = z/2.5 is used; where it is longer
  than z the ledge is a short cantilever and the same tie takes the bending.
* Bearing node under the strip (CCT): σ = F/(bearing width) ≤ 0.85·ν'·fcd.
* Shear at the beam face 6.2.2(6): β·VEd ≤ VRd,c with β = av/2d (0.5 ≤ β ≤ 1), VEd ≤ 0.5·b·d·ν·fcd.
* Links (J.3(2)): with a_c ≤ 0.5·hc closed horizontal links ≥ 0.25·As,main; with a_c > 0.5·hc and
  F > VRd,c closed vertical links ≥ 0.5·F/fyd.
* QP crack width at the beam face from the QP bending F·a_c.
* Hanger steel in the rear beam: the ledge hangs its whole load on the beam's side, so vertical
  bars (or link legs) near that face of F/fyd per metre take it up into the beam, in addition to the
  beam's shear links.

Rear beam: the ledge adds a line load F at an eccentricity e = beam width/2 + a_c from the beam's
centre line, so a torque F·e per metre. Between its supports (pile spacing Ls, from the workbook)
the beam carries, on top of the Plaxis actions at every station, ΔV = F·Ls/2, ΔT = F·e·Ls/2 and
ΔM = F·Ls²/12 (continuous beam), each added to the size of the Plaxis value, ULS and QP. Plaxis
is taken not to model the approach slab, so nothing is counted twice.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import ApproachSlabInput, BeamInput, DesignSettings
from .crack import E_S, crack_width

ELEMENT = "Approach Slab"
STEP = 0.05  # m, beam elements
WHEEL_STEP = 0.25  # m, wheel positions
LEDGE_SPACINGS = (100.0, 125.0, 150.0, 200.0)


# --- Analysis ---------------------------------------------------------------------------------


def _element(dx: float, ei: float) -> np.ndarray:
    return (
        ei
        / dx**3
        * np.array(
            [
                [12, 6 * dx, -12, 6 * dx],
                [6 * dx, 4 * dx * dx, -6 * dx, 2 * dx * dx],
                [-12, -6 * dx, 12, -6 * dx],
                [6 * dx, 2 * dx * dx, -6 * dx, 4 * dx * dx],
            ]
        )
    )


def _stiffness(n: int, dx: float, ei: float) -> np.ndarray:
    k = np.zeros((2 * n, 2 * n))
    ke = _element(dx, ei)
    for e in range(n - 1):
        i = [2 * e, 2 * e + 1, 2 * e + 2, 2 * e + 3]
        k[np.ix_(i, i)] += ke
    return k


class Beam:
    """A 1 m strip on the ledge (x = 0) and on the ground or its far-end support."""

    def __init__(self, length: float, ei: float, unsupported: float, k_sub: float):
        self.n = max(int(round(length / STEP)), 10) + 1
        self.x = np.linspace(0.0, length, self.n)
        self.dx = self.x[1] - self.x[0]
        self.K = _stiffness(self.n, self.dx, ei)
        self.ei = ei
        self.on_ground = self.x > unsupported + 1e-9
        self.spring = np.where(self.on_ground, k_sub * self.dx, 0.0)
        self.spring[[0, -1]] *= 0.5
        self.far_support = not self.on_ground.any()
        # Span for the wheel's effective width: the unsupported length, or the whole slab.
        self.span = length if self.far_support or unsupported < 1.0 else min(unsupported, length)

    def solve(self, q: np.ndarray, p: np.ndarray) -> dict[str, Any]:
        """Nodal loads: q (kN/m at nodes, lumped) and p (kN at nodes); ground springs in compression only."""
        f = np.zeros(2 * self.n)
        f[0::2] = -(q * self.dx + p)
        f[0] += q[0] * self.dx / 2
        f[-2] += q[-1] * self.dx / 2
        fixed = [0] + ([2 * (self.n - 1)] if self.far_support else [])
        active = self.spring > 0
        for _ in range(30):
            K = self.K.copy()
            K[0::2, 0::2] += np.diag(np.where(active, self.spring, 0.0))
            free = np.setdiff1d(np.arange(2 * self.n), fixed)
            u = np.zeros(2 * self.n)
            u[free] = np.linalg.solve(K[np.ix_(free, free)], f[free])
            w = u[0::2]
            now = (self.spring > 0) & (w < 1e-12)  # a spring works while the slab presses down
            if (now == active).all():
                break
            active = now
        r = self.K @ u - f
        r[0::2] += np.where(active, self.spring, 0.0) * u[0::2]
        # Bending moments (sagging +, EI·w'') and shears from the element end forces: no load acts
        # inside an element (loads are at the nodes), so M is linear and V constant in each.
        m = np.zeros(self.n)
        v = np.zeros(self.n)
        ke = _element(self.dx, self.ei)
        for e in range(self.n - 1):
            fe = ke @ u[2 * e : 2 * e + 4]
            m[e] = -fe[1]
            m[e + 1] = fe[3]
            v[e] = max(v[e], abs(fe[0]))
            v[e + 1] = max(v[e + 1], abs(fe[0]))
        return {
            "M": m,
            "V": v,
            "R": float(r[0]),
            "R_far": float(r[-2]) if self.far_support else 0.0,
            "w": u[0::2],
        }


def _lumped(x: np.ndarray, value: float, lo: float = -1.0, hi: float = 1e9) -> np.ndarray:
    return np.where((x >= lo - 1e-9) & (x <= hi + 1e-9), value, 0.0)


def effective_width(slab: ApproachSlabInput, beam: Beam, xw: float) -> float:
    """b_eff (m) of a wheel at xw: a + 2h + 2.4·x·(1 − x/L), x to the nearer support of the span."""
    a = slab.wheel_contact / 1000 + 2 * slab.thickness / 1000
    L = beam.span
    x = min(xw, max(L - xw, 0.0)) if xw <= L else 0.0
    return a + 2.4 * x * (1 - x / L) if L > 0 else a


def analyse(slab: ApproachSlabInput, settings: DesignSettings) -> dict[str, Any]:
    """ULS and QP envelopes of M and V per metre, and the bearing reaction on the ledge."""
    conc = concrete(slab.concrete or settings.materials.concrete)
    h = slab.thickness / 1000
    ei = conc.ecm * 1e3 * 1.0 * h**3 / 12  # kNm² per m (uncracked)
    Lu = slab.length if slab.unsupported_length is None else min(slab.unsupported_length, slab.length)
    beam = Beam(slab.length, ei, Lu, slab.subgrade_modulus)
    x = beam.x
    g = slab.unit_weight * h + slab.surfacing
    patterns = [("full", 0.0, slab.length)]
    if not beam.far_support and Lu > 0:
        patterns += [("unsupported length", 0.0, Lu), ("on the ground", Lu, slab.length)]
    wheels: list[float | None] = [None]
    if slab.wheel_load > 0:
        first = slab.wheel_contact / 2000  # the wheel's edge at the bearing line
        wheels += list(np.arange(first, slab.length - first + 1e-9, WHEEL_STEP))
    d_v = slab.thickness - 60.0  # mm, for the shear reduction of loads near the bearing
    env: dict[str, dict[str, np.ndarray]] = {}
    reactions: dict[str, list[float]] = {"uls": [], "qp": [], "uls_spread": []}
    cases = 0
    zero = np.zeros_like(x)
    dead = beam.solve(np.full_like(x, g), zero)
    for combo, gg, gq, psi, wheel_on in (
        ("uls", slab.gamma_g, slab.gamma_q, 1.0, True),
        ("qp", 1.0, 1.0, slab.psi2, False),
    ):
        mx, mn, vv = np.full_like(x, -np.inf), np.full_like(x, np.inf), np.zeros_like(x)
        for _, lo, hi in patterns + [("none", 1.0, 0.0)]:
            q = np.full_like(x, gg * g) + gq * psi * _lumped(x, slab.surcharge, lo, hi)
            for xw in wheels if wheel_on else [None]:
                p = np.zeros_like(x)
                beta = 1.0
                if xw is not None:
                    i = int(np.argmin(abs(x - xw)))
                    p[i] = gq * slab.wheel_load / effective_width(slab, beam, float(x[i]))
                    # 6.2.2(6): a load within 2d of the bearing counts β = av/2d in the shear there.
                    to_support = (
                        float(x[i]) if not beam.far_support else min(float(x[i]), slab.length - float(x[i]))
                    )
                    av = max(to_support - slab.wheel_contact / 2000, 0.0) * 1000
                    beta = min(max(av / (2 * d_v), 0.25), 1.0)
                r = beam.solve(q, p)
                cases += 1
                mx, mn = np.maximum(mx, r["M"]), np.minimum(mn, r["M"])
                near = (x <= 2 * d_v / 1000) | (beam.far_support & (x >= slab.length - 2 * d_v / 1000))
                if beta < 1:
                    rv = beam.solve(q, p * beta)
                    vv = np.maximum(vv, np.where(near, abs(rv["V"]), abs(r["V"])))
                else:
                    vv = np.maximum(vv, abs(r["V"]))
                reactions[combo].append(r["R"])
                if xw is None:
                    reactions[combo + "_spread"] = reactions.get(combo + "_spread", []) + [r["R"]]
        env[combo] = {"M_max": mx, "M_min": mn, "V": vv}
    # The whole wheel's reaction on the ledge, at the bearing: all of it.
    wheel_total = slab.gamma_q * slab.wheel_load if slab.wheel_load > 0 else 0.0
    return {
        "beam": beam,
        "x": x,
        "env": env,
        "cases": cases,
        "g_kPa": g,
        "R_uls": max(reactions["uls"]),
        "R_qp": max(reactions["qp"]),
        "R_g": dead["R"],
        "R_uls_spread": max(reactions["uls_spread"]),
        "P_wheel_uls": wheel_total,
        "span_m": beam.span,
        "unsupported_m": Lu,
        "far_support": beam.far_support,
    }


# --- Sections per metre -----------------------------------------------------------------------


def cracked(m_knm: float, area: float, d: float, ae: float) -> tuple[float, float]:
    """(σs MPa, x mm) of a 1 m singly reinforced section under M (kNm), cracked, elastic."""
    if area <= 0 or m_knm <= 0:
        return 0.0, 0.0
    rho = area / (1000 * d)
    k = -ae * rho + math.sqrt((ae * rho) ** 2 + 2 * ae * rho)
    x = k * d
    z = d - x / 3
    return m_knm * 1e6 / (area * z), x


def mrd(area: float, d: float, fcd: float, fyd: float) -> float:
    """kNm per metre of a singly reinforced 1 m strip (rectangular block 0.8x, η = 1)."""
    x = area * fyd / (0.8 * 1000 * fcd)
    return area * fyd * (d - 0.4 * x) / 1e6


def as_min(fctm: float, fyk: float, d: float, b: float = 1000.0) -> float:
    return max(0.26 * fctm / fyk, 0.0013) * b * d


def _options(settings: DesignSettings, spacings: tuple[float, ...] | list[float]) -> list[tuple]:
    """(mm²/m, Ø, spacing) of one layer, least steel first."""
    r = settings.reinforcement
    out = []
    for phi in r.bar_diameters:
        if phi < 10:
            continue
        for s in spacings:
            if s - phi >= max(r.slab_min_clear_spacing, phi) - 1e-9:
                out.append((1000 * math.pi * phi * phi / 4 / s, phi, float(s)))
    return sorted(out, key=lambda o: (o[0], -o[1]))


def _label(o: tuple) -> str:
    return f"Ø{o[1]} @ {o[2]:g}"


def _pick(options, need: float, check=None) -> tuple | None:
    for o in options:
        if o[0] >= need - 1e-9 and (check is None or check(o)):
            return o
    return None


def vrdc(fck: float, gamma_c: float, d: float, rho: float, b: float = 1000.0) -> float:
    k = min(1 + math.sqrt(200 / d), 2.0)
    v_min = 0.035 * k**1.5 * math.sqrt(fck)
    return max(0.18 / gamma_c * k * (100 * min(rho, 0.02) * fck) ** (1 / 3), v_min) * b * d / 1e3


def design_face(
    face: str,
    m_uls: float,
    m_qp: float,
    h: float,
    cover: float,
    limit: float,
    settings: DesignSettings,
    conc,
    fyk: float,
) -> dict[str, Any]:
    pf = settings.partial_factors
    fcd = pf.alpha_cc * conc.fck / pf.gamma_c
    fyd = fyk / pf.gamma_s
    ae = E_S / (conc.ecm / (1 + settings.cracking.creep_coefficient))
    opts = _options(settings, settings.reinforcement.slab_spacings or [150.0, 200.0])

    def d_of(o):
        return h - cover - o[1] / 2

    def need(o):
        d = d_of(o)
        m = abs(m_uls) * 1e6
        k = m / (1000 * d * d * conc.fck)
        z = min(0.5 * d * (1 + math.sqrt(max(1 - 3.53 * k, 0.0))), 0.95 * d)
        return max(m / (z * fyd), as_min(conc.fctm, fyk, d))

    def wk(o):
        d = d_of(o)
        s, x = cracked(abs(m_qp), o[0], d, ae)
        return crack_width(
            s,
            h=h,
            d=d,
            x=x,
            b=1000.0,
            area=o[0],
            phi=o[1],
            cover=cover,
            spacing=o[2],
            fctm=conc.fctm,
            ecm=conc.ecm,
        )

    chosen = next((o for o in opts if o[0] >= need(o) - 1e-9 and wk(o)["wk"] <= limit + 1e-9), None)
    status = "ok"
    if chosen is None:
        chosen, status = opts[-1] if opts else None, "no bar arrangement passes"
    if chosen is None:
        return {"face": face, "passed": False, "notes": ["No bars available."]}
    d = d_of(chosen)
    cap = mrd(chosen[0], d, fcd, fyd)
    c = wk(chosen)
    k = abs(m_uls) * 1e6 / (1000 * d * d * conc.fck)
    return {
        "face": face,
        "bars": _label(chosen),
        "phi": chosen[1],
        "spacing_mm": chosen[2],
        "as_mm2_per_m": round(chosen[0]),
        "as_req_mm2_per_m": round(need(chosen)),
        "as_min_mm2_per_m": round(as_min(conc.fctm, fyk, d)),
        "d_mm": round(d),
        "M_Ed_kNm": round(abs(m_uls), 1),
        "M_Rd_kNm": round(cap, 1),
        "M_qp_kNm": round(abs(m_qp), 1),
        "K": round(k, 3),
        "wk_mm": c["wk"],
        "sigma_s_MPa": c["sigma_s"],
        "wk_limit_mm": limit,
        "utilisation": round(max(abs(m_uls) / cap if cap > 0 else math.inf, c["wk"] / limit), 3),
        "passed": status == "ok" and abs(m_uls) <= cap + 1e-6 and c["wk"] <= limit + 1e-9,
        "status": status,
    }


# --- Ledge ------------------------------------------------------------------------------------


def design_ledge(
    slab: ApproachSlabInput,
    settings: DesignSettings,
    f_uls: float,
    f_qp: float,
    rear: BeamInput | None,
    spread_uls: float | None = None,
    wheel_uls: float = 0.0,
) -> dict[str, Any]:
    """Strut and tie of the ledge per metre, its links, the hanger steel into the rear beam, and the
    load and torque it puts on the rear beam."""
    L = slab.ledge
    pf = settings.partial_factors
    conc = concrete(slab.concrete or settings.materials.concrete)
    fck = conc.fck
    fcd = pf.alpha_cc * fck / pf.gamma_c
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    fyd = fyk / pf.gamma_s
    nu_p = 1 - fck / 250
    cover = L.cover if L.cover is not None else settings.durability.covers.beams
    b = 1000.0
    hc = L.depth
    notes: list[str] = []
    a_c = L.projection - L.edge_distance - L.bearing_width / 2
    if a_c <= 0:
        return {
            "passed": False,
            "utilisation": None,
            "notes": ["The bearing strip does not fit on the ledge: make it project further."],
        }
    w_self = slab.unit_weight * L.projection * L.depth / 1e6  # kN/m
    f_ed = f_uls + slab.gamma_g * w_self
    f_qp_all = f_qp + w_self
    # Load centre of F with the self weight at half the projection.
    a_f = (f_uls * a_c + slab.gamma_g * w_self * L.projection / 2) / f_ed
    h_ed = L.horizontal_ratio * f_ed
    # Along the rear beam: the reaction of the spread loads as a line load, the wheel as a point load.
    line_uls = (f_uls if spread_uls is None else spread_uls) + slab.gamma_g * w_self
    opts = _options(settings, LEDGE_SPACINGS)
    sigma_ccc = nu_p * fcd

    def tie(o) -> dict[str, float]:
        d = hc - cover - o[1] / 2
        a_h = hc - d + L.bearing_thickness
        z = 0.9 * d
        for _ in range(50):
            a = max(a_f, z / 2.5)
            ft = f_ed * a / z + h_ed * (z + a_h) / z
            x = max(ft - h_ed, 0.0) * 1e3 / (b * sigma_ccc)
            z_new = d - x / 2
            if abs(z_new - z) < 0.01:
                z = z_new
                break
            z = z_new
        a = max(a_f, z / 2.5)
        ft = f_ed * a / z + h_ed * (z + a_h) / z
        x = max(ft - h_ed, 0.0) * 1e3 / (b * sigma_ccc)
        return {"d": d, "z": z, "a": a, "ft": ft, "x": x, "a_h": a_h}

    def ok(o) -> bool:
        t = tie(o)
        need = max(t["ft"] * 1e3 / fyd, as_min(conc.fctm, fyk, t["d"]))
        return o[0] >= need - 1e-9 and crack(o, t)["wk"] <= L.crack_width_limit + 1e-9

    def crack(o, t):
        ae = E_S / (conc.ecm / (1 + settings.cracking.creep_coefficient))
        m_qp = (f_qp * a_c + w_self * L.projection / 2) / 1e3
        s, x = cracked(m_qp, o[0], t["d"], ae)
        return crack_width(
            s,
            h=hc,
            d=t["d"],
            x=x,
            b=b,
            area=o[0],
            phi=o[1],
            cover=cover,
            spacing=o[2],
            fctm=conc.fctm,
            ecm=conc.ecm,
        )

    chosen = next((o for o in opts if ok(o)), None)
    status = "ok"
    if chosen is None:
        chosen, status = opts[-1], "no tie bars pass: a deeper ledge is needed"
        notes.append("No tie bar arrangement passes: make the ledge deeper.")
    t = tie(chosen)
    d, z = t["d"], t["z"]
    tan_theta = z / max(a_f, 1e-9)
    if a_f < z / 2.5:
        notes.append(
            f"a_c = {a_f:.0f} mm is shorter than z/2.5 = {z / 2.5:.0f} mm: the strut is taken at "
            "tan θ = 2.5 (J.3)."
        )
    if tan_theta < 1:
        notes.append(
            f"a_c = {a_f:.0f} mm is longer than z = {z:.0f} mm: the ledge works as a short cantilever; "
            "the tie "
            "takes its bending."
        )
    x_ok = t["x"] <= 0.5 * d
    if not x_ok:
        notes.append("The compression node at the beam face is deeper than d/2: make the ledge deeper.")
    crk = crack(chosen, t)
    as_tie = t["ft"] * 1e3 / fyd
    as_req = max(as_tie, as_min(conc.fctm, fyk, d))
    # Bearing node under the strip (CCT, 6.5.4(4)b).
    sigma_b = f_ed * 1e3 / (L.bearing_width * b)
    sigma_b_rd = 0.85 * nu_p * fcd
    # Shear at the beam face.
    av = max(a_c - L.bearing_width / 2, 0.0)
    beta = min(max(av / (2 * d), 0.5), 1.0)
    v_rdc = vrdc(fck, pf.gamma_c, d, chosen[0] / (b * d), b)
    v_max = 0.5 * b * d * 0.6 * (1 - fck / 250) * fcd / 1e3
    # Links (J.3(2)).
    if a_f <= 0.5 * hc:
        links = {
            "kind": "horizontal",
            "as_mm2_per_m": round(0.25 * chosen[0]),
            "rule": "a_c ≤ 0.5·hc: closed horizontal links ≥ 0.25·As,main over the ledge depth (J.3(2)).",
        }
    elif f_ed > v_rdc:
        links = {
            "kind": "vertical",
            "as_mm2_per_m": round(0.5 * f_ed * 1e3 / fyd),
            "rule": "a_c > 0.5·hc and F > VRd,c: closed vertical links ≥ 0.5·F/fyd (J.3(2)).",
        }
    else:
        links = {
            "kind": "none",
            "as_mm2_per_m": 0,
            "rule": "a_c > 0.5·hc and F ≤ VRd,c: no links needed (J.3(2)).",
        }
    link_bars = None
    if links["as_mm2_per_m"]:
        need = links["as_mm2_per_m"]
        link_bars = _pick(_options(settings, LEDGE_SPACINGS), need / 2)  # two legs per closed link
        links["bars"] = f"closed {_label(link_bars)} (2 legs)" if link_bars else None
    # Hanger steel into the rear beam.
    hang = _pick(_options(settings, LEDGE_SPACINGS), f_ed * 1e3 / fyd)
    top_below = L.top_below_beam_top
    if top_below is None:
        top_below = slab.thickness + L.bearing_thickness
    beam_fit = None
    ecc = None
    if rear is not None:
        if rear.width is not None:
            ecc = rear.width / 2 + a_f
        room = rear.depth - top_below - hc
        beam_fit = room >= -1e-6
        if not beam_fit:
            notes.append(
                f"The ledge ({top_below:.0f} mm down + {hc:.0f} mm deep) reaches below the rear beam "
                f"({rear.depth:.0f} mm)."
            )
    else:
        notes.append("No rear beam in this section: the ledge is designed on its own.")
    utils = {
        "tie": as_req / chosen[0],
        "crack": crk["wk"] / L.crack_width_limit,
        "bearing": sigma_b / sigma_b_rd,
        "shear": beta * f_ed / v_rdc if links["kind"] != "vertical" else 0.0,
        "shear_max": f_ed / v_max,
        "node": t["x"] / (0.5 * d),
    }
    if links["kind"] == "vertical":
        notes.append("The ledge's shear is taken by its vertical links (J.3(2)).")
    u = max(utils.values())
    hang_kg = (hang[0] / 1e6 * (top_below + hc + 2 * 45 * hang[1]) / 1000 * STEEL_DENSITY) if hang else 0.0
    tie_kg = chosen[0] / 1e6 * (L.projection + 2 * 45 * chosen[1] + 500) / 1000 * STEEL_DENSITY
    link_kg = (
        (link_bars[0] * 2 / 1e6) * (2 * (L.projection + hc) / 1000) * STEEL_DENSITY / 2 if link_bars else 0.0
    )
    return {
        "projection_mm": L.projection,
        "depth_mm": hc,
        "top_below_beam_top_mm": top_below,
        "cover_mm": cover,
        "bearing_width_mm": L.bearing_width,
        "bearing_thickness_mm": L.bearing_thickness,
        "edge_distance_mm": L.edge_distance,
        "a_c_mm": round(a_c),
        "a_F_mm": round(a_f),
        "F_Ed_kN_per_m": round(f_ed, 1),
        "F_qp_kN_per_m": round(f_qp_all, 1),
        "H_Ed_kN_per_m": round(h_ed, 1),
        "self_weight_kN_per_m": round(w_self, 1),
        "d_mm": round(d),
        "z_mm": round(z),
        "x_node_mm": round(t["x"]),
        "tan_theta": round(tan_theta, 2),
        "F_t_kN_per_m": round(t["ft"], 1),
        "tie": {
            "bars": _label(chosen),
            "phi": chosen[1],
            "spacing_mm": chosen[2],
            "as_mm2_per_m": round(chosen[0]),
            "as_req_mm2_per_m": round(as_req),
            "detail": "U-bars looped round a transverse bar at the ledge tip, anchored 45Ø into the beam",
        },
        "crack": {"wk_mm": crk["wk"], "sigma_s_MPa": crk["sigma_s"], "limit_mm": L.crack_width_limit},
        "bearing": {"sigma_MPa": round(sigma_b, 2), "sigma_Rd_MPa": round(sigma_b_rd, 2)},
        "shear": {
            "beta": round(beta, 2),
            "V_Ed_kN": round(f_ed, 1),
            "V_Rd_c_kN": round(v_rdc, 1),
            "V_Rd_max_kN": round(v_max, 1),
        },
        "links": links,
        "hanger": {
            "as_req_mm2_per_m": round(f_ed * 1e3 / fyd),
            "bars": f"{_label(hang)} vertical legs at the beam's land face" if hang else None,
            "note": "In addition to the rear beam's shear links.",
        },
        "rear_beam": {
            "line_load_uls_kN_per_m": round(line_uls, 1),
            "line_load_qp_kN_per_m": round(f_qp_all, 1),
            "wheel_uls_kN": round(wheel_uls, 1),
            "a_F_mm": round(a_f),
            "eccentricity_mm": None if ecc is None else round(ecc),
            "torque_uls_kNm_per_m": None if ecc is None else round(line_uls * ecc / 1e3, 1),
            "fits": beam_fit,
        },
        "steel_kg_per_m": round(tie_kg + link_kg + hang_kg, 1),
        "hanger_kg_per_m": round(hang_kg, 1),
        "concrete_m3_per_m": round(L.projection * hc / 1e6, 3),
        "utilisations": {k: round(v, 3) for k, v in utils.items()},
        "utilisation": round(u, 3),
        "passed": status == "ok" and u <= 1 + 1e-6 and x_ok and beam_fit is not False,
        "notes": notes,
    }


# --- The approach slab ------------------------------------------------------------------------


def design_approach(
    slab: ApproachSlabInput, settings: DesignSettings, rear: BeamInput | None = None
) -> dict[str, Any]:
    """Design the approach slab per metre width and the ledge it rests on."""
    pf = settings.partial_factors
    grade = slab.concrete or settings.materials.concrete
    conc = concrete(grade)
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    fyd = fyk / pf.gamma_s
    fcd = pf.alpha_cc * conc.fck / pf.gamma_c
    h = slab.thickness
    c_top = slab.cover_top if slab.cover_top is not None else settings.durability.covers.slab_top
    c_bot = slab.cover_bottom
    a = analyse(slab, settings)
    x, uls, qp = a["x"], a["env"]["uls"], a["env"]["qp"]
    i_sag, i_hog = int(np.argmax(uls["M_max"])), int(np.argmin(uls["M_min"]))
    m_sag, m_hog = max(float(uls["M_max"][i_sag]), 0.0), min(float(uls["M_min"][i_hog]), 0.0)
    m_sag_qp, m_hog_qp = max(float(qp["M_max"].max()), 0.0), min(float(qp["M_min"].min()), 0.0)
    bottom = design_face(
        "bottom", m_sag, m_sag_qp, h, c_bot, slab.crack_width_limit_bottom, settings, conc, fyk
    )
    top = design_face("top", m_hog, m_hog_qp, h, c_top, slab.crack_width_limit, settings, conc, fyk)
    notes = [
        f"Per metre width. Loads: permanent {a['g_kPa']:.1f} kPa (self weight and surfacing), surcharge "
        f"{slab.surcharge:g} kPa, wheel {slab.wheel_load:g} kN on {slab.wheel_contact:g} mm; "
        f"{a['cases']} load cases.",
        (
            f"Spans {slab.length:g} m from the ledge to the slab on grade (no ground support assumed)."
            if a["far_support"]
            else f"No ground support over {a['unsupported_m']:g} m from the ledge; on the ground "
            f"(k = {slab.subgrade_modulus:g} kN/m³) beyond."
        ),
    ]
    # Distribution bars across: 20% of the main bars, and the minimum.
    dist = {}
    for face, main, cover in (("bottom", bottom, c_bot), ("top", top, c_top)):
        opts = _options(settings, settings.reinforcement.slab_spacings or [150.0, 200.0])
        d2 = h - cover - (main.get("phi") or 16) - 8
        need = max(0.2 * (main.get("as_mm2_per_m") or 0), as_min(conc.fctm, fyk, d2))
        o = _pick(opts, need)
        dist[face] = {
            "bars": _label(o) if o else None,
            "as_mm2_per_m": round(o[0]) if o else None,
            "as_req": round(need),
        }
    # Shear at d from the bearing.
    d_v = min(bottom.get("d_mm") or h - 60, top.get("d_mm") or h - 60)
    at = x >= d_v / 1000 - 1e-9
    if a["far_support"]:
        at &= x <= slab.length - d_v / 1000 + 1e-9
    v_ed = float(uls["V"][at].max()) if at.any() else float(uls["V"].max())
    rho = min((bottom.get("as_mm2_per_m") or 0), (top.get("as_mm2_per_m") or 0) or 1e9) / (1000 * d_v)
    v_rd = vrdc(conc.fck, pf.gamma_c, d_v, rho)
    shear: dict[str, Any] = {"V_Ed_kN": round(v_ed, 1), "V_Rd_c_kN": round(v_rd, 1), "d_mm": round(d_v)}
    u_shear = v_ed / v_rd
    if v_ed > v_rd:
        nu1 = 0.6 * (1 - conc.fck / 250)
        vmax = 1000 * 0.9 * d_v * nu1 * fcd / (2.5 + 1 / 2.5) / 1e3
        shear["links_mm2_per_m2"] = round(v_ed * 1e3 / (0.9 * d_v * fyd * 2.5) * 1e3)
        shear["V_Rd_max_kN"] = round(vmax, 1)
        u_shear = v_ed / vmax
        notes.append(
            f"VEd {v_ed:.0f} kN/m > VRd,c {v_rd:.0f} kN/m: shear links near the ledge "
            f"({shear['links_mm2_per_m2']} mm² per m², cot θ 2.5) or a thicker slab."
        )
    shear["without_links"] = round(v_ed / v_rd, 3)
    shear["utilisation"] = round(u_shear, 3)  # with links where they are needed
    link_kg_m = 0.0  # per metre of berth
    if "links_mm2_per_m2" in shear:
        zone = float((uls["V"] > v_rd).sum()) * (x[1] - x[0])
        shear["links_zone_m"] = round(zone, 2)
        link_kg_m = shear["links_mm2_per_m2"] / 1e6 * (h - c_top - c_bot) / 1000 * zone * STEEL_DENSITY
    ledge = design_ledge(slab, settings, a["R_uls"], a["R_qp"], rear, a["R_uls_spread"], a["P_wheel_uls"])
    kg_m2 = (
        sum((f.get("as_mm2_per_m") or 0) for f in (bottom, top, dist["bottom"], dist["top"]))
        / 1e6
        * STEEL_DENSITY
        * 1.0
        * 1.1
    )  # 10% for laps
    utils = [bottom.get("utilisation") or 0, top.get("utilisation") or 0, u_shear]
    u = max(utils)
    step = max(1, len(x) // 60)
    return {
        "element": ELEMENT,
        "kind": "approach_slab",
        "length_m": slab.length,
        "thickness_mm": h,
        "concrete": grade,
        "cover_top_mm": c_top,
        "cover_bottom_mm": c_bot,
        "joint_mm": slab.joint_width,
        "span_m": round(a["span_m"], 2),
        "far_support": a["far_support"],
        "bending": {"bottom": bottom, "top": top},
        "M_sag_at_m": round(float(x[i_sag]), 2),
        "M_hog_at_m": round(float(x[i_hog]), 2),
        "distribution": dist,
        "shear": shear,
        "reaction": {
            "uls_kN_per_m": round(a["R_uls"], 1),
            "qp_kN_per_m": round(a["R_qp"], 1),
            "permanent_kN_per_m": round(a["R_g"], 1),
        },
        "diagram": {
            "x": [round(float(v), 2) for v in x[::step]],
            "M_max": [round(float(v), 1) for v in uls["M_max"][::step]],
            "M_min": [round(float(v), 1) for v in uls["M_min"][::step]],
            "V": [round(float(v), 1) for v in uls["V"][::step]],
            "M_qp_max": [round(float(v), 1) for v in qp["M_max"][::step]],
            "M_qp_min": [round(float(v), 1) for v in qp["M_min"][::step]],
        },
        "ledge": ledge,
        "steel": {
            "kg_per_m2": round(kg_m2, 1),
            "kg_per_m3": round(kg_m2 / (h / 1000), 1),
            "slab_kg_per_m": round(kg_m2 * slab.length + link_kg_m, 1),
            "links_kg_per_m": round(link_kg_m, 1),
            "per_m_of_berth_kg": round(
                kg_m2 * slab.length + link_kg_m + (ledge.get("steel_kg_per_m") or 0), 1
            ),
        },
        "concrete_m3_per_m": round(slab.length * h / 1000 + (ledge.get("concrete_m3_per_m") or 0), 3),
        "utilisation": round(max(u, ledge.get("utilisation") or 0), 3),
        "slab_utilisation": round(u, 3),
        "passed": bool(
            bottom.get("passed") and top.get("passed") and u_shear <= 1 + 1e-6 and ledge.get("passed")
        ),
        "notes": notes,
    }


def rear_beam_additions(ledge: dict[str, Any], width_mm: float, spacing_m: float) -> dict[str, float]:
    """What the ledge adds to the rear beam between supports Ls apart: (ULS and QP) ΔV, ΔT, ΔM.

    Line load q at e = b/2 + a_F from the centre line: ΔV = q·Ls/2, ΔT = q·e·Ls/2, ΔM = q·Ls²/12;
    the wheel P (ULS only): ΔV = P, ΔT = P·e, ΔM = P·Ls/8.
    """
    rb = ledge.get("rear_beam") or {}
    e = (width_mm / 2 + (rb.get("a_F_mm") or 0.0)) / 1000
    Ls = spacing_m
    q_u, q_q, p = (
        rb.get("line_load_uls_kN_per_m") or 0.0,
        rb.get("line_load_qp_kN_per_m") or 0.0,
        rb.get("wheel_uls_kN") or 0.0,
    )
    return {
        "e_m": round(e, 3),
        "spacing_m": round(Ls, 2),
        "V_uls": q_u * Ls / 2 + p,
        "T_uls": e * (q_u * Ls / 2 + p),
        "M_uls": q_u * Ls**2 / 12 + p * Ls / 8,
        "V_qp": q_q * Ls / 2,
        "T_qp": e * q_q * Ls / 2,
        "M_qp": q_q * Ls**2 / 12,
    }
