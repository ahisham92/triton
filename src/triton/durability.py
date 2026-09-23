"""Default covers and corrosion allowances from the chosen codes and the design life.

Covers, EN 1992-1-1 4.4.1: c_nom = c_min,dur + Δc_dev (10 mm), with c_min,dur from
Table 4.4N for the structural class. S4 is the class for a 50-year life; Table
4.3N adds 2 classes for 100 years (75 years is taken as +1 between them). No
reduction for strength class is taken. Exposure per element:

    piles              XS3 (tidal and splash), and at least 75 mm, the usual bored pile cover
    combi wall infill  XS2 (inside the tube)
    slab top           XS1 (airborne salt)
    slab bottom        XS3 (over the water, splash)
    beams              XS3

Corrosion, EN 1993-5 Table 4.2, sea water in a temperate climate, loss of
thickness per exposed face, interpolated between the tabulated lives:

    casing, combi tube  zone of high attack (low water and splash)
    sheet piles         zone of permanent immersion or intertidal

BS 6349-1-4 tables are not loaded yet; choosing BS 6349 keeps the values that
are set until they are.
"""

from __future__ import annotations

from typing import Any

import numpy as np

DEVIATION = 10  # Δc_dev, mm
# Table 4.4N, c_min,dur (mm) for structural classes S1..S6.
C_MIN_DUR = {
    "XS1": (20, 25, 30, 35, 40, 45),
    "XS2": (25, 30, 35, 40, 45, 50),
    "XS3": (30, 35, 40, 45, 50, 55),
}
EXPOSURE = {
    "piles": "XS3",
    "combi_infill": "XS2",
    "slab_top": "XS1",
    "slab_bottom": "XS3",
    "beams": "XS3",
}
PILE_MINIMUM = 75

# Table 4.2 (mm) at 5, 25, 50, 75 and 100 years.
LIVES = (5, 25, 50, 75, 100)
HIGH_ATTACK = (0.55, 1.90, 3.75, 5.60, 7.50)
IMMERSION = (0.25, 0.90, 1.75, 2.60, 3.50)


def structural_class(life: int) -> int:
    """1-based structural class: S4 for up to 50 years, S5 up to 75, S6 beyond."""
    return 4 if life <= 50 else 5 if life <= 75 else 6


def en1992_covers(life: int) -> dict[str, float]:
    s = structural_class(life)
    out = {k: float(C_MIN_DUR[x][s - 1] + DEVIATION) for k, x in EXPOSURE.items()}
    out["piles"] = max(out["piles"], PILE_MINIMUM)
    return out


def en1993_5_corrosion(life: int) -> dict[str, float]:
    def at(row: tuple[float, ...]) -> float:
        if life > LIVES[-1]:  # beyond the table: carry on at the last rate
            return round(row[-1] + (life - LIVES[-1]) * (row[-1] - row[-2]) / (LIVES[-1] - LIVES[-2]), 2)
        return round(float(np.interp(life, LIVES, row)), 2)

    return {"casing": at(HIGH_ATTACK), "combi_tube": at(HIGH_ATTACK), "sheet_pile_per_face": at(IMMERSION)}


def defaults(cover_code: str, corrosion_code: str, life: int) -> dict[str, Any]:
    """Covers and corrosion for the codes and life; None where the code's table is not loaded."""
    notes = []
    covers = en1992_covers(life) if cover_code == "en1992" else None
    corrosion = en1993_5_corrosion(life) if corrosion_code == "en1993_5" else None
    if covers is None:
        notes.append("BS 6349-1-4 covers are not loaded yet: the covers set are kept.")
    if corrosion is None:
        notes.append("BS 6349-1-4 corrosion rates are not loaded yet: the allowances set are kept.")
    return {"covers": covers, "corrosion": corrosion, "notes": notes}
