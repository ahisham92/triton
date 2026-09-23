"""Run the element designs of a project against its checked workbook."""

from __future__ import annotations

from typing import Any

from ..project import PileInput, Project, _now
from ..validation import ImportResult
from .piles import design_pile


def run_piles(project: Project, workbook: ImportResult) -> dict[str, Any]:
    sheets = workbook.elements()
    results, skipped = [], []
    for name, element in project.elements.items():
        if not isinstance(element, PileInput):
            continue
        if name not in sheets:
            skipped.append(f"{name}: no usable sheets in the workbook.")
            continue
        results.append(design_pile(name, element, project.design, sheets[name]).to_dict())
    return {"run_at": _now(), "piles": results, "skipped": skipped}
