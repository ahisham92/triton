"""Displacements estimated from the straining actions, with no Plaxis displacement run.

The workbook gives the moments along every member. The curvature is κ = M / EI and integrating it
twice along the member gives its deflected shape: the rotation θ = ∫κ ds, then the displacement
w = ∫θ ds. The moments are taken as straight lines between the Plaxis nodes, so both integrals are
exact for them (a tip load on a cantilever gives P L³ / 3EI, a uniform moment M L² / 2EI).

* Piles and the combi wall: down the member, both ways. M3 bends it across the quay (its local 2
  axis) and M2 along the quay (local 3), as the workbook check found the directions. Every pile of
  the element (every plan position) is worked out and the one that moves most is shown.
* Sheet pile wall: M11 per metre down the wall, in 1 m strips along it (points inside the king
  piles left out), with the AZ section's EI per metre; the strip that moves most is shown.
* Boundary conditions (Design tab): fixed at the toe by default, no displacement and no rotation,
  since the member is deeply embedded. Or "firm soil": no displacement at the toe nor at the firm
  soil level, the toe rotating to suit.
* Slabs and beams, a simple strip estimate: 1 m strips each way, M11 or M22 per metre (sagging +)
  and gross Ecm·h³/12; the vertical deflection relative to a straight line between the supports
  (the piles and king piles under the strip). It leaves out the supports' own movement and any
  cantilever beyond the last support.
* Stiffness: gross (uncracked) E·I by default: the pile's concrete circle at Ecm; the combi wall's
  corroded tube Ea·Ia plus the infill Ecm·Ic where it is filled (the E·I split its design uses), the
  tube alone below; the AZ section's catalogue inertia at 210 GPa. Cracked: piles by EN 1992-1-1
  7.4.3, κ = ζ·κII + (1 − ζ)·κI with ζ = 1 − β (Mcr / M)², β = 0.5 and Mcr = (fctm + N / Ac)·W, κII
  from the cracked section with the designed cage (the minimum steel of Table 9.6N before the pile
  is designed); the combi wall infill at Ke·Ecm·Ic, Ke = 0.6 (EN 1994-1-1 6.7.3.3). Steel does not
  crack; slabs and beams stay gross. Long term: Ec,eff = Ecm / (1 + φ).
* Loads: the QP combination (as the crack width checks) where the element has one, else its
  governing phase, the one with the largest moment. ULS moments carry their partial factors, so the
  estimate is then on the high side. Load multipliers are not applied (they make actions design
  values); the working zone is.

Limitations: the shape is the member's own bending measured from its toe. It leaves out the soil's
springs, the toe moving or rotating in the ground, axial shortening and second-order (P–Δ) effects,
and it is only as good as the Plaxis moments (their mesh, and the stiffness Plaxis gave the member).
It is an estimate to compare with, not a Plaxis displacement result.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..axes import CLEAR, _corr, sag_factor
from ..elements import CombinationType, combination_type
from ..forces import design_forces
from ..importer import LABEL, SheetData
from ..materials import concrete
from ..project import (
    BeamInput,
    CombiWallInput,
    DeflectionSettings,
    DesignSettings,
    PileInput,
    Section,
    SheetPileInput,
    SlabInput,
    pile_cover,
    with_project_grades,
)
from ..validation import ImportResult
from .combi import combi_section
from .pile_cracks import cracked_stress
from .piles import MIN_BAR, MIN_BARS, min_area_pile
from .runner import factored_elements
from .sheet_piles import E_STEEL, normalise
from .sheet_piles import section as az_section
from .tube import KE

BETA = 0.5  # EN 1992-1-1 7.4.3(3): sustained or repeated loads
STRIP = 1.0  # m, width of the wall and plate strips
STEP = 0.5  # m, plate results averaged in bands this long along a strip
LEVEL = 0.25  # m, wall results averaged in bands this deep
MAX_STATIONS = 160  # stations kept per curve for the page and the report

TOE = {
    "fixed": "Fixed at the toe: no displacement and no rotation",
    "firm_soil": "Held at the toe and at the firm soil level; the toe rotates",
}
ESTIMATE = (
    "Estimated from the straining actions (curvature M / EI integrated twice along each member), not a "
    "Plaxis displacement result."
)


# --- Integration --------------------------------------------------------------------------------------


def integrate(s: np.ndarray, kappa: np.ndarray, hold: float | None = None) -> np.ndarray:
    """Displacement w (m) at stations ``s`` (m, increasing from the toe) of curvature ``kappa`` (1/m).

    w = θ = 0 at s[0] (fixed), or with ``hold`` w = 0 at s[0] and at s = hold, the rotation at s[0]
    giving it. κ is a straight line between stations, so both integrals are exact for it.
    """
    s, kappa = np.asarray(s, float), np.asarray(kappa, float)
    h = np.diff(s)
    ka, kb = kappa[:-1], kappa[1:]
    theta = np.concatenate([[0.0], np.cumsum(h * (ka + kb) / 2)])
    w = np.concatenate([[0.0], np.cumsum(theta[:-1] * h + h * h * (2 * ka + kb) / 6)])
    if hold is not None and hold > s[0] + 1e-9:
        at = float(np.interp(hold, s, w))
        w = w - (s - s[0]) * at / (hold - s[0])
    return w


def between_supports(
    s: np.ndarray, kappa: np.ndarray, supports: list[float]
) -> tuple[np.ndarray, np.ndarray]:
    """Stations and displacement relative to a straight line between each pair of neighbouring
    supports (w = 0 at every support), from the first support to the last."""
    s, kappa = np.asarray(s, float), np.asarray(kappa, float)
    grid = np.union1d(s, supports)
    k = np.interp(grid, s, kappa)
    w = integrate(grid, k)
    keep = (grid >= supports[0] - 1e-9) & (grid <= supports[-1] + 1e-9)
    out = np.zeros(len(grid))
    for a, b in zip(supports[:-1], supports[1:], strict=True):
        span = (grid >= a - 1e-9) & (grid <= b + 1e-9)
        wa, wb = np.interp(a, grid, w), np.interp(b, grid, w)
        out[span] = w[span] - wa - (grid[span] - a) * (wb - wa) / (b - a)
    return grid[keep], out[keep]


# --- Helpers ------------------------------------------------------------------------------------------


def pick_combination(
    sheets: dict[str, SheetData], cols: list[str], wanted: str = ""
) -> tuple[str | None, str]:
    """The combination the estimate uses, and why: the one asked for, else the QP one (the largest
    if several), else the phase with the largest moment."""
    usable = {c: s for c, s in sheets.items() if not s.frame.empty and set(cols) <= set(s.frame.columns)}
    if not usable:
        return None, ""

    def size(c: str) -> float:
        return float(np.nanmax(usable[c].frame[cols].abs().to_numpy()))

    missing = ""
    if wanted.strip():
        hit = next((c for c in usable if c.lower() == wanted.strip().lower()), None)
        if hit is not None:
            return hit, "as chosen on the Design tab"
        missing = f"{wanted.strip()} is not in the workbook for this element, so "
    qp = [c for c in usable if combination_type(c) is CombinationType.SLS_QP]
    if qp:
        return max(qp, key=size), missing + "QP (quasi-permanent), as the crack width checks"
    return max(usable, key=size), (
        missing + "the governing phase (largest moment): there is no QP combination, and ULS moments "
        "carry their partial factors, so the estimate is on the high side"
    )


def _thin(s: np.ndarray, w: np.ndarray) -> list[list[float]]:
    """[station, mm] pairs, at most MAX_STATIONS of them, keeping both ends and the largest."""
    n = len(s)
    idx = np.arange(n)
    if n > MAX_STATIONS:
        idx = np.unique(
            np.concatenate([np.linspace(0, n - 1, MAX_STATIONS).round().astype(int), [np.argmax(abs(w))]])
        )
    return [[round(float(s[i]), 3), round(float(w[i]) * 1e3, 2)] for i in idx]


def _curve(key: str, label: str, s: np.ndarray, w: np.ndarray, head: bool = True) -> dict[str, Any]:
    i = int(np.argmax(np.abs(w)))
    return {
        "key": key,
        "label": label,
        "stations": _thin(s, w),
        "head_mm": round(float(w[-1]) * 1e3, 2) if head else None,
        "max_mm": round(float(w[i]) * 1e3, 2),
        "max_at": round(float(s[i]), 2),
    }


def _summary(entry: dict[str, Any]) -> dict[str, Any]:
    """The element's head and largest displacement over its directions."""
    dirs = entry["directions"]
    big = max(dirs, key=lambda d: abs(d["max_mm"]))
    heads = [d for d in dirs if d["head_mm"] is not None]
    head = max(heads, key=lambda d: abs(d["head_mm"])) if heads else None
    entry.update(
        max_mm=big["max_mm"],
        max_at=big["max_at"],
        max_direction=big["label"],
        head_mm=None if head is None else head["head_mm"],
        head_direction=None if head is None else head["label"],
    )
    return entry


