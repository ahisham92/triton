"""Bollard tie bars: the factored bollard pull carried back into the deck by straight bars.

The pull F = γ·capacity·g is taken horizontal (the most onerous for the ties). Its component along
the quay goes into the front beam's own longitudinal bars; the ties carry the component straight
back from the quay face, largest when the pull is square to the face: F ≤ Σ n·As·fyd·cos β·cos α
for the groups at plan angle α (|α| < 90°) and slope β in elevation, tension only.

Each tie is lapped with the slab bottom bars over the lap length, checked to EN 1992-1-1 8.7.3:
l0 = α6·(Ø/4)·(σsd/fbd), σsd = fyd × the tie utilisation, fbd = 2.25·η1·fctd with η1 = 1 (bottom
bars, good bond) and α6 = 1.5 (all bars lapped at one section), at least
max(0.3·α6·lb,rqd, 15Ø, 200 mm).
"""

from __future__ import annotations

import math
from typing import Any

from ..materials import REINFORCEMENT_GRADES, concrete
from ..project import Bollard, DesignSettings

G = 9.81


def check_bollard(b: Bollard, concrete_grade: str, settings: DesignSettings) -> dict[str, Any]:
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
        "method": (
            f"F = {b.load_factor:g} × {b.capacity:g} t × g, horizontal and square to the quay face; the ties "
            f"(tension only, {b.tie_slope:g}° in elevation) resist Σ As·fyd·cos β·cos α. The pull along the "
            "quay goes into the front beam's longitudinal bars. Laps to EN 1992-1-1 8.7.3."
        ),
    }
