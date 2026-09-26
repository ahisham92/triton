"""Run the element designs of a project against its checked workbook."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import replace
from typing import Any

from ..alignment import element_in_part, part_elements, section_parts, tag_part, trim_ends
from ..axes import infer_axes
from ..elements import ElementType
from ..forces import scale_forces
from ..geometry import elements_geometry, section_geometry
from ..importer import SheetData
from ..joints import restraint_length, section_joints, segment_lengths
from ..materials import SHEET_PILE_GRADES
from ..project import (
    ApproachSlabInput,
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
from .approach import ELEMENT as APPROACH
from .approach import design_approach
from .beams import design_beam
from .combi import design_combi_wall
from .construction_joints import add_weights, beam_lines, beam_top, for_beam, for_pile, for_slab
from .governing import steel_sets, uls_frame
from .peaks import treat_peaks
from .pile_heads import assumed_heads
from .piles import design_pile
from .slabs import design_slab_meshes
from .spw_design import design_spw
from .standard import MODES
from .standard import mark as mark_standard
from .stop import Stopped


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


def _zone_note(section: Section, trimmed: bool = True) -> str | None:
    cut = []
    if trimmed and section.end_trim > 0:
        cut.append(f"{section.end_trim:g} m at each end along the berth")
    if trimmed and section.side_trim > 0:
        cut.append(f"{section.side_trim:g} m at each side across the quay")
    trim = f"Results within {' and '.join(cut)} are not used (FE edges; Sections tab)." if cut else None
    if not section.has_zone:
        return trim
    parts = []
    for axis, lo, hi in (("X", section.x_min, section.x_max), ("Y", section.y_min, section.y_max)):
        if lo is not None or hi is not None:
            lo_s = "−∞" if lo is None else f"{lo:g}"
            hi_s = "∞" if hi is None else f"{hi:g}"
            parts.append(f"{axis} {lo_s} to {hi_s} m")
    zone = f"Working zone {', '.join(parts)}: results outside it are not used."
    return zone if trim is None else f"{zone} {trim}"


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


def along_axis(section: Section) -> str:
    slab = next((e for e in section.elements.values() if isinstance(e, SlabInput)), None)
    return "X" if slab is not None and slab.strip_direction == "Y" else "Y"


def section_alignment(
    section: Section, sheets: dict[str, dict[str, SheetData]]
) -> tuple[list, dict[str, Any]]:
    """The parts the section's slabs and beams are designed in (none for a straight berth), from its
    results as designed (multipliers and working zone applied)."""
    return section_parts(section.alignment, sheets, along_axis(section))


def run_section(
    settings: DesignSettings,
    section: Section,
    workbook: ImportResult,
    progress: Callable[[float, str], None] | None = None,
    only: Collection[str] | None = None,
    deadline: float | None = None,
    approach: ApproachSlabInput | None = None,
    furniture_at: Callable[[float], list[dict[str, Any]]] | None = None,
    mode: str = "detailed",
) -> dict[str, Any]:
    """Design the piles, combi walls and beams of one section; pick the sheet pile wall's governing sets.
    ``progress(fraction, step)`` is told as each element is started. ``only``: design just these
    elements. ``deadline`` (``time.monotonic()``): start no element after it (at least one is done);
    the rest are listed in ``left``, the ones done in ``designed``. ``approach``: the project's approach
    slab, designed as the element "Approach Slab" with its ledge on the rear beam, whose load and
    torque the rear beam takes too. ``mode``: "detailed" or "standard": the same design, Standard marked as
    having no drawings, AdSec files or clash checks (see design/standard.py)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    started: list[str] = []
    handled: list[str] = []
    left: list[str] = []
    on: list[str] = []  # the element being designed, if any

    def tick(name: str) -> None:
        on.clear()
        if progress:
            progress(
                len(started) / max(len(only) if only is not None else len(section.elements), 1),
                f"Designing {name}",
            )
        started.append(name)
        on.append(name)

    def take(name: str) -> bool:
        if only is not None and name not in only:
            return False
        if deadline is not None and handled and time.monotonic() > deadline:
            left.append(name)
            return False
        handled.append(name)
        return True

    out: dict[str, Any] = {k: [] for k in (*RESULT_KINDS, "skipped")}
    out |= {"alignment": None, "joints": None, "end_trim": None}
    stopped = False
    section, heads = assumed_heads(section, workbook)
    try:
        _design(settings, section, workbook, approach, furniture_at, tick, take, out, only=only)
        on.clear()
    except Stopped:
        # Stop pressed: the elements finished so far keep their results; the one under way is dropped.
        stopped = True
        for kind in RESULT_KINDS:
            out[kind] = [e for e in out[kind] if e.get("element") not in on]
        found = {e.get("element") for kind in RESULT_KINDS for e in out[kind]}
        said = {m.split(":")[0] for m in out["skipped"]}
        unfinished = [n for n in handled if n in on or not (n in started or n in found or n in said)]
        handled[:] = [n for n in handled if n not in unfinished]
        left[:0] = unfinished
    for e in out["piles"]:
        if e.get("element") in heads:
            e.setdefault("notes", []).append(heads[e["element"]])
    if mode == "standard":
        for kind in RESULT_KINDS:
            for e in out[kind]:
                mark_standard(kind, e)
    result = {
        "run_at": _now(),
        "mode": mode,
        **{k: out[k] for k in RESULT_KINDS},
        "alignment": out["alignment"] or {"parts": [], "points": []},
        "joints": out["joints"],
        "end_trim": out["end_trim"],
        "skipped": out["skipped"],
        "designed": handled,
        "left": left,
        "working_zone": any(
            v is not None for v in (section.x_min, section.x_max, section.y_min, section.y_max)
        ),
    }
    if stopped:
        result["stopped"] = True
        if out["alignment"] is None:  # not reached: the earlier layout stays
            del result["alignment"], result["joints"]
    return result


