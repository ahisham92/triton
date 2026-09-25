"""The deformed shape of a section's whole structure, one load combination at a time, for the 3D view.

Every pile and king pile (each plan position, not only the one that moves most), the sheet pile wall
in 1 m strips, and the deck's plates in 1 m strips, from the displacement estimate of the Design tab
(triton/design/deflection.py: curvature M / EI integrated twice, with its toe condition, stiffness and
long-term settings). It is that estimate drawn together, so it carries the same limits: it is not a
Plaxis displacement result, and the soil's own movement is not in it.

* Piles and king piles: M3 moves the member in its local 2 direction and M2 in its local 3, which the
  workbook check found as X or Y.
* Sheet pile wall: M11 per metre moves it across the wall. Its sign is set so its head moves the same
  way as the combi wall's king piles beside it (they are one wall), where there is a combi wall.
* Deck (slabs and beams): moved sideways by the mean of the pile heads' movement (the deck is stiff in
  its plane), and bent vertically between the piles and king piles under each 1 m strip, as the
  estimate's strips (sagging down).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .axes import sag_factor
from .design import deflection as D
from .design.combi import combi_section
from .design.runner import factored_elements
from .design.sheet_piles import E_STEEL, normalise
from .design.sheet_piles import section as az_section
from .design.tube import KE
from .forces import design_forces
from .importer import LABEL
from .project import (
    BeamInput,
    CombiWallInput,
    DesignSettings,
    PileInput,
    Section,
    SheetPileInput,
    SlabInput,
    with_project_grades,
)
from .validation import ImportResult

MAX_POINTS = 60  # per member curve sent to the page


def _thin(rows: list[list[float]]) -> list[list[float]]:
    if len(rows) <= MAX_POINTS:
        return rows
    idx = np.unique(np.linspace(0, len(rows) - 1, MAX_POINTS).round().astype(int))
    return [rows[i] for i in idx]


def combinations(section: Section, workbook: ImportResult) -> list[str]:
    """The combinations with moments in any pile, wall or plate, in the section's order."""
    sheets = factored_elements(section.model_copy(update={"load_factors": []}), workbook)
    seen: set[str] = set()
    for own in sheets.values():
        for c, s in own.items():
            if not s.frame.empty and {"M_2", "M_3", "M_11"} & set(s.frame.columns):
                seen.add(c)
    order = [c for c in section.combinations if c in seen]
    return order + sorted(seen - set(order))


def _global(axes: dict | None) -> tuple[str, str]:
    """Global axes of a beam element's local 2 and 3 (X, Y when not found)."""
    local = (axes or {}).get("local") or {}
    two, three = local.get("2"), local.get("3")
    if two in ("X", "Y") and three in ("X", "Y") and two != three:
        return two, three
    return "X", "Y"


def shapes(
    settings: DesignSettings,
    section: Section,
    workbook: ImportResult,
    combination: str,
    results: dict | None = None,
) -> dict[str, Any]:
    ds = section.deflection
    sheets = factored_elements(section.model_copy(update={"load_factors": []}), workbook)
    axes = {a["element"]: a for a in getattr(workbook, "axes", None) or []}
    designed = {p["element"]: p for p in (results or {}).get("piles") or []}
    members: list[dict[str, Any]] = []
    walls: list[dict[str, Any]] = []
    plates: list[dict[str, Any]] = []
    notes: list[str] = []
    missing: list[str] = []

    def own(name: str) -> Any:
        s = (sheets.get(name) or {}).get(combination)
        return None if s is None or s.frame.empty else s

    king: list[tuple[float, float, float]] = []
    for name, el in section.elements.items():
        if isinstance(el, CombiWallInput) and (s := own(name)) is not None:
            king += [
                (x, y, el.tube_diameter / 2000)
                for x, y in s.frame[["X", "Y"]].round(2).drop_duplicates().to_numpy()
            ]
    for name, el in section.elements.items():
        s = own(name)
        if isinstance(el, PileInput | CombiWallInput | SheetPileInput | SlabInput | BeamInput) and s is None:
            missing.append(name)
            continue
        if isinstance(el, PileInput):
            members += _piles(name, el, settings, ds, s, axes.get(name), designed.get(name))
        elif isinstance(el, CombiWallInput):
            members += _kings(name, el, settings, ds, s, axes.get(name))
        elif isinstance(el, SheetPileInput):
            w = _wall(name, el, ds, s, king)
            if w:
                walls.append(w)
    # The sheet pile wall moves with the king piles beside it.
    kings = [m for m in members if m["kind"] == "combi_wall"]
    for w in walls:
        if not kings or not w["strips"]:
            continue
        ax = w["across"]
        k = 1 if ax == "X" else 2
        kh = float(np.mean([m["points"][-1][k] for m in kings]))
        wh = float(np.mean([st["points"][-1][1] for st in w["strips"]]))
        if kh * wh < 0:
            for st in w["strips"]:
                st["points"] = [[z, -u] for z, u in st["points"]]
            notes.append(f"{w['element']}: turned to move with the combi wall's king piles (one wall).")
    heads = [m["points"][-1] for m in members]
    shift = (
        [float(np.mean([h[1] for h in heads])), float(np.mean([h[2] for h in heads]))]
        if heads
        else [0.0, 0.0]
    )
    supports = D._supports(sheets, section)
    for name, el in section.elements.items():
        if isinstance(el, SlabInput | BeamInput) and (s := own(name)) is not None:
            p = _plate(name, el, settings, ds, s, axes.get(name), supports)
            if p:
                plates.append(p)
    peak = max(
        [abs(v) for m in members for p in m["points"] for v in p[1:]]
        + [abs(p[1]) for w in walls for st in w["strips"] for p in st["points"]]
        + [abs(p[1]) for pl in plates for st in pl["strips"] for p in st["points"]]
        + [abs(v) for v in shift]
        + [0.0]
    )
    return {
        "estimate": True,
        "note": D.ESTIMATE,
        "combination": combination,
        "settings": ds.model_dump(mode="json"),
        "members": members,
        "walls": walls,
        "plates": plates,
        "deck_shift_mm": [round(v, 2) for v in shift],
        "max_mm": round(peak, 2),
        "missing": missing,
        "notes": notes,
    }


