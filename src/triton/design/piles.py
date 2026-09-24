"""Longitudinal reinforcement of concrete piles for axial force and bending (EN 1992-1-1).

For each pile element (e.g. ``Pile(1)``, a row of piles in the Plaxis model) the
design takes every ULS node result below the pile head, and picks the
arrangement of equal bars on one circle that carries all of them with the
least steel (or lowest cost), subject to the pile detailing rules of 9.8.5 and
the bar spacing rules of 8.2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from ..elements import CombinationType, combination_type
from ..forces import design_forces
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import DesignSettings, PileInput, pile_cover, with_project_grades
from .circular import CircularSection, ConcreteLaw, Ring, SteelLaw, hull_indices
from .governing import qp_loads, station_sets
from .pile_cracks import pile_crack_widths

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
    def from_sheets(
        cls, sheets: dict[str, SheetData], head_level: float | None, above: float = 0.0, qp: bool = False
    ) -> PileLoads:
        """ULS results (or the QP ones with ``qp``) in the design sign convention.

        Results up to ``above`` (m) over the head level are kept and taken at the head level.
        """
        parts = []
        for combo, sheet in sheets.items():
            ctype = combination_type(combo)
            if (ctype is CombinationType.SLS_QP) != qp:
                continue
            f = design_forces(sheet.frame, sheet.parsed.spec, ["N", "Q_12", "Q_13", "M_2", "M_3"])
            if head_level is not None:
                f = f[f["Z"] <= head_level + above + 1e-9]
                f = f.assign(Z=f["Z"].clip(upper=head_level))
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
    shear: dict | None = None
    steel: dict | None = None
    alternatives: list[dict] = field(default_factory=list)
    governing_sets: list[dict] = field(default_factory=list)
    connection: dict | None = None
    casing: dict | None = None
    cracks: dict | None = None
    bands: list[list[float]] = field(default_factory=list)
    moments: list[dict] = field(default_factory=list)
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
            "shear": self.shear,
            "steel": self.steel,
            "governing_sets": self.governing_sets,
            "connection": self.connection,
            "casing": self.casing,
            "cracks": self.cracks,
            "bands": self.bands,
            "moments": self.moments,
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
    radius = pile.diameter / 2 - pile_cover(pile, settings) - pile.link_diameter - phi1 / 2
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
        radius = pile.diameter / 2 - pile_cover(pile, settings) - pile.link_diameter - phi1 / 2
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
        deduct=pf.deduct_bar_area,
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


def crack_loads(pile: PileInput, qp: pd.DataFrame) -> pd.DataFrame:
    """The QP rows whose crack width is checked: none inside a steel casing."""
    c = pile.casing
    if c is None or qp.empty:
        return qp
    inside = (qp["Z"] >= c.bottom_level - 1e-9) & (qp["Z"] <= c.top_level + 1e-9)
    return qp[~inside]


def crack_widths(pile: PileInput, a: Arrangement, settings: DesignSettings, qp: pd.DataFrame) -> pd.DataFrame:
    """7.3.4 crack width of each QP row (see ``pile_cracks``)."""
    conc = concrete(pile.concrete)
    return pile_crack_widths(
        pile.diameter,
        [(r.count, r.diameter, r.radius) for r in a.rings],
        qp,
        fctm=conc.fctm,
        ecm=conc.ecm,
        creep=settings.cracking.creep_coefficient,
    )


def crack_utilisation(
    pile: PileInput, a: Arrangement, settings: DesignSettings, qp: pd.DataFrame
) -> np.ndarray:
    if qp.empty:
        return np.zeros(0)
    return crack_widths(pile, a, settings, qp)["wk"].to_numpy() / pile.crack_width_limit


class _Checker:
    """Maximum utilisation of a cage over all loads, using only the hull points of each load group.

    With QP loads the crack width over its limit counts too.
    """

    def __init__(
        self, pile: PileInput, settings: DesignSettings, loads: pd.DataFrame, qp: pd.DataFrame | None = None
    ) -> None:
        self.pile, self.settings = pile, settings
        self.qp = qp if qp is not None else pd.DataFrame()
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
                [
                    float(_section(self.pile, a, self.settings, flag).utilisation(n, m).max())
                    for flag, n, m in self.groups
                ]
                + [float(crack_utilisation(self.pile, a, self.settings, self.qp).max(initial=0.0))]
            )
        return self.memo[a]


def design_pile(
    name: str, pile: PileInput, settings: DesignSettings, sheets: dict[str, SheetData]
) -> PileDesign:
    pile = with_project_grades(pile, settings.materials, settings.durability)
    above = settings.results_into_connection / 1e3
    full_loads = None
    casing_check = None
    if pile.casing is not None and pile.casing.role == "structural":
        full_loads = PileLoads.from_sheets(sheets, pile.head_level, above).frame
        sheets, casing_check = structural_casing(pile, settings, sheets)
    loads = PileLoads.from_sheets(sheets, pile.head_level, above).frame
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
        geom |= {
            "head_level_m": round(head, 2),
            "toe_level_m": round(float(loads["Z"].min()), 2),
            "head_level_set": pile.head_level is not None,
        }
    notes: list[str] = []
    if casing_check is not None:
        notes.append(casing_check["note"])
    if pile.head_level is None:
        notes.append("No pile top level is set, so results inside the slab are included.")
    elif above > 0:
        notes.append(
            f"Results up to {pile.head_level + above:g} m ({above * 100:g} cm into the slab) are included, "
            f"taken at the top level {pile.head_level:g} m."
        )
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

    qp = crack_loads(pile, qp_loads(sheets, pile.head_level, above))
    check = _Checker(pile, settings, loads, qp)
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
            f"No cage within the {pr.max_steel_ratio:g}% limit and the allowed rows carries the loads "
            "and keeps the QP crack widths within the limit; the strongest one is shown."
        )

    curtailment = None
    if pr.curtail and passed:
        from .curtailment import curtail  # imports this module

        curtailment = curtail(name, pile, settings, loads, chosen, area_min, qp)
    shear, steel = _shear_and_steel(pile, settings, loads, chosen, curtailment, geom)
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
    runs = (curtailment or {}).get("runs") or []
    stations = [(r["top"], r["bottom"], r["cage"]) for r in runs] or [
        (geom["head_level_m"], geom["toe_level_m"], chosen.to_dict())
    ]
    governing_sets = station_sets(pile, settings, loads, qp_loads(sheets, pile.head_level, above), stations)
    cracks = crack_summary(pile, settings, qp, stations)
    if casing_check is not None and not casing_check["tube"]["passed"]:
        passed = False
        notes.append("The steel casing fails its check.")
    if not cracks["passed"]:
        passed = False
        notes.append(
            f"QP crack width {cracks['wk_mm']:g} mm is over the {pile.crack_width_limit:g} mm limit."
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
        shear=shear,
        steel=steel,
        alternatives=alternatives,
        governing_sets=governing_sets,
        connection=connection_check(pile, settings, loads if full_loads is None else full_loads, chosen),
        casing=casing_check,
        cracks=cracks,
        bands=util_bands(loads),
        moments=[
            {"z": float(z), "M_kNm": round(float(m), 1)}
            for z, m in loads.groupby(loads["Z"].mul(2).round() / 2)["M"]
            .max()
            .sort_index(ascending=False)
            .items()
        ],
        notes=notes,
        curve=np.round(sec.interaction(), 1).tolist(),
        profile=[{"z": float(r.Z), "util": float(r.util)} for r in profile.itertuples()],
        points=[
            [c, round(n, 1), round(m, 1), round(u, 3)]
            for c, n, m, u in loads[["combination", "N", "M", "util"]].itertuples(index=False)
        ],
    )


def crack_summary(pile: PileInput, settings: DesignSettings, qp: pd.DataFrame, stations: list[tuple]) -> dict:
    """The worst QP crack width down the pile with the cage of each station, and a profile per 0.5 m."""
    out: dict = {"limit_mm": pile.crack_width_limit, "wk_mm": None, "passed": True, "profile": []}
    c = pile.casing
    if c is not None:
        out["casing"] = f"No crack check inside the steel casing, {c.bottom_level:g} to {c.top_level:g} m."
    if qp.empty:
        out["note"] = "No QP results outside a casing: no crack check."
        return out
    parts = []
    for i, (top, bottom, cage) in enumerate(stations):
        last = i == len(stations) - 1
        rows = qp[(qp["Z"] <= top + 1e-9) & ((qp["Z"] > bottom + 1e-9) | last)]
        if rows.empty:
            continue
        rings = [RingSpec(g["count"], g["diameter"], g["radius"], 0.0) for g in cage["rings"]]
        a = Arrangement(tuple(rings), 1, 0.0, 0.0)
        part = rows.join(crack_widths(pile, a, settings, rows)).assign(cage=cage["label"])
        parts.append(part)
        out.setdefault("stations", []).append(
            {"top": top, "bottom": bottom, "wk_mm": round(float(part["wk"].max()), 3)}
        )
    if not parts:
        return out
    f = pd.concat(parts)
    w = f.loc[f["wk"].idxmax()]
    out |= {
        "wk_mm": round(float(w["wk"]), 3),
        "passed": bool(f["wk"].max() <= pile.crack_width_limit + 1e-9),
        "governing": {
            "combination": w["combination"],
            "z": round(float(w["Z"]), 2),
            "N_kN": round(float(w["N"]), 1),
            "M_kNm": round(float(w["M"]), 1),
            "sigma_s_MPa": round(float(w["sigma_s"]), 1),
            "sr_max_mm": None if pd.isna(w["sr_max"]) else round(float(w["sr_max"])),
            "rho_eff": round(float(w["rho_eff"]), 4),
            "x_mm": round(float(w["x"])),
            "cage": w["cage"],
        },
        "profile": [
            {"z": float(z), "wk": round(float(v), 3)}
            for z, v in f.groupby(f["Z"].mul(2).round() / 2)["wk"].max().sort_index(ascending=False).items()
        ],
    }
    return out


def util_bands(loads: pd.DataFrame) -> list[list[float]]:
    """[x, y, z, utilisation]: the highest utilisation of each pile per 0.5 m band, for the 3D view."""
    if not {"X", "Y", "Z", "util"} <= set(loads.columns):
        return []
    key = [loads["X"].round(2), loads["Y"].round(2), loads["Z"].mul(2).round() / 2]
    g = loads.groupby(key)["util"].max()
    return [[float(x), float(y), float(z), round(float(u), 3)] for (x, y, z), u in g.items()]


def structural_casing(
    pile: PileInput, settings: DesignSettings, sheets: dict[str, SheetData]
) -> tuple[dict[str, SheetData], dict]:
    """A casing designed with the pile: between its levels the actions are shared by E·I with the
    corroded casing (as in the combi wall). Returns the sheets with the concrete's share, and the
    casing's own check (EN 1993, plastic: the casing is concrete filled)."""
    from ..forces import CombiSection, scale_forces
    from .tube import Tube, check_tube, tube_loads

    c = pile.casing
    loss = c.corrosion_loss or 0.0
    conc = concrete(pile.concrete)
    share = CombiSection(
        pile.diameter / 1e3,
        c.thickness / 1e3,
        loss / 1e3,
        e_steel=210e6,
        e_concrete=conc.ecm * 1e3,
    ).steel_share

    def inside(f: pd.DataFrame) -> pd.Series:
        return (f["Z"] >= c.bottom_level - 1e-9) & (f["Z"] <= c.top_level + 1e-9)

    concrete_part, casing_part = {}, {}
    for combo, sheet in sheets.items():
        f = sheet.frame
        if f.empty or "Z" not in f:
            concrete_part[combo] = sheet
            continue
        m = inside(f)
        concrete_part[combo] = replace(
            sheet, frame=pd.concat([f[~m], scale_forces(f[m], 1 - share)]).sort_index()
        )
        if m.any():
            casing_part[combo] = replace(sheet, frame=f[m])
    above = settings.results_into_connection / 1e3
    loads = tube_loads(casing_part, share, -math.inf, pile.head_level, above)
    pf = settings.partial_factors
    tube = Tube(pile.diameter, c.thickness, loss, c.steel or settings.materials.structural_steel)
    check = check_tube(tube, loads, method="ec3", gamma_m0=pf.gamma_m0, gamma_m1=pf.gamma_m1)
    note = (
        f"Structural steel casing from {c.bottom_level:g} to {c.top_level:g} m: there, {share:.0%} of the "
        f"actions go to the casing and {1 - share:.0%} to the reinforced concrete (E·I, casing corroded by "
        f"{loss:g} mm, Ecm {conc.ecm / 1e3:.1f} GPa). The concrete is taken at the full pile diameter. "
        "No crack width check inside the casing."
    )
    return concrete_part, {
        "top": c.top_level,
        "bottom": c.bottom_level,
        "steel_share": round(share, 3),
        "tube": check,
        "note": note,
    }