def _one_sign(g: pd.DataFrame) -> list[str]:
    """Turn a Beam part of a member whose moments run against its EmbeddedBeam part's, so dM/dz has one
    relation to the shear (M3 to Q12, M2 to Q13) all along it. Plaxis gives the two kinds (the combi
    wall's upper and lower parts) moments of opposite sign; integrated as they are, one part would bend
    the wrong way. Members of one kind are left as they are."""
    turned = []
    runs = (g["part"] != g["part"].shift()).cumsum()
    for m, q in (("M_3", "Q_12"), ("M_2", "Q_13")):
        if m not in g.columns or q not in g.columns:
            continue
        found = []
        for _, run in g.groupby(runs):
            if len(run) < 5 or np.ptp(run["Z"].to_numpy(float)) <= 0:
                continue
            r = _corr(np.gradient(run[m].to_numpy(float), run["Z"].to_numpy(float)), run[q].to_numpy(float))
            if np.isfinite(r) and abs(r) >= CLEAR:
                found.append((bool(run["part"].iloc[0]), len(run), np.sign(r), run.index))
        embedded = [f for f in found if f[0]]
        if not embedded or len(embedded) == len(found):
            continue
        ref = max(embedded, key=lambda f: f[1])[2]
        for part, _, sign, index in found:
            if not part and sign != ref:
                g.loc[index, m] = -g.loc[index, m]
                turned.append(f"{m.replace('_', '')} of the Beam part")
    return turned


