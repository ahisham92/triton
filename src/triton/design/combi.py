"""Combi wall king piles: reinforced concrete infill plus the steel tube.

Where the tube is filled (from the front beam soffit down to the infill bottom
level) every straining action is shared between the tube and the infill in
proportion to E·I, with the corroded tube (``forces.CombiSection``). Below the
infill the tube carries everything.

* The infill is a circular reinforced concrete section of the tube's inner
  diameter, designed like a pile (N–M cage, reductions down the length, links).
  The tube is a permanent casing, so there is no crack width check.
* The tube is checked with ``tube.check_tube``.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import numpy as np

from ..forces import CombiSection, scale_forces
from ..importer import SheetData
from ..materials import concrete
from ..project import (
    Casing,
    CombiWallInput,
    DesignSettings,
    PileInput,
    UserCage,
    _office_tube_zones,
    with_project_grades,
)
from .governing import placeholder_sets, steel_sets
from .piles import design_pile
from .sheet_piles import UF_CAP
from .tube import Tube, check_tube, steel_gone, tube_loads, zone_index


def split_loss(wall: CombiWallInput) -> float:
    """The loss (mm) off the tube's diameter for the E·I split where one loss holds over the filled length:
    the single loss (walls without land sides typed, or a tube eaten by corrosion)."""
    return wall.corrosion_loss


def _section(wall: CombiWallInput, loss: float) -> CombiSection:
    return CombiSection(
        wall.tube_diameter / 1e3,
        wall.tube_thickness / 1e3,
        loss / 1e3,
        e_steel=210e6,
        e_concrete=concrete(wall.concrete).ecm * 1e3,
        concrete_bottom_level=wall.concrete_bottom_level,
    )


def combi_section(wall: CombiWallInput) -> CombiSection:
    """The section of the filled zone where the infill takes most (the largest average loss)."""
    shares = share_zones(wall)
    return min((s for s in shares if s[3] is not None), key=lambda s: s[2].steel_share, default=shares[0])[2]


ShareZones = list[tuple[float, float, CombiSection, "str | None"]]  # (top, bottom, section, zone name)


def share_zones(wall: CombiWallInput) -> ShareZones:
    """(top, bottom, section, name) of the E·I split down the filled length, one per corrosion zone: each
    zone's average of the sea and land sides, as the office sheets (splash 4.5 / 0 gives 2.25 and the
    infill 67%). Without land sides typed (walls stored before) or with the tube gone, the single loss over
    the whole length, as before. Zones wholly below the infill come back with no name."""
    gone = wall.corrosion_loss is not None and wall.corrosion_loss >= wall.tube_thickness
    if gone or not wall.corrosion_zones or all(z.land is None for z in wall.corrosion_zones):
        return [(math.inf, -math.inf, _section(wall, wall.corrosion_loss), "")]
    out, top = [], math.inf
    for (_, _, tube), z in zip(tube_zones(wall), wall.corrosion_zones, strict=True):
        loss = wall.corrosion_loss if z.land is None else z.mean_outside
        filled = top > wall.concrete_bottom_level + 1e-9
        out.append((top, z.bottom_level, _section(wall, loss), tube.name if filled else None))
        top = z.bottom_level
    return out


def steel_share_at(zones: ShareZones, z) -> np.ndarray:
    """The steel's E·I share at each level (the zone's; the last zone carries on to the toe)."""
    shares = np.array([s[2].steel_share for s in zones])
    return shares[zone_index(zones, np.asarray(z, dtype=float))]


def tube_zones(wall: CombiWallInput) -> list[tuple[float, float, Tube]]:
    """(top, bottom, tube) down the king pile from the corrosion zones, or one tube for the whole length."""

    def tube(outside: float, inside: float = 0.0, name: str = "", mean: float | None = None) -> Tube:
        return Tube(
            wall.tube_diameter,
            wall.tube_thickness,
            outside,
            wall.steel,
            wall.fabrication_class,
            inside,
            fy_set=wall.tube_fy,
            name=name,
            mean_outside=mean,
        )

    if not wall.corrosion_zones:
        return [(math.inf, -math.inf, tube(wall.corrosion_loss))]
    out, top = [], math.inf
    # Zones saved before they had names take the office sheet's name for the same level and losses.
    office = {(o.bottom_level, o.outside, o.inside): o.name for o in _office_tube_zones()}
    for z in wall.corrosion_zones:
        name = z.name or office.get((z.bottom_level, z.outside, z.inside), "")
        mean = None if z.land is None else z.mean_outside
        out.append((top, z.bottom_level, tube(z.max_outside, z.inside, name, mean)))
        top = z.bottom_level
    return out


def tube_gone(wall: CombiWallInput) -> str | None:
    """The note when corrosion eats the tube before the end of the design life, else None."""
    return steel_gone(wall.corrosion_loss, wall.tube_thickness, "tube", "infill")


def infill_as_pile(wall: CombiWallInput) -> PileInput:
    # A tube eaten by corrosion is no casing: the infill's crack width is checked.
    gone = tube_gone(wall)
    return PileInput(
        diameter=wall.tube_diameter - 2 * wall.tube_thickness,
        cover=wall.cover,
        link_diameter=wall.link_diameter,
        concrete=wall.concrete,
        bar_count=wall.bar_count,
        head_level=wall.top_level_to_ignore,
        # The tube is a permanent casing over the whole infill: no crack width check, so the cracks
        # do not drive the infill cage either.
        casing=None
        if gone
        else Casing(role="crack_only", top_level=1000.0, bottom_level=-1000.0, thickness=wall.tube_thickness),
    )


def design_combi_wall(
    name: str,
    wall: CombiWallInput,
    settings: DesignSettings,
    sheets: dict[str, SheetData],
    cage: UserCage | None = None,
    standard: bool = False,
) -> dict[str, Any]:
    wall = with_project_grades(wall, settings.materials, settings.durability)
    gone = tube_gone(wall)
    split = share_zones(wall)
    share = combi_section(wall).steel_share  # the smallest over the filled zones; 0 when the tube is gone
    bottom = wall.concrete_bottom_level

    infill_sheets = {}
    for combo, sheet in sheets.items():
        f = sheet.frame
        f = f[f["Z"] >= bottom - 1e-9]
        if not f.empty:
            infill_sheets[combo] = replace(sheet, frame=scale_forces(f, 1 - steel_share_at(split, f["Z"])))
    infill = design_pile(name, infill_as_pile(wall), settings, infill_sheets, cage, standard).to_dict()
    infill["notes"] = [
        n.replace("into the slab", "into the front beam")
        for n in infill["notes"]
        if not n.startswith("No pile top level")
    ]
    infill["head_name"] = "the front beam"
    if infill.get("cracks") and not gone:
        infill["cracks"]["casing"] = "The tube is a permanent casing: no crack width check."
        infill["cracks"].pop("note", None)
    for station in [] if gone else infill.get("governing_sets") or []:
        station["qp"] = placeholder_sets()  # the tube is a casing: no crack width check

    above = settings.results_into_connection / 1e3
    tube_share = 1.0 if wall.tube_share == "all" else (lambda z: steel_share_at(split, z))
    loads = tube_loads(sheets, tube_share, bottom, wall.top_level_to_ignore, above)
    pf = settings.partial_factors
    conc = concrete(wall.concrete)
    column = None
    if not loads.empty:
        column = {
            "top": wall.top_level_to_ignore
            if wall.top_level_to_ignore is not None
            else float(loads["Z"].max()),
            "toe": float(loads["Z"].min()),
            "filled_from": bottom,
            "infill_diameter": wall.tube_diameter - 2 * wall.tube_thickness,
            "fck": conc.fck,
            "ecm": conc.ecm,
            "factor": wall.buckling_length_factor,
            "curve": wall.buckling_curve,
            "firm": wall.firm_soil_level,
            "column_ei": wall.column_ei * 1e9 if wall.column_ei else None,  # kN·m² to N·mm²
        }
    if gone:
        # No tube to check. Below the infill only the tube carried the actions, so any action left
        # there has nothing to carry it: unsafe by far.
        below = loads[~loads["filled"].astype(bool)] if not loads.empty else loads
        acts = not below.empty and bool(
            ((below["N"].abs() > 1) | (below["V"].abs() > 1) | (below["M"].abs() > 1)).any()
        )
        steel = {
            "utilisation": UF_CAP if acts else None,
            "passed": not acts,
            "notes": [gone],
            # The tube as rolled, for the reports and quantities: nothing of it is left to count.
            "section": {
                "diameter_mm": wall.tube_diameter,
                "thickness_mm": wall.tube_thickness,
                "corrosion_mm": wall.corrosion_loss,
                "corroded_diameter_mm": wall.tube_diameter - 2 * wall.tube_thickness,
                "corroded_thickness_mm": 0.0,
                "grade": wall.steel,
                "class_unfilled": None,
            },
        }
        if acts:
            steel["notes"].append(
                f"Below the infill ({bottom:g} m) the tube carried the actions alone: with it gone nothing "
                "is left to carry them there."
            )
    else:
        zones = tube_zones(wall)
        steel = check_tube(
            zones, loads, method=wall.tube_check, gamma_m0=pf.gamma_m0, gamma_m1=pf.gamma_m1, column=column
        )
    steel["governing_sets"] = steel_sets(loads, "beam")

    filled = [(s[3], s[2].steel_share) for s in split if s[3] is not None]
    by_zone = len(filled) > 1 and len({round(v, 4) for _, v in filled}) > 1
    each = ", ".join(f"{n or 'zone ' + str(i + 1)} {1 - v:.0%}" for i, (n, v) in enumerate(filled))
    split_note = (
        f"Actions where the tube is filled: {share:.0%} to the steel tube and {1 - share:.0%} to the "
        f"infill (E·I, corroded tube, Ecm {conc.ecm / 1e3:.1f} GPa). Below {bottom:g} m the tube carries "
        "everything."
    )
    if by_zone:
        split_note = (
            f"Actions where the tube is filled are shared by E·I zone by zone, with each zone's corroded "
            f"tube (average of the sea and land sides, Ecm {conc.ecm / 1e3:.1f} GPa); the infill takes "
            f"{each}. Below {bottom:g} m the tube carries everything."
        )
    if wall.tube_share == "all":
        split_note = (
            f"The tube carries every action along its length (checked elastically, class 4 effective "
            f"properties); the infill is still designed for its E·I share, "
            f"{each if by_zone else f'{1 - share:.0%}'}."
        )
    notes = [
        split_note,
        "The tube is a permanent casing, so the infill has no crack width check.",
    ]
    if gone:
        notes = [gone, *steel["notes"][1:]]
    if wall.top_level_to_ignore is None:
        notes.append("No king pile top level is set, so results inside the front beam are included.")

    u = [x for x in (infill.get("utilisation"), steel.get("utilisation")) if x is not None]
    # With the tube gone and nothing below the infill, the infill alone is the design.
    whole = max(u) if len(u) == 2 else (infill.get("utilisation") if gone else None)
    positions = infill.get("positions") or []
    count = wall.count or len(positions) or 1
    infill["count"] = count
    if (infill.get("steel") or {}).get("total_kg") is not None:
        infill["steel"]["element_total_t"] = round(infill["steel"]["total_kg"] * count / 1000, 2)
    return {
        "element": name,
        "kind": "combi_wall",
        "count": count,
        "infill_bottom_level": bottom,
        "positions": positions,
        "steel_share": round(share, 3),
        # The steel's share in each filled zone, top down (one entry when one loss holds throughout).
        "steel_shares": [
            {
                "zone": n or "",
                "top": None if math.isinf(t) else t,
                "bottom": None if math.isinf(b) else b,
                "share": round(sec.steel_share, 3),
            }
            for t, b, sec, n in split
            if n is not None
        ],
        "tube_gone": bool(gone),
        "top_level_set": wall.top_level_to_ignore is not None,
        "utilisation": whole,
        "passed": bool(infill["passed"] and steel["passed"]),
        "infill": infill,
        "tube": steel,
        "notes": notes,
    }


def zone_share(w: dict[str, Any], z: float) -> float:
    """The steel's E·I share at level ``z`` of a designed combi wall: its zone's; the last zone carries on."""
    zones = w.get("steel_shares") or []
    for s in zones:
        if s["bottom"] is None or z >= s["bottom"] - 1e-9:
            return s["share"]
    return zones[-1]["share"] if zones else w["steel_share"]


# The combi wall is designed and mapped as one element, but its two parts are reported as two
# elements in every summary, so the part that fails shows by name.
PARTS = (("infill", "concrete infill"), ("steel", "steel"))


def part_name(wall: str, part: str) -> str:
    return f"{wall} – {dict(PARTS)[part]}"


def parts(w: dict[str, Any]) -> list[dict[str, Any]]:
    """The concrete infill and the steel tube of a designed combi wall, each as its own element.

    Works on results stored before the split: both parts were always designed separately."""
    inf, tube = w.get("infill") or {}, w.get("tube") or {}
    g = tube.get("governing") or {}
    return [
        {
            "element": part_name(w["element"], "infill"),
            "wall": w["element"],
            "part": "infill",
            "utilisation": inf.get("utilisation"),
            "passed": bool(inf.get("passed")),
            "governs": "N–M" if inf.get("utilisation") is not None else None,
        },
        {
            "element": part_name(w["element"], "steel"),
            "wall": w["element"],
            "part": "steel",
            "utilisation": tube.get("utilisation"),
            "passed": bool(tube.get("passed")),
            "governs": g.get("check"),
        },
    ]


def failing_parts(w: dict[str, Any]) -> list[str]:
    return [p["element"] for p in parts(w) if not p["passed"]]
