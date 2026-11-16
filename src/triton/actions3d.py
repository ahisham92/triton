"""Straining actions of one element for its 3D view on the Design tab (display only).

The Plaxis values of every node, with the section's load multipliers and working zone as designed,
gathered onto a light grid so a phone can draw them: frames (piles, king piles) in 0.5 m steps down
each member, plates (deck, beams, walls) in square cells on the plate (1 m on the deck, 0.5 m on
beams and walls). Each point keeps the least and the largest value of every action in every
combination; a case shows the one of larger size with its sign, and the envelopes are taken over
the design combinations (all but the QP ones). Signs as Plaxis gives them (N not turned for concrete).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .elements import CombinationType, ElementType, ResultKind, combination_type

# key, label, unit, what it is
FRAME_ACTIONS = [
    ("N", "N", "kN", "axial force"),
    ("Q_12", "Q12", "kN", "shear force, local 2 direction"),
    ("Q_13", "Q13", "kN", "shear force, local 3 direction"),
    ("M_2", "M2", "kNm", "bending moment about local 2 (along the berth for piles)"),
    ("M_3", "M3", "kNm", "bending moment about local 3 (across the berth for piles)"),
    ("M_1", "M1", "kNm", "torsion"),
]
PLATE_ACTIONS = [
    ("N_1", "N11", "kN/m", "axial force per metre, local 1 direction"),
    ("N_2", "N22", "kN/m", "axial force per metre, local 2 direction"),
    ("Q_13", "Q13", "kN/m", "out-of-plane shear per metre, local 1 direction"),
    ("Q_23", "Q23", "kN/m", "out-of-plane shear per metre, local 2 direction"),
    ("M_11", "M11", "kNm/m", "bending moment per metre, bars in local 1 direction"),
    ("M_22", "M22", "kNm/m", "bending moment per metre, bars in local 2 direction"),
    ("M_12", "M12", "kNm/m", "twisting moment per metre"),
    ("Q_12", "Q12", "kN/m", "in-plane shear per metre"),
]
FRAME_STEP = 0.5  # m down a member
CELL = {ElementType.SLAB: 1.0}  # m; beams and walls 0.5 m
CELL_DEFAULT = 0.5
CASES = ("env_abs", "env_max", "env_min")


def _grid(frame: pd.DataFrame, kind: ResultKind, cell: float, flat: str) -> pd.DataFrame:
    """Each node's grid point: x, y, z columns rounded onto the grid."""
    g = pd.DataFrame(index=frame.index)
    if kind is ResultKind.BEAM:
        g["gx"] = frame["X"].round(2)
        g["gy"] = frame["Y"].round(2)
        g["gz"] = (np.floor(frame["Z"] / FRAME_STEP) * FRAME_STEP + FRAME_STEP / 2).round(2)
        return g
    for a in "XYZ":
        col = f"g{a.lower()}"
        if a == flat:
            g[col] = 0.0  # one cell through the plate's thickness; drawn at its mean level
        else:
            g[col] = (np.floor(frame[a] / cell) * cell + cell / 2).round(3)
    return g


def _flat_axis(frames: list[pd.DataFrame]) -> str:
    f = pd.concat([x[["X", "Y", "Z"]] for x in frames])
    spread = {a: float(f[a].max() - f[a].min()) for a in "XYZ"}
    return min(spread, key=spread.get)


