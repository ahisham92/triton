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
from .circular import CircularSection, ConcreteLaw, Ring, SteelLaw, hull_indices

MIN_BAR = 16  # mm, EN 1992-1-1 9.8.5(3)
MIN_BARS = 6  # 9.8.5(3)
MAX_RATIO = 0.04  # 9.5.2(3), outside laps
MAX_RATIO_AT_LAPS = 0.08  # 9.5.2(3)


def min_area_pile(ac_mm2: float) -> float:
    """Minimum longitudinal steel of bored piles, EN 1992-1-1 Table 9.6N (mm²)."""
    ac = ac_mm2 / 1e6
    if ac <= 0.5:
        return 0.005 * ac_mm2
    if ac <= 1.0:
        return 2500.0
    return 0.0025 * ac_mm2


@dataclass(frozen=True)
class RingSpec:
    count: int
    diameter: int  # mm
    radius: float  # mm, circle through the bar centres
    clear_spacing: float  # mm, between neighbouring bars of this row

    @property
    def area(self) -> float:
        return self.count * math.pi * self.diameter**2 / 4


@dataclass(frozen=True)
class Arrangement:
    """A cage of one or more rows of bars; row 1 is the outer row."""

    rings: tuple[RingSpec, ...]
    rows: float  # 1, 1.5, 2, 2.5 or 3
    weight_per_m: float  # kg/m
    cost_per_m: float

    @property
    def area(self) -> float:
        return sum(r.area for r in self.rings)

    @property
    def bar_count(self) -> int:
        return sum(r.count for r in self.rings)

    @property
    def outer(self) -> RingSpec:
        return self.rings[0]

    @property
    def clear_spacing(self) -> float:
        """Clear spacing of the outer row."""
        return self.outer.clear_spacing

    @property
    def label(self) -> str:
        """Row by row from the outside, e.g. 26Ø32 + 13Ø16."""
        return " + ".join(f"{r.count}Ø{r.diameter}" for r in self.rings)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "rows": self.rows,
            "bar_count": self.bar_count,
            "rings": [
                {
                    "count": r.count,
                    "diameter": r.diameter,
                    "radius": round(r.radius, 1),
                    "clear_spacing_mm": round(r.clear_spacing),
                }
                for r in self.rings
            ],
            "area_mm2": round(self.area),
            "clear_spacing_mm": round(self.clear_spacing),
            "weight_kg_per_m": round(self.weight_per_m, 1),
            "cost_per_m": round(self.cost_per_m, 2),
        }


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
    spacing_limits: dict = field(default_factory=dict)
    section: dict = field(default_factory=dict)
    positions: list[list[float]] = field(default_factory=list)
    curtailment: dict | None = None
    alternatives: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    curve: list[list[float]] = field(default_factory=list)
    profile: list[dict] = field(default_factory=list)
    points: list[list] = field(default_factory=list)

    def to_dict(self) -> dict:
        a = self.arrangement
        return {
            "element": self.element,
            "arrangement": None if a is None else a.to_dict(),
            "utilisation": round(self.utilisation, 3) if math.isfinite(self.utilisation) else None,
            "passed": self.passed,
            "governing": self.governing,
            "area_min_mm2": round(self.area_min),
            "area_max_mm2": round(self.area_max),
            "spacing_limits": self.spacing_limits,
            "section": self.section,
            "positions": self.positions,
            "curtailment": self.curtailment,
            "steel_ratio_kg_m3": round(self.steel_ratio_kg_m3, 1),
            "reinforcement_ratio_pct": round(100 * self.reinforcement_ratio, 3),
            "alternatives": self.alternatives,
            "notes": self.notes,
            "curve": self.curve,
            "profile": self.profile,
            "points": self.points,
        }


# --- Candidate cages -------------------------------------------------------------------

