"""Moved piles: what happens to the piles, beams and slab when piles move, without a new Plaxis run.

A scenario moves piles in plan: a whole row (every pile of a pile element) or single piles, by dX and
dY. Triton then works out the change the move makes and adds it to the Plaxis results:

1. The deck (slab and beams, as plates at their own thicknesses) is a grillage on pile springs
   (``design.grillage``): every pile, and every combi wall king pile, is a vertical spring at its head
   with the stiffness E·A / (0.5·L) of its own length L in the workbook (times the scenario's factor).
2. For each load combination, loads on the deck are found that give back exactly the pile head loads
   Plaxis gives for the piles where they are: each pile takes a uniform load over the part of the deck
   nearest to it, sized so that the grillage's pile loads are the Plaxis ones.
3. The same loads on the grillage with the moved springs give the change in every pile load and in
   the deck's moments and shears. The change is added to the Plaxis results: pile N along the whole
   pile, slab and beam M11, M22, M12, Q13 and Q23 at every node. The moved piles' results move with
   them.
4. Piles, combi walls, beams and slab are designed again with the normal checks, and each is shown
   against its design as the section has it.

It is an approximation: the grillage only carries vertical load (a pile's bending moments stay as
Plaxis gives them) and the soil round the piles does not change. Triton says when a pile's load
changes so much that a new Plaxis run is needed.

Scenarios are kept in ``<section>/moved.json`` with each run's designs in ``<section>/moved/``; they
never change the section or its results.
"""

from __future__ import annotations

import gzip
import json
import math
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from . import fresh
from .alignment import combine_parts
from .axes import infer_axes, sag_factor
from .design.grillage import Grid, Solver, Spring, actions, sample
from .design.runner import run_section
from .design.slabs import _map
from .materials import E_STRUCTURAL_STEEL, concrete
from .project import (
    BeamCage,
    BeamFace,
    BeamInput,
    CageRow,
    CombiWallInput,
    PileInput,
    Project,
    Section,
    SlabInput,
    SlabMesh,
    SlabStrips,
    UserCage,
)
from .validation import ImportResult

# --- Scenario inputs -------------------------------------------------------------------------------


class PileMove(BaseModel):
    """Piles of one element moved in plan. ``x``/``y`` empty: every pile of the element (the row)."""

    element: str = Field(..., title="Pile element", min_length=1)
    x: float | None = Field(None, title="Pile at X (m)", description="Empty: every pile of the element.")
    y: float | None = Field(None, title="Pile at Y (m)")
    dx: float = Field(0.0, title="Moved by dX (m)", ge=-20, le=20)
    dy: float = Field(0.0, title="Moved by dY (m)", ge=-20, le=20)

    def picks(self, x: float, y: float) -> bool:
        if self.x is None or self.y is None:
            return True
        return abs(self.x - x) <= 0.05 and abs(self.y - y) <= 0.05


