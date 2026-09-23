"""Reducing pile reinforcement down the pile: zones, laps and cut lengths.

The pile is split from the head down into bar runs. Each run has one cage and
its bars end ``lap`` below the run (lapped with the run under it), except at
the toe. Consecutive runs keep the same number of bars in the outer row so
the bars can be lapped (26Ø32 above 26Ø16), and may drop rows (2 rows above
1 row). A cage must carry every load in its own run, including the lap zone
below its top, where the bars above are still being anchored.

Two ways to choose the runs:

* ``least_steel``: least total weight including laps, every run at least the
  minimum zone length;
* ``standard_lengths``: as many bars as possible with a standard cut length
  (e.g. 6, 8, 9, 12 m), then least weight.

Every bar is at most the longest bar length, and at laps the steel of both
cages is at most 8% of the concrete (EN 1992-1-1 9.5.2(3)).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..materials import STEEL_DENSITY
from ..project import DesignSettings, PileInput
from .piles import MAX_RATIO_AT_LAPS, Arrangement, _families, _utilisation

STEP = 0.05  # m, level grid
LENGTH_STEP = 0.25  # m, run lengths tried for least steel


@dataclass(frozen=True)
class Run:
    top: float
    bottom: float
    cage: Arrangement
    extension: tuple[float, ...]  # m below ``bottom`` for each row (lap), 0 at the toe
    joint: str  # "lap", "coupler" or "toe"

    @property
    def length(self) -> float:
        return self.top - self.bottom

    def bar_lengths(self) -> list[float]:
        return [self.length + e for e in self.extension]

    @property
    def weight(self) -> float:
        return sum(
            r.area / 1e6 * STEEL_DENSITY * (self.length + e)
            for r, e in zip(self.cage.rings, self.extension, strict=True)
        )


def _laps(settings: DesignSettings, upper: Arrangement, lower: Arrangement | None) -> tuple[float, ...]:
    """Length (m) each row of ``upper`` runs on below its run: a lap, or 0 with couplers or at the toe."""
    pr = settings.piles
    if lower is None or pr.splice == "coupler":
        return tuple(0.0 for _ in upper.rings)
    out = []
    for i, r in enumerate(upper.rings):
        below = lower.rings[i].diameter if i < len(lower.rings) else 0
        # Lapped with the matching row below, or anchored over the same length if the row stops.
        # Rounded up to 50 mm as detailed.
        out.append(math.ceil(pr.lap_factor * max(r.diameter, below) / 50 - 1e-9) * 0.05)
    return tuple(out)


def _fits_below(lower: Arrangement, upper: Arrangement) -> bool:
    """Same outer bar count, no more rows, and no row with bigger bars than the row above it."""
    return (
        lower.outer.count == upper.outer.count
        and lower.rows <= upper.rows
        and lower.area <= upper.area
        and all(lo.diameter <= up.diameter for lo, up in zip(lower.rings, upper.rings, strict=False))
    )


def _cages(pile: PileInput, settings: DesignSettings, top: Arrangement, area_min: float) -> list[Arrangement]:
    """Cages that can sit below ``top``."""
    fixed = pile.model_copy(update={"bar_count": top.outer.count})
    out = {top}
    for fam in _families(fixed, settings):
        for a in fam:
            if a.area >= area_min and _fits_below(a, top):
                out.add(a)
    return sorted(out, key=lambda a: (a.area, a.rows))


def curtail(
    name: str,
    pile: PileInput,
    settings: DesignSettings,
    loads: pd.DataFrame,
    top_cage: Arrangement,
    area_min: float,
) -> dict:
    pr = settings.piles
    head = float(pile.head_level if pile.head_level is not None else loads["Z"].max())
    toe = float(loads["Z"].min())
    n_bands = max(1, math.ceil((head - toe) / STEP - 1e-9))
    band = np.clip(((head - loads["Z"].to_numpy()) / STEP).astype(int), 0, n_bands - 1)
    ac = math.pi * pile.diameter**2 / 4
    volume = ac / 1e6 * (head - toe)

    cages = _cages(pile, settings, top_cage, area_min)
    ok, band_util = {}, {}
    for c in cages:
        u = _utilisation(pile, c, settings, loads)
        worst = np.zeros(n_bands)
        np.maximum.at(worst, band, u)
        band_util[c] = worst
        ok[c] = np.concatenate([[0], np.cumsum(worst > 1.0 + 1e-9)])  # failing bands before each index
    cages = _prune(cages, ok)

    def fits(c: Arrangement, p: int, e: int) -> bool:
        return ok[c][e] - ok[c][p] == 0

    runs = _search(pr, settings, cages, fits, n_bands, head, ac)
    unified = _search(pr, settings, [top_cage], fits, n_bands, head, ac, least=True)
    if runs is None:
        runs = unified
    notes = []
    if runs is None:
        return {"element": name, "runs": [], "notes": ["The cage does not carry the loads over the pile."]}
    weight = sum(r.weight for r in runs)
    unified_weight = sum(r.weight for r in unified) if unified else None
    standard = set(pr.standard_bar_lengths)
    for r in runs:
        if pr.curtailment == "standard_lengths" and not any(
            abs(r.bar_lengths()[0] - s) < 0.01 for s in standard
        ):
            notes.append(f"{r.top:.2f} to {r.bottom:.2f} m: bars are not a standard cut length.")
    notes.append("Starter bars into the slab above the pile head are not included.")
    return {
        "element": name,
        "mode": pr.curtailment,
        "splice": pr.splice,
        "head_level": round(head, 2),
        "toe_level": round(toe, 2),
        "length_m": round(head - toe, 2),
        "runs": [_run_dict(r, band_util, head) for r in runs],
        "weight_kg": round(weight, 1),
        "steel_ratio_kg_m3": round(weight / volume, 1),
        "unified_weight_kg": None if unified_weight is None else round(unified_weight, 1),
        "unified_steel_ratio_kg_m3": None if unified_weight is None else round(unified_weight / volume, 1),
        "couplers": sum(r.cage.bar_count for r in runs[:-1]) if pr.splice == "coupler" else 0,
        "notes": notes,
    }


def _prune(cages: list[Arrangement], ok: dict) -> list[Arrangement]:
    """Drop cages that are heavier than another cage that works everywhere they do."""
    keep = []
    for c in cages:
        works = ok[c][1:] == ok[c][:-1]
        dominated = any(
            d is not c
            and d.area <= c.area
            and d.rows <= c.rows
            and d.outer.diameter <= c.outer.diameter
            and (d.area, d.rows) < (c.area, c.rows)
            and np.all((ok[d][1:] == ok[d][:-1]) | ~works)
            for d in cages
        )
        if not dominated:
            keep.append(c)
    return keep


def _search(pr, settings, cages, fits, n_bands, head, ac, least=False) -> list[Run] | None:
    """Best sequence of runs from the head (index 0) to the toe (index n_bands)."""
    standard_mode = pr.curtailment == "standard_lengths" and not least
    min_q = max(1, round(pr.min_zone_length / STEP))
    q_step = round(LENGTH_STEP / STEP)
    max_len = pr.max_bar_length
    lap_limit = MAX_RATIO_AT_LAPS * ac * (1 + 1e-9)
    # best[(p, cage)] = (penalty, weight, previous state, run)
    best: dict[tuple, tuple] = {(0, None): (0, 0.0, None, None)}
    frontier = [0]
    seen = {0}
    while frontier:
        p = min(frontier)
        frontier.remove(p)
        states = [(k, v) for k, v in best.items() if k[0] == p]
        for (_, prev), (pen, w, _, _) in states:
            for c in cages:
                if prev is not None and not _fits_below(c, prev):
                    continue
                laps = _laps(settings, c, c)  # lap lengths below a run ending above the toe
                ext = max(laps)
                lengths = set()
                remaining = n_bands - p
                if remaining * STEP <= max_len + 1e-9:
                    lengths.add(remaining)
                if standard_mode:
                    for s in pr.standard_bar_lengths:
                        q = math.floor((s - ext) / STEP + 1e-9)
                        if 0 < q < remaining:
                            lengths.add(q)
                for q in range(min_q, remaining, q_step):
                    if q * STEP + ext <= max_len + 1e-9:
                        lengths.add(q)
                for q in lengths:
                    e = p + q
                    at_toe = e == n_bands
                    if q < min_q and not (p == 0 and at_toe):  # only a short pile may be one short run
                        continue
                    if not fits(c, p, e):
                        continue
                    if prev is not None and pr.splice == "lap" and prev.area + c.area > lap_limit:
                        continue
                    run_ext = tuple(0.0 for _ in c.rings) if at_toe else laps
                    joint = "toe" if at_toe else pr.splice
                    run = Run(round(head - p * STEP, 3), round(head - e * STEP, 3), c, run_ext, joint)
                    if max(run.bar_lengths()) > max_len + 1e-9:
                        continue
                    penalty = pen
                    if standard_mode and not any(
                        abs(run.bar_lengths()[0] - s) < 0.01 for s in pr.standard_bar_lengths
                    ):
                        penalty += 1
                    cand = (penalty, w + run.weight, (p, prev), run)
                    key = (e, c)
                    if key not in best or cand[:2] < best[key][:2]:
                        best[key] = cand
                    if e not in seen and not at_toe:
                        seen.add(e)
                        frontier.append(e)
    ends = [(v[:2], k) for k, v in best.items() if k[0] == n_bands]
    if not ends:
        return None
    key = min(ends)[1]
    runs = []
    while best[key][3] is not None:
        runs.append(best[key][3])
        key = best[key][2]
    return runs[::-1]


def _run_dict(r: Run, band_util: dict, head: float) -> dict:
    p = round((head - r.top) / STEP)
    e = round((head - r.bottom) / STEP)
    util = float(band_util[r.cage][p:e].max()) if e > p else 0.0
    return {
        "top": r.top,
        "bottom": r.bottom,
        "length_m": round(r.length, 2),
        "cage": r.cage.to_dict(),
        "bar_lengths_m": [round(x, 2) for x in r.bar_lengths()],
        "lap_below_m": [round(x, 2) for x in r.extension],
        "joint": r.joint,
        "utilisation": round(util, 3),
        "weight_kg": round(r.weight, 1),
    }
