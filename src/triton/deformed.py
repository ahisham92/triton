"""The deformed shape of a section's whole structure, one load combination at a time, for the 3D view.

It is the Design tab's displacement estimate (triton/design/deflection.py) drawn together: every pile
and king pile (each plan position, not only the one that moves most), the sheet pile wall in 1 m
strips and the deck's plates in 1 m strips, with the same settings: how the piles and walls are held
(tied at the deck by default), the stiffness, long term, and the phase the movement is measured from.
So it has the same limits: it is not a Plaxis displacement result, and the ground's own movement is
not in it.

* Piles and king piles: M3 moves the member in its local 2 direction and M2 in its local 3, which the
  workbook check found as X or Y. Tied at the deck, every head under the deck moves with it.
* Sheet pile wall: M11 per metre moves it across the wall, turned to the combi wall's sign as the
  estimate does (the sheets and the king piles hold back the same ground).
* Deck (slabs and beams): moved sideways with the piles' heads (the deck's movement when tied, else
  the mean head movement), and bent vertically between the piles and king piles under each 1 m strip,
  as the estimate's strips (sagging down).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .axes import sag_factor
from .design import deflection as D
from .design.runner import factored_elements
from .project import (
    BeamInput,
    DeflectionSettings,
    DesignSettings,
    Section,
    SlabInput,
    with_project_grades,
)
from .validation import ImportResult

MAX_POINTS = 60  # per member curve sent to the page


def _thin(rows: list[list[float]]) -> list[list[float]]:
    if len(rows) <= MAX_POINTS:
        return rows
    idx = np.unique(np.linspace(0, len(rows) - 1, MAX_POINTS).round().astype(int))
    return [rows[i] for i in idx]


def _sheets(section: Section, workbook: ImportResult, phases: ImportResult | None, baseline: str) -> dict:
    """Element -> combination -> sheet as the estimate reads them, with the phase the movement is
    measured from added where it is not one of the section's combinations."""
    plain = section.model_copy(update={"load_factors": []})
    sheets = factored_elements(plain, workbook)
    wanted = baseline.strip().lower()
    if wanted and phases is not None:
        for name, combos in factored_elements(plain, phases).items():
            for combo, sheet in combos.items():
                if combo.lower() == wanted and not any(c.lower() == wanted for c in sheets.get(name, {})):
                    sheets.setdefault(name, {})[combo] = sheet
    return sheets


def combinations(section: Section, workbook: ImportResult) -> list[str]:
    """The combinations with moments in any pile, wall or plate, in the section's order; never the
    phase the movement is measured from."""
    sheets = factored_elements(section.model_copy(update={"load_factors": []}), workbook)
    base = section.deflection.baseline.strip().lower()
    seen: set[str] = set()
    for own in sheets.values():
        for c, s in own.items():
            if not s.frame.empty and {"M_2", "M_3", "M_11"} & set(s.frame.columns) and c.lower() != base:
                seen.add(c)
    order = [c for c in section.combinations if c in seen]
    return order + sorted(seen - set(order))