def _members(
    frame: pd.DataFrame, cols: list[str]
) -> tuple[list[tuple[float, float, pd.DataFrame]], list[str]]:
    """Each vertical member of an element (a plan position): its results by level, repeats averaged,
    with one sign convention for its moments (``_one_sign``); and the parts turned."""
    label = frame[LABEL].astype(str) if LABEL in frame.columns else pd.Series("", index=frame.index)
    frame = frame.assign(part=label.str.contains("embedded", case=False))
    keep = cols + [c for c in ("Q_12", "Q_13") if c in frame.columns and c not in cols]
    out, turned = [], []
    for (x, y), g in frame.groupby([frame["X"].round(1), frame["Y"].round(1)], sort=True):
        g = (
            g.assign(Z=g["Z"].round(3))
            .groupby("Z", as_index=False)
            .agg({**{c: "mean" for c in keep}, "part": "first"})
        )
        g = g.sort_values("Z").reset_index(drop=True)
        if len(g) < 4:
            continue
        turned += [t for t in _one_sign(g) if t not in turned]
        out.append((float(x), float(y), g))
    return out, turned


def _turned_note(turned: list[str]) -> list[str]:
    if not turned:
        return []
    return [
        f"{', '.join(sorted(set(turned)))} turned to the EmbeddedBeam part's sign, so dM/dz follows the "
        "shear the same way all along: Plaxis gives the two kinds of member opposite signs."
    ]