# Rows of each layout as (count factor, bar) with bar 1 = outer row size, 2 = inner row size.
ROW_LAYOUTS: dict[float, tuple[tuple[float, int], ...]] = {
    1: ((1, 1),),
    1.5: ((1, 1), (0.5, 2)),
    2: ((1, 1), (1, 2)),
    2.5: ((1, 1), (1, 2), (0.5, 2)),
    3: ((1, 1), (1, 2), (1, 2)),
}


def _min_clear(settings: DesignSettings, phi: float) -> float:
    """Clear spacing within a row: the project minimum, and never below EN 1992-1-1 8.2(2)."""
    return max(settings.piles.min_clear_spacing, ec2_min_clear(settings, phi))


def ec2_min_clear(settings: DesignSettings, phi: float) -> float:
    """EN 1992-1-1 8.2(2) with the recommended k1 = 1, k2 = 5 mm."""
    return max(phi, settings.piles.aggregate_size + 5, 20.0)


def max_ratio(settings: DesignSettings) -> float:
    return settings.piles.max_steel_ratio / 100


def _row_gap(settings: DesignSettings, phi_a: float, phi_b: float) -> float:
    gap = settings.piles.row_clear_spacing
    return gap if gap is not None else ec2_min_clear(settings, max(phi_a, phi_b))


def make_arrangement(
    pile: PileInput, settings: DesignSettings, rows: float, n: int, phi1: int, phi2: int | None
) -> Arrangement | None:
    """The cage with n bars of phi1 in the outer row, or None if it breaks a spacing rule."""
    pr, r = settings.piles, settings.reinforcement
    layout = ROW_LAYOUTS[rows]
    if any(f == 0.5 for f, _ in layout) and n % 2:
        return None  # a half row sits behind every second bar
    radius = pile.diameter / 2 - pile.cover - pile.link_diameter - phi1 / 2
    rings: list[RingSpec] = []
    prev_phi = None
    for i, (factor, which) in enumerate(layout):
        phi = phi1 if which == 1 else phi2
        if prev_phi is not None:
            radius -= prev_phi / 2 + _row_gap(settings, prev_phi, phi) + phi / 2
        count = int(n * factor)
        if radius <= 0 or count < 1:
            return None
        clear = 2 * math.pi * radius / count - phi
        if clear < _min_clear(settings, phi) - 1e-9:
            return None
        if i == 0 and clear > pr.max_clear_spacing + 1e-9:
            return None
        rings.append(RingSpec(count, phi, radius, clear))
        prev_phi = phi
    area = sum(x.area for x in rings)
    if area > max_ratio(settings) * math.pi * pile.diameter**2 / 4:
        return None
    weight = area / 1e6 * STEEL_DENSITY
    bars = sum(x.count for x in rings)
    cost = weight / 1000 * r.rebar_cost_per_tonne + bars * r.cost_per_bar_placed
    return Arrangement(tuple(rings), rows, weight, cost)


def _sort_key(settings: DesignSettings):
    if settings.reinforcement.objective == "min_cost":
        return lambda a: (a.cost_per_m, a.area, a.rows, a.bar_count)
    return lambda a: (a.area, a.rows, a.bar_count)