def connection_check(
    pile: PileInput, settings: DesignSettings, loads: pd.DataFrame, cage: Arrangement
) -> dict | None:
    """N–M check where a structural casing stops: welded bars (cover 0) plus the head cage, no casing.

    The zone runs ``connection_length`` up from the casing top, or down from the pile's top level when
    the casing reaches it. The concrete diameter is the pile diameter; the welded bars sit against the
    casing, their centres φ/2 inside it.
    """
    c = pile.casing
    if c is None or c.role != "structural" or loads.empty:
        return None
    top = pile.head_level if pile.head_level is not None else float(loads["Z"].max())
    if c.top_level < top - 1e-9:
        lo, hi = c.top_level, min(c.top_level + c.connection_length, top)
    else:
        lo, hi = top - c.connection_length, top
    zone = loads[(loads["Z"] >= lo - 1e-9) & (loads["Z"] <= hi + 1e-9)]
    phi = c.connection_bar_diameter
    welded = c.connection_bar_count or 0
    rings = list(cage.rings)
    if welded:
        spacing = math.pi * (pile.diameter - phi) / welded - phi
        rings.insert(0, RingSpec(welded, phi, pile.diameter / 2 - phi / 2, spacing))
    section = replace(cage, rings=tuple(rings))
    out = {
        "top": round(hi, 2),
        "bottom": round(lo, 2),
        "welded": f"{welded}Ø{phi}" if welded else None,
        "cage": cage.label,
        "utilisation": None,
        "passed": None,
        "governing": None,
        "notes": [],
    }
    if not welded:
        out["notes"].append("No welded bars are entered: the cage alone is checked.")
    if zone.empty:
        out["notes"].append("No results in the connection zone.")
        return out
    util = _utilisation(pile, section, settings, zone)
    i = int(np.argmax(util))
    g = zone.iloc[i]
    out |= {
        "utilisation": round(float(util[i]), 3),
        "passed": bool(util[i] <= 1.0),
        "governing": {
            "combination": g["combination"],
            "z": round(float(g["Z"]), 2),
            "N_kN": round(float(g["N"]), 1),
            "M_kNm": round(float(g["M"]), 1),
        },
    }
    if welded and spacing < _min_clear(settings, phi):
        out["notes"].append(
            f"The welded bars are {spacing:.0f} mm apart, closer than the minimum clear spacing."
        )
    return out


