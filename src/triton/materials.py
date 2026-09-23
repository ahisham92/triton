"""Material catalogue: concrete to EN 1992-1-1 Table 3.1, steel grades to EN 10025 / EN 10080."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

CONCRETE_GRADES = [
    "C25/30",
    "C28/35",
    "C30/37",
    "C32/40",
    "C35/45",
    "C40/50",
    "C45/55",
    "C50/60",
]
REINFORCEMENT_GRADES = {"B500B": 500.0, "B500C": 500.0}
# Nominal yield strength (MPa) for t <= 16 mm and 16 < t <= 40 mm.
STRUCTURAL_STEEL_GRADES = {
    "S275": (275.0, 265.0),
    "S355": (355.0, 345.0),
    "S390": (390.0, 380.0),
    "S430": (430.0, 420.0),
    "S460": (460.0, 440.0),
}
SHEET_PILE_GRADES = {
    "S240GP": 240.0,
    "S270GP": 270.0,
    "S320GP": 320.0,
    "S355GP": 355.0,
    "S390GP": 390.0,
    "S430GP": 430.0,
}
BAR_DIAMETERS = [8, 10, 12, 14, 16, 20, 25, 28, 32, 40]
STEEL_DENSITY = 7850.0  # kg/m3
E_REINFORCEMENT = 200_000.0  # MPa
E_STRUCTURAL_STEEL = 210_000.0  # MPa


@dataclass(frozen=True)
class Concrete:
    grade: str
    fck: float  # MPa, cylinder
    fck_cube: float
    fcm: float
    fctm: float
    ecm: float  # MPa

    def to_dict(self) -> dict:
        return asdict(self)


def concrete(grade: str) -> Concrete:
    m = re.fullmatch(r"C(\d+)/(\d+)", grade.strip())
    if not m:
        raise ValueError(f"Unknown concrete grade '{grade}'. Use the form C32/40.")
    fck, cube = float(m.group(1)), float(m.group(2))
    fcm = fck + 8.0
    fctm = 0.30 * fck ** (2 / 3) if fck <= 50 else 2.12 * math.log(1 + fcm / 10)
    ecm = 22_000.0 * (fcm / 10) ** 0.3
    return Concrete(grade, fck, cube, fcm, round(fctm, 2), round(ecm, -2))


def structural_steel_fy(grade: str, thickness_mm: float) -> float:
    try:
        thin, thick = STRUCTURAL_STEEL_GRADES[grade]
    except KeyError:
        raise ValueError(f"Unknown structural steel grade '{grade}'.") from None
    if thickness_mm > 40:
        raise ValueError("Plates thicker than 40 mm need the yield strength from the mill certificate.")
    return thin if thickness_mm <= 16 else thick


def catalogue() -> dict:
    return {
        "concrete": [concrete(g).to_dict() for g in CONCRETE_GRADES],
        "reinforcement": [{"grade": g, "fyk": f} for g, f in REINFORCEMENT_GRADES.items()],
        "structural_steel": [
            {"grade": g, "fy_t16": a, "fy_t40": b} for g, (a, b) in STRUCTURAL_STEEL_GRADES.items()
        ],
        "sheet_pile_steel": [{"grade": g, "fy": f} for g, f in SHEET_PILE_GRADES.items()],
        "bar_diameters": BAR_DIAMETERS,
    }
