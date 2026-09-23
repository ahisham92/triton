"""Run the element designs of a project against its checked workbook."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..forces import scale_forces
from ..importer import SheetData
from ..project import CombiWallInput, DesignSettings, PileInput, Section, _now
from ..validation import ImportResult
from .combi import design_combi_wall
from .piles import design_pile


def factored_elements(section: Section, workbook: ImportResult) -> dict[str, dict[str, SheetData]]:
    """element -> combination -> sheet, with the section's load multipliers applied to the forces."""
    out = {}
    for element, combos in workbook.elements().items():
        out[element] = {}
        for combo, sheet in combos.items():
            f = section.factor_for(sheet.name)
            out[element][combo] = sheet if f == 1.0 else replace(sheet, frame=scale_forces(sheet.frame, f))
    return out


def _multiplier_note(section: Section, sheets: dict[str, SheetData]) -> str | None:
    factored = [
        f"{c} ×{section.factor_for(s.name):g}" for c, s in sheets.items() if section.factor_for(s.name) != 1.0
    ]
    return "Load multipliers applied: " + ", ".join(factored) + "." if factored else None


def run_section(settings: DesignSettings, section: Section, workbook: ImportResult) -> dict[str, Any]:
    """Design the piles and combi walls of one section."""
    sheets = factored_elements(section, workbook)
    known = {s.name for s in workbook.sheets}
    missing = sorted({n for r in section.load_factors for n in r.sheets} - known)
    piles, walls, skipped = [], [], []
    for name, element in section.elements.items():
        if not isinstance(element, PileInput | CombiWallInput):
            continue
        if name not in sheets:
            skipped.append(f"{name}: no usable sheets in the workbook.")
            continue
        if isinstance(element, CombiWallInput):
            wall = design_combi_wall(name, element, settings, sheets[name])
            if note := _multiplier_note(section, sheets[name]):
                wall["notes"].insert(0, note)
            walls.append(wall)
            continue
        notes = []
        d = design_pile(name, element, settings, sheets[name])
        if note := _multiplier_note(section, sheets[name]):
            notes.append(note)
        d.notes[:0] = notes
        out = d.to_dict()
        out["count"] = element.count or len(out.get("positions") or []) or 1
        steel = out.get("steel") or {}
        if steel.get("total_kg") is not None:
            out["steel"]["element_total_t"] = round(steel["total_kg"] * out["count"] / 1000, 2)
        piles.append(out)
    if missing:
        skipped.append(f"Load multiplier sheets not in the workbook: {', '.join(missing)}.")
    return {"run_at": _now(), "piles": piles, "combi_walls": walls, "skipped": skipped}
