"""Bollard tie bars: the factored bollard pull carried back into the deck by straight bars.

The pull F = γ·capacity·g is taken horizontal (the most onerous for the ties). Its component along
the quay goes into the front beam's own longitudinal bars; the ties carry the component straight
back from the quay face, largest when the pull is square to the face: F ≤ Σ n·As·fyd·cos β·cos α
for the groups at plan angle α (|α| < 90°) and slope β in elevation, tension only.

Each tie is lapped with the slab bottom bars over the lap length, checked to EN 1992-1-1 8.7.3:
l0 = α6·(Ø/4)·(σsd/fbd), σsd = fyd × the tie utilisation, fbd = 2.25·η1·fctd with η1 = 1 (bottom
bars, good bond) and α6 = 1.5 (all bars lapped at one section), at least
max(0.3·α6·lb,rqd, 15Ø, 200 mm).

Extra bars in the front beam for the bollard, over the bollard's bay (king pile to king pile each
side), on top of the beam's own design:

* Torsion: the pull square to the face at the line height over the cope turns the beam about its
  axis, T = F·(line height + h/2), half to each side. EN 1992-1-1 6.3.2 on the thin-walled section
  (tef = A/u, at least twice the axis distance of the bars; θ = 45°): links Asw/s = TEd / (2·Ak·fyd)
  per leg of the outer link, longitudinal bars ΣAsl = TEd·uk / (2·Ak·fyd) round the section, and the
  struts TEd ≤ TRd,max = 2ν·fcd·Ak·tef·sinθ·cosθ.
* The pull along the quay, straight into the beam's top bars: As = F / fyd, anchored past the bay.
* Uplift from the steepest line, P = F·sin(angle), on the beam spanning between king piles:
  M = P·L/4 with the top in tension, As = M / (0.9·d·fyd).

The slab at the bollard is thickened behind the beam; at the step to the normal slab the whole tie
force F passes through the thin slab as a tension N at the tie bars' level, e below the slab's
mid-depth (the ties end in the bottom layer), over the thickening's width: each face needs
As = N·(z/2 ± e) / (z·fyd); the bottom from the ties that cross the step (their component square to
the quay; the mesh is left to the slab's own bending), the top from the slab's top mesh. The ties' slope gives a shear V = F·tan β at the step, carried by
links alone (no concrete term in tension). The thickening's bottom bars run a lap length past the
step (8.7.3).
"""

from __future__ import annotations

import math
from typing import Any

from ..materials import REINFORCEMENT_GRADES, concrete
from ..project import Bollard, DesignSettings

G = 9.81


