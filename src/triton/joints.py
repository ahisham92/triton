"""Expansion joints: where to break the deck and beams of a berth into segments.

The berth is laid out as its straight runs one after the other (the lengths given on the Sections
tab, else the berth length from Costing as one run; never the model's length, which covers only a
short piece of the berth), measured as chainage from its start. Joints are placed by the rules in
Design settings:

* No segment is longer than the longest segment allowed (58 m by default, the office's joint
  spacing) nor shorter than the shortest; with a preferred length the berth gets about that length
  per segment instead of the fewest joints.
* A joint sits mid-way between two rows of piles (the deck cantilevers half a bay each side), at a
  row (a doubled row, one each side of the joint), or anywhere (every 0.5 m). The pile rows are the
  piles' positions along the berth in the workbook (their spacing, a row every s from half a bay
  into each run) unless the spacing and first row are given.
* A joint keeps a clear distance from every fender, bollard or other item of quay furniture: the
  positions given for the section, else the Furniture tab's items at their spacing (before they are
  moved clear of the joints), else the items priced each on the Costing tab, one at each end of
  the berth and evenly spaced between at their spacing.
* Each corner of a corner berth is a joint (a setting), and so is every joint set by hand.

Between two fixed joints (corners, joints by hand, the ends of the berth) the fewest segments that
meet the longest length are used, and of the allowed positions the ones that make the segments as
nearly equal as possible are picked (least sum of squares from the mean length, found exactly by
dynamic programming). When no position meets every rule, the clearance to furniture is dropped
first, then the pile rows, and the layout says so.

With the setting on, each beam's and slab's temperature and shrinkage restraint check takes as its
length between movement joints the longest segment of its own part of the berth, and lists the
restraint crack width for every other segment length too.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np

from .project import DesignSettings, ExpansionJoints, PileInput, Section, SectionJoints

STEP = 0.5  # m: positions tried when a joint may go anywhere
TOL = 1e-6


# --- Inputs from the model ------------------------------------------------------------------------


def pile_rows(
    positions: list[list[float]], parts: list[Any] | None = None, along: str = "Y"
) -> tuple[float | None, list[float]]:
    """The spacing of the rows of piles along the berth (median gap between rows) and the row
    positions along it, from the piles' plan positions. Piles within 0.3 m along the berth are one
    row. A corner berth's piles are turned with their part first."""
    from .alignment import part_of, rotate_points

    if not positions:
        return None, []
    pts = np.asarray(positions, float)[:, :2]
    groups: list[np.ndarray] = []
    if parts:
        owner = part_of(parts, pts[:, 0], pts[:, 1])
        for part in parts:
            sel = pts[owner == part.index]
            if len(sel):
                x, y = rotate_points(part, sel[:, 0], sel[:, 1])
                groups.append(np.asarray(y if along == "Y" else x))
    else:
        groups.append(pts[:, 1] if along == "Y" else pts[:, 0])
    gaps: list[float] = []
    rows_all: list[float] = []
    for g in groups:
        rows: list[list[float]] = []
        for v in sorted(g.tolist()):
            if rows and v - rows[-1][-1] <= 0.3:
                rows[-1].append(v)
            else:
                rows.append([v])
        centres = [float(np.mean(r)) for r in rows]
        rows_all.extend(centres)
        gaps.extend(b - a for a, b in zip(centres, centres[1:], strict=False) if b - a > 0.5)
    if not gaps:
        return None, rows_all
    return round(float(np.median(gaps)), 3), rows_all


def furniture_from_costing(section: Section, berth: float) -> list[dict[str, Any]]:
    """Items priced each on the Costing tab, one at each end of the berth and evenly between."""
    out = []
    for item in section.costing.items:
        if item.unit != "each" or not item.name.strip():
            continue
        n = (
            item.count
            if item.count is not None
            else (math.floor(berth / item.spacing + TOL) + 1 if item.spacing else None)
        )
        if not n:
            continue
        spots = [berth / 2] if n == 1 else np.linspace(0.0, berth, n).tolist()
        out += [{"name": item.name.strip(), "chainage": round(float(c), 3)} for c in spots]
    return out


def berth_runs(section: Section, parts: list[Any] | None) -> tuple[list[float], str]:
    """The lengths of the berth's straight runs, and where they came from. Never the model's own
    length: the model covers only a short piece of the berth."""
    sj = section.joints
    if sj.runs:
        return list(sj.runs), "the runs given on the Sections tab"
    berth = section.costing.berth_length
    if berth:
        return [berth], "the berth length on the Costing tab"
    return [], ""


# --- Placing the joints ---------------------------------------------------------------------------


