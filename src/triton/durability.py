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

BS 6349 (Ahmed's N25185 design report, section 3.4):

    covers      75 mm for piling members and members against the ground, 50 mm for the
                superstructure (slab and beams). Not tied to the design life.
    corrosion   BS 6349-1-4:2021 mean values, 50 years: 4.5 mm/side in the splash zone
                (casing and combi tube) and 2.5 mm/side in continuous immersion (sheet
                piles). Other lives scale in proportion, as the values are constant rates.
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


BS6349_COVERS = {"piles": 75.0, "combi_infill": 75.0, "slab_top": 50.0, "slab_bottom": 50.0, "beams": 50.0}
BS6349_SPLASH = 4.5 / 50  # mm per side per year
BS6349_IMMERSION = 2.5 / 50


def bs6349_covers(life: int) -> dict[str, float]:
    return dict(BS6349_COVERS)


def bs6349_corrosion(life: int) -> dict[str, float]:
    splash, immersed = round(BS6349_SPLASH * life, 2), round(BS6349_IMMERSION * life, 2)
    return {"casing": splash, "combi_tube": splash, "sheet_pile_per_face": immersed}


def defaults(cover_code: str, corrosion_code: str, life: int) -> dict[str, Any]:
    """Covers and corrosion allowances for the chosen codes and design life."""
    covers = en1992_covers(life) if cover_code == "en1992" else bs6349_covers(life)
    corrosion = en1993_5_corrosion(life) if corrosion_code == "en1993_5" else bs6349_corrosion(life)
    notes = []
    if cover_code == "bs6349":
        notes.append("BS 6349 covers are the project values (75 mm piling, 50 mm superstructure).")
    return {"covers": covers, "corrosion": corrosion, "notes": notes}
