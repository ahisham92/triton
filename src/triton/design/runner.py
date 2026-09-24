"""Run the element designs of a project against its checked workbook."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import replace
from typing import Any

from ..axes import infer_axes
from ..elements import ElementType
from ..forces import scale_forces
from ..geometry import section_geometry
from ..importer import SheetData
from ..materials import SHEET_PILE_GRADES
from ..project import (
    BeamInput,
    CombiWallInput,
    DesignSettings,
    PileInput,
    Section,
    SheetPileInput,
    SlabInput,
    _now,
)
from ..validation import ImportResult
from .beams import design_beam
from .combi import design_combi_wall
from .governing import steel_sets, uls_frame
from .peaks import treat_peaks
from .piles import design_pile
from .slabs import design_slab
from .spw_design import design_spw


def king_piles(section: Section, geometry: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    """Plan position and radius (m) of every combi wall king pile in the section's workbook."""
    out = []
    for g in geometry:
        el = section.elements.get(g["element"])
        if isinstance(el, CombiWallInput):
            out += [(x, y, el.tube_diameter / 2000) for x, y, *_ in g.get("lines") or []]
    return out


def factored_elements(section: Section, workbook: ImportResult) -> dict[str, dict[str, SheetData]]:
    """element -> combination -> sheet, with the section's load multipliers applied to the forces
    and only the results inside the section's working zone (and below a sheet pile wall's top level)."""
    out = {}
    for element, combos in workbook.elements().items():
        out[element] = {}
        for combo, sheet in combos.items():
            f = section.factor_for(sheet.name)
            frame = sheet.frame if f == 1.0 else scale_forces(sheet.frame, f)
            if section.has_zone and {"X", "Y"} <= set(frame.columns):
                frame = frame[section.in_zone(frame["X"], frame["Y"])]
            wall = section.elements.get(element)
            if isinstance(wall, SheetPileInput) and wall.top_level is not None and "Z" in frame.columns:
                frame = frame[frame["Z"] <= wall.top_level + 1e-6]  # above it: in the capping beam
            out[element][combo] = sheet if frame is sheet.frame else replace(sheet, frame=frame)
    return out


def _positions(sheets: dict[str, SheetData]) -> list[list[float]]:
    """X, Y of every pile of an element in the workbook, working zone or not."""
    seen: set[tuple[float, float]] = set()
    for sheet in sheets.values():
        if {"X", "Y"} <= set(sheet.frame.columns):
            seen |= set(map(tuple, sheet.frame[["X", "Y"]].round(2).to_numpy().tolist()))
    return [list(p) for p in sorted(seen, key=lambda p: (p[1], p[0]))]


def _multiplier_note(section: Section, sheets: dict[str, SheetData]) -> str | None:
    factored = [
        f"{c} ×{section.factor_for(s.name):g}" for c, s in sheets.items() if section.factor_for(s.name) != 1.0
    ]
    return "Load multipliers applied: " + ", ".join(factored) + "." if factored else None


def _zone_note(section: Section) -> str | None:
    if not section.has_zone:
        return None
    parts = []
    for axis, lo, hi in (("X", section.x_min, section.x_max), ("Y", section.y_min, section.y_max)):
        if lo is not None or hi is not None:
            lo_s = "−∞" if lo is None else f"{lo:g}"
            hi_s = "∞" if hi is None else f"{hi:g}"
            parts.append(f"{axis} {lo_s} to {hi_s} m")
    return f"Working zone {', '.join(parts)}: results outside it are not used."


def _peak_note(section: Section, peaks: list[dict]) -> str | None:
    if not peaks:
        return None
    left = sum(p["treatment"] == "left out" for p in peaks)
    how = "averaged with the nodes either side" if section.peaks == "average" else "used as they are"
    text = f"{len(peaks)} isolated peak(s) above {section.peak_ratio:g}× their neighbours, {how}"
    return text + (f"; {left} left out." if left else ".")