class Scenario(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = Field("Moved piles", min_length=1, max_length=80)
    moves: list[PileMove] = Field(default_factory=list)
    stiffness_factor: float = Field(
        1.0,
        title="Pile head stiffness × ",
        gt=0.05,
        le=20,
        description="Times E·A / (0.5·L) of each pile. Lower it for piles that settle more (friction "
        "piles in soft soil), raise it for end-bearing piles on rock.",
    )
    grid: float = Field(0.5, title="Grillage spacing (m)", ge=0.25, le=2.0)

    @field_validator("id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        if not v.isalnum() or len(v) > 32:
            raise ValueError("Invalid scenario id.")
        return v


# Warnings: a pile whose head load changes by more than this share of its largest Plaxis load (or
# 100 kN, whichever is more) needs a new Plaxis run to confirm; so does a move across the quay of
# more than ACROSS_M (the piles' frame action with the wall changes, which the grillage leaves out).
RERUN_SHARE = 0.25
ACROSS_M = 0.25
CLOSE_D = 2.5  # piles closer than this many diameters, centre to centre: their group action changes


# --- Supports --------------------------------------------------------------------------------------


def _heads(frame: pd.DataFrame) -> pd.DataFrame:
    """Topmost result of each pile of an element: X, Y rounded to 1 cm, Z, N."""
    f = frame.assign(px=frame["X"].round(2), py=frame["Y"].round(2))
    top = f.loc[f.groupby(["px", "py"])["Z"].idxmax()]
    lengths = f.groupby(["px", "py"])["Z"].agg(lambda z: z.max() - z.min())
    return top.assign(length=lengths.loc[list(zip(top["px"], top["py"], strict=True))].to_numpy())


def supports(
    section: Section, elements: dict[str, dict[str, Any]], e_concrete: float
) -> list[dict[str, Any]]:
    """Every pile and combi wall king pile: plan position, diameter, head stiffness and head N
    (compression +) by combination."""
    out = []
    for name, element in section.elements.items():
        if not isinstance(element, PileInput | CombiWallInput) or name not in elements:
            continue
        combos = {c: s for c, s in elements[name].items() if not s.frame.empty and "N" in s.frame}
        if not combos:
            continue
        heads = {c: _heads(s.frame) for c, s in combos.items()}
        first = next(iter(heads.values()))
        for _, r in first.iterrows():
            x, y, L = float(r["px"]), float(r["py"]), max(float(r["length"]), 1.0)
            if isinstance(element, PileInput):
                D = element.diameter / 1000
                grade = element.concrete or None
                e = concrete(grade).ecm * 1e3 if grade else e_concrete
                ea = e * math.pi * D**2 / 4
            else:
                D = element.tube_diameter / 1000
                t = element.tube_thickness / 1000
                grade = element.concrete or None
                e = concrete(grade).ecm * 1e3 if grade else e_concrete
                ea = E_STRUCTURAL_STEEL * 1e3 * math.pi * (D - t) * t + e * math.pi * (D - 2 * t) ** 2 / 4
            n = {}
            for c, h in heads.items():
                row = h[((h["px"] - x).abs() < 0.011) & ((h["py"] - y).abs() < 0.011)]
                if len(row):
                    n[c] = -float(row["N"].iloc[0])
            out.append(
                {
                    "element": name,
                    "kind": "pile" if isinstance(element, PileInput) else "combi",
                    "x": x,
                    "y": y,
                    "D": D,
                    "length": L,
                    "k": ea / (0.5 * L),
                    "N": n,
                }
            )
    return out


def _fill_missing(sup: list[dict[str, Any]], combos: list[str]) -> list[str]:
    """A pile element with no sheet for a combination: its head loads there are taken from its most
    similar combination, scaled by the other piles' total. Returns a note for each one filled."""
    notes = []
    by_el: dict[str, list[dict]] = {}
    for s in sup:
        by_el.setdefault(s["element"], []).append(s)
    for c in combos:
        for el, items in by_el.items():
            if all(c in s["N"] for s in items):
                continue
            others = [s for s in sup if s["element"] != el and c in s["N"]]
            best, scale = None, 1.0
            for c2 in combos:
                if c2 == c or not all(c2 in s["N"] for s in items):
                    continue
                both = [s for s in others if c2 in s["N"]]
                a = sum(s["N"][c] for s in both)
                b = sum(s["N"][c2] for s in both)
                if both and abs(b) > 1e-6:
                    score = abs(math.log(abs(a / b))) if a / b > 0 else 99.0
                    if best is None or score < best[0]:
                        best, scale = (score, c2), a / b
            if best is None:
                continue
            for s in items:
                s["N"].setdefault(c, s["N"][best[1]] * scale)
            notes.append(
                f"{el} has no {c} sheet: its head loads there are taken as its {best[1]} loads × "
                f"{scale:.2f} (the other piles' total in {c} over {best[1]})."
            )
    return notes


# --- Deck grid -------------------------------------------------------------------------------------


def deck_plates(section: Section, elements: dict[str, dict[str, Any]]) -> dict[str, pd.DataFrame]:
    """Plan nodes (X, Y) of every slab and beam of the section in the workbook."""
    out = {}
    for name, element in section.elements.items():
        if not isinstance(element, SlabInput | BeamInput) or name not in elements:
            continue
        frames = [s.frame[["X", "Y"]] for s in elements[name].values() if not s.frame.empty]
        if frames:
            out[name] = pd.concat(frames).round(3).drop_duplicates().reset_index(drop=True)
    return out


def _thickness(element: Any) -> float:
    return (element.thickness if isinstance(element, SlabInput) else element.depth) / 1000


def deck_grid(section: Section, plates: dict[str, pd.DataFrame], h: float, e_kpa: float) -> Grid:
    pts = pd.concat([p.assign(t=_thickness(section.elements[n])) for n, p in plates.items()])
    x0, x1 = float(pts["X"].min()), float(pts["X"].max())
    y0, y1 = float(pts["Y"].min()), float(pts["Y"].max())
    # Keep the grid to about 6000 nodes.
    while (x1 - x0) / h * (y1 - y0) / h > 6000:
        h *= 1.25
    nx = max(int(math.ceil((x1 - x0) / h - 1e-6)) + 1, 2)
    ny = max(int(math.ceil((y1 - y0) / h - 1e-6)) + 1, 2)
    gx, gy = np.meshgrid(x0 + h * np.arange(nx), y0 + h * np.arange(ny), indexing="ij")
    px, py, pt = pts["X"].to_numpy(), pts["Y"].to_numpy(), pts["t"].to_numpy()
    flat_x, flat_y = gx.ravel(), gy.ravel()
    near_d = np.full(flat_x.size, np.inf)
    near_t = np.zeros(flat_x.size)
    for s in range(0, px.size, 400):  # in chunks: grid × nodes distances
        d = np.hypot(flat_x[:, None] - px[None, s : s + 400], flat_y[:, None] - py[None, s : s + 400])
        k = d.argmin(axis=1)
        dk = d[np.arange(d.shape[0]), k]
        better = dk < near_d
        near_d[better] = dk[better]
        near_t[better] = pt[s : s + 400][k[better]]
    reach = max(0.75 * h, 1.0)
    t = np.where(near_d <= reach, near_t, 0.0).reshape(nx, ny)
    return Grid(x0, y0, h, nx, ny, t, e_kpa)


def _patches(grid: Grid, sup: list[dict[str, Any]]) -> np.ndarray:
    """Unit uniform load (1 kPa) over the deck nearest each pile: (nx, ny, 3, piles) node loads."""
    gx, gy = grid.xy()
    area = np.full((grid.nx, grid.ny), grid.h**2)
    area[[0, -1], :] /= 2
    area[:, [0, -1]] /= 2
    area = np.where(grid.active, area, 0.0)
    sx = np.array([s["x"] for s in sup])
    sy = np.array([s["y"] for s in sup])
    owner = np.hypot(gx[..., None] - sx, gy[..., None] - sy).argmin(axis=-1)
    f = np.zeros((grid.nx, grid.ny, 3, len(sup)))
    for n in range(len(sup)):
        f[:, :, 0, n] = np.where(owner == n, area, 0.0)
    return f


# --- The change a move makes -----------------------------------------------------------------------


def _moved_position(s: dict[str, Any], moves: list[PileMove]) -> tuple[float, float, PileMove | None]:
    for m in moves:
        if m.element == s["element"] and m.picks(s["x"], s["y"]):
            return s["x"] + m.dx, s["y"] + m.dy, m
    return s["x"], s["y"], None


def plate_signs(
    settings: Any, workbook: ImportResult, names: list[str]
) -> tuple[dict[str, dict[str, str] | None], dict[str, float], list[str]]:
    """Local axes and the sagging factor of each plate, as the slab and beam designs read them."""
    found = list(getattr(workbook, "axes", None) or [])
    if settings.plate_positive_moment == "auto" and any(
        a["kind"] == "plate" and a["element"] in names and "positive" not in a for a in found
    ):
        found = infer_axes(workbook.elements())[0]
    axes = {a["element"]: a.get("local") for a in found}
    signs = {a["element"]: a for a in found if a["kind"] == "plate"}
    sag, notes = {}, []
    for n in names:
        sag[n], note = sag_factor(settings.plate_positive_moment, signs.get(n))
        notes.append(f"{n}: {note}")
    return axes, sag, notes


def changes(project: Project, section: Section, workbook: ImportResult, scenario: Scenario) -> dict[str, Any]:
    """The grillage's change in pile head loads and deck actions for the scenario's moves, by
    combination, and the warnings that go with it."""
    settings = project.design
    elements = workbook.elements()
    slab = next((e for e in section.elements.values() if isinstance(e, SlabInput)), None)
    grade = (slab.concrete if slab is not None else None) or settings.materials.concrete
    e_kpa = concrete(grade).ecm * 1e3
    sup = supports(section, elements, concrete(settings.materials.concrete).ecm * 1e3)
    plates = deck_plates(section, elements)
    problems = []
    if not plates:
        problems.append(
            "The section has no slab or beam results in the workbook: there is no deck to move piles under."
        )
    if not any(s["kind"] == "pile" for s in sup):
        problems.append("The section has no pile results in the workbook.")
    for m in scenario.moves:
        if m.element not in {s["element"] for s in sup if s["kind"] == "pile"}:
            problems.append(f"{m.element} is not a pile element with results in this section.")
        elif not any(m.element == s["element"] and m.picks(s["x"], s["y"]) for s in sup):
            problems.append(f"{m.element} has no pile at X {m.x:g}, Y {m.y:g}.")
    if not any(m.dx or m.dy for m in scenario.moves):
        problems.append("Move at least one pile.")
    if problems:
        return {"problems": problems}
    combos = sorted({c for s in sup for c in s["N"]} | {c for n in plates for c in elements[n]})
    notes = _fill_missing(sup, combos)
    combos = [c for c in combos if all(c in s["N"] for s in sup)]
    grid = deck_grid(section, plates, scenario.grid, e_kpa)
    for s in sup:
        s["k"] *= scenario.stiffness_factor
    moved = []
    for s in sup:
        x, y, m = _moved_position(s, scenario.moves)
        s["new_x"], s["new_y"], s["moved"] = round(x, 3), round(y, 3), m is not None and (m.dx or m.dy)
        if s["moved"]:
            moved.append(s)
    x_max, y_max = grid.x0 + grid.h * (grid.nx - 1), grid.y0 + grid.h * (grid.ny - 1)
    off = [
        s
        for s in moved
        if not (grid.x0 - 1e-6 <= s["new_x"] <= x_max + 1e-6 and grid.y0 - 1e-6 <= s["new_y"] <= y_max + 1e-6)
        or not grid.active[
            int(round((min(max(s["new_x"], grid.x0), x_max) - grid.x0) / grid.h)),
            int(round((min(max(s["new_y"], grid.y0), y_max) - grid.y0) / grid.h)),
        ]
    ]
    if off:
        where = ", ".join(f"{s['element']} to X {s['new_x']:g}, Y {s['new_y']:g}" for s in off[:4])
        return {"problems": [f"Moved off the deck: {where}."]}

    t0 = time.monotonic()
    old = Solver(grid, [Spring(s["x"], s["y"], s["k"]) for s in sup])
    new = Solver(grid, [Spring(s["new_x"], s["new_y"], s["k"]) for s in sup])
    f = _patches(grid, sup)
    u_old, u_new = old.solve(f), new.solve(f)
    r_old, r_new = old.reactions(u_old), new.reactions(u_new)  # (patches, piles)
    a_old, a_new = actions(grid, u_old), actions(grid, u_new)
    N = np.array([[s["N"][c] for s in sup] for c in combos]).T  # (piles, combos)
    try:
        load = np.linalg.solve(r_old.T, N)  # (patches, combos): the kPa on each pile's part of the deck
    except np.linalg.LinAlgError:
        return {"problems": ["The deck model could not be matched to the Plaxis pile loads."]}
    dR = (r_new - r_old).T @ load  # (piles, combos)
    delta = {k: (a_new[k] - a_old[k]) @ load for k in a_old}
    solved_s = round(time.monotonic() - t0, 1)

    piles = []
    for i, s in enumerate(sup):
        most = max(abs(v) for v in s["N"].values()) if s["N"] else 0.0
        worst = int(np.argmax(np.abs(dR[i])))
        piles.append(
            {
                "element": s["element"],
                "kind": s["kind"],
                "x": s["x"],
                "y": s["y"],
                "new_x": s["new_x"],
                "new_y": s["new_y"],
                "moved": bool(s["moved"]),
                "k_MN_per_m": round(s["k"] / 1000),
                "N": {c: round(s["N"][c], 1) for c in combos},
                "dN": {c: round(float(dR[i, j]), 1) for j, c in enumerate(combos)},
                "largest_N_kN": round(most, 1),
                "largest_change_kN": round(float(dR[i, worst]), 1),
                "change_combination": combos[worst],
                "change_share": round(abs(float(dR[i, worst])) / max(most, 100.0), 3),
            }
        )
    warnings = []
    big = [p for p in piles if p["change_share"] > RERUN_SHARE]
    if big:
        worst = max(big, key=lambda p: p["change_share"])
        warnings.append(
            f"{len(big)} pile(s) change load by more than {RERUN_SHARE:.0%} of their largest Plaxis load "
            f"(up to {worst['change_share']:.0%} at {worst['element']} X {worst['x']:g}, Y {worst['y']:g}): "
            "confirm with a new Plaxis run before relying on this."
        )
    across = sorted({m.element for m in scenario.moves if abs(m.dx) > ACROSS_M})
    if across:
        warnings.append(
            f"{', '.join(across)} moved across the quay (dX): the piles' bending moments are kept from "
            "Plaxis, but moving a row across the quay changes how the piles and the wall share the "
            "horizontal load. Confirm with a new Plaxis run."
        )
    for s in moved:
        for o in sup:
            if o is s:
                continue
            ox, oy = (o["new_x"], o["new_y"])
            gap = math.hypot(s["new_x"] - ox, s["new_y"] - oy)
            if gap < CLOSE_D * max(s["D"], o["D"]) - 1e-6:
                warnings.append(
                    f"{s['element']} at X {s['new_x']:g}, Y {s['new_y']:g} is {gap:.2f} m from "
                    f"{o['element']} "
                    f"at X {ox:g}, Y {oy:g}, less than {CLOSE_D:g} D: the piles act as a group; the soil "
                    "springs Plaxis gave them no longer hold."
                )
                break
    return {
        "problems": [],
        "combinations": combos,
        "grid": grid,
        "supports": sup,
        "piles": piles,
        "delta": delta,
        "warnings": warnings,
        "notes": notes,
        "solve_s": solved_s,
    }


def shear_sign(frame: pd.DataFrame, cols: dict[str, str], held: np.ndarray) -> tuple[float, float]:
    """Plaxis's shear sign s in Q = s·div M, read from the plate's own results as the axis check reads
    it: on a 1 m grid of averages away from the edges and from the pile heads (``held``: X, Y).
    Returns s (+1 when unclear) and the correlation it was read with."""
    need = [cols["Mx"], cols["My"], "M_12", cols["Vx"], cols["Vy"]]
    f = frame.drop_duplicates(["X", "Y"])
    if len(f) < 50 or not set(need) <= set(f.columns):
        return 1.0, 0.0
    x, y = f["X"].to_numpy(float), f["Y"].to_numpy(float)
    far = np.ones(len(f), bool)
    for hx, hy in held:
        far &= np.hypot(x - hx, y - hy) > 2.0
    cell = pd.DataFrame({"i": np.floor(x).astype(int), "j": np.floor(y).astype(int), "far": far})
    cell[need] = f[need].to_numpy(float)
    g = cell.groupby(["i", "j"])
    avg, ok = g[need].mean(), g["far"].all()
    ii = np.arange(avg.index.get_level_values(0).min(), avg.index.get_level_values(0).max() + 1)
    jj = np.arange(avg.index.get_level_values(1).min(), avg.index.get_level_values(1).max() + 1)
    grid = {c: avg[c].unstack().reindex(index=ii, columns=jj).to_numpy(float) for c in need}
    m = ok.unstack().reindex(index=ii, columns=jj).fillna(False).to_numpy(bool).copy()
    if m.shape[0] < 6 or m.shape[1] < 6:
        return 1.0, 0.0
    m[[0, 1, -2, -1], :] = False
    m[:, [0, 1, -2, -1]] = False

    def d1(v: np.ndarray, axis: int) -> np.ndarray:
        return (np.roll(v, -1, axis) - np.roll(v, 1, axis)) / 2

    slope = np.concatenate(
        [
            (d1(grid[cols["Mx"]], 0) + d1(grid["M_12"], 1))[m],
            (d1(grid[cols["My"]], 1) + d1(grid["M_12"], 0))[m],
        ]
    )
    q = np.concatenate([grid[cols["Vx"]][m], grid[cols["Vy"]][m]])
    k = np.isfinite(slope) & np.isfinite(q)
    if k.sum() < 20 or np.std(q[k]) < 1e-9 or np.std(slope[k]) < 1e-9:
        return 1.0, 0.0
    r = float(np.corrcoef(q[k], slope[k])[0, 1])
    return (1.0 if r >= 0 else -1.0), r


def moved_workbook(
    project: Project, section: Section, workbook: ImportResult, ch: dict[str, Any]
) -> tuple[ImportResult, list[str]]:
    """The workbook with the change added: pile N (and the moved piles' positions) and the slab and
    beam actions at every node. Returns the notes on how the signs were read."""
    grid, delta = ch["grid"], ch["delta"]
    combos = ch["combinations"]
    col = {c: j for j, c in enumerate(combos)}
    sup = ch["supports"]
    names = [n for n, e in section.elements.items() if isinstance(e, SlabInput | BeamInput)]
    axes, sag, sign_notes = plate_signs(project.design, workbook, names)
    # Plaxis's moments are one tensor (M11 = sag·Mx, M12 = sag·Mxy) and its shears Q = s·div M, with s
    # read from the slab's own results.
    elements = workbook.elements()
    slab = next((n for n in names if isinstance(section.elements[n], SlabInput) and n in elements), None)
    ref = slab or next((n for n in names if n in elements), None)
    s_q, notes = 1.0, []
    if ref is not None:
        cols = _map(axes.get(ref))
        f = max((sh.frame for sh in elements[ref].values()), key=len)
        held = np.array([[p["x"], p["y"]] for p in sup])
        s_q, r = shear_sign(f, cols, held)
        if r:
            notes.append(
                f"Plaxis shears are {'+' if s_q > 0 else '−'}div M in {ref} (r {abs(r):.2f}), so the "
                "grillage's change in shear is added with that sign."
            )
        else:
            notes.append(f"The sign of {ref}'s Plaxis shears could not be read: Q = +div M is assumed.")
    sheets = []
    for sheet in workbook.sheets:
        p = sheet.parsed
        if p is None or sheet.frame.empty or p.combination not in col:
            sheets.append(sheet)
            continue
        j = col[p.combination]
        name = p.element
        f = sheet.frame
        if name in section.elements and isinstance(section.elements[name], PileInput | CombiWallInput):
            f = f.copy()
            px, py = f["X"].round(2).to_numpy(), f["Y"].round(2).to_numpy()
            for s, dn in zip(sup, ch["piles"], strict=True):
                if s["element"] != name:
                    continue
                at = (np.abs(px - s["x"]) < 0.011) & (np.abs(py - s["y"]) < 0.011)
                d = dn["dN"][p.combination]
                for c in ("N", "N_min", "N_max"):
                    if c in f:
                        f.loc[at, c] = f.loc[at, c] - d  # Plaxis: compression −
                if s["moved"]:
                    f.loc[at, "X"] = f.loc[at, "X"] + (s["new_x"] - s["x"])
                    f.loc[at, "Y"] = f.loc[at, "Y"] + (s["new_y"] - s["y"])
            sheets.append(replace(sheet, frame=f))
            continue
        if name in names:
            f = f.copy()
            cols = _map(axes.get(name))
            x, y = f["X"].to_numpy(float), f["Y"].to_numpy(float)
            add = {
                cols["Mx"]: sag[name] * sample(grid, delta["Mx"][..., j : j + 1], x, y)[:, 0],
                cols["My"]: sag[name] * sample(grid, delta["My"][..., j : j + 1], x, y)[:, 0],
                "M_12": sag[name] * sample(grid, delta["Mxy"][..., j : j + 1], x, y)[:, 0],
                cols["Vx"]: s_q * sag[name] * sample(grid, delta["Qx"][..., j : j + 1], x, y)[:, 0],
                cols["Vy"]: s_q * sag[name] * sample(grid, delta["Qy"][..., j : j + 1], x, y)[:, 0],
            }
            for c, v in add.items():
                for cc in (c, f"{c}_min", f"{c}_max"):
                    if cc in f:
                        f[cc] = f[cc].to_numpy(float) + v
            sheets.append(replace(sheet, frame=f))
            continue
        sheets.append(sheet)
    out = ImportResult(sheets=sheets, issues=workbook.issues, axes=workbook.axes, raw=None)
    return out, sign_notes + notes


# --- Storage ---------------------------------------------------------------------------------------


def _file(d: Path) -> Path:
    return d / "moved.json"


def load(d: Path) -> dict[str, Any]:
    try:
        return json.loads(_file(d).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save(d: Path, data: dict[str, Any]) -> None:
    tmp = _file(d).with_suffix(".mtmp")
    tmp.write_text(json.dumps(data, default=str), "utf-8")
    tmp.replace(_file(d))


def scenarios(d: Path) -> list[Scenario]:
    out = []
    for s in load(d).get("scenarios") or []:
        try:
            out.append(Scenario.model_validate(s))
        except ValueError:
            continue
    return out


def put_scenario(d: Path, scenario: Scenario) -> None:
    data = load(d)
    kept = [s for s in data.get("scenarios") or [] if s.get("id") != scenario.id]
    data["scenarios"] = kept + [scenario.model_dump(mode="json")]
    save(d, data)


def delete_scenario(d: Path, scenario_id: str) -> None:
    data = load(d)
    data["scenarios"] = [s for s in data.get("scenarios") or [] if s.get("id") != scenario_id]
    data.setdefault("runs", {}).pop(scenario_id, None)
    save(d, data)
    prune(d, data)


def _design_path(d: Path, key: str) -> Path:
    return d / "moved" / f"{key}.json.gz"


def load_run(d: Path, key: str) -> dict[str, Any] | None:
    path = _design_path(d, key)
    if not path.exists():
        return None
    return json.loads(gzip.decompress(path.read_bytes()))


def _save_run(d: Path, key: str, run: dict[str, Any]) -> None:
    (d / "moved").mkdir(exist_ok=True)
    path = _design_path(d, key)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(run, default=str).encode(), 5))
    tmp.replace(path)


def prune(d: Path, data: dict[str, Any]) -> None:
    folder = d / "moved"
    if not folder.is_dir():
        return
    used = {r.get("key") for r in (data.get("runs") or {}).values()}
    for f in folder.glob("*.json.gz"):
        if f.name[: -len(".json.gz")] not in used:
            f.unlink(missing_ok=True)


# --- Running ---------------------------------------------------------------------------------------

KINDS = ("piles", "combi_walls", "beams", "slabs")
NEEDS_DESIGN = "Design the section on the Design tab first: a scenario checks the bars that design has."


def _designed_names(section: Section) -> list[str]:
    order = (PileInput, CombiWallInput, BeamInput, SlabInput)
    return [n for t in order for n, e in section.elements.items() if isinstance(e, t)]


def run_key(
    project: Project, section: Section, scenario: Scenario, workbook: dict | None, results: dict | None
) -> str:
    """Everything a run depends on: the section's design inputs, the design whose bars it checks, and
    the scenario's moves and model."""
    now = fresh.fingerprint(project, section, workbook)
    return fresh._hash(
        [now, (results or {}).get("run_at"), scenario.model_dump(mode="json", exclude={"name", "id"})]
    )


# The moved workbook of the last few runs, so that a run in steps works it out once.
_CACHE: dict[str, tuple[ImportResult, dict[str, Any]]] = {}


def _moved(
    key: str, project: Project, section: Section, workbook: ImportResult, scenario: Scenario
) -> tuple[ImportResult | None, dict[str, Any]]:
    if key in _CACHE:
        return _CACHE[key]
    ch = changes(project, section, workbook, scenario)
    if ch["problems"]:
        return None, {"problems": ch["problems"]}
    wb, sign_notes = moved_workbook(project, section, workbook, ch)
    grid = ch["grid"]
    summary = _change_summary(ch) | {
        "sign_notes": sign_notes,
        "deck": {
            "x": [grid.x0, round(grid.x0 + grid.h * (grid.nx - 1), 3)],
            "y": [grid.y0, round(grid.y0 + grid.h * (grid.ny - 1), 3)],
        },
    }
    while len(_CACHE) >= 2:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (wb, summary)
    return wb, summary


def _design_of(res: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((e for k in KINDS for e in res.get(k) or [] if e["element"] == name), None)


def run(
    project: Project,
    section: Section,
    workbook: ImportResult,
    summary: dict | None,
    results: dict[str, Any] | None,
    d: Path,
    scenario: Scenario,
    deadline: float | None = None,
    tell=None,
) -> dict[str, Any]:
    """Check every pile, combi wall, beam and slab with the piles moved and the bars as designed, then
    design afresh the ones that no longer pass, until ``deadline`` (at least one step). ``left`` counts
    the steps still to do."""
    data = load(d)
    runs = data.setdefault("runs", {})
    if not results or not any(results.get(k) for k in KINDS):
        runs[scenario.id] = {"problems": [NEEDS_DESIGN]}
        save(d, data)
        return {"left": 0, "problems": [NEEDS_DESIGN]}
    key = run_key(project, section, scenario, summary, results)
    mine = runs.get(scenario.id) or {}
    stored = load_run(d, key) if mine.get("key") == key else None
    stored = stored or {"checks": {}, "redesigns": {}}
    if tell:
        tell(0.02, "Working out the change in pile loads and deck actions")
    wb, change = _moved(key, project, section, workbook, scenario)
    if wb is None:
        runs[scenario.id] = {"key": key, "problems": change["problems"]}
        save(d, data)
        return {"left": 0, "problems": change["problems"]}
    stored["change"] = change
    fixed, loose = as_designed(section, results)
    stored["loose"] = loose
    names = _designed_names(section)
    before = {n: _design_of(combine_parts(results), n) for n in names}

    def steps() -> list[tuple[str, str]]:
        out = [("check", n) for n in names if n not in stored["checks"] and before.get(n) is not None]
        for n, c in stored["checks"].items():
            if (
                c is not None
                and _state(before.get(n), c, change.get("piles")) in UNSAFE
                and n not in stored["redesigns"]
            ):
                out.append(("redesign", n))
        return out

    done = 0
    while True:
        todo = steps()
        if not todo or (deadline is not None and done and time.monotonic() > deadline):
            break
        what, name = todo[0]
        if tell:
            verb = "Checking" if what == "check" else "Designing afresh"
            tell(0.1 + 0.9 * done / max(done + len(todo), 1), f"{verb} {name} with the piles moved")
        sec = fixed if what == "check" else section
        res = combine_parts(run_section(project.design, sec, wb, only=[name]))
        stored["checks" if what == "check" else "redesigns"][name] = _design_of(res, name)
        done += 1
    left = len(steps())
    stored["run_at"] = time.strftime("%Y-%m-%d %H:%M")
    _save_run(d, key, stored)
    runs[scenario.id] = {"key": key, "left": left}
    save(d, data)
    prune(d, data)
    return {"left": left, "done": done, "problems": []}


def _change_summary(ch: dict[str, Any]) -> dict[str, Any]:
    grid = ch["grid"]
    delta = ch["delta"]
    act = grid.active[..., None]
    peak = {k: round(float(np.abs(np.where(act, v, 0.0)).max()), 1) for k, v in delta.items()}
    return {
        "combinations": ch["combinations"],
        "piles": ch["piles"],
        "warnings": ch["warnings"],
        "notes": ch["notes"],
        "grid_m": grid.h,
        "grid_nodes": int(grid.active.sum()),
        "largest_deck_change": {
            "Mx_kNm_per_m": peak["Mx"],
            "My_kNm_per_m": peak["My"],
            "Mxy_kNm_per_m": peak["Mxy"],
            "Qx_kN_per_m": peak["Qx"],
            "Qy_kN_per_m": peak["Qy"],
        },
        "solve_s": ch["solve_s"],
    }


# --- The bars as designed --------------------------------------------------------------------------


def as_designed(section: Section, results: dict[str, Any]) -> tuple[Section, list[str]]:
    """The section with the bars its current design has, set as if by the user, so that a run checks
    those bars instead of choosing new ones: each pile's and combi wall infill's head cage, each beam's
    bars, and the slab's meshes, stations, mesh spacing, additional bars of every strip and the lines of
    piles its column strips lie on (moved piles do not move the strips the bars are in). Returns the
    elements it could not fix (their bars are chosen afresh)."""
    user_cages = dict(section.user_cages)
    beam_cages = dict(section.beam_cages)
    slab_strips = dict(section.slab_strips)
    elements = dict(section.elements)
    loose: list[str] = []

    def cage_of(arrangement: dict | None) -> UserCage | None:
        rings = (arrangement or {}).get("rings") or []
        if not rings:
            return None
        return UserCage(
            rows=max(1, min(4, float(arrangement.get("rows") or 1))),
            count=max(6, int(rings[0]["count"])),
            diameter=int(rings[0]["diameter"]),
            row_bars=[CageRow(count=int(r["count"]), diameter=int(r["diameter"])) for r in rings],
            over_limit_with_couplers=bool((arrangement or {}).get("with_couplers")),
        )

    for p in results.get("piles") or []:
        name = p["element"]
        if name in section.user_cages or name not in elements:
            continue
        cage = cage_of(p.get("arrangement") if isinstance(p.get("arrangement"), dict) else None)
        if cage is None:
            loose.append(name)
            continue
        if p.get("with_couplers"):
            cage = cage.model_copy(update={"over_limit_with_couplers": True})
        user_cages[name] = cage
    for w in results.get("combi_walls") or []:
        name = w["element"]
        if name in section.user_cages or name not in elements:
            continue
        infill = w.get("infill") or {}
        cage = cage_of(infill.get("arrangement") if isinstance(infill.get("arrangement"), dict) else None)
        if cage is None:
            loose.append(name)
            continue
        user_cages[name] = cage
    for b in results.get("beams") or []:
        key = b.get("key") or b["element"]
        if key in section.beam_cages or b["element"] not in elements:
            continue
        c = b.get("cage") or {}
        try:
            beam_cages[key] = BeamCage(
                top=BeamFace(
                    count=c["top"]["count"], diameter=c["top"]["phi"], layers=c["top"].get("layers", 1)
                ),
                bottom=BeamFace(
                    count=c["bottom"]["count"],
                    diameter=c["bottom"]["phi"],
                    layers=c["bottom"].get("layers", 1),
                ),
                side=BeamFace(count=c["side"]["count"], diameter=c["side"]["phi"]),
            )
        except (KeyError, TypeError, ValueError):
            loose.append(key)
    for d in results.get("slabs") or []:
        name = d["element"]
        key = d.get("key") or name
        slab = elements.get(name)
        if not isinstance(slab, SlabInput):
            continue
        meshes = {}
        for layer, info in (d.get("layers") or {}).items():
            basic = (info or {}).get("basic") or {}
            if basic.get("phi") and basic.get("spacing_mm") and f"mesh_{layer}" in SlabInput.model_fields:
                meshes[f"mesh_{layer}"] = SlabMesh(
                    diameter=int(basic["phi"]),
                    spacing=float(basic["spacing_mm"]),
                    layers=int(basic.get("layers") or 1),
                )
        if meshes and key == name:  # a corner berth's parts share the element: its meshes stay its own
            elements[name] = slab.model_copy(update=meshes)
        sd = d.get("strip_design") or {}
        mine = section.slab_strips.get(key) or SlabStrips()
        mesh_label = {
            k: ((v or {}).get("basic") or {}).get("label") for k, v in (d.get("layers") or {}).items()
        }
        bars = {}
        for r in sd.get("rows") or []:
            key_r, text = r.get("key"), r.get("additional") or "mesh only"
            if not key_r or key_r.endswith("|mesh"):  # the mesh is set on the slab above
                continue
            if text == mesh_label.get(key_r.split("|")[0]) and r.get("spec"):
                # Bars that read like the mesh itself: given layer by layer, so they are not taken as it.
                text = "layers: " + " | ".join("–" if p is None else f"Ø{p[0]:g}@{p[1]:g}" for p in r["spec"])
            bars[key_r] = text
        spacing = (d.get("mesh_choice") or {}).get("chosen_mm")
        slab_strips[key] = mine.model_copy(
            update={
                "stations": mine.stations if mine.stations is not None else (sd.get("stations") or None),
                "bars": {**bars, **mine.bars},
                "spacing": mine.spacing if mine.spacing is not None else spacing,
                "lines": sd.get("lines") or None,
            }
        )
    fixed = section.model_copy(
        update={
            "elements": elements,
            "user_cages": user_cages,
            "beam_cages": beam_cages,
            "slab_strips": slab_strips,
        }
    )
    return fixed, loose


# --- The tab's view --------------------------------------------------------------------------------


def _util(design: dict | None) -> float | None:
    """An element's utilisation; a slab's includes its shear (its own leaves the links out)."""
    if not design:
        return None
    u = design.get("utilisation")
    if design.get("kind") == "slab" or "strip_design" in design:
        su = (design.get("shear") or {}).get("utilisation")
        if su is not None and u is not None:
            u = max(u, su)
    return None if u is None else round(float(u), 3)


def _bars(design: dict | None) -> str | None:
    if not design:
        return None
    a = design.get("arrangement")
    if isinstance(a, dict) and a.get("label"):
        return a["label"]
    infill = (design.get("infill") or {}).get("arrangement")
    if isinstance(infill, dict) and infill.get("label"):
        return f"Infill {infill['label']}"
    if (design.get("cage") or {}).get("label"):
        return design["cage"]["label"]
    st = design.get("steel") or {}
    if st.get("kg_per_m3"):
        return f"{st['kg_per_m3']} kg/m³"
    return None


def _state(before: dict | None, after: dict | None, piles: list[dict] | None = None) -> str:
    """passes; fails now (it passed before); worse than before (it failed before, and now a part that
    passed fails, or its utilisation went up); failed before too."""
    if after is None:
        return "not checked"
    if after.get("passed"):
        return "passes"
    if not before or before.get("passed"):
        return "fails now"
    ub, ua = _util(before), _util(after)
    if ub is not None and ua is not None and ua > ub + 0.005:
        return "worse than before"
    if "strip_design" in after and before:
        if any(
            r["before"] is not None and r["before"] <= 1 + 1e-9 and not r["passed"]
            for r in _slab_rows(before, after)
        ):
            return "worse than before"
        if any(
            q["passed_before"] and not q["passed"] and not q["under_beam"]
            for q in _punching(before, after, piles or [])
        ):
            return "worse than before"
    return "failed before too"


UNSAFE = ("fails now", "worse than before")


def _row_util(r: dict) -> float:
    u = float(r.get("ratio") or 0)
    if r.get("wk_mm") is not None and r.get("wk_limit_mm"):
        u = max(u, float(r["wk_mm"]) / float(r["wk_limit_mm"]))
    return round(u, 3)


def _slab_rows(before: dict, after: dict) -> list[dict[str, Any]]:
    """Strips (and zones) whose utilisation moved, or that fail with the piles moved."""
    b = {r["key"]: r for r in (before.get("strip_design") or {}).get("rows") or [] if r.get("key")}
    out = []
    for r in (after.get("strip_design") or {}).get("rows") or []:
        k = r.get("key")
        if not k:
            continue
        ub = _row_util(b[k]) if k in b else None
        ua = _row_util(r)
        if ua <= 1 + 1e-9 and ub is not None and abs(ua - ub) < 0.01:
            continue
        out.append(
            {
                "key": k,
                "layer": r.get("layer"),
                "station": r.get("station"),
                "strip": r.get("strip"),
                "bars": r.get("bars"),
                "before": ub,
                "after": ua,
                "M_before": (b.get(k) or {}).get("M_kNm_per_m"),
                "M_after": r.get("M_kNm_per_m"),
                "wk_after": r.get("wk_mm"),
                "wk_limit": r.get("wk_limit_mm"),
                "passed": ua <= 1 + 1e-9,
            }
        )
    return sorted(out, key=lambda r: -(r["after"] or 0))


def _punching(before: dict, after: dict, piles: list[dict]) -> list[dict[str, Any]]:
    """Each pile head's punching before and with the piles moved, matched by where the pile was."""
    moved = {(p["element"], p["x"], p["y"]): (p["new_x"], p["new_y"]) for p in piles}
    b = {(q["pile"], q["x"], q["y"]): q for q in before.get("punching") or []}
    a = {(q["pile"], q["x"], q["y"]): q for q in after.get("punching") or []}
    out = []
    for (pile, x, y), q in b.items():
        nx, ny = moved.get((pile, x, y), (x, y))
        qa = next(
            (
                v
                for (p2, x2, y2), v in a.items()
                if p2 == pile and abs(x2 - nx) < 0.02 and abs(y2 - ny) < 0.02
            ),
            None,
        )
        out.append(
            {
                "pile": pile,
                "x": x,
                "y": y,
                "new_x": nx,
                "new_y": ny,
                "moved": (nx, ny) != (x, y),
                "V_before": q.get("V_kN"),
                "V_after": qa.get("V_kN") if qa else None,
                "before": q.get("utilisation"),
                "after": qa.get("utilisation") if qa else None,
                "links_before": bool(q.get("needs_reinforcement")),
                "links_after": bool(qa and qa.get("needs_reinforcement")),
                "passed_before": bool(q.get("passed")),
                "passed": bool(qa and qa.get("passed")),
                "under_beam": qa is None,
            }
        )
    return out


def pile_rows(section: Section, workbook: ImportResult) -> dict[str, list[list[float]]]:
    """Plan positions of the piles of each pile element, for picking what moves."""
    out = {}
    elements = workbook.elements()
    for name, element in section.elements.items():
        if isinstance(element, PileInput) and name in elements:
            f = next((s.frame for s in elements[name].values() if not s.frame.empty), None)
            if f is not None:
                heads = _heads(f)
                out[name] = sorted(
                    ([float(x), float(y)] for x, y in zip(heads["px"], heads["py"], strict=True)),
                    key=lambda p: (p[1], p[0]),
                )
    return out


def view(
    project: Project,
    section: Section,
    workbook: ImportResult | None,
    summary: dict | None,
    results: dict[str, Any] | None,
    d: Path,
) -> dict[str, Any]:
    data = load(d)
    combined = combine_parts(results or {})
    names = _designed_names(section)
    out = []
    for sc in scenarios(d):
        mine = (data.get("runs") or {}).get(sc.id) or {}
        entry: dict[str, Any] = {"scenario": sc.model_dump(mode="json")}
        key = run_key(project, section, sc, summary, results) if results else None
        if mine.get("problems") and (mine.get("key") in (None, key)):
            entry |= {"state": "problems", "problems": mine["problems"]}
            out.append(entry)
            continue
        stored = load_run(d, mine["key"]) if mine.get("key") else None
        if stored is None:
            entry["state"] = "not run"
            out.append(entry)
            continue
        entry["state"] = (
            "out of date" if mine.get("key") != key else ("done" if not mine.get("left") else "part done")
        )
        entry["left"] = mine.get("left", 0)
        entry["run_at"] = stored.get("run_at")
        entry["change"] = stored.get("change")
        entry["loose"] = stored.get("loose") or []
        piles = (stored.get("change") or {}).get("piles") or []
        rows = []
        for n in names:
            b = _design_of(combined, n)
            c = (stored.get("checks") or {}).get(n)
            r = (stored.get("redesigns") or {}).get(n)
            rows.append(
                {
                    "element": n,
                    "kind": trials_kind(section.elements[n]),
                    "before": {
                        "utilisation": _util(b),
                        "passed": bool(b and b.get("passed")),
                        "bars": _bars(b),
                    }
                    if b
                    else None,
                    "check": {"utilisation": _util(c), "passed": bool(c and c.get("passed"))} if c else None,
                    "redesign": {
                        "utilisation": _util(r),
                        "passed": bool(r and r.get("passed")),
                        "bars": _bars(r),
                    }
                    if r
                    else None,
                    "state": _state(b, c, piles)
                    if c is not None
                    else ("not checked" if b is None else "to do"),
                    "failure": (c or {}).get("failure") or [],
                }
            )
            if c and b and "strip_design" in c:
                rows[-1]["strips"] = _slab_rows(b, c)
                rows[-1]["punching"] = _punching(b, c, piles)
        entry["elements"] = rows
        entry["fails_now"] = [r["element"] for r in rows if r["state"] in UNSAFE]
        entry["failed_before"] = [r["element"] for r in rows if r["state"] == "failed before too"]
        out.append(entry)
    return {
        "scenarios": out,
        "piles": pile_rows(section, workbook) if workbook is not None else {},
        "designed": bool(results and any(results.get(k) for k in KINDS)),
        "design_run_at": (results or {}).get("run_at"),
        "rerun_share": RERUN_SHARE,
    }


def trials_kind(element: Any) -> str:
    if isinstance(element, CombiWallInput):
        return "combi wall"
    if isinstance(element, PileInput):
        return "pile"
    if isinstance(element, BeamInput):
        return "beam"
    return "slab"
