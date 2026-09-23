"""The office's truss model of the front beam between king piles (report section 5.3).

Each king pile spacing s carries a node load P from the beam (crane line load, surcharge over the
beam width and the beam's own weight) and from the slab behind it (surcharge and weight over the
tributary width). Struts run from each load down to the king pile heads either side, at
θ = atan(z / (s/2)), with z the lever arm between the top and bottom bar centroids; the bottom bars
tie them: F = P / (2 sin θ), T = F cos θ = P·s / (4z). The ties are checked at a service stress
(the office's 1 t/cm² for a 0.1 mm crack width): As,req = T / σ.
"""

from __future__ import annotations

import math
from typing import Any

from ..project import FrontBeamTruss


def spacing_from_supports(positions: list[float]) -> float | None:
    """The typical king pile spacing: the median gap between supports along the beam."""
    s = sorted(positions)
    gaps = sorted(b - a for a, b in zip(s, s[1:], strict=False) if b - a > 0.5)
    return gaps[len(gaps) // 2] if gaps else None


def check_truss(
    t: FrontBeamTruss, b_mm: float, h_mm: float, z_mm: float, as_bottom: float, spacing: float | None
) -> dict[str, Any]:
    s = t.pile_spacing or spacing
    if not s:
        note = "No king pile spacing: set it for the truss check."
        return {"utilisation": None, "passed": True, "note": note}
    b, h, z = b_mm / 1000, h_mm / 1000, z_mm / 1000
    theta = math.atan(z / (s / 2))
    beam = s * (t.crane_load + t.surcharge * b + t.unit_weight * b * h)
    cases = [("Typical", t.slab_thickness)]
    if t.bollard_slab_thickness is not None:
        cases.append(("At bollards", t.bollard_slab_thickness))
    out = []
    for label, ts in cases:
        slab = s * t.slab_width * (t.surcharge + t.unit_weight * ts / 1000)
        p = beam + slab
        strut = p / (2 * math.sin(theta))
        tie = strut * math.cos(theta)
        req = tie * 1e3 / t.working_stress  # mm²
        out.append(
            {
                "case": label,
                "slab_thickness_mm": ts,
                "P_beam_kN": round(beam),
                "P_slab_kN": round(slab),
                "P_kN": round(p),
                "F_strut_kN": round(strut),
                "T_kN": round(tie),
                "As_req_mm2": round(req),
                "utilisation": round(req / as_bottom, 3) if as_bottom > 0 else None,
            }
        )
    us = [c["utilisation"] for c in out]
    u = None if None in us else max(us)
    return {
        "spacing_m": round(s, 3),
        "spacing_from": "input" if t.pile_spacing else "workbook",
        "lever_arm_mm": round(z_mm),
        "theta_deg": round(math.degrees(theta), 2),
        "strut_factor": round(1 / (2 * math.sin(theta)), 3),
        "tie_factor": round(1 / (2 * math.tan(theta)), 3),
        "working_stress_MPa": t.working_stress,
        "As_provided_mm2": round(as_bottom),
        "cases": out,
        "utilisation": u,
        "passed": u is not None and u <= 1.0,
        "method": (
            "Strut-and-tie between king piles (office report 5.3): P from the crane load, surcharge and "
            "weight over the beam, and the slab's surcharge and weight over its tributary width, per "
            "king pile spacing; θ = atan(z / (s/2)); F = P / (2 sin θ); T = F cos θ, carried by the "
            f"bottom bars at {t.working_stress:g} MPa (service)."
        ),
    }