def _piles(name, pile, settings, ds, sheet, axes, result) -> list[dict[str, Any]]:
    pile = with_project_grades(pile, settings.materials, settings.durability)
    cols = [c for c in ("N", "Q_12", "Q_13", "M_2", "M_3") if c in sheet.frame.columns]
    if not {"M_2", "M_3"} <= set(cols):
        return []
    f = design_forces(sheet.frame, sheet.parsed.spec, cols)
    if LABEL in sheet.frame.columns:
        f[LABEL] = sheet.frame.loc[f.index, LABEL]
    if "N" not in f.columns:
        f["N"] = 0.0
    if pile.head_level is not None:
        f = f[f["Z"] <= pile.head_level + 1e-6]
    found, _ = D._members(f, ["N", "M_2", "M_3"])
    d = pile.diameter
    e_c, _ = D._e_concrete(pile.concrete, settings, ds)
    ei = e_c * np.pi * d**4 / 64 * 1e-9
    cracked = ds.stiffness == "cracked"
    zones = D._cages(pile, settings, result)[0] if cracked else []
    fctm = D.concrete(pile.concrete).fctm
    two, _three = _global(axes)
    out = []
    for x, y, g in found:
        z = g["Z"].to_numpy(float)
        m2, m3 = g["M_2"].to_numpy(float), g["M_3"].to_numpy(float)
        if cracked:
            m = np.hypot(m2, m3)
            k = D._cracked_kappa(d, e_c, fctm, g["N"].to_numpy(float), m, z, zones)
            per = np.where(m > 1e-9, k / np.maximum(m, 1e-9), 1 / ei)
        else:
            per = np.full(len(z), 1 / ei)
        hold, _ = D._hold(ds, None, z[0], z[-1])
        out.append(
            _member(
                name, "pile", x, y, z, D.integrate(z, m3 * per, hold), D.integrate(z, m2 * per, hold), two
            )
        )
    return out


def _kings(name, wall, settings, ds, sheet, axes) -> list[dict[str, Any]]:
    wall = with_project_grades(wall, settings.materials, settings.durability)
    frame = sheet.frame
    if not {"M_2", "M_3"} <= set(frame.columns):
        return []
    f = frame[[c for c in ("X", "Y", "Z", "M_2", "M_3", "Q_12", "Q_13", LABEL) if c in frame.columns]]
    if wall.top_level_to_ignore is not None:
        f = f[f["Z"] <= wall.top_level_to_ignore + 1e-6]
    found, _ = D._members(f, ["M_2", "M_3"])
    sec = combi_section(wall)
    e_c, _ = D._e_concrete(wall.concrete, settings, ds)
    ke = KE if ds.stiffness == "cracked" else 1.0
    steel = sec.e_steel * sec.i_steel
    filled = steel + ke * e_c * 1e3 * sec.i_concrete
    two, _three = _global(axes)
    out = []
    for x, y, g in found:
        z = g["Z"].to_numpy(float)
        per = 1 / np.where(z >= wall.concrete_bottom_level - 1e-9, filled, steel)
        hold, _ = D._hold(ds, wall.firm_soil_level, z[0], z[-1])
        w3 = D.integrate(z, g["M_3"].to_numpy(float) * per, hold)
        w2 = D.integrate(z, g["M_2"].to_numpy(float) * per, hold)
        out.append(_member(name, "combi_wall", x, y, z, w3, w2, two))
    return out