def check_bollard(
    b: Bollard,
    concrete_grade: str,
    settings: DesignSettings,
    beam: tuple[float, float, float, float] | None = None,
    span: float | None = None,
) -> dict[str, Any]:
    """``beam``: the front beam's width, depth, cover to links and link (mm) for its extra bars;
    ``span``: the king pile spacing (m) when the bollard gives none."""
    pf = settings.partial_factors
    fyd = REINFORCEMENT_GRADES[settings.reinforcement.grade] / pf.gamma_s
    force = b.load_factor * b.capacity * G  # kN
    slope = math.cos(math.radians(b.tie_slope))
    ties = []
    for t in b.ties:
        tension = t.count * math.pi * t.diameter**2 / 4 * fyd * slope / 1e3  # kN along the bars
        ties.append(
            {
                "bars": f"{t.count}Ø{t.diameter}",
                "angle_deg": t.angle,
                "T_Rd_kN": round(tension),
                "normal_kN": round(tension * max(math.cos(math.radians(t.angle)), 0.0)),
            }
        )
    r = sum(t["normal_kN"] for t in ties)
    util = force / r if r > 0 else math.inf

    conc = concrete(concrete_grade)
    fctd = 0.7 * conc.fctm / pf.gamma_c
    fbd = 2.25 * 1.0 * fctd
    sigma = min(util, 1.0) * fyd
    laps = []
    for t in b.ties:
        lb = t.diameter / 4 * sigma / fbd
        l0 = max(1.5 * lb, 0.3 * 1.5 * t.diameter / 4 * fyd / fbd, 15 * t.diameter, 200.0)
        laps.append({"bars": f"{t.count}Ø{t.diameter} at {t.angle:g}°", "l0_mm": round(l0)})
    lap_u = max(x["l0_mm"] for x in laps) / b.lap_length if laps else 0.0
    u = max(util, lap_u)
    extra = in_beam(b, force, concrete_grade, fyd, beam, span) if beam else None
    step = at_step(b, force, concrete_grade, fyd, fbd, settings) if b.thickening else None
    for part in (extra, step):
        if part and part["utilisation"] is not None:
            u = max(u, part["utilisation"])
    return {
        "capacity_t": b.capacity,
        "load_factor": b.load_factor,
        "F_Ed_kN": round(force),
        "R_kN": round(r),
        "tie_utilisation": round(util, 3) if math.isfinite(util) else None,
        "ties": ties,
        "laps": laps,
        "lap_length_mm": b.lap_length,
        "lap_utilisation": round(lap_u, 3),
        "utilisation": round(u, 3) if math.isfinite(u) else None,
        "passed": bool(u <= 1.0),
        "beam_bars": extra,
        "thickening": step,
        "method": (
            f"F = {b.load_factor:g} × {b.capacity:g} t × g, horizontal and square to the quay face; the ties "
            f"(tension only, {b.tie_slope:g}° in elevation) resist Σ As·fyd·cos β·cos α. The pull along the "
            "quay goes into the front beam's longitudinal bars. Laps to EN 1992-1-1 8.7.3."
        ),
    }


def _bars(area: float, d: int, least: int = 2) -> str:
    n = max(least, math.ceil(area / (math.pi * d * d / 4) - 1e-9))
    return f"{n}Ø{d}"


def in_beam(
    b: Bollard,
    force: float,
    grade: str,
    fyd: float,
    beam: tuple[float, float, float, float],
    span: float | None,
) -> dict[str, Any]:
    """The front beam's extra bars for the bollard (torsion, the pull along the quay, uplift)."""
    width, depth, cover, link = beam
    conc = concrete(grade)
    fcd = conc.fck / 1.5
    a, u = width * depth, 2 * (width + depth)
    axis = cover + link + b.extra_bar / 2
    tef = max(a / u, 2 * axis)
    ak = (width - tef) * (depth - tef)
    uk = 2 * ((width - tef) + (depth - tef))
    t = force * (b.line_height + depth / 2e3)  # kNm, square to the face
    ted = t / 2
    asw = ted * 1e6 / (2 * ak * fyd)  # mm²/mm per leg
    asl = ted * 1e6 * uk / (2 * ak * fyd)  # mm²
    nu = 0.6 * (1 - conc.fck / 250)
    trd = 2 * nu * fcd * ak * tef * 0.5 / 1e6  # kNm, θ = 45°
    along = force * 1e3 / fyd
    L = b.span or span or 3.2
    p_up = force * math.sin(math.radians(b.max_line_angle))
    m_up = p_up * L / 4
    d = depth - axis
    up = m_up * 1e6 / (0.9 * d * fyd)
    leg = math.pi * b.extra_link**2 / 4
    pitch = leg / asw if asw > 0 else None
    rows = [
        {
            "what": "Torsion: extra links (each leg)",
            "need": f"{asw * 1e3:.0f} mm²/m",
            "bars": f"Ø{b.extra_link} legs at {pitch:.0f} mm, added to the beam's shear links"
            if pitch
            else "none",
        },
        {
            "what": "Torsion: extra longitudinal bars",
            "need": f"{asl:.0f} mm²",
            "bars": _bars(asl, b.extra_bar, 4) + " round the section",
        },
        {
            "what": "Pull along the quay",
            "need": f"{along:.0f} mm²",
            "bars": _bars(along, b.extra_bar) + " in the top",
        },
        {
            "what": f"Uplift, line at {b.max_line_angle:g}°",
            "need": f"{up:.0f} mm²",
            "bars": _bars(up, b.extra_bar) + " in the top",
        },
    ]
    top = along + up + asl * width / u
    util = ted / trd
    return {
        "T_kNm": round(t, 1),
        "T_Ed_kNm": round(ted, 1),
        "T_Rd_max_kNm": round(trd, 1),
        "tef_mm": round(tef),
        "Ak_m2": round(ak / 1e6, 3),
        "uk_m": round(uk / 1e3, 3),
        "uplift_kN": round(p_up, 1),
        "M_uplift_kNm": round(m_up, 1),
        "span_m": L,
        "rows": rows,
        "top_total_mm2": round(top),
        "top_bars": _bars(top, b.extra_bar),
        "length_m": round(2 * L, 2),
        "utilisation": round(util, 3),
        "passed": bool(util <= 1.0),
        "note": f"Over the bollard's bay: {L:g} m each side of it. The pull square to the face turns the beam; "
        "the top bars take the pull along the quay, the uplift and their share of the torsion bars.",
    }