def _candidates(
    lo: float,
    hi: float,
    runs: list[tuple[float, float]],
    spacing: float | None,
    first: float | None,
    position: str,
    furniture: list[float],
    clearance: float,
    min_seg: float,
) -> list[float]:
    """Chainages between ``lo`` and ``hi`` (at least the shortest segment from both) a joint may take."""
    out: list[float] = []
    if position == "anywhere" or not spacing:
        k0 = math.ceil((lo + min_seg) / STEP - TOL)
        out = [k * STEP for k in range(k0, math.floor((hi - min_seg) / STEP + TOL) + 1)]
    else:
        f = spacing / 2 if first is None else first
        shift = 0.5 if position == "midway" else 0.0
        for a, b in runs:
            if b <= lo or a >= hi:
                continue
            k = math.floor((max(a, lo) - a - f) / spacing - shift) - 1
            while True:
                c = a + f + (k + shift) * spacing
                if c > min(b, hi) + TOL:
                    break
                if a + TOL < c < b - TOL:
                    out.append(round(c, 6))
                k += 1
        out = sorted(c for c in set(out) if lo + min_seg - TOL <= c <= hi - min_seg + TOL)
    if clearance > 0:
        out = [c for c in out if all(abs(c - q) >= clearance - TOL for q in furniture)]
    return out


def _best(
    lo: float, hi: float, cands: list[float], n: int, max_seg: float, min_seg: float
) -> list[float] | None:
    """The ``n - 1`` joints from ``cands`` that split [lo, hi] into ``n`` segments each within
    [min_seg, max_seg], nearest to equal (least sum of squares). None when none can."""
    nodes = [lo, *cands, hi]
    m = len(nodes)
    target = (hi - lo) / n
    inf = math.inf
    cost = [inf] * m
    cost[0] = 0.0
    back: list[list[int]] = []
    for _ in range(n):
        new = [inf] * m
        prev = [-1] * m
        for j in range(1, m):
            for i in range(j - 1, -1, -1):
                g = nodes[j] - nodes[i]
                if g > max_seg + TOL:
                    break
                if g < min_seg - TOL or cost[i] == inf:
                    continue
                c = cost[i] + (g - target) ** 2
                if c < new[j]:
                    new[j], prev[j] = c, i
        back.append(prev)
        cost = new
    if cost[m - 1] == inf:
        return None
    out, j = [], m - 1
    for step in range(n - 1, -1, -1):
        j = back[step][j]
        if step:
            out.append(nodes[j])
    return sorted(out)


def _split(lo: float, hi: float, rules: ExpansionJoints, cands: list[float]) -> list[float] | None:
    length = hi - lo
    if length <= rules.max_segment + TOL:
        return []
    n0 = math.ceil(length / rules.max_segment - TOL)
    if rules.preferred_segment:
        n0 = max(n0, round(length / rules.preferred_segment))
    for n in range(n0, n0 + 6):
        if (hi - lo) / n < rules.min_segment - TOL:
            break
        got = _best(lo, hi, cands, n, rules.max_segment, rules.min_segment)
        if got is not None:
            return got
    return None


