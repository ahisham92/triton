"""Run the element designs of a project against its checked workbook."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..forces import scale_forces
from ..importer import SheetData
from ..project import PileInput, Project, _now
from ..validation import ImportResult
from .piles import design_pile


def factored_elements(project: Project, workbook: ImportResult) -> dict[str, dict[str, SheetData]]:
    """element -> combination -> sheet, with the project's load multipliers applied to the forces."""
    out = {}
    for element, combos in workbook.elements().items():
        out[element] = {}
        for combo, sheet in combos.items():
            f = project.factor_for(sheet.name)
            out[element][combo] = sheet if f == 1.0 else replace(sheet, frame=scale_forces(sheet.frame, f))
    return out


def run_piles(project: Project, workbook: ImportResult) -> dict[str, Any]:
    sheets = factored_elements(project, workbook)
    known = {s.name for s in workbook.sheets}
    missing = sorted({n for r in project.load_factors for n in r.sheets} - known)
    results, skipped = [], []
    for name, element in project.elements.items():
        if not isinstance(element, PileInput):
            continue
        if name not in sheets:
            skipped.append(f"{name}: no usable sheets in the workbook.")
            continue
        d = design_pile(name, element, project.design, sheets[name])
        factored = [
            f"{c} ×{project.factor_for(s.name):g}"
            for c, s in sheets[name].items()
            if project.factor_for(s.name) != 1.0
        ]
        if factored:
            d.notes.insert(0, "Load multipliers applied: " + ", ".join(factored) + ".")
        results.append(d.to_dict())
    if missing:
        skipped.append(f"Load multiplier sheets not in the workbook: {', '.join(missing)}.")
    return {"run_at": _now(), "piles": results, "skipped": skipped}