def _hold(ds: DeflectionSettings, own: float | None, toe: float, top: float) -> tuple[float | None, str]:
    """The level held besides the toe (None: fixed at the toe), and the boundary condition in words."""
    if ds.toe == "firm_soil":
        level = own if own is not None else ds.firm_soil_level
        if level is not None and toe + 1e-6 < level < top - 1e-6:
            return level, f"{TOE['firm_soil']} ({toe:g} m and {level:g} m)."
        why = "no firm soil level" if level is None else f"firm soil level {level:g} m is not on the member"
        return None, f"{TOE['fixed']} ({toe:g} m): {why}."
    return None, f"{TOE['fixed']} ({toe:g} m)."


def _e_concrete(grade: str, settings: DesignSettings, ds: DeflectionSettings) -> tuple[float, str]:
    """E of the concrete (MPa) and its words: Ecm, or Ec,eff = Ecm / (1 + φ) long term."""
    c = concrete(grade)
    if ds.long_term:
        phi = settings.cracking.creep_coefficient
        return c.ecm / (1 + phi), f"{grade} Ec,eff = Ecm / (1 + {phi:g}) = {c.ecm / (1 + phi) / 1e3:.1f} GPa"
    return c.ecm, f"{grade} Ecm {c.ecm / 1e3:.1f} GPa"


def _directions(axes: dict[str, Any] | None) -> tuple[str, str]:
    """Words for the displacement from M3 and from M2."""
    local = (axes or {}).get("local") if isinstance(axes, dict) else axes
    if local and local.get("2") and local.get("3"):
        return (
            f"{local['2']}, across the quay (from M3)",
            f"{local['3']}, along the quay (from M2)",
        )
    return "local 2 (from M3)", "local 3 (from M2)"


# --- Piles --------------------------------------------------------------------------------------------


def _cages(pile: PileInput, settings: DesignSettings, result: dict | None) -> tuple[list, str]:
    """(top, bottom, rings) down the pile for its cracked section, and where they come from. Rings are
    (count, bar diameter, radius of the bar centres), the outer row first."""

    def rings(cage: dict) -> tuple:
        return tuple(
            (int(r["count"]), float(r["diameter"]), float(r["radius"])) for r in cage.get("rings") or []
        )

    runs = ((result or {}).get("curtailment") or {}).get("runs") or []
    zones = [
        (float(r["top"]), float(r["bottom"]), rings(r["cage"])) for r in runs if rings(r.get("cage") or {})
    ]
    if zones:
        return zones, "the designed cage down the pile"
    arrangement = (result or {}).get("arrangement")
    if arrangement and rings(arrangement):
        return [
            (math.inf, -math.inf, rings(arrangement))
        ], f"the designed cage {arrangement.get('label', '')}"
    d = pile.diameter
    area = min_area_pile(math.pi * d * d / 4)
    count = max(MIN_BARS, math.ceil(area / (math.pi * MIN_BAR**2 / 4)))
    radius = d / 2 - pile_cover(pile, settings) - pile.link_diameter - MIN_BAR / 2
    return (
        [(math.inf, -math.inf, ((count, float(MIN_BAR), radius),))],
        f"the minimum steel {count}Ø{MIN_BAR} (EN 1992-1-1 Table 9.6N; the pile is not designed yet)",
    )


def _cage_at(zones: list, z: np.ndarray) -> np.ndarray:
    """Index of the cage zone at each level (the nearest zone outside them all)."""
    tops = np.array([t for t, _, _ in zones])
    bottoms = np.array([b for _, b, _ in zones])
    gap = np.maximum(bottoms[None, :] - z[:, None], 0) + np.maximum(z[:, None] - tops[None, :], 0)
    return np.argmin(gap, axis=1)


