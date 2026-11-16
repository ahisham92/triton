"""Isolated peaks in the results of piles and the combi wall.

Finite element results sometimes show a single node whose moment is far above
the nodes either side of it. Along each pile (one X, Y position), for each
combination, a node is an isolated peak when its resultant moment is more than
``ratio`` times the larger of its neighbours' and it is at least 20% of the
largest moment in that pile, so small wiggles near zero are not flagged.

Each peak is then used as it is (``raw``), replaced by the mean of its
neighbours (``average``), or left out when the user excludes it.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from ..importer import SheetData

ACTIONS = ("N", "Q_12", "Q_13", "M_1", "M_2", "M_3")
SIGNIFICANT = 0.2


def peak_key(element: str, combination: str, node: int) -> str:
    return f"{element}|{combination}|{node}"


def find_peaks(frame: pd.DataFrame, ratio: float, top: float | None = None) -> list[tuple[Any, list[Any]]]:
    """(row label of the peak, row labels of its neighbours) for every isolated peak.

    Peaks above ``top`` (inside the slab or beam, not designed) are not reported.
    """
    if frame.empty or not {"X", "Y", "Z", "M_2", "M_3"} <= set(frame.columns):
        return []
    f = frame.drop_duplicates("Node") if "Node" in frame.columns else frame
    out = []
    for _, g in f.groupby([f["X"].round(2), f["Y"].round(2)], sort=False):
        g = g.sort_values("Z", ascending=False)
        m = np.hypot(g["M_2"].to_numpy(float), g["M_3"].to_numpy(float))
        if len(m) < 3 or m.max() <= 0:
            continue
        prev = np.r_[np.nan, m[:-1]]
        nxt = np.r_[m[1:], np.nan]
        nb = np.fmax(prev, nxt)
        hit = (m > ratio * nb) & (m >= SIGNIFICANT * m.max())
        if top is not None:
            hit &= g["Z"].to_numpy(float) <= top + 1e-9
        labels = g.index.to_list()
        for i in np.flatnonzero(hit):
            nbrs = [labels[j] for j in (i - 1, i + 1) if 0 <= j < len(labels)]
            out.append((labels[i], nbrs))
    return out


def treat_peaks(
    element: str,
    sheets: dict[str, SheetData],
    mode: str,
    ratio: float,
    excluded: set[str],
    top: float | None = None,
) -> tuple[dict[str, SheetData], list[dict[str, Any]]]:
    """Sheets with peaks averaged or left out as chosen, and the list of peaks found."""
    out, found = {}, []
    for combo, sheet in sheets.items():
        frame = sheet.frame
        peaks = find_peaks(frame, ratio, top)
        if not peaks:
            out[combo] = sheet
            continue
        frame = frame.copy()
        drop = []
        acts = [a for a in ACTIONS if a in frame.columns]
        for label, nbrs in peaks:
            row = frame.loc[label]
            node = int(row["Node"]) if "Node" in frame.columns else int(label)
            key = peak_key(element, combo, node)
            m = float(np.hypot(row["M_2"], row["M_3"]))
            around = frame.loc[nbrs]
            m_nb = float(np.hypot(around["M_2"], around["M_3"]).max())
            same_node = frame.index[frame["Node"] == node] if "Node" in frame.columns else [label]
            if key in excluded:
                treatment = "left out"
                drop.extend(same_node)
            elif mode == "average":
                treatment = "averaged"
                frame.loc[same_node, acts] = around[acts].mean().to_numpy()
            else:
                treatment = "used as it is"
            found.append(
                {
                    "key": key,
                    "combination": combo,
                    "node": node,
                    "x": round(float(row["X"]), 2),
                    "y": round(float(row["Y"]), 2),
                    "z": round(float(row["Z"]), 2),
                    "M_kNm": round(m, 1),
                    "neighbours_M_kNm": round(m_nb, 1),
                    "treatment": treatment,
                }
            )
        out[combo] = replace(sheet, frame=frame.drop(index=drop))
    return out, found