def _shear_and_steel(pile, settings, loads, chosen, curtailment, geom) -> tuple[dict | None, dict | None]:
    """Links for shear, and the steel of one pile: longitudinal (with laps) and links."""
    from .pile_shear import CageZone, design_shear

    head, toe = geom.get("head_level_m"), geom.get("toe_level_m")
    if head is None or toe is None or head <= toe:
        return None, None
    runs = (curtailment or {}).get("runs") or []
    if runs:
        zones = [
            CageZone(
                r["top"],
                r["bottom"],
                r["cage"]["area_mm2"],
                r["cage"]["rings"][0]["radius"],
                max(g["diameter"] for g in r["cage"]["rings"]),
                min(g["diameter"] for g in r["cage"]["rings"]),
                max(r["lap_below_m"]),
            )
            for r in runs
        ]
        longitudinal = curtailment["weight_kg"]
    else:
        diameters = [g.diameter for g in chosen.rings]
        zones = [CageZone(head, toe, chosen.area, chosen.outer.radius, max(diameters), min(diameters), 0.0)]
        longitudinal = chosen.weight_per_m * (head - toe)
    shear = design_shear(pile, settings, loads, zones)
    volume = math.pi * pile.diameter**2 / 4 / 1e6 * (head - toe)
    total = longitudinal + shear["links_kg"]
    steel = {
        "longitudinal_kg": round(longitudinal, 1),
        "links_kg": shear["links_kg"],
        "total_kg": round(total, 1),
        "kg_per_m3": round(total / volume, 1),
        "concrete_m3": round(volume, 2),
    }
    return shear, steel


def _alternatives(chosen: Arrangement, passing: list[Arrangement], key) -> list[Arrangement]:
    """The chosen cage, then the best passing cage for each number of rows and outer bar size."""
    best: dict[tuple, Arrangement] = {}
    for a in sorted(passing, key=key):
        best.setdefault((a.rows, a.outer.diameter), a)
    rest = [a for a in best.values() if a != chosen]
    return [chosen] + sorted(rest, key=key)[:7]