def _cracked_kappa(
    d: float, e_c: float, fctm: float, n: np.ndarray, m: np.ndarray, z: np.ndarray, zones: list
) -> np.ndarray:
    """Curvature (1/m) of a circular pile under N (kN, compression +) and M (kNm, resultant) by EN
    1992-1-1 7.4.3: between the uncracked and the fully cracked section, ζ = 1 − β (Mcr / M)²."""
    area, w, inertia = math.pi * d * d / 4, math.pi * d**3 / 32, math.pi * d**4 / 64
    k1 = m * 1e6 / (e_c * inertia) * 1e3
    mcr = np.maximum((fctm + n * 1e3 / area) * w / 1e6, 0.0)
    k2 = k1.copy()
    which = _cage_at(zones, z)
    for i, (_, _, rings) in enumerate(zones):
        sel = (which == i) & (m > mcr)
        if not sel.any():
            continue
        depth, bars = [], []
        for count, phi, radius in rings:
            ang = 2 * math.pi * np.arange(count) / count
            depth.append(d / 2 - radius * np.cos(ang))
            bars.append(np.full(count, math.pi * phi * phi / 4))
        _, k, _ = cracked_stress(d, np.concatenate(depth), np.concatenate(bars), n[sel], m[sel], e_c)
        k2[sel] = k * 1e3
    zeta = np.where(m > mcr, 1 - BETA * (mcr / np.maximum(m, 1e-9)) ** 2, 0.0)
    return zeta * k2 + (1 - zeta) * k1


def _pile(
    name: str,
    pile: PileInput,
    settings: DesignSettings,
    ds: DeflectionSettings,
    sheets: dict[str, SheetData],
    axes: dict | None,
    result: dict | None,
) -> dict[str, Any] | None:
    pile = with_project_grades(pile, settings.materials, settings.durability)
    combo, why = pick_combination(sheets, ["M_2", "M_3"], ds.combination)
    if combo is None:
        return None
    sheet = sheets[combo]
    cols = [c for c in ("N", "Q_12", "Q_13", "M_2", "M_3") if c in sheet.frame.columns]
    f = design_forces(sheet.frame, sheet.parsed.spec, cols)
    if LABEL in sheet.frame.columns:
        f[LABEL] = sheet.frame.loc[f.index, LABEL]
    if "N" not in f.columns:
        f["N"] = 0.0
    if pile.head_level is not None:
        f = f[f["Z"] <= pile.head_level + 1e-6]
    members, turned = _members(f, ["N", "M_2", "M_3"])
    if not members:
        return None
    d = pile.diameter
    e_c, e_words = _e_concrete(pile.concrete, settings, ds)
    fctm = concrete(pile.concrete).fctm
    ei = e_c * math.pi * d**4 / 64 * 1e-9  # kN·m²
    cracked = ds.stiffness == "cracked"
    zones, cage_words = _cages(pile, settings, result) if cracked else ([], "")
    across, along = _directions(axes)
    best, low = None, ei
    for x, y, g in members:
        z = g["Z"].to_numpy(float)
        m2, m3 = g["M_2"].to_numpy(float), g["M_3"].to_numpy(float)
        if cracked:
            m = np.hypot(m2, m3)
            k = _cracked_kappa(d, e_c, fctm, g["N"].to_numpy(float), m, z, zones)
            per = np.where(m > 1e-9, k / np.maximum(m, 1e-9), 1 / ei)  # 1 / EI_eff
            low = min(low, float(1 / per.max()))
        else:
            per = np.full(len(z), 1 / ei)
        hold, boundary = _hold(ds, None, z[0], z[-1])
        w3, w2 = integrate(z, m3 * per, hold), integrate(z, m2 * per, hold)
        size = max(abs(w3).max(), abs(w2).max())
        if best is None or size > best[0]:
            best = (size, x, y, z, w3, w2, boundary)
    _, x, y, z, w3, w2, boundary = best
    stiff = f"Gross concrete circle Ø{d:g} mm, {e_words}: EI {ei:,.0f} kN·m² (any steel casing left out)."
    if cracked:
        stiff += (
            f" Cracked where M > Mcr (EN 1992-1-1 7.4.3, β {BETA:g}) with {cage_words}: EI down to "
            f"{low:,.0f} kN·m² ({low / ei:.0%} of gross)."
        )
    return _summary(
        {
            "element": name,
            "kind": "pile",
            "axis": "level",
            "combination": combo,
            "combination_note": why,
            "at": f"pile at X {x:g}, Y {y:g} m, the one that moves most of {len(members)}",
            "boundary": boundary,
            "stiffness": stiff,
            "head_level": round(float(z[-1]), 2),
            "toe_level": round(float(z[0]), 2),
            "directions": [_curve("M_3", across, z, w3), _curve("M_2", along, z, w2)],
            "notes": _turned_note(turned),
        }
    )