def element_actions(sheets: dict[str, Any]) -> dict[str, Any] | None:
    """One element's actions on its grid, for every combination: combination -> sheet (SheetData
    with ``frame`` and ``parsed.spec``), multipliers and working zone already applied."""
    usable = {
        c: s
        for c, s in sheets.items()
        if s.parsed and {"X", "Y", "Z"} <= set(s.frame.columns) and len(s.frame)
    }
    if not usable:
        return None
    spec = next(iter(usable.values())).parsed.spec
    kind = spec.kind
    table = FRAME_ACTIONS if kind is ResultKind.BEAM else PLATE_ACTIONS
    cols = [k for k, *_ in table if any(k in s.frame.columns for s in usable.values())]
    cell = CELL.get(spec.type, CELL_DEFAULT)
    flat = "" if kind is ResultKind.BEAM else _flat_axis([s.frame for s in usable.values()])
    grids = {c: _grid(s.frame, kind, cell, flat) for c, s in usable.items()}
    keys = ["gx", "gy", "gz"]
    allg = pd.concat(grids.values()).drop_duplicates().sort_values(keys).reset_index(drop=True)
    # The drawn level of a plate cell: the mean of its nodes over every combination.
    if flat:
        both = pd.concat([pd.concat([grids[c], usable[c].frame[[flat]]], axis=1) for c in usable])
        lvl = both.groupby(keys)[flat].mean().round(3)
        allg = allg.merge(lvl.rename("lvl").reset_index(), on=keys, how="left")
    index = pd.MultiIndex.from_frame(allg[keys])
    points = []
    for row in allg.itertuples(index=False):
        p = {"X": row.gx, "Y": row.gy, "Z": row.gz}
        if flat:
            p[flat] = row.lvl
        points.append([p["X"], p["Y"], p["Z"]])
    combos = list(usable)
    lo: dict[str, dict[str, list]] = {}
    hi: dict[str, dict[str, list]] = {}
    for c, s in usable.items():
        f = pd.concat([grids[c], s.frame[[k for k in cols if k in s.frame.columns]]], axis=1)
        g = f.groupby(keys)
        mn = g.min().reindex(index)
        mx = g.max().reindex(index)
        lo[c] = {k: _list(mn[k]) if k in mn else [None] * len(points) for k in cols}
        hi[c] = {k: _list(mx[k]) if k in mx else [None] * len(points) for k in cols}
    labels = {k: (lab, unit, what) for k, lab, unit, what in table}
    filled = [None] * len(points)
    if flat:
        filled = _fill_gaps(points, lo, hi, cell, flat)
    return {
        "kind": "frame" if kind is ResultKind.BEAM else "plate",
        "type": spec.type.value,
        "flat": flat,
        "size": FRAME_STEP if kind is ResultKind.BEAM else cell,
        "points": points,
        "combinations": combos,
        "design": [c for c in combos if combination_type(c) is not CombinationType.SLS_QP] or combos,
        "actions": [
            {"key": k, "label": labels[k][0], "unit": labels[k][1], "what": labels[k][2]} for k in cols
        ],
        "lo": lo,
        "hi": hi,
        "filled": filled,
    }


FILL_REACH = 4  # cells: a square with no node of its own is filled when nodes lie both sides of it


def _fill_gaps(points: list, lo: dict, hi: dict, cell: float, flat: str) -> list:
    """Squares with no Plaxis node in them where the mesh is coarser than the grid: each square that
    has squares with nodes on both sides of it (along X, Y or a diagonal, within ``FILL_REACH``) takes
    the nearest one's values, so the plate reads as one surface; its edges and openings stay as they
    are. Adds the squares to ``points``, ``lo`` and ``hi`` in place; returns, per point, the index of
    the square it copies (None for a square with nodes of its own)."""
    u, v = [a for a in "XYZ" if a != flat]
    ax = {"X": 0, "Y": 1, "Z": 2}
    at = {}
    for n, p in enumerate(points):
        at[(round((p[ax[u]] - cell / 2) / cell), round((p[ax[v]] - cell / 2) / cell))] = n
    if not at:
        return [None] * len(points)
    iu = [k[0] for k in at]
    iv = [k[1] for k in at]
    steps = [(1, 0), (0, 1), (1, 1), (1, -1)]
    new: list[tuple[tuple[int, int], int]] = []
    for i in range(min(iu), max(iu) + 1):
        for j in range(min(iv), max(iv) + 1):
            if (i, j) in at:
                continue
            best = None
            for di, dj in steps:
                ahead = next((t for t in range(1, FILL_REACH + 1) if (i + t * di, j + t * dj) in at), None)
                back = next((t for t in range(1, FILL_REACH + 1) if (i - t * di, j - t * dj) in at), None)
                if ahead is None or back is None:
                    continue
                for t, sgn in ((ahead, 1), (back, -1)):
                    d = t * (2**0.5 if di and dj else 1)
                    if best is None or d < best[0]:
                        best = (d, at[(i + sgn * t * di, j + sgn * t * dj)])
            if best is not None:
                new.append(((i, j), best[1]))
    filled: list = [None] * len(points)
    for (i, j), donor in new:
        p = list(points[donor])
        p[ax[u]] = round(i * cell + cell / 2, 3)
        p[ax[v]] = round(j * cell + cell / 2, 3)
        points.append(p)
        filled.append(donor)
        for table in (lo, hi):
            for by in table.values():
                for vals in by.values():
                    vals.append(vals[donor])
    return filled