def at_step(
    b: Bollard, force: float, grade: str, fyd: float, fbd: float, settings: DesignSettings
) -> dict[str, Any]:
    """The connection of the thickened slab at the bollard to the normal slab."""
    t = b.slab_thickness
    cover = settings.durability.covers.slab_bottom
    top_cover = settings.durability.covers.slab_top
    tie_d = max((x.diameter for x in b.ties), default=32)
    a_bot = cover + tie_d / 2
    a_top = top_cover + b.slab_bar / 2
    z = t - a_bot - a_top
    e = t / 2 - a_bot  # the ties' level below mid-depth
    n = force
    width = b.thickening_width * 1000
    beta = math.radians(b.tie_slope)
    need_bot = n * 1e3 * (z / 2 + e) / (z * fyd)
    need_top = max(n * 1e3 * (z / 2 - e) / (z * fyd), 0.0)
    ties = sum(
        x.count * math.pi * x.diameter**2 / 4 * max(math.cos(math.radians(x.angle)), 0.0) for x in b.ties
    )
    mesh = width / b.slab_spacing * math.pi * b.slab_bar**2 / 4
    have_bot = ties  # the mesh is busy with the slab's own bending: only the ties are counted
    have_top = mesh
    u_bot = need_bot / have_bot
    u_top = need_top / have_top if have_top else math.inf
    v = force * math.tan(beta)
    d = t - a_bot
    links = v * 1e3 / (0.9 * d * fyd)  # mm²/mm over the width
    per_m2 = links * 1e3 / (width / 1e3)  # mm² per m² of the step's plan strip, per m along the span
    lap = max(1.5 * b.slab_bar / 4 * fyd / fbd, 15 * b.slab_bar, 200.0)
    util = max(u_bot, u_top)
    return {
        "slab_mm": t,
        "thickening_mm": b.thickening,
        "length_m": b.thickening_length,
        "width_m": b.thickening_width,
        "N_kN": round(n, 1),
        "e_mm": round(e),
        "M_kNm": round(n * e / 1e3, 1),
        "z_mm": round(z),
        "bottom": {
            "need_mm2": round(need_bot),
            "ties_mm2": round(ties),
            "mesh_mm2": round(mesh),
            "utilisation": round(u_bot, 3),
        },
        "top": {"need_mm2": round(need_top), "mesh_mm2": round(mesh), "utilisation": round(u_top, 3)},
        "extra_bottom": _bars(max(need_bot - have_bot, 0.0), b.slab_bar, 0) if need_bot > have_bot else "",
        "V_kN": round(v, 1),
        "links_mm2_per_m": round(links * 1e3),
        "links_note": f"Asw/s = {links * 1e3:.0f} mm²/m in all, shared over the {b.thickening_width:g} m width "
        f"({per_m2:.0f} mm²/m per m of width), no concrete term (the slab is in tension there)",
        "lap_past_step_mm": round(lap),
        "utilisation": round(util, 3),
        "passed": bool(util <= 1.0),
    }