# --- Combi wall ---------------------------------------------------------------------------------------


def _combi(
    name: str,
    wall: CombiWallInput,
    settings: DesignSettings,
    ds: DeflectionSettings,
    sheets: dict[str, SheetData],
    axes: dict | None,
) -> dict[str, Any] | None:
    wall = with_project_grades(wall, settings.materials, settings.durability)
    combo, why = pick_combination(sheets, ["M_2", "M_3"], ds.combination)
    if combo is None:
        return None
    frame = sheets[combo].frame
    f = frame[[c for c in ("X", "Y", "Z", "M_2", "M_3", "Q_12", "Q_13", LABEL) if c in frame.columns]]
    if wall.top_level_to_ignore is not None:
        f = f[f["Z"] <= wall.top_level_to_ignore + 1e-6]
    members, turned = _members(f, ["M_2", "M_3"])
    if not members:
        return None
    sec = combi_section(wall)
    e_c, e_words = _e_concrete(wall.concrete, settings, ds)
    ke = KE if ds.stiffness == "cracked" else 1.0
    steel = sec.e_steel * sec.i_steel  # kN·m²
    filled = steel + ke * e_c * 1e3 * sec.i_concrete
    across, along = _directions(axes)
    best = None
    for x, y, g in members:
        z = g["Z"].to_numpy(float)
        per = 1 / np.where(z >= wall.concrete_bottom_level - 1e-9, filled, steel)
        hold, boundary = _hold(ds, wall.firm_soil_level, z[0], z[-1])
        w3 = integrate(z, g["M_3"].to_numpy(float) * per, hold)
        w2 = integrate(z, g["M_2"].to_numpy(float) * per, hold)
        size = max(abs(w3).max(), abs(w2).max())
        if best is None or size > best[0]:
            best = (size, x, y, z, w3, w2, boundary)
    _, x, y, z, w3, w2, boundary = best
    infill = f"{'0.6 × ' if ke != 1 else ''}{e_words}"
    stiff = (
        f"Tube Ø{wall.tube_diameter:g} × {wall.tube_thickness:g} mm with {wall.corrosion_loss:g} mm "
        f"corrosion loss, Ea·Ia {steel:,.0f} kN·m² plus the infill ({infill}) above "
        f"{wall.concrete_bottom_level:g} m: EI {filled:,.0f} kN·m²; the tube alone below."
    )
    if ke != 1:
        stiff += " Infill cracked: Ke = 0.6 (EN 1994-1-1 6.7.3.3)."
    return _summary(
        {
            "element": name,
            "kind": "combi_wall",
            "axis": "level",
            "combination": combo,
            "combination_note": why,
            "at": f"king pile at X {x:g}, Y {y:g} m, the one that moves most of {len(members)}",
            "boundary": boundary,
            "stiffness": stiff,
            "head_level": round(float(z[-1]), 2),
            "toe_level": round(float(z[0]), 2),
            "directions": [_curve("M_3", across, z, w3), _curve("M_2", along, z, w2)],
            "notes": _turned_note(turned),
        }
    )


# --- Sheet pile wall ----------------------------------------------------------------------------------


