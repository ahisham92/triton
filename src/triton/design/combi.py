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

from dataclasses import replace
from typing import Any

from ..forces import CombiSection, scale_forces
from ..importer import SheetData
from ..materials import concrete
from ..project import CombiWallInput, DesignSettings, PileInput
from .governing import placeholder_sets
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


def infill_as_pile(wall: CombiWallInput) -> PileInput:
    return PileInput(
        diameter=wall.tube_diameter - 2 * wall.tube_thickness,
        cover=wall.cover,
        link_diameter=wall.link_diameter,
        concrete=wall.concrete,
        bar_count=wall.bar_count,
        head_level=wall.top_level_to_ignore,
    )


def design_combi_wall(
    name: str, wall: CombiWallInput, settings: DesignSettings, sheets: dict[str, SheetData]
) -> dict[str, Any]:
    sec = combi_section(wall)
    share = sec.steel_share
    bottom = wall.concrete_bottom_level

    infill_sheets = {}
    for combo, sheet in sheets.items():
        f = sheet.frame
        f = f[f["Z"] >= bottom - 1e-9]
        if not f.empty:
            infill_sheets[combo] = replace(sheet, frame=scale_forces(f, 1 - share))
    infill = design_pile(name, infill_as_pile(wall), settings, infill_sheets).to_dict()
    infill["notes"] = [
        n.replace("into the slab", "into the front beam")
        for n in infill["notes"]
        if not n.startswith("No pile top level")
    ]
    infill["head_name"] = "the front beam"
    for station in infill.get("governing_sets") or []:
        station["qp"] = placeholder_sets()  # the tube is a casing: no crack width check

    tube = Tube(
        wall.tube_diameter, wall.tube_thickness, wall.corrosion_loss, wall.steel, wall.fabrication_class
    )
    above = settings.results_into_connection / 1e3
    steel = check_tube(tube, tube_loads(sheets, share, bottom, wall.top_level_to_ignore, above))

    notes = [
        f"Actions where the tube is filled: {share:.0%} to the steel tube and {1 - share:.0%} to the "
        f"infill (E·I, corroded tube, Ecm {concrete(wall.concrete).ecm / 1e3:.1f} GPa). Below "
        f"{bottom:g} m the tube carries everything.",
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
        "utilisation": max(u) if len(u) == 2 else None,
        "passed": bool(infill["passed"] and steel["passed"]),
        "infill": infill,
        "tube": steel,
        "notes": notes,
    }