def combi_bands(wall: dict[str, Any], positions: list[list[float]]) -> list[list[float]]:
    """[x, y, z, utilisation] per king pile and 0.5 m band: the infill's or the tube's, whichever is higher.

    Below the infill only the tube works; its utilisation is per level, the same at every king pile.
    """
    tube = {float(q["z"]): float(q["util"]) for q in wall["tube"].get("profile") or []}
    out: dict[tuple[float, float, float], float] = {}
    for x, y, z, u in wall["infill"].get("bands") or []:
        out[(x, y, z)] = max(u, tube.get(z, 0.0))
    for x, y in positions:
        for z, u in tube.items():
            out.setdefault((x, y, z), u)
    return [[x, y, z, round(u, 3)] for (x, y, z), u in out.items()]


def run_section(
    settings: DesignSettings,
    section: Section,
    workbook: ImportResult,
    progress: Callable[[float, str], None] | None = None,
    only: Collection[str] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Design the piles, combi walls and beams of one section; pick the sheet pile wall's governing sets.
    ``progress(fraction, step)`` is told as each element is started. ``only``: design just these
    elements. ``deadline`` (``time.monotonic()``): start no element after it (at least one is done);
    the rest are listed in ``left``, the ones done in ``designed``."""
    started: list[str] = []
    handled: list[str] = []
    left: list[str] = []

    def tick(name: str) -> None:
        if progress:
            progress(
                len(started) / max(len(only) if only is not None else len(section.elements), 1),
                f"Designing {name}",
            )
        started.append(name)

    def take(name: str) -> bool:
        if only is not None and name not in only:
            return False
        if deadline is not None and handled and time.monotonic() > deadline:
            left.append(name)
            return False
        handled.append(name)
        return True

    raw = workbook.elements()
    sheets = factored_elements(section, workbook)
    known = {s.name for s in workbook.sheets}
    missing = sorted({n for r in section.load_factors for n in r.sheets} - known)
    excluded = set(section.excluded_peaks)
    piles, walls, skipped = [], [], []
    for name, element in section.elements.items():
        if not isinstance(element, PileInput | CombiWallInput) or not take(name):
            continue
        if name not in sheets or all(s.frame.empty for s in sheets[name].values()):
            where = " inside the working zone" if name in sheets else ""
            skipped.append(f"{name}: no usable results in the workbook{where}.")
            continue
        top = element.top_level_to_ignore if isinstance(element, CombiWallInput) else element.head_level
        if top is not None:
            top += settings.results_into_connection / 1e3
        tick(name)
        own, peaks = treat_peaks(name, sheets[name], section.peaks, section.peak_ratio, excluded, top)
        notes = [_multiplier_note(section, own), _zone_note(section), _peak_note(section, peaks)]
        notes = [n for n in notes if n]
        positions = _positions(raw.get(name, {}))
        count = element.count or len(positions) or 1
        if isinstance(element, CombiWallInput):
            wall = design_combi_wall(name, element, settings, own, section.user_cages.get(name))
            wall["notes"][:0] = notes
            wall["peaks"] = peaks
            share = 1 - wall["steel_share"]  # the infill's moments are its share of the Plaxis ones
            wall["infill"]["peaks"] = [
                {
                    **q,
                    "M_kNm": round(q["M_kNm"] * share, 1),
                    "neighbours_M_kNm": round(q["neighbours_M_kNm"] * share, 1),
                }
                for q in peaks
            ]
            wall["bands"] = combi_bands(wall, positions)
            wall["tension"] = wall["infill"].get("tension") or {}
            wall["positions"] = wall["infill"]["positions"] = positions
            wall["count"] = wall["infill"]["count"] = count
            if (wall["infill"].get("steel") or {}).get("total_kg") is not None:
                wall["infill"]["steel"]["element_total_t"] = round(
                    wall["infill"]["steel"]["total_kg"] * count / 1000, 2
                )
            walls.append(wall)
            continue
        d = design_pile(name, element, settings, own, section.user_cages.get(name))
        d.notes[:0] = notes
        out = d.to_dict()
        out["peaks"] = peaks
        out["positions"] = positions
        out["count"] = count
        steel = out.get("steel") or {}
        if steel.get("total_kg") is not None:
            out["steel"]["element_total_t"] = round(steel["total_kg"] * count / 1000, 2)
        piles.append(out)
    spws = []
    geometry = None
    for name, combos in sheets.items():
        if not any(s.parsed and s.parsed.spec.type is ElementType.SHEET_PILE_WALL for s in combos.values()):
            continue
        if not take(name):
            continue
        sets = steel_sets(uls_frame(combos), "plate")
        if not sets["rows"]:
            where = " inside the working zone" if section.has_zone else ""
            skipped.append(f"{name}: no usable results in the workbook{where}.")
            continue
        z = [
            float(v)
            for sh in combos.values()
            if not sh.frame.empty
            for v in (sh.frame["Z"].min(), sh.frame["Z"].max())
        ]
        entry = {
            "element": name,
            "kind": "sheet_pile_wall",
            "governing_sets": sets,
            "length_m": round(max(z) - min(z), 2) if z else None,
        }
        wall = section.elements.get(name)
        if isinstance(wall, SheetPileInput):
            tick(name)
            fy = SHEET_PILE_GRADES[wall.steel or settings.materials.sheet_pile_steel]
            if geometry is None:
                geometry = section_geometry(workbook)
            entry["design"] = design_spw(name, wall, fy, combos, king_piles(section, geometry))
            entry["notes"] = [n for n in (_multiplier_note(section, combos), _zone_note(section)) if n]
        spws.append(entry)
    for name, element in section.elements.items():
        if isinstance(element, SheetPileInput) and name not in sheets and take(name):
            skipped.append(f"{name}: no usable results in the workbook.")
    beams = []
    found = getattr(workbook, "axes", None) or []
    plates = [n for n, e in section.elements.items() if isinstance(e, (BeamInput, SlabInput)) and take(n)]
    if settings.plate_positive_moment == "auto" and any(
        a["kind"] == "plate" and a["element"] in plates and "positive" not in a for a in found
    ):
        found = infer_axes(workbook.elements())[0]  # read before Triton read the sign
    axes = {a["element"]: a.get("local") for a in found}
    signs = {a["element"]: a for a in found if a["kind"] == "plate"}
    for name, element in section.elements.items():
        if not isinstance(element, BeamInput) or not take(name):
            continue
        if name not in sheets or all(s.frame.empty for s in sheets[name].values()):
            where = " inside the working zone" if name in sheets else ""
            skipped.append(f"{name}: no usable results in the workbook{where}.")
            continue
        if geometry is None:
            geometry = section_geometry(workbook)
        own = {c: s for c, s in sheets[name].items() if not s.frame.empty}
        tick(name)
        b = design_beam(
            name,
            element,
            settings,
            own,
            geometry,
            section.elements,
            axes.get(name),
            section.beam_cages.get(name),
            signs.get(name),
        )
        b["notes"][:0] = [n for n in (_multiplier_note(section, own), _zone_note(section)) if n]
        beams.append(b)
    slabs = []
    pile_sheets = {
        n: sheets[n] for n, e in section.elements.items() if isinstance(e, PileInput) and n in sheets
    }
    for name, element in section.elements.items():
        if not isinstance(element, SlabInput) or not take(name):
            continue
        if name not in sheets or all(s.frame.empty for s in sheets[name].values()):
            where = " inside the working zone" if name in sheets else ""
            skipped.append(f"{name}: no usable results in the workbook{where}.")
            continue
        if geometry is None:
            geometry = section_geometry(workbook)
        own = {c: s for c, s in sheets[name].items() if not s.frame.empty}
        tick(name)
        d = design_slab(
            name,
            element,
            settings,
            own,
            geometry,
            section.elements,
            axes.get(name),
            pile_sheets,
            section.slab_strips.get(name),
            signs.get(name),
        )
        d["notes"][:0] = [n for n in (_multiplier_note(section, own), _zone_note(section)) if n]
        slabs.append(d)
    if missing:
        skipped.append(f"Load multiplier sheets not in the workbook: {', '.join(missing)}.")
    return {
        "run_at": _now(),
        "piles": piles,
        "combi_walls": walls,
        "sheet_pile_walls": spws,
        "beams": beams,
        "slabs": slabs,
        "skipped": skipped,
        "designed": handled,
        "left": left,
        "working_zone": any(
            v is not None for v in (section.x_min, section.x_max, section.y_min, section.y_max)
        ),
    }
