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
from .piles import MAX_RATIO_AT_LAPS, Arrangement, _families, _utilisation, crack_utilisation, design_top

STEP = 0.05  # m, level grid
LENGTH_STEP = 0.25  # m, run lengths tried for least steel


@dataclass(frozen=True)
class Run:
    top: float
    bottom: float
    cage: Arrangement
    extension: tuple[float, ...]  # m below ``bottom`` for each row (lap), 0 at the toe
    joint: str  # "lap", "coupler" or "toe"
    above: tuple[float, ...] = ()  # m above ``top`` for each row: into the element over the pile head

    @property
    def length(self) -> float:
        return self.top - self.bottom

    def _above(self) -> tuple[float, ...]:
        return self.above or tuple(0.0 for _ in self.extension)

    def bar_lengths(self) -> list[float]:
        return [a + self.length + e for a, e in zip(self._above(), self.extension, strict=True)]

    @property
    def weight(self) -> float:
        return sum(
            r.area / 1e6 * STEEL_DENSITY * length
            for r, length in zip(self.cage.rings, self.bar_lengths(), strict=True)
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


def anchorage(settings: DesignSettings, cage: Arrangement) -> tuple[float, ...]:
    """Length (m) each row runs on above the pile head into the element over it, rounded up to 50 mm."""
    f = settings.piles.head_anchorage_factor
    return tuple(math.ceil(f * r.diameter / 50 - 1e-9) * 0.05 for r in cage.rings)


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
    qp: pd.DataFrame | None = None,
    head_may_fail: bool = False,
    head_couplers: bool = False,
    max_cages: int | None = None,
    length_step: float = LENGTH_STEP,
) -> dict:
    """Runs from the head down, the head one with ``top_cage``.

    ``head_may_fail``: the head cage is taken as it is where it does not carry the loads (a cage set
    by the user, or the strongest there is), and the zones below are still designed.
    ``head_couplers``: the head cage is spliced with couplers, whatever the project's splices.
    ``max_cages`` and ``length_step`` (m): fewer cages and coarser run lengths, for a quicker search
    (Standard design).
    """
    pr = settings.piles
    # The design may run up to a face inside the slab (see ``design_top``); the bars run on into the
    # slab from the pile's top level.
    head = design_top(pile, loads)
    into_slab = 0.0 if pile.head_level is None else head - pile.head_level
    toe = float(loads["Z"].min())
    n_bands = max(1, math.ceil((head - toe) / STEP - 1e-9))
    band = np.clip(((head - loads["Z"].to_numpy()) / STEP).astype(int), 0, n_bands - 1)
    if qp is not None and len(qp):
        qp_band = np.clip(((head - qp["Z"].to_numpy()) / STEP).astype(int), 0, n_bands - 1)
    ac = math.pi * pile.diameter**2 / 4
    volume = ac / 1e6 * (head - toe)

    cages = _cages(pile, settings, top_cage, area_min)
    ok, band_util = {}, {}
    for c in cages:
        u = _utilisation(pile, c, settings, loads)
        strength, crack = np.zeros(n_bands), np.zeros(n_bands)
        np.maximum.at(strength, band, u)
        if qp is not None and len(qp):
            np.maximum.at(crack, qp_band, crack_utilisation(pile, c, settings, qp))
        worst = np.maximum(strength, crack)
        band_util[c] = (strength, crack)
        ok[c] = np.concatenate([[0], np.cumsum(worst > 1.0 + 1e-9)])  # failing bands before each index
    cages = _prune(cages, ok)
    if max_cages is not None and len(cages) > max_cages:
        cages = _spread(cages, top_cage, max_cages)

    failing = {id(c): ok[c] for c in cages}  # by identity: hashing a cage is slow, and this runs often

    def fits(c: Arrangement, p: int, e: int) -> bool:
        bad = failing[id(c)] if id(c) in failing else ok[c]
        return (head_may_fail and p == 0 and c == top_cage) or bad[e] - bad[p] == 0

    coupled = {top_cage} if head_couplers else set()
    runs = _search(
        pr,
        settings,
        cages,
        fits,
        n_bands,
        head,
        ac,
        coupled=coupled,
        into_slab=into_slab,
        length_step=length_step,
    )
    unified = _search(
        pr, settings, [top_cage], fits, n_bands, head, ac, least=True, coupled=coupled, into_slab=into_slab
    )
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
    if runs and runs[0].above and max(runs[0].above) > 0:
        notes.append(
            f"The top bars run on {' / '.join(f'{x:g}' for x in runs[0].above)} m above "
            f"{'the design face' if into_slab else 'the pile head'} into the element over it "
            f"({pr.head_anchorage_factor:g}φ from the pile's top level), counted in their lengths and weight."
        )
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
        "couplers": sum(r.cage.bar_count for r in runs if r.joint == "coupler"),
        "notes": notes,
    }


def _spread(cages: list[Arrangement], top_cage: Arrangement, n: int) -> list[Arrangement]:
    """At most ``n`` cages, the head cage among them, spread evenly over the steel areas (lightest
    first): fewer steps down the pile, found much sooner (Standard design)."""
    rest = sorted((c for c in cages if c != top_cage), key=lambda a: (a.area, a.rows))
    if len(rest) >= n:
        picks = np.linspace(0, len(rest) - 1, n - 1).round().astype(int)
        rest = [rest[i] for i in sorted(set(picks.tolist()))]
    return rest + ([top_cage] if top_cage in cages else [])


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