def shapes(
    settings: DesignSettings,
    section: Section,
    workbook: ImportResult,
    combination: str,
    results: dict | None = None,
    phases: ImportResult | None = None,
) -> dict[str, Any]:
    ds: DeflectionSettings = section.deflection.model_copy(update={"combination": combination})
    est = D.estimate_shapes(settings, section, workbook, combination, results, phases)
    missing: list[str] = []
    members = []
    for m in est["members"]:
        if m["combination"] != combination:
            # Not in this combination: the estimate fell back to another one for it.
            if m["element"] not in missing:
                missing.append(m["element"])
            continue
        members.append({k: m[k] for k in ("element", "kind", "x", "y")} | {"points": _thin(m["points"])})
    notes = [f"{s}" for s in est.get("skipped") or []]
    if ds.baseline.strip():
        notes.append(f"The movement after {ds.baseline.strip()}: its moments taken off first.")
    deck = est.get("deck")
    if deck:
        notes.append(deck["words"])
    # The deck moves sideways with the pile heads (tied: all the same), else their mean.
    heads = [m["points"][-1] for m in members if m["kind"] == "pile"] or [m["points"][-1] for m in members]
    shift = (
        [float(np.mean([h[1] for h in heads])), float(np.mean([h[2] for h in heads]))] if heads else [0, 0]
    )
    sheets = _sheets(section, workbook, phases, ds.baseline)
    axes = {a["element"]: a for a in getattr(workbook, "axes", None) or []}
    supports = D._supports(sheets, section)
    plates = []
    for name, el in section.elements.items():
        if isinstance(el, SlabInput | BeamInput):
            o = {c: s for c, s in (sheets.get(name) or {}).items() if not s.frame.empty}
            if combination not in o:
                missing.append(name)
                continue
            p = _plate(name, el, settings, ds, o, combination, axes.get(name), supports)
            if p:
                plates.append(p)
    peak = max(
        [abs(v) for m in members for p in m["points"] for v in p[1:]]
        + [abs(p[1]) for pl in plates for st in pl["strips"] for p in st["points"]]
        + [abs(v) for v in shift]
        + [0.0]
    )
    return {
        "estimate": True,
        "note": D.ESTIMATE,
        "combination": combination,
        "settings": ds.model_dump(mode="json"),
        "members": members,
        "walls": [],
        "plates": plates,
        "deck_shift_mm": [round(float(v), 2) for v in shift],
        "max_mm": round(peak, 2),
        "missing": missing,
        "notes": notes,
    }


def _plate(name, element, settings, ds, sheets, combination, axes, supports) -> dict[str, Any] | None:
    local = (axes or {}).get("local") or {}
    if not {local.get("1"), local.get("2")} <= {"X", "Y"}:
        return None
    frame = sheets[combination].frame
    if not {"M_11", "M_22"} <= set(frame.columns):
        return None
    element = with_project_grades(element, settings.materials, settings.durability)
    z = float(frame["Z"].median())
    f = frame[["X", "Y", "M_11", "M_22"]].dropna()
    base, _ = D.pick_baseline(sheets, ["M_11", "M_22"], ds.baseline, combination)
    if base:
        # As the estimate: the same mesh in every phase, the base moments taken off at the same points.
        def at_points(fr: pd.DataFrame) -> pd.DataFrame:
            return fr.assign(X=fr["X"].round(2), Y=fr["Y"].round(2)).groupby(["X", "Y"]).mean()

        now = at_points(f)
        then = at_points(sheets[base].frame[["X", "Y", "M_11", "M_22"]].dropna()).reindex(now.index)
        f = (now - then.fillna(0.0)).reset_index()
    h = element.thickness if isinstance(element, SlabInput) else element.depth
    e_c, _ = D._e_concrete(element.concrete, settings, ds)
    ei = e_c * h**3 / 12 * 1e-6
    sag, _ = sag_factor(settings.plate_positive_moment, axes)
    # Slabs span across the quay between the pile rows (local 1); beams along their length.
    if isinstance(element, SlabInput):
        k = "1"
    else:
        span = {a: float(np.ptp(f[a].to_numpy(float))) for a in ("X", "Y")}
        k = "1" if span[local["1"]] >= span[local["2"]] else "2"
    along = local[k]
    col = f"M_{k}{k}"
    across = "Y" if along == "X" else "X"
    strips = []
    for strip, g in f.groupby(np.floor(f[across] / D.STRIP)):
        centre = (strip + 0.5) * D.STRIP
        near = (
            supports[np.abs(supports[:, 1 if across == "Y" else 0] - centre) <= D.STRIP]
            if len(supports)
            else []
        )
        s_all = g[along].to_numpy(float)
        held = D._strip_supports(
            near[:, 0 if along == "X" else 1] if len(near) else [], s_all.min(), s_all.max()
        )
        if len(held) < 2:
            continue
        m = g.groupby((g[along] / D.STEP).round() * D.STEP)[col].mean().sort_index()
        if len(m) < 3:
            continue
        s, w = D.between_supports(m.index.to_numpy(float), sag * m.to_numpy(float) / ei, held)
        rows = [[round(float(a), 3), round(float(b) * 1e3, 2)] for a, b in zip(s, w, strict=True)]
        strips.append({"at": round(float(centre), 3), "points": _thin(rows)})
    if not strips:
        return None
    return {"element": name, "along": along, "across": across, "z": round(z, 3), "strips": strips}