def _families(pile: PileInput, settings: DesignSettings) -> list[list[Arrangement]]:
    """Cages grouped by (outer bar, rows, inner bar), each group in order of bar count.

    Within a group every row keeps its radius, so capacity rises with the bar count.
    """
    pr = settings.piles
    bars = [d for d in settings.reinforcement.bar_diameters if d >= MIN_BAR]
    out = []
    for phi1 in bars:
        radius = pile.diameter / 2 - pile.cover - pile.link_diameter - phi1 / 2
        if radius <= 0:
            continue
        circumference = 2 * math.pi * radius
        if pile.bar_count is not None:
            counts = [pile.bar_count]
        else:
            lo = max(MIN_BARS, math.ceil(circumference / (pr.max_clear_spacing + phi1)))
            hi = int(circumference // (_min_clear(settings, phi1) + phi1))
            counts = list(range(lo, hi + 1))
        if pr.even_bar_count:
            counts = [n for n in counts if n % 2 == 0]
        for rows in pr.rows:
            inner = [None] if rows == 1 else [d for d in bars if d <= phi1]
            for phi2 in inner:
                fam = [a for n in counts if (a := make_arrangement(pile, settings, rows, n, phi1, phi2))]
                if fam:
                    out.append(fam)
    return out


def candidate_arrangements(pile: PileInput, settings: DesignSettings) -> list[Arrangement]:
    """Every cage that meets the spacing and 4% rules, best first by the chosen objective."""
    return sorted((a for fam in _families(pile, settings) for a in fam), key=_sort_key(settings))


# --- Design ------------------------------------------------------------------------------


def _section(pile: PileInput, a: Arrangement, settings: DesignSettings, accidental: bool) -> CircularSection:
    pf = settings.partial_factors
    gamma_c, gamma_s = (
        (pf.gamma_c_accidental, pf.gamma_s_accidental) if accidental else (pf.gamma_c, pf.gamma_s)
    )
    fck = concrete(pile.concrete).fck
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    return CircularSection(
        pile.diameter,
        tuple(Ring(r.count, r.diameter, r.radius) for r in a.rings),
        ConcreteLaw(fck, gamma_c, pf.alpha_cc),
        SteelLaw(fyk, gamma_s),
    )


def _accidental(loads: pd.DataFrame) -> np.ndarray:
    special = [CombinationType.SEISMIC.value, CombinationType.ACCIDENTAL.value]
    return loads["category"].isin(special).to_numpy()


def _utilisation(
    pile: PileInput, a: Arrangement, settings: DesignSettings, loads: pd.DataFrame
) -> np.ndarray:
    util = np.zeros(len(loads))
    accidental = _accidental(loads)
    for flag in (False, True):
        mask = accidental == flag
        if mask.any():
            sec = _section(pile, a, settings, flag)
            util[mask] = sec.utilisation(loads["N"].to_numpy()[mask], loads["M"].to_numpy()[mask])
    return util


class _Checker:
    """Maximum utilisation of a cage over all loads, using only the hull points of each load group."""

    def __init__(self, pile: PileInput, settings: DesignSettings, loads: pd.DataFrame) -> None:
        self.pile, self.settings = pile, settings
        self.groups = []
        accidental = _accidental(loads)
        for flag in (False, True):
            sub = loads[accidental == flag]
            if len(sub):
                idx = hull_indices(sub["N"].to_numpy(), sub["M"].to_numpy())
                self.groups.append((flag, sub["N"].to_numpy()[idx], sub["M"].to_numpy()[idx]))
        self.memo: dict[Arrangement, float] = {}

    def __call__(self, a: Arrangement) -> float:
        if a not in self.memo:
            self.memo[a] = max(
                float(_section(self.pile, a, self.settings, flag).utilisation(n, m).max())
                for flag, n, m in self.groups
            )
        return self.memo[a]


def design_pile(
    name: str, pile: PileInput, settings: DesignSettings, sheets: dict[str, SheetData]
) -> PileDesign:
    loads = PileLoads.from_sheets(sheets, pile.head_level).frame
    ac = math.pi * pile.diameter**2 / 4
    area_min, area_max = min_area_pile(ac), max_ratio(settings) * ac
    pr = settings.piles
    limits = {
        "min_clear_mm": pr.min_clear_spacing,
        "max_clear_mm": pr.max_clear_spacing,
        "row_gap_mm": pr.row_clear_spacing,
        "aggregate_mm": pr.aggregate_size,
        "max_ratio_pct": pr.max_steel_ratio,
    }
    geom = {"diameter_mm": pile.diameter, "cover_mm": pile.cover, "link_diameter_mm": pile.link_diameter}
    if not loads.empty:
        head = pile.head_level if pile.head_level is not None else float(loads["Z"].max())
        geom |= {"head_level_m": round(head, 2), "toe_level_m": round(float(loads["Z"].min()), 2)}
    notes: list[str] = []
    if pile.casing is not None and pile.casing.role == "structural":
        notes.append(
            "The structural casing is not yet included in the N–M check; the pile is designed as "
            "reinforced concrete only, which is conservative."
        )
    if pile.head_level is None:
        notes.append("No pile head level is set, so results inside the slab are included.")
    if loads.empty:
        notes.append("No ULS results.")
        return PileDesign(name, None, 0.0, False, {}, area_min, area_max, 0.0, 0.0, limits, geom, notes=notes)

    families = [[a for a in fam if a.area >= area_min] for fam in _families(pile, settings)]
    families = [f for f in families if f]
    if not families:
        fixed = f" with {pile.bar_count} bars in the outer row" if pile.bar_count else ""
        notes.append(f"No cage fits the pile{fixed} with the chosen bar sizes, rows and spacing limits.")
        return PileDesign(
            name, None, math.inf, False, {}, area_min, area_max, 0.0, 0.0, limits, geom, notes=notes
        )

    check = _Checker(pile, settings, loads)
    passing: list[Arrangement] = []
    strongest, strongest_u = None, math.inf
    for fam in families:
        u = check(fam[-1])
        if u < strongest_u:
            strongest, strongest_u = fam[-1], u
        if u > 1.0:
            continue
        lo, hi = 0, len(fam) - 1  # fam[hi] passes; find the fewest bars that pass
        while lo < hi:
            mid = (lo + hi) // 2
            if check(fam[mid]) <= 1.0:
                hi = mid
            else:
                lo = mid + 1
        passing.append(fam[hi])

    key = _sort_key(settings)
    passed = bool(passing)
    if passed:
        pool = passing
        if pr.extra_rows_only_when_needed and any(a.rows == 1 for a in passing):
            pool = [a for a in passing if a.rows == 1]
        chosen = min(pool, key=key)
    else:
        chosen = strongest
        notes.append(
            f"No cage within the {pr.max_steel_ratio:g}% limit and the allowed rows carries the loads; "
            "the strongest one is shown."
        )

    curtailment = None
    if pr.curtail and passed:
        from .curtailment import curtail  # imports this module

        curtailment = curtail(name, pile, settings, loads, chosen, area_min)
    positions = (
        loads[["X", "Y"]].round(2).drop_duplicates().sort_values(["Y", "X"]).to_numpy().tolist()
        if {"X", "Y"} <= set(loads.columns)
        else []
    )

    chosen_util = _utilisation(pile, chosen, settings, loads)
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
    alternatives = [
        {
            **a.to_dict(),
            "utilisation": round(check(a), 3),
            "steel_ratio_kg_m3": round(a.area / ac * STEEL_DENSITY, 1),
            "chosen": a == chosen,
        }
        for a in _alternatives(chosen, passing, key)
    ]
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
        spacing_limits=limits,
        section=geom,
        positions=positions,
        curtailment=curtailment,
        alternatives=alternatives,
        notes=notes,
        curve=np.round(sec.interaction(), 1).tolist(),
        profile=[{"z": float(r.Z), "util": float(r.util)} for r in profile.itertuples()],
        points=[
            [c, round(n, 1), round(m, 1), round(u, 3)]
            for c, n, m, u in loads[["combination", "N", "M", "util"]].itertuples(index=False)
        ],
    )


def _alternatives(chosen: Arrangement, passing: list[Arrangement], key) -> list[Arrangement]:
    """The chosen cage, then the best passing cage for each number of rows and outer bar size."""
    best: dict[tuple, Arrangement] = {}
    for a in sorted(passing, key=key):
        best.setdefault((a.rows, a.outer.diameter), a)
    rest = [a for a in best.values() if a != chosen]
    return [chosen] + sorted(rest, key=key)[:7]