def regions(points: list, parts: list, zone_length: float | None) -> list[str] | None:
    """The design part each point is in (Part 1, Part 2, ... and, when the slab's corner zone is
    designed on its own, the corner zone), or None on a straight berth."""
    from .alignment import corner_zones, part_of

    if len(parts) < 2 or not points:
        return None
    x = np.array([p[0] for p in points], float)
    y = np.array([p[1] for p in points], float)
    owner = part_of(parts, x, y)
    names = [parts[int(i)].name for i in owner]
    if zone_length:
        zone = corner_zones(parts, x, y, zone_length)
        for n, k in enumerate(zone):
            if k >= 0:
                names[n] = "Corner zone" if len(parts) == 2 else f"Corner zone {int(k) + 1}"
    return names


def _list(s: pd.Series) -> list:
    return [None if pd.isna(v) else round(float(v), 2) for v in s.to_numpy()]


def pick(data: dict[str, Any], case: str, action: str) -> dict[str, Any]:
    """The values to draw: a combination's (the larger in size of the least and the largest in each
    cell, with its sign) or an envelope's, with the combination each envelope value comes from."""
    keys = [a["key"] for a in data["actions"]]
    if action not in keys:
        action = keys[0]
    n = len(data["points"])
    lo = np.array(
        [[np.nan if v is None else v for v in data["lo"][c][action]] for c in data["combinations"]]
    ).reshape(-1, n)
    hi = np.array(
        [[np.nan if v is None else v for v in data["hi"][c][action]] for c in data["combinations"]]
    ).reshape(-1, n)
    combos = data["combinations"]
    out: dict[str, Any] = {"case": case, "action": action}
    if case in combos:
        i = combos.index(case)
        a, b = lo[i], hi[i]
        value = np.where(np.abs(a) > np.abs(b), a, b)
        out.update(values=_arr(value), lo=_arr(a), hi=_arr(b))
        return out
    if case not in CASES:
        case = out["case"] = "env_abs"
    rows = [combos.index(c) for c in data["design"]]
    lo, hi = lo[rows], hi[rows]
    has = ~np.all(np.isnan(hi), axis=0)
    fill_hi = np.where(np.isnan(hi), -np.inf, hi)
    fill_lo = np.where(np.isnan(lo), np.inf, lo)
    imax, imin = fill_hi.argmax(axis=0), fill_lo.argmin(axis=0)
    vmax = np.where(has, fill_hi.max(axis=0), np.nan)
    vmin = np.where(has, fill_lo.min(axis=0), np.nan)
    if case == "env_max":
        value, src = vmax, imax
    elif case == "env_min":
        value, src = vmin, imin
    else:
        take_min = np.abs(vmin) > np.abs(vmax)
        value, src = np.where(take_min, vmin, vmax), np.where(take_min, imin, imax)
    names = [data["design"][int(i)] for i in src]
    out.update(
        values=_arr(value),
        lo=_arr(vmin),
        hi=_arr(vmax),
        source=[nm if h else None for nm, h in zip(names, has, strict=True)],
    )
    return out


def _arr(a: np.ndarray) -> list:
    return [None if not np.isfinite(v) else round(float(v), 2) for v in a]