def _member(name, kind, x, y, z, w3, w2, two) -> dict[str, Any]:
    """[z, u_X, u_Y] in mm down the member, the toe first."""
    ux, uy = (w3, w2) if two == "X" else (w2, w3)
    rows = [
        [round(float(a), 3), round(float(b) * 1e3, 2), round(float(c) * 1e3, 2)]
        for a, b, c in zip(z, ux, uy, strict=True)
    ]
    return {"element": name, "kind": kind, "x": float(x), "y": float(y), "points": _thin(rows)}


def _wall(name, wall, ds, sheet, king) -> dict[str, Any] | None:
    if "M_11" not in sheet.frame.columns:
        return None
    f = sheet.frame[["X", "Y", "Z", "M_11"]].dropna()
    for x, y, r in king:
        f = f[np.hypot(f["X"] - x, f["Y"] - y) >= r - 1e-6]
    if f.empty:
        return None
    along = "X" if np.ptp(f["X"].to_numpy(float)) > np.ptp(f["Y"].to_numpy(float)) else "Y"
    across = "Y" if along == "X" else "X"
    at = float(f[across].median())
    sec = az_section(normalise(wall.section_name))
    ei = E_STEEL * sec.inertia * 1e-5
    strips = []
    for strip, g in f.groupby(np.floor(f[along] / D.STRIP)):
        g = g.groupby((g["Z"] / D.LEVEL).round() * D.LEVEL)["M_11"].mean().sort_index()
        if len(g) < 4:
            continue
        z = g.index.to_numpy(float)
        hold, _ = D._hold(ds, wall.firm_soil_level, z[0], z[-1])
        w = D.integrate(z, g.to_numpy(float) / ei, hold)
        rows = [[round(float(a), 3), round(float(b) * 1e3, 2)] for a, b in zip(z, w, strict=True)]
        strips.append({"at": round(float((strip + 0.5) * D.STRIP), 3), "points": _thin(rows)})
    return {"element": name, "along": along, "across": across, "position": round(at, 3), "strips": strips}


def _plate(name, element, settings, ds, sheet, axes, supports) -> dict[str, Any] | None:
    local = (axes or {}).get("local") or {}
    if not {local.get("1"), local.get("2")} <= {"X", "Y"}:
        return None
    frame = sheet.frame
    if not {"M_11", "M_22"} <= set(frame.columns):
        return None
    element = with_project_grades(element, settings.materials, settings.durability)
    f = frame[["X", "Y", "Z", "M_11", "M_22"]].dropna()
    h = element.thickness if isinstance(element, SlabInput) else element.depth
    e_c, _ = D._e_concrete(element.concrete, settings, ds)
    ei = e_c * h**3 / 12 * 1e-6
    sag, _ = sag_factor(settings.plate_positive_moment, axes)
    # Slabs span across the quay between the pile rows (local 1); beams along their length.
    if isinstance(element, SlabInput):
        k = "1"
    else:
        span = {a: float(np.ptp(f[a].to_numpy(float))) for a in ("X", "Y")}
        k = "1" if span[local["1"]] >= span[local["2"]] else "2"
    along = local[k]
    col = f"M_{k}{k}"
    across = "Y" if along == "X" else "X"
    z = float(f["Z"].median())
    strips = []
    for strip, g in f.groupby(np.floor(f[across] / D.STRIP)):
        centre = (strip + 0.5) * D.STRIP
        near = (
            supports[np.abs(supports[:, 1 if across == "Y" else 0] - centre) <= D.STRIP]
            if len(supports)
            else []
        )
        s_all = g[along].to_numpy(float)
        held = D._strip_supports(
            near[:, 0 if along == "X" else 1] if len(near) else [], s_all.min(), s_all.max()
        )
        if len(held) < 2:
            continue
        m = g.groupby((g[along] / D.STEP).round() * D.STEP)[col].mean().sort_index()
        if len(m) < 3:
            continue
        s, w = D.between_supports(m.index.to_numpy(float), sag * m.to_numpy(float) / ei, held)
        rows = [[round(float(a), 3), round(float(b) * 1e3, 2)] for a, b in zip(s, w, strict=True)]
        strips.append({"at": round(float(centre), 3), "points": _thin(rows)})
    if not strips:
        return None
    return {"element": name, "along": along, "across": across, "z": round(z, 3), "strips": strips}
