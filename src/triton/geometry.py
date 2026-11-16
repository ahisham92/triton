"""Element geometry of a section for the 3D view, taken from the node coordinates in the workbook.

Beams (piles, combi wall king piles) become one vertical line per X, Y position; plates (deck,
beams, sheet pile wall) become the box their nodes span, which is flat in one direction.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .elements import ResultKind
from .validation import ImportResult


def section_geometry(workbook: ImportResult) -> list[dict[str, Any]]:
    return elements_geometry(workbook.elements())


def elements_geometry(elements: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """As ``section_geometry``, from element -> combination -> sheet."""
    out = []
    for name, combos in elements.items():
        spec = next((s.parsed.spec for s in combos.values() if s.parsed), None)
        frames = [
            s.frame[["X", "Y", "Z"]] for s in combos.values() if {"X", "Y", "Z"} <= set(s.frame.columns)
        ]
        if spec is None or not frames:
            continue
        f = pd.concat(frames)
        item: dict[str, Any] = {"element": name, "type": spec.type.value, "kind": spec.kind.value}
        if spec.kind is ResultKind.BEAM:
            g = f.groupby([f["X"].round(2), f["Y"].round(2)])["Z"]
            item["lines"] = [
                [x, y, round(float(hi), 2), round(float(lo), 2)]
                for (x, y), lo, hi in zip(g.min().index, g.min().to_numpy(), g.max().to_numpy(), strict=True)
            ]
        else:
            item["box"] = {a: [round(float(f[a].min()), 2), round(float(f[a].max()), 2)] for a in "XYZ"}
        out.append(item)
    return out