def _spw(
    name: str,
    wall: SheetPileInput,
    ds: DeflectionSettings,
    sheets: dict[str, SheetData],
    king_piles: list[tuple[float, float, float]],
) -> dict[str, Any] | None:
    combo, why = pick_combination(sheets, ["M_11"], ds.combination)
    if combo is None:
        return None
    f = sheets[combo].frame[["X", "Y", "Z", "M_11"]].dropna()
    for x, y, r in king_piles:
        f = f[np.hypot(f["X"] - x, f["Y"] - y) >= r - 1e-6]
    if f.empty:
        return None
    along = "X" if np.ptp(f["X"].to_numpy(float)) > np.ptp(f["Y"].to_numpy(float)) else "Y"
    sec = az_section(normalise(wall.section_name))
    ei = E_STEEL * sec.inertia * 1e-5  # kN·m² per m
    best, count = None, 0
    for strip, g in f.groupby(np.floor(f[along] / STRIP)):
        g = g.groupby((g["Z"] / LEVEL).round() * LEVEL)["M_11"].mean().sort_index()
        if len(g) < 4:
            continue
        count += 1
        z = g.index.to_numpy(float)
        hold, boundary = _hold(ds, wall.firm_soil_level, z[0], z[-1])
        w = integrate(z, g.to_numpy(float) / ei, hold)
        if best is None or abs(w).max() > best[0]:
            best = (abs(w).max(), (strip + 0.5) * STRIP, z, w, boundary)
    if best is None:
        return None
    _, at, z, w, boundary = best
    return _summary(
        {
            "element": name,
            "kind": "sheet_pile_wall",
            "axis": "level",
            "combination": combo,
            "combination_note": why,
            "at": f"1 m strip at {along} {at:g} m, the one that moves most of {count}",
            "boundary": boundary,
            "stiffness": (
                f"{sec.name}, I {sec.inertia:,.0f} cm⁴/m at E {E_STEEL / 1e3:g} GPa: EI {ei:,.0f} kN·m² "
                "per metre (no corrosion loss)."
            ),
            "head_level": round(float(z[-1]), 2),
            "toe_level": round(float(z[0]), 2),
            "directions": [_curve("M_11", "across the wall (from M11)", z, w)],
        }
    )


# --- Slabs and beams ----------------------------------------------------------------------------------


def _supports(sheets: dict[str, dict[str, SheetData]], section: Section) -> np.ndarray:
    """Plan positions (X, Y) of the piles and king piles under the deck."""
    pts = []
    for name, element in section.elements.items():
        if not isinstance(element, PileInput | CombiWallInput) or name not in sheets:
            continue
        f = next((s.frame for s in sheets[name].values() if not s.frame.empty), None)
        if f is not None and {"X", "Y"} <= set(f.columns):
            pts.append(f[["X", "Y"]].round(1).drop_duplicates().to_numpy(float))
    return np.concatenate(pts) if pts else np.empty((0, 2))


def _strip_supports(along: np.ndarray, lo: float, hi: float) -> list[float]:
    """Support positions along a strip, merged within 0.5 m; one just beyond the plate's end (the
    beam over a pile row, a king pile under the front beam) holds that end."""
    out: list[float] = []
    for p in sorted(along):
        if p < lo - 2.0 or p > hi + 2.0:
            continue
        p = min(max(p, lo), hi)
        if not out or p - out[-1] > STEP:
            out.append(p)
    return out


