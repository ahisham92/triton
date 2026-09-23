"""Longitudinal reinforcement of concrete piles for axial force and bending (EN 1992-1-1).

For each pile element (e.g. ``Pile(1)``, a row of piles in the Plaxis model) the
design takes every ULS node result below the pile head, and picks the
arrangement of equal bars on one circle that carries all of them with the
least steel (or lowest cost), subject to the pile detailing rules of 9.8.5 and
the bar spacing rules of 8.2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..elements import CombinationType, combination_type
from ..forces import design_forces
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import DesignSettings, PileInput
from .circular import CircularSection, ConcreteLaw, SteelLaw

MIN_BAR = 16  # mm, EN 1992-1-1 9.8.5(3)
MIN_BARS = 6  # 9.8.5(3)
MAX_CLEAR_SPACING = 200.0  # mm around the periphery, 9.8.5(3)
MAX_RATIO = 0.04  # 9.5.2(3)
AGGREGATE = 20.0  # mm, for the 8.2(2) clear spacing rule


def min_area_pile(ac_mm2: float) -> float:
    """Minimum longitudinal steel of bored piles, EN 1992-1-1 Table 9.6N (mm²)."""
    ac = ac_mm2 / 1e6
    if ac <= 0.5:
        return 0.005 * ac_mm2
    if ac <= 1.0:
        return 2500.0
    return 0.0025 * ac_mm2


@dataclass
class Arrangement:
    bar_count: int
    bar_diameter: int
    bar_radius: float  # mm
    area: float  # mm²
    clear_spacing: float  # mm
    weight_per_m: float  # kg/m
    cost_per_m: float

    @property
    def label(self) -> str:
        return f"{self.bar_count}Ø{self.bar_diameter}"


@dataclass
class PileLoads:
    """ULS results of one pile element in the design sign convention (N compression +)."""

    frame: pd.DataFrame  # columns: combination, category, Node, Y, Z, N, M, M_2, M_3

    @classmethod
    def from_sheets(cls, sheets: dict[str, SheetData], head_level: float | None) -> PileLoads:
        parts = []
        for combo, sheet in sheets.items():
            ctype = combination_type(combo)
            if ctype is CombinationType.SLS_QP:
                continue
            f = design_forces(sheet.frame, sheet.parsed.spec, ["N", "Q_12", "Q_13", "M_2", "M_3"])
            if head_level is not None:
                f = f[f["Z"] <= head_level + 1e-9]
            f = f.assign(combination=combo, category=ctype.value, M=np.hypot(f["M_2"], f["M_3"]))
            parts.append(f)
        if not parts:
            return cls(pd.DataFrame(columns=["combination", "category", "Node", "Y", "Z", "N", "M"]))
        return cls(pd.concat(parts, ignore_index=True))


@dataclass
class PileDesign:
    element: str
    arrangement: Arrangement | None
    utilisation: float
    passed: bool
    governing: dict
    area_min: float
    area_max: float
    steel_ratio_kg_m3: float
    reinforcement_ratio: float
    alternatives: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    curve: list[list[float]] = field(default_factory=list)
    profile: list[dict] = field(default_factory=list)
    points: list[list] = field(default_factory=list)

    def to_dict(self) -> dict:
        a = self.arrangement
        return {
            "element": self.element,
            "arrangement": None
            if a is None
            else {
                "label": a.label,
                "bar_count": a.bar_count,
                "bar_diameter": a.bar_diameter,
                "bar_radius": round(a.bar_radius, 1),
                "area_mm2": round(a.area),
                "clear_spacing_mm": round(a.clear_spacing),
                "weight_kg_per_m": round(a.weight_per_m, 1),
                "cost_per_m": round(a.cost_per_m, 2),
            },
            "utilisation": round(self.utilisation, 3) if math.isfinite(self.utilisation) else None,
            "passed": self.passed,
            "governing": self.governing,
            "area_min_mm2": round(self.area_min),
            "area_max_mm2": round(self.area_max),
            "steel_ratio_kg_m3": round(self.steel_ratio_kg_m3, 1),
            "reinforcement_ratio_pct": round(100 * self.reinforcement_ratio, 3),
            "alternatives": self.alternatives,
            "notes": self.notes,
            "curve": self.curve,
            "profile": self.profile,
            "points": self.points,
        }


def candidate_arrangements(pile: PileInput, settings: DesignSettings) -> list[Arrangement]:
    r = settings.reinforcement
    d = pile.diameter
    ac = math.pi * d * d / 4
    out = []
    for phi in r.bar_diameters:
        if phi < MIN_BAR:
            continue
        radius = d / 2 - pile.cover - pile.link_diameter - phi / 2
        if radius <= 0:
            continue
        smin = max(r.min_clear_spacing, phi, AGGREGATE + 5, 20.0)
        circumference = 2 * math.pi * radius
        n_max = int(circumference // (smin + phi))
        n_min = max(MIN_BARS, math.ceil(circumference / (MAX_CLEAR_SPACING + phi)))
        for n in range(n_min, n_max + 1):
            area = n * math.pi * phi * phi / 4
            if area > MAX_RATIO * ac:
                break
            weight = area / 1e6 * STEEL_DENSITY
            cost = weight / 1000 * r.rebar_cost_per_tonne + n * r.cost_per_bar_placed
            out.append(Arrangement(n, phi, radius, area, circumference / n - phi, weight, cost))
    key = (
        (lambda a: (a.cost_per_m, a.area)) if r.objective == "min_cost" else (lambda a: (a.area, a.bar_count))
    )
    return sorted(out, key=key)


def _section(pile: PileInput, a: Arrangement, settings: DesignSettings, accidental: bool) -> CircularSection:
    pf = settings.partial_factors
    gamma_c, gamma_s = (
        (pf.gamma_c_accidental, pf.gamma_s_accidental) if accidental else (pf.gamma_c, pf.gamma_s)
    )
    fck = concrete(pile.concrete).fck
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    return CircularSection(
        pile.diameter,
        a.bar_count,
        a.bar_diameter,
        a.bar_radius,
        ConcreteLaw(fck, gamma_c, pf.alpha_cc),
        SteelLaw(fyk, gamma_s),
    )


def _utilisation(
    pile: PileInput, a: Arrangement, settings: DesignSettings, loads: pd.DataFrame
) -> np.ndarray:
    util = np.zeros(len(loads))
    accidental = (
        loads["category"].isin([CombinationType.SEISMIC.value, CombinationType.ACCIDENTAL.value]).to_numpy()
    )
    for flag in (False, True):
        mask = accidental == flag
        if mask.any():
            sec = _section(pile, a, settings, flag)
            util[mask] = sec.utilisation(loads["N"].to_numpy()[mask], loads["M"].to_numpy()[mask])
    return util


def design_pile(
    name: str, pile: PileInput, settings: DesignSettings, sheets: dict[str, SheetData]
) -> PileDesign:
    loads = PileLoads.from_sheets(sheets, pile.head_level).frame
    ac = math.pi * pile.diameter**2 / 4
    area_min, area_max = min_area_pile(ac), MAX_RATIO * ac
    notes: list[str] = []
    if pile.casing is not None and pile.casing.role == "structural":
        notes.append(
            "The structural casing is not yet included in the N–M check; the pile is designed as "
            "reinforced concrete only, which is conservative."
        )
    if pile.head_level is None:
        notes.append("No pile head level is set, so results inside the slab are included.")
    if loads.empty:
        return PileDesign(
            name, None, 0.0, False, {}, area_min, area_max, 0.0, 0.0, notes=notes + ["No ULS results."]
        )

    candidates = [a for a in candidate_arrangements(pile, settings) if a.area >= area_min]
    if not candidates:
        notes.append("No bar arrangement fits the pile with the chosen bar sizes and spacing limits.")
        return PileDesign(name, None, math.inf, False, {}, area_min, area_max, 0.0, 0.0, notes=notes)

    chosen, chosen_util, best, best_util = None, None, None, None
    tried: list[tuple[Arrangement, float]] = []
    for a in candidates:
        util = _utilisation(pile, a, settings, loads)
        u = float(util.max())
        tried.append((a, u))
        if best is None or u < best_util.max():
            best, best_util = a, util
        if u <= 1.0:
            chosen, chosen_util = a, util
            break
    passed = chosen is not None
    if not passed:
        chosen, chosen_util = best, best_util
        notes.append("No arrangement within the 4% limit carries the loads; the strongest one is shown.")

    i = int(np.argmax(chosen_util))
    g = loads.iloc[i]
    sec = _section(pile, chosen, settings, False)
    governing = {
        "combination": g["combination"],
        "node": int(g["Node"]),
        "y": round(float(g["Y"]), 2),
        "z": round(float(g["Z"]), 2),
        "N_kN": round(float(g["N"]), 1),
        "M_kNm": round(float(g["M"]), 1),
        "M_Rd_kNm": round(sec.moment_capacity(float(g["N"])), 1),
    }
    loads = loads.assign(util=chosen_util)
    profile = (
        loads.groupby(loads["Z"].round(1))["util"].max().sort_index(ascending=False).reset_index().round(3)
    )
    alternatives = _alternatives(pile, settings, loads, chosen, tried)
    return PileDesign(
        name,
        chosen,
        float(chosen_util.max()),
        passed,
        governing,
        area_min,
        area_max,
        steel_ratio_kg_m3=chosen.area / ac * STEEL_DENSITY,
        reinforcement_ratio=chosen.area / ac,
        alternatives=alternatives,
        notes=notes,
        curve=np.round(sec.interaction(), 1).tolist(),
        profile=[{"z": float(r.Z), "util": float(r.util)} for r in profile.itertuples()],
        points=[
            [c, round(n, 1), round(m, 1), round(u, 3)]
            for c, n, m, u in loads[["combination", "N", "M", "util"]].itertuples(index=False)
        ],
    )


def _alternatives(pile, settings, loads, chosen, tried) -> list[dict]:
    """The chosen arrangement and the next few passing ones with other bar sizes."""
    seen = {chosen.bar_diameter}
    rows = [(chosen, float(loads["util"].max()))]
    for a in candidate_arrangements(pile, settings):
        if a.bar_diameter in seen or a.area < chosen.area:
            continue
        u = float(_utilisation(pile, a, settings, loads).max())
        if u <= 1.0:
            rows.append((a, u))
            seen.add(a.bar_diameter)
        if len(rows) >= 4:
            break
    ac = math.pi * pile.diameter**2 / 4
    return [
        {
            "label": a.label,
            "area_mm2": round(a.area),
            "utilisation": round(u, 3),
            "steel_ratio_kg_m3": round(a.area / ac * STEEL_DENSITY, 1),
            "cost_per_m": round(a.cost_per_m, 2),
            "clear_spacing_mm": round(a.clear_spacing),
        }
        for a, u in rows
    ]
