"""A pile's top level (the slab or beam soffit) when it is left empty, from the Plaxis model.

The pile's results run up to the plate it is connected to, and its topmost node sits inside that
connection, where the moment is largest. The design reads a pile's forces at its top level (plus the
distance into the connection in Design settings), so an empty top level reads them at that node.
When it is empty, the top level is taken as the level of the plate over the pile (the pile's topmost
node) less the thickness of that slab or the depth of that beam: the plate taken at the top of the
concrete. That is an assumption: set the real soffit on the pile.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..project import BeamInput, PileInput, Section, SlabInput
from ..validation import ImportResult

NEAR = 0.6  # m, a plate node this close in plan is over the pile
ON = 0.5  # m, the pile's topmost node this close to the plate's level ends at it


def _frame(sheets: dict) -> Any:
    frames = (s.frame for s in sheets.values())
    return next((f for f in frames if not f.empty and {"X", "Y", "Z"} <= set(f.columns)), None)


def assumed_heads(section: Section, workbook: ImportResult) -> tuple[Section, dict[str, str]]:
    """The section with the empty pile top levels filled in where the plate over the pile is known,
    and a note for each pile filled in."""
    elements = workbook.elements()
    plates = []
    for name, el in section.elements.items():
        f = _frame(elements.get(name) or {})
        if isinstance(el, SlabInput | BeamInput) and f is not None:
            h = el.thickness if isinstance(el, SlabInput) else el.depth
            pts = f[["X", "Y", "Z"]].round(2).drop_duplicates().to_numpy(float)
            plates.append((name, h, pts))
    if not plates:
        return section, {}
    changed, notes = {}, {}
    for name, el in section.elements.items():
        if not isinstance(el, PileInput) or el.head_level is not None:
            continue
        f = _frame(elements.get(name) or {})
        if f is None:
            continue
        top = f.groupby([f["X"].round(1), f["Y"].round(1)])["Z"].idxmax()
        tops = f.loc[top, ["X", "Y", "Z"]].to_numpy(float)
        found: dict[str, list[float]] = {}
        for x, y, z in tops:
            best = None
            for plate, h, pts in plates:
                d = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
                i = int(np.argmin(d))
                if d[i] <= NEAR and abs(pts[i, 2] - z) <= ON and (best is None or d[i] < best[0]):
                    best = (d[i], plate, h, pts[i, 2])
            if best is not None:
                found.setdefault(best[1], []).append(best[3] - best[2] / 1e3)
        if not found:
            continue
        plate = max(found, key=lambda p: len(found[p]))
        level = round(float(np.mean(found[plate])), 2)
        h = next(h for p, h, _ in plates if p == plate)
        changed[name] = el.model_copy(update={"head_level": level})
        notes[name] = (
            f"Top level not set: taken as {level:g} m, the {plate} level in the Plaxis model less its "
            f"{h:g} mm (assumed: the plate at the top of the concrete). Set the real soffit on the pile; "
            "the forces are read there."
        )
    if not changed:
        return section, {}
    return section.model_copy(update={"elements": {**section.elements, **changed}}), notes