def place_joints(
    rules: ExpansionJoints,
    runs: list[float],
    spacing: float | None = None,
    first_row: float | None = None,
    furniture: list[dict[str, Any]] | None = None,
    fixed: list[float] | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    """The joints and segments of a berth made of ``runs`` (m), by ``rules``."""
    furniture = furniture or []
    spans: list[tuple[float, float]] = []
    c = 0.0
    for r in runs:
        spans.append((c, c + r))
        c += r
    total = c
    warnings: list[str] = []
    joints: dict[float, str] = {}
    if rules.at_corners:
        for a, _ in spans[1:]:
            joints[round(a, 6)] = "corner"
    for f in fixed or []:
        if 0 < f < total:
            joints.setdefault(round(float(f), 6), "by hand")
        else:
            warnings.append(f"The joint set by hand at {f:g} m is not inside the berth (0 to {total:.1f} m).")
    items = [float(q["chainage"]) for q in furniture]
    position = rules.position if spacing else "anywhere"
    if rules.position != "anywhere" and not spacing:
        warnings.append(
            "No pile row spacing (none found in the workbook): joints are placed anywhere. Give the "
            "spacing on the Sections tab."
        )
    if not manual:
        bounds = [0.0, *sorted(joints), total]
        for lo, hi in zip(bounds, bounds[1:], strict=False):
            got = None
            tries = [(position, rules.furniture_clearance, "")]
            if rules.furniture_clearance > 0:
                tries.append((position, 0.0, "the clearance to furniture was dropped"))
            if position != "anywhere":
                tries.append(("anywhere", 0.0, "the clearance to furniture and the pile rows were dropped"))
            for pos, clear, why in tries:
                cands = _candidates(lo, hi, spans, spacing, first_row, pos, items, clear, rules.min_segment)
                got = _split(lo, hi, rules, cands)
                if got is not None:
                    if why:
                        warnings.append(f"Between {lo:.1f} and {hi:.1f} m no position met every rule: {why}.")
                    break
            if got is None:
                n = math.ceil((hi - lo) / rules.max_segment - TOL)
                got = [lo + (hi - lo) * k / n for k in range(1, n)]
                warnings.append(
                    f"Between {lo:.1f} and {hi:.1f} m the rules cannot all be met (shortest segment "
                    f"{rules.min_segment:g} m against the longest {rules.max_segment:g} m): split equally."
                )
            for g in got:
                joints.setdefault(round(g, 6), "rules")
    chain = sorted(joints)
    ends = [0.0, *chain, total]

    def run_of(x: float) -> int:
        return next((i for i, (a, b) in enumerate(spans) if a - TOL <= x <= b + TOL), len(spans) - 1)

    segments = []
    for i, (a, b) in enumerate(zip(ends, ends[1:], strict=False)):
        touched = [k for k, (ra, rb) in enumerate(spans) if ra < b - TOL and rb > a + TOL]
        seg = {
            "name": f"S{i + 1}",
            "start": round(a, 3),
            "end": round(b, 3),
            "length": round(b - a, 3),
            "runs": touched,
        }
        if seg["length"] > rules.max_segment + TOL:
            warnings.append(f"{seg['name']} is {seg['length']:.1f} m, longer than {rules.max_segment:g} m.")
        segments.append(seg)
    out_joints = []
    for x in chain:
        k = run_of(x)
        near = [abs(x - q) for q in items]
        out_joints.append(
            {
                "chainage": round(x, 3),
                "run": k,
                "in_run": round(x - spans[k][0], 3),
                "from": joints[x],
                "nearest_furniture_m": round(min(near), 2) if near else None,
            }
        )
    longest = [
        max((s["length"] for s in segments if k in s["runs"]), default=None) for k in range(len(spans))
    ]
    rows = []
    if spacing:
        f = spacing / 2 if first_row is None else first_row
        for a, b in spans:
            x = a + f
            while x < b - TOL:
                rows.append(round(x, 3))
                x += spacing
    return {
        "berth_length_m": round(total, 3),
        "runs": [
            {"name": f"Run {i + 1}", "start": round(a, 3), "end": round(b, 3), "length": round(b - a, 3)}
            for i, (a, b) in enumerate(spans)
        ],
        "joints": out_joints,
        "segments": segments,
        "longest_by_run": longest,
        "longest_m": max((s["length"] for s in segments), default=None),
        "pile_rows": rows,
        "furniture": furniture,
        "warnings": warnings,
        "position": position,
    }


# --- A section --------------------------------------------------------------------------------------


def section_joints(
    settings: DesignSettings,
    section: Section,
    raw: dict[str, Any],
    parts: list[Any] | None = None,
    along: str = "Y",
    furniture_at: Callable[[float], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The joint layout of a section, from its inputs and the piles in the workbook (``raw``: the
    workbook's elements, as ``ImportResult.elements()``). ``furniture_at(berth length)``: the quay
    furniture's positions along the berth (the Furniture tab), used before the Costing items."""
    rules = settings.joints
    sj: SectionJoints = section.joints
    runs, runs_from = berth_runs(section, parts)
    if not runs:
        return {
            "runs": [],
            "joints": [],
            "segments": [],
            "warnings": [],
            "text": "Give the berth length on the Costing tab (or the straight runs on the Sections tab) to "
            "place the joints. Until then each beam and slab uses its own length between movement joints.",
        }
    spacing, spacing_from = sj.pile_spacing, "given"
    if spacing is None:
        positions: list[list[float]] = []
        for name, el in section.elements.items():
            if isinstance(el, PileInput):
                positions += _pile_positions(raw.get(name) or {})
        spacing, _ = pile_rows(positions, parts, along)
        spacing_from = "the piles in the workbook" if spacing else "none"
    total = sum(runs)
    if sj.furniture:
        furniture = [{"name": f.name or "Item", "chainage": f.chainage} for f in sj.furniture]
        furniture_from = "the positions given on the Sections tab"
    elif furniture_at is not None and (furniture := furniture_at(total)):
        furniture_from = "the quay furniture at its spacing (Furniture tab)"
    else:
        furniture = furniture_from_costing(section, total)
        furniture_from = "the items priced each on the Costing tab" if furniture else "none"
    out = place_joints(rules, runs, spacing, sj.first_row, furniture, sj.fixed, sj.mode == "manual")
    if parts and len(parts) > 1 and len(runs) == 1:
        out["warnings"].insert(
            0,
            f"The model has {len(parts)} parts (a corner berth) but the berth is one run: give the length "
            "of each straight run on the Sections tab to put joints at the corners.",
        )
    out.update(
        {
            "runs_from": runs_from,
            "pile_spacing_m": spacing,
            "pile_spacing_from": spacing_from,
            "first_row_m": sj.first_row if sj.first_row is not None else (spacing / 2 if spacing else None),
            "furniture_from": furniture_from,
            "rules": rules.model_dump(),
            "mode": sj.mode,
            "parts_match_runs": bool(parts) and len(parts) == len(runs),
        }
    )
    n = len(out["joints"])
    segs = out["segments"]
    out["text"] = (
        f"{n} joint{'s' if n != 1 else ''} along {out['berth_length_m']:.1f} m of berth (from {runs_from}): "
        f"{len(segs)} segment{'s' if len(segs) != 1 else ''} of "
        + ", ".join(f"{s['length']:.1f}" for s in segs)
        + " m."
    )
    return out


def _pile_positions(sheets: dict[str, Any]) -> list[list[float]]:
    """Plan positions of an element's piles (the heads' X, Y), as the runner takes them."""
    from .design.runner import _positions

    return _positions(sheets)


def restraint_length(layout: dict[str, Any] | None, part_index: int | None) -> float | None:
    """The length between movement joints for a beam or slab (of a corner berth's part)."""
    if not layout or not layout.get("segments"):
        return None
    by_run = layout.get("longest_by_run") or []
    if part_index is not None and layout.get("parts_match_runs") and part_index < len(by_run):
        return by_run[part_index]
    return layout.get("longest_m")


def segment_lengths(layout: dict[str, Any] | None, part_index: int | None) -> list[float]:
    """The distinct segment lengths (longest first) a beam or slab (of a part) runs through."""
    if not layout or not layout.get("segments"):
        return []
    segs = layout["segments"]
    if part_index is not None and layout.get("parts_match_runs"):
        segs = [s for s in segs if part_index in s["runs"]]
    return sorted({round(s["length"], 1) for s in segs}, reverse=True)


def joints_drawing(
    layout: dict[str, Any], settings: Any, title: str = "", width: float = 20.0
) -> dict[str, Any]:
    """The joint layout as a drawing (for ``dxf.to_dxf``): the berth laid out straight at 1:1 in mm, a
    band ``width`` m deep, its joints, pile rows, furniture and corners, and each segment's length."""
    mm = 1000.0
    L = layout["berth_length_m"] * mm
    w = width * mm
    h = 1200.0  # text height, mm
    items: list[dict[str, Any]] = [{"type": "rect", "layer": "concrete", "a": [0.0, 0.0], "b": [L, w]}]
    for c in layout.get("pile_rows") or []:
        items.append({"type": "line", "layer": "zones", "a": [c * mm, 0.15 * w], "b": [c * mm, 0.85 * w]})
    for r in (layout.get("runs") or [])[1:]:
        x = r["start"] * mm
        items.append({"type": "line", "layer": "zones", "a": [x, -0.3 * w], "b": [x, 1.3 * w]})
        items.append({"type": "text", "layer": "text", "at": [x + h / 2, 1.3 * w], "h": h, "text": "Corner"})
    for f in layout.get("furniture") or []:
        x = f["chainage"] * mm
        items.append({"type": "circle", "layer": "zones", "c": [x, w + 1500.0], "r": 600.0})
        items.append(
            {"type": "text", "layer": "text", "at": [x + 800.0, w + 1000.0], "h": h * 0.6, "text": f["name"]}
        )
    for jt in layout.get("joints") or []:
        x = jt["chainage"] * mm
        items.append({"type": "line", "layer": "joints", "a": [x, -2000.0], "b": [x, w + 2000.0]})
        items.append(
            {
                "type": "text",
                "layer": "text",
                "at": [x + h / 3, -2000.0 - 1.5 * h],
                "h": h,
                "text": f"EJ {jt['chainage']:.1f}",
            }
        )
    for g in layout.get("segments") or []:
        mid = (g["start"] + g["end"]) / 2 * mm
        items.append(
            {
                "type": "text",
                "layer": "text",
                "at": [mid - 3 * h, w / 2 - h / 2],
                "h": h * 1.5,
                "text": f"{g['name']}  {g['length']:.1f} m",
            }
        )
    items.append(
        {
            "type": "text",
            "layer": "text",
            "at": [0.0, w + 6000.0],
            "h": h * 2,
            "text": title or "Expansion joints",
        }
    )
    layers = {
        "concrete": {"cad_layer": settings.concrete_cad_layer},
        "zones": {"cad_layer": settings.zones_cad_layer},
        "text": {"cad_layer": settings.text_cad_layer},
        "joints": {"cad_layer": "TRITON-JOINTS"},
    }
    box = [-2000.0, -2000.0 - 3 * h, L + 2000.0, w + 6000.0 + 2 * h]
    return {"layers": layers, "views": [{"items": items, "box": box}]}
