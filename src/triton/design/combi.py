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
from .tube import Tube, check_tube, tube_loads


def combi_section(wall: CombiWallInput) -> CombiSection:
    return CombiSection(
        wall.tube_diameter / 1e3,
        wall.tube_thickness / 1e3,
        wall.corrosion_loss / 1e3,
        e_steel=210e6,
        e_concrete=concrete(wall.concrete).ecm * 1e3,
        concrete_bottom_level=wall.concrete_bottom_level,
    )


def tube_zones(wall: CombiWallInput) -> list[tuple[float, float, Tube]]:
    """(top, bottom, tube) down the king pile from the corrosion zones, or one tube for the whole length."""

    def tube(outside: float, inside: float = 0.0, name: str = "") -> Tube:
        return Tube(
            wall.tube_diameter,
            wall.tube_thickness,
            outside,
            wall.steel,
            wall.fabrication_class,
            inside,
            fy_set=wall.tube_fy,
            name=name,
        )

    if not wall.corrosion_zones:
        return [(math.inf, -math.inf, tube(wall.corrosion_loss))]
    out, top = [], math.inf
    # Zones saved before they had names take the office sheet's name for the same level and losses.
    office = {(o.bottom_level, o.outside, o.inside): o.name for o in _office_tube_zones()}
    for z in wall.corrosion_zones:
        name = z.name or office.get((z.bottom_level, z.outside, z.inside), "")
        out.append((top, z.bottom_level, tube(z.outside, z.inside, name)))
        top = z.bottom_level
    return out


def infill_as_pile(wall: CombiWallInput) -> PileInput:
    return PileInput(
        diameter=wall.tube_diameter - 2 * wall.tube_thickness,
        cover=wall.cover,
        link_diameter=wall.link_diameter,
        concrete=wall.concrete,
        bar_count=wall.bar_count,
        head_level=wall.top_level_to_ignore,
        # The tube is a permanent casing over the whole infill: no crack width check, so the cracks
        # do not drive the infill cage either.
        casing=Casing(
            role="crack_only", top_level=1000.0, bottom_level=-1000.0, thickness=wall.tube_thickness
        ),
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
    sec = combi_section(wall)
    share = sec.steel_share
    bottom = wall.concrete_bottom_level

    infill_sheets = {}
    for combo, sheet in sheets.items():
        f = sheet.frame
        f = f[f["Z"] >= bottom - 1e-9]
        if not f.empty:
            infill_sheets[combo] = replace(sheet, frame=scale_forces(f, 1 - share))
    infill = design_pile(name, infill_as_pile(wall), settings, infill_sheets, cage, standard).to_dict()
    infill["notes"] = [
        n.replace("into the slab", "into the front beam")
        for n in infill["notes"]
        if not n.startswith("No pile top level")
    ]
    infill["head_name"] = "the front beam"
    if infill.get("cracks"):
        infill["cracks"]["casing"] = "The tube is a permanent casing: no crack width check."
        infill["cracks"].pop("note", None)
    for station in infill.get("governing_sets") or []:
        station["qp"] = placeholder_sets()  # the tube is a casing: no crack width check

    zones = tube_zones(wall)
    above = settings.results_into_connection / 1e3
    tube_share = 1.0 if wall.tube_share == "all" else share
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
    steel = check_tube(
        zones, loads, method=wall.tube_check, gamma_m0=pf.gamma_m0, gamma_m1=pf.gamma_m1, column=column
    )
    steel["governing_sets"] = steel_sets(loads, "beam")

    split = (
        f"Actions where the tube is filled: {share:.0%} to the steel tube and {1 - share:.0%} to the "
        f"infill (E·I, corroded tube, Ecm {conc.ecm / 1e3:.1f} GPa). Below {bottom:g} m the tube carries "
        "everything."
    )
    if wall.tube_share == "all":
        split = (
            f"The tube carries every action along its length (checked elastically, class 4 effective "
            f"properties); the infill is still designed for its E·I share, {1 - share:.0%}."
        )
    notes = [
        split,
        "The tube is a permanent casing, so the infill has no crack width check.",
    ]
    if wall.top_level_to_ignore is None:
        notes.append("No king pile top level is set, so results inside the front beam are included.")

    u = [x for x in (infill.get("utilisation"), steel.get("utilisation")) if x is not None]
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
        "top_level_set": wall.top_level_to_ignore is not None,
        "utilisation": max(u) if len(u) == 2 else None,
        "passed": bool(infill["passed"] and steel["passed"]),
        "infill": infill,
        "tube": steel,
        "notes": notes,
    }