RESULT_KINDS = ("piles", "combi_walls", "sheet_pile_walls", "beams", "slabs", "approach_slabs")


def _design(
    settings: DesignSettings,
    section: Section,
    workbook: ImportResult,
    approach: ApproachSlabInput | None,
    furniture_at: Callable[[float], list[dict[str, Any]]] | None,
    tick: Callable[[str], None],
    take: Callable[[str], bool],
    found_so_far: dict[str, Any],
    standard: bool = False,
    only: list[str] | None = None,
) -> None:
    """run_section's work, element by element, into ``found_so_far`` as it goes (a Stop keeps it)."""
    raw = workbook.elements()
    sheets = factored_elements(section, workbook)
    # The berth's line and parts from the whole model, before its ends are trimmed.
    line = section_alignment(section, sheets)
    if section.end_trim > 0 or section.side_trim > 0:
        # The FE edges: results near each element's ends along the berth (round a corner) and its sides
        # across the quay are left out.
        pile_names = {n for n, e in section.elements.items() if isinstance(e, PileInput)}
        sheets, trimmed = trim_ends(
            sheets,
            line[1].get("points"),
            along_axis(section),
            section.end_trim,
            pile_names,
            section.side_trim,
        )
        found_so_far["end_trim"] = trimmed
    known = {s.name for s in workbook.sheets}
    missing = sorted({n for r in section.load_factors for n in r.sheets} - known)
    excluded = set(section.excluded_peaks)
    piles, walls, skipped = found_so_far["piles"], found_so_far["combi_walls"], found_so_far["skipped"]
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
        zone = _zone_note(section, trimmed=isinstance(element, CombiWallInput))
        notes = [_multiplier_note(section, own), zone, _peak_note(section, peaks)]
        notes = [n for n in notes if n]
        positions = _positions(raw.get(name, {}))
        count = element.count or len(positions) or 1
        if isinstance(element, CombiWallInput):
            wall = design_combi_wall(name, element, settings, own, section.user_cages.get(name), standard)
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
        d = design_pile(name, element, settings, own, section.user_cages.get(name), standard)
        d.notes[:0] = notes
        out = d.to_dict()
        out["peaks"] = peaks
        out["positions"] = positions
        out["count"] = count
        if not standard:
            out["construction_joints"] = add_weights(for_pile(name, element, settings, own, out), count)
        steel = out.get("steel") or {}
        if steel.get("total_kg") is not None:
            out["steel"]["element_total_t"] = round(steel["total_kg"] * count / 1000, 2)
        piles.append(out)
    spws = found_so_far["sheet_pile_walls"]
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
    beams = found_so_far["beams"]
    found = getattr(workbook, "axes", None) or []
    # The beams and slabs this run may design (taken one by one below, when their turn comes: taking
    # them here listed them as designed in a step that ran out of time before reaching them).
    plates = [
        n
        for n, e in section.elements.items()
        if isinstance(e, (BeamInput, SlabInput)) and (only is None or n in only)
    ]
    if settings.plate_positive_moment == "auto" and any(
        a["kind"] == "plate" and a["element"] in plates and "positive" not in a for a in found
    ):
        found = infer_axes(workbook.elements())[0]  # read before Triton read the sign
    axes = {a["element"]: a.get("local") for a in found}
    signs = {a["element"]: a for a in found if a["kind"] == "plate"}
    parts, alignment = line if plates else ([], {"parts": [], "points": []})
    joints = (
        section_joints(settings, section, raw, parts, along_axis(section), furniture_at) if plates else None
    )
    found_so_far["alignment"], found_so_far["joints"] = alignment, joints
    use_joints = bool(joints and joints.get("segments") and settings.joints.use_in_restraint)

    def with_joints(element: Any, part: Any) -> tuple[Any, list[float]]:
        """The element with its length between movement joints from the joint layout, and the segment
        lengths its part of the berth runs through."""
        if not use_joints:
            return element, []
        index = part.index if part is not None else None
        length = restraint_length(joints, index)
        if not length:
            return element, []
        return element.model_copy(update={"joint_spacing": length}), segment_lengths(joints, index)

    def joint_note(element: Any, length: float) -> str:
        return (
            f"Restraint: {length:.1f} m between movement joints, the longest segment of the expansion "
            f"joint layout (Design settings; the element's own {element.joint_spacing:g} m is not used)."
        )

    # Per part: every element's results inside it, turned onto the quay's axis, and their geometry.
    views: list[tuple[Any, dict[str, dict[str, SheetData]], list[dict[str, Any]]]] = []
    if parts:
        for part in parts:
            own = part_elements(sheets, parts, part, axes)
            views.append((part, own, elements_geometry(own)))

    def runs(name: str) -> list[tuple[Any, dict[str, SheetData], list[dict[str, Any]], str]]:
        """(part, the element's sheets, geometry, choice key) for each part the element is in."""
        nonlocal geometry
        if not parts:
            if geometry is None:
                geometry = section_geometry(workbook)
            own = {c: s for c, s in sheets[name].items() if not s.frame.empty}
            return [(None, own, geometry, name)]
        out = []
        for part, own_all, geo in views:
            own = {c: s for c, s in (own_all.get(name) or {}).items() if not s.frame.empty}
            if own:
                out.append((part, own, geo, f"{name} · {part.name}" if len(parts) > 1 else name))
        return out

    rear = next(
        (e for e in section.elements.values() if isinstance(e, BeamInput) and e.kind == "rear_beam"), None
    )
    approach_design = None
    if approach is not None:
        approach_design = design_approach(approach, settings, rear)
    for name, element in section.elements.items():
        if not isinstance(element, BeamInput) or not take(name):
            continue
        if name not in sheets or all(s.frame.empty for s in sheets[name].values()):
            where = " inside the working zone" if name in sheets else ""
            skipped.append(f"{name}: no usable results in the workbook{where}.")
            continue
        tick(name)
        for part, own, geo, key in runs(name):
            placed, lengths = with_joints(element, part)
            b = design_beam(
                name,
                placed,
                settings,
                own,
                geo,
                section.elements,
                axes.get(name),
                section.beam_cages.get(key),
                signs.get(name),
                joint_lengths=lengths,
                ledge=(approach_design or {}).get("ledge") if element.kind == "rear_beam" else None,
                standard=standard,
            )
            b["notes"][:0] = [n for n in (_multiplier_note(section, own), _zone_note(section)) if n]
            if lengths and element.restraint_factor is None:
                b["notes"].append(joint_note(element, placed.joint_spacing))
            if lengths:
                b["restraint"]["length_from"] = "expansion joints"
            if element.construction_joints and not standard:
                top = beam_top(section.clashes, name, b.get("level_m") or 0.0, float(b.get("depth_mm") or 0))
                b["construction_joints"] = add_weights(
                    for_beam(
                        name,
                        element,
                        settings,
                        own,
                        geo,
                        section.elements,
                        axes.get(name),
                        signs.get(name),
                        b,
                        top,
                    )
                )
            beams.append(b if part is None else tag_part(b, part, parts))
    slabs = found_so_far["slabs"]
    for name, element in section.elements.items():
        if not isinstance(element, SlabInput) or not take(name):
            continue
        if name not in sheets or all(s.frame.empty for s in sheets[name].values()):
            where = " inside the working zone" if name in sheets else ""
            skipped.append(f"{name}: no usable results in the workbook{where}.")
            continue
        tick(name)
        for part, own, geo, key in runs(name):
            around = views[part.index][1] if part else sheets
            pile_sheets = {
                n: around[n] for n, e in section.elements.items() if isinstance(e, PileInput) and n in around
            }
            placed, lengths = with_joints(element, part)
            d = design_slab_meshes(
                name,
                element_in_part(placed, part),
                settings,
                own,
                geo,
                section.elements,
                axes.get(name),
                pile_sheets,
                section.slab_strips.get(key),
                signs.get(name),
                standard,
            )
            d["notes"][:0] = [n for n in (_multiplier_note(section, own), _zone_note(section)) if n]
            if element.construction_joints and not standard:
                lines = beam_lines(around, section.elements, axes)
                d["construction_joints"] = add_weights(
                    for_slab(name, element, settings, own, axes.get(name), signs.get(name), d, lines)
                )
            if lengths:
                if element.restraint_check != "off" and element.restraint_factor is None:
                    d["notes"].append(joint_note(element, placed.joint_spacing))
                if isinstance(d.get("restraint"), dict):
                    d["restraint"]["length_from"] = "expansion joints"
            slabs.append(d if part is None else tag_part(d, part, parts))
    approach_slabs = found_so_far["approach_slabs"]
    if approach_design is not None and take(APPROACH):
        tick(APPROACH)
        approach_slabs.append(approach_design)
    if missing:
        skipped.append(f"Load multiplier sheets not in the workbook: {', '.join(missing)}.")