def _search(
    pr,
    settings,
    cages,
    fits,
    n_bands,
    head,
    ac,
    least=False,
    coupled=frozenset(),
    into_slab=0.0,
    length_step=LENGTH_STEP,
) -> list[Run] | None:
    """Best sequence of runs from the head (index 0) to the toe (index n_bands).

    Cages are handled by their place in ``cages`` and a run is built only when it is kept: a pile
    tries about a million runs, and hashing cages and building runs took most of the time."""
    standard_mode = pr.curtailment == "standard_lengths" and not least
    standard = tuple(pr.standard_bar_lengths)
    min_q = max(1, round(pr.min_zone_length / STEP))
    q_step = round(length_step / STEP)
    max_len = pr.max_bar_length
    lap_limit = MAX_RATIO_AT_LAPS * ac * (1 + 1e-9)
    lapped_splice = pr.splice == "lap"
    n = len(cages)
    # What each cage needs, worked out once (by its place in ``cages``).
    is_coupled = [c in coupled for c in cages]
    area = [c.area for c in cages]
    ring_mass = [tuple(r.area / 1e6 * STEEL_DENSITY for r in c.rings) for c in cages]
    zeros = [tuple(0.0 for _ in c.rings) for c in cages]
    laps = [zeros[i] if is_coupled[i] else _laps(settings, c, c) for i, c in enumerate(cages)]
    head_above = [tuple(max(0.0, round(x - into_slab, 3)) for x in anchorage(settings, c)) for c in cages]
    below = [[_fits_below(c, prev) for c in cages] for prev in cages]  # below[prev][c]
    # by_p[p] = {cage index or None: (penalty, weight, previous state, run parts)}; the states at a
    # level, in the order they were found.
    by_p: dict[int, dict] = {0: {None: (0, 0.0, None, None)}}
    frontier = [0]
    seen = {0}
    while frontier:
        p = min(frontier)
        frontier.remove(p)
        for prev, (pen, w, _, _) in list(by_p.get(p, {}).items()):
            for ci in range(n):
                if prev is not None and not below[prev][ci]:
                    continue
                c = cages[ci]
                lap = laps[ci]
                above = head_above[ci] if p == 0 else ()
                ext = max(lap) + max(above, default=0.0)
                lengths = set()
                remaining = n_bands - p
                if remaining * STEP <= max_len + 1e-9:
                    lengths.add(remaining)
                if standard_mode:
                    for s in standard:
                        q = math.floor((s - ext) / STEP + 1e-9)
                        if 0 < q < remaining:
                            lengths.add(q)
                for q in range(min_q, remaining, q_step):
                    if q * STEP + ext <= max_len + 1e-9:
                        lengths.add(q)
                over_lap = (
                    prev is not None
                    and lapped_splice
                    and not is_coupled[prev]
                    and area[prev] + area[ci] > lap_limit
                )
                if over_lap:
                    continue
                top = round(head - p * STEP, 3)
                above_rows = above or zeros[ci]
                masses = ring_mass[ci]
                for q in lengths:
                    e = p + q
                    at_toe = e == n_bands
                    if q < min_q and not (p == 0 and at_toe):  # only a short pile may be one short run
                        continue
                    if not fits(c, p, e):
                        continue
                    run_ext = zeros[ci] if at_toe else lap
                    bottom = round(head - e * STEP, 3)
                    length = top - bottom
                    bars = [a + length + x for a, x in zip(above_rows, run_ext, strict=True)]
                    if max(bars) > max_len + 1e-9:
                        continue
                    penalty = pen
                    if standard_mode and not any(abs(bars[0] - s) < 0.01 for s in standard):
                        penalty += 1
                    weight = w + sum(m * b for m, b in zip(masses, bars, strict=True))
                    level = by_p.setdefault(e, {})
                    kept = level.get(ci)
                    if kept is None or (penalty, weight) < kept[:2]:
                        joint = "toe" if at_toe else "coupler" if is_coupled[ci] else pr.splice
                        level[ci] = (penalty, weight, (p, prev), (top, bottom, ci, run_ext, joint, above))
                    if e not in seen and not at_toe:
                        seen.add(e)
                        frontier.append(e)
    ends = [(v[:2], ci) for ci, v in by_p.get(n_bands, {}).items()]
    if not ends:
        return None
    key = (n_bands, min(ends)[1])
    runs = []
    while by_p[key[0]][key[1]][3] is not None:
        top, bottom, ci, run_ext, joint, above = by_p[key[0]][key[1]][3]
        runs.append(Run(top, bottom, cages[ci], run_ext, joint, above))
        key = by_p[key[0]][key[1]][2]
    return runs[::-1]


def _run_dict(r: Run, band_util: dict, head: float) -> dict:
    p = round((head - r.top) / STEP)
    e = round((head - r.bottom) / STEP)
    strength, crack = band_util[r.cage]
    util = float(strength[p:e].max()) if e > p else 0.0
    crack_util = float(crack[p:e].max()) if e > p else 0.0
    return {
        "top": r.top,
        "bottom": r.bottom,
        "length_m": round(r.length, 2),
        "cage": r.cage.to_dict(),
        "bar_lengths_m": [round(x, 2) for x in r.bar_lengths()],
        "lap_below_m": [round(x, 2) for x in r.extension],
        "above_head_m": [round(x, 2) for x in r.above],
        "joint": r.joint,
        "utilisation": round(util, 3),  # ULS N–M
        "crack_utilisation": round(crack_util, 3),  # SLS (QP): crack width over its limit
        "weight_kg": round(r.weight, 1),
    }