def _plate(
    name: str,
    element: SlabInput | BeamInput,
    settings: DesignSettings,
    ds: DeflectionSettings,
    sheets: dict[str, SheetData],
    axes: dict | None,
    supports: np.ndarray,
) -> dict[str, Any] | None:
    local = (axes or {}).get("local") or {}
    if not {local.get("1"), local.get("2")} <= {"X", "Y"}:
        return None
    element = with_project_grades(element, settings.materials, settings.durability)
    combo, why = pick_combination(sheets, ["M_11", "M_22"], ds.combination)
    if combo is None:
        return None
    f = sheets[combo].frame[["X", "Y", "M_11", "M_22"]].dropna()
    h = element.thickness if isinstance(element, SlabInput) else element.depth
    e_c, e_words = _e_concrete(element.concrete, settings, ds)
    ei = e_c * h**3 / 12 * 1e-6  # kN·m² per metre
    sag, _ = sag_factor(settings.plate_positive_moment, axes)
    curves = []
    where = []
    for k, col in (("1", "M_11"), ("2", "M_22")):
        along = local[k]
        across = "Y" if along == "X" else "X"
        best = None
        for strip, g in f.groupby(np.floor(f[across] / STRIP)):
            centre = (strip + 0.5) * STRIP
            near = (
                supports[np.abs(supports[:, 1 if across == "Y" else 0] - centre) <= STRIP]
                if len(supports)
                else []
            )
            s_all = g[along].to_numpy(float)
            held = _strip_supports(
                near[:, 0 if along == "X" else 1] if len(near) else [], s_all.min(), s_all.max()
            )
            if len(held) < 2:
                continue
            m = g.groupby((g[along] / STEP).round() * STEP)[col].mean().sort_index()
            if len(m) < 3:
                continue
            s, w = between_supports(m.index.to_numpy(float), sag * m.to_numpy(float) / ei, held)
            if best is None or abs(w).max() > best[0]:
                best = (abs(w).max(), centre, s, w, len(held))
        if best is not None:
            _, centre, s, w, n = best
            label = f"vertical, strips along {along} (from M{k}{k}), up +"
            curves.append(_curve(col, label, s, w, head=False))
            where.append(f"along {along}: the 1 m strip at {across} {centre:g} m over {n} supports")
    if not curves:
        return None
    return _summary(
        {
            "element": name,
            "kind": "slab" if isinstance(element, SlabInput) else "beam",
            "axis": "position",
            "combination": combo,
            "combination_note": why,
            "at": "; ".join(where) + ", the strip that moves most each way",
            "boundary": "Simple strip estimate: no displacement at the piles and king piles under the strip "
            "(their own movement left out); cantilevers beyond the last support left out.",
            "stiffness": f"Gross {h:g} mm section per metre, {e_words}: EI {ei:,.0f} kN·m² per metre.",
            "directions": curves,
        }
    )


# --- Section ------------------------------------------------------------------------------------------


def estimate(
    settings: DesignSettings, section: Section, workbook: ImportResult, results: dict | None = None
) -> dict[str, Any]:
    """Estimated displacements of every pile, combi wall, sheet pile wall, beam and slab of a section
    from its workbook (the working zone applied, load multipliers not). ``results``: the stored
    design, whose pile cages the cracked stiffness uses."""
    ds = section.deflection
    sheets = factored_elements(section.model_copy(update={"load_factors": []}), workbook)
    axes = {a["element"]: a for a in getattr(workbook, "axes", None) or []}
    designed = {p["element"]: p for p in (results or {}).get("piles") or []}
    out, skipped = [], []

    def add(name: str, entry: dict | None) -> None:
        if entry is None:
            skipped.append(f"{name}: no results to estimate from.")
        else:
            out.append(entry)

    king = []
    for name, el in section.elements.items():
        if isinstance(el, CombiWallInput) and name in sheets:
            f = next((s.frame for s in sheets[name].values() if not s.frame.empty), None)
            if f is not None:
                king += [
                    (x, y, el.tube_diameter / 2000)
                    for x, y in f[["X", "Y"]].round(2).drop_duplicates().to_numpy()
                ]
    supports = _supports(sheets, section)
    for name, el in section.elements.items():
        own = sheets.get(name) or {}
        own = {c: s for c, s in own.items() if not s.frame.empty}
        if not own:
            if isinstance(el, PileInput | CombiWallInput | SheetPileInput | SlabInput | BeamInput):
                skipped.append(f"{name}: no results in the workbook.")
            continue
        try:
            if isinstance(el, PileInput):
                add(name, _pile(name, el, settings, ds, own, axes.get(name), designed.get(name)))
            elif isinstance(el, CombiWallInput):
                add(name, _combi(name, el, settings, ds, own, axes.get(name)))
            elif isinstance(el, SheetPileInput):
                add(name, _spw(name, el, ds, own, king))
            elif isinstance(el, SlabInput | BeamInput):
                entry = _plate(name, el, settings, ds, own, axes.get(name), supports)
                if entry is None:
                    skipped.append(
                        f"{name}: no strip with two supports under it, or its directions are not known from "
                        "the workbook check."
                    )
                else:
                    out.append(entry)
        except ValueError as e:
            skipped.append(f"{name}: {e}")
    return {
        "estimate": True,
        "note": ESTIMATE,
        "settings": ds.model_dump(mode="json"),
        "elements": out,
        "skipped": skipped,
    }
