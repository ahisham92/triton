"""Whether a section's results still match its inputs.

A design records a fingerprint of everything it was run from: the project's design settings, the
section's working zone and peak handling, each element, the load multipliers, the sheet mapping
and the workbook. When any of them changes afterwards, the results are flagged as out of date,
naming what changed, until the section is designed again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .project import BeamInput, Project, Section, SlabInput

_SECTION_OWN = {
    "id",
    "name",
    "elements",
    "load_factors",
    "sheet_map",
    "costing",
    "combinations",
    "combination_map",
    "review",
    "user_cages",
    "beam_cages",
    "slab_strips",
    "clashes",
    "checks",
    "furniture",
    "displacements",
    "deflection",
    "joints",
}


def _furniture_spots(project: Project, section: Section) -> list | None:
    """The quay furniture's positions the joints keep clear of (the joints set the restraint length)."""
    from .furniture import nominal

    if section.joints.furniture:
        return None  # positions given for the section, already in its joints
    berth = sum(section.joints.runs) or section.costing.berth_length or 0.0
    return nominal(project.furniture, section.furniture, berth) or None


# Costing items added to the defaults with the Furniture tab.
_NEW = {"Ladders", "Storm pins", "Crane stoppers"}


def _hash(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


APPROACH = "Approach Slab"


def names(project: Project, section: Section) -> list[str]:
    """Every element the section designs: its own, and the project's approach slab when it has one."""
    return list(section.elements) + ([APPROACH] if project.approach is not None else [])


def _rear_beam(section: Section) -> Any:
    return next((e for e in section.elements.values() if getattr(e, "kind", None) == "rear_beam"), None)


def fingerprint(project: Project, section: Section, workbook: dict[str, Any] | None) -> dict[str, str]:
    """What the design of a section depends on, part by part, as short hashes."""
    parts = {
        "design settings": _hash(project.design.model_dump(mode="json", exclude={"joints"})),
        "working zone and peaks": _hash(section.model_dump(mode="json", exclude=_SECTION_OWN)),
        "load multipliers": _hash([f.model_dump(mode="json") for f in section.load_factors]),
        "sheet mapping": _hash({k: v.model_dump(mode="json") for k, v in section.sheet_map.items()}),
        "load combinations": _hash([section.combinations, section.combination_map]),
        "reviewed warnings": _hash(sorted(section.review.items())),
        "workbook": _hash([(workbook or {}).get(k) for k in ("version", "uploaded_at")]),
    }
    joints = project.design.joints
    plates = any(isinstance(e, (BeamInput, SlabInput)) for e in section.elements.values())
    if joints.use_in_restraint and plates:
        # The joint layout sets the beams' and slabs' restraint length: its rules, the section's runs
        # (or the berth length on the Costing tab when no runs are given) and the furniture priced
        # each. A section with no beam or slab does not use it, so its costing stays out of the design.
        c = section.costing
        # Items with no berth place nothing; those added to the defaults later then keep earlier hashes.
        berth = sum(section.joints.runs) or c.berth_length
        parts["expansion joints"] = _hash(
            [
                joints.model_dump(mode="json"),
                section.joints.model_dump(mode="json"),
                None if section.joints.runs else c.berth_length,
                [
                    i.model_dump(mode="json")
                    for i in c.items
                    if i.unit == "each" and (berth or i.name not in _NEW)
                ],
            ]
            # Only when the Furniture tab places something, so earlier designs keep their hash.
            + ([spots] if (spots := _furniture_spots(project, section)) else [])
        )
    for name, element in section.elements.items():
        cage = section.user_cages.get(name) or section.beam_cages.get(name) or section.slab_strips.get(name)
        own = element.model_dump(mode="json")
        for key in ("rooms", "manholes", "channels", "construction_joints"):
            if not own.get(key):
                own.pop(key, None)  # none: the fingerprint it had before these existed
        # A corner berth's parts keep their own bars and stations ("Deck · Part 2").
        each = {
            k: v.model_dump(mode="json")
            for store in (section.beam_cages, section.slab_strips)
            for k, v in store.items()
            if k.startswith(f"{name} · ")
        }
        value = own if cage is None else [own, cage.model_dump(mode="json")]
        if project.approach is not None and getattr(element, "kind", None) == "rear_beam":
            value = [value, project.approach.model_dump(mode="json")]  # the ledge's load and torque
        parts[name] = _hash([value, sorted(each.items())] if each else value)
    if project.approach is not None:
        rear = _rear_beam(section)
        parts[APPROACH] = _hash(
            [
                project.approach.model_dump(mode="json"),
                rear.model_dump(mode="json") if rear is not None else None,
            ]
        )
    return parts


def _diff(then: dict[str, str], now: dict[str, str]) -> list[str]:
    out = []
    for part in list(now) + [p for p in then if p not in now]:
        if then.get(part) == now.get(part):
            continue
        if part not in then:
            if part in ("load combinations", "reviewed warnings", "expansion joints"):
                continue  # designed before these were kept
            out.append(f"{part} (added)")
        elif part not in now:
            out.append(f"{part} (removed)")
        else:
            out.append(part)
    return out


def element_inputs(now: dict[str, str], elements: Iterable[str], names: Iterable[str]) -> dict:
    """What each of ``names`` was designed from: the section-wide parts and its own."""
    shared = {k: v for k, v in now.items() if k not in set(elements)}
    return {n: {**shared, **({n: now[n]} if n in now else {})} for n in names}


def _by_element(results: dict[str, Any], elements: Iterable[str]) -> dict[str, dict[str, str]] | None:
    """Each designed element's inputs; results designed in one go before elements could be designed
    on their own carry one fingerprint for all."""
    by = results.get("element_inputs")
    if isinstance(by, dict):
        return by
    then = results.get("inputs")
    if not isinstance(then, dict):
        return None
    names = set(elements) | {e["element"] for e in _designed(results)}
    shared = {k: v for k, v in then.items() if k not in names}
    return {n: {**shared, n: then[n]} for n in names if n in then}


KINDS = ("piles", "combi_walls", "beams", "slabs", "sheet_pile_walls", "approach_slabs")


def _designed(results: dict[str, Any]) -> list[dict]:
    return [e for k in KINDS for e in results.get(k) or []]


def status(results: dict[str, Any], now: dict[str, str], elements: Iterable[str]) -> tuple[list | None, list]:
    """What changed since the results were designed ([] = up to date, None for results designed
    before fingerprints were kept), and the elements whose results are out of date."""
    elements = list(elements)
    by = _by_element(results, elements)
    if by is None:
        return None, []
    shared = {k: v for k, v in now.items() if k not in set(elements)}
    changed: list[str] = []
    stale: list[str] = []
    for name, then in by.items():
        diff = _diff(then, {**shared, name: now[name]} if name in now else shared)
        if diff:
            stale.append(name)
        changed += [d for d in diff if d not in changed]
    for name in elements:
        if name not in by:
            changed.append(f"{name} (added)")
            stale.append(name)
    return changed, stale


def changes(results: dict[str, Any], now: dict[str, str]) -> list[str] | None:
    """The inputs changed since the results were designed in one go: [] when none, None for results
    designed before fingerprints were kept (nothing to compare)."""
    then = results.get("inputs")
    return _diff(then, now) if isinstance(then, dict) else None


def with_status(project: Project, section: Section, results: dict[str, Any], workbook: dict | None) -> dict:
    """The results with ``changed``: what changed since they were designed ([] = up to date), and
    ``stale``: the elements whose results are out of date."""
    changed, stale = status(results, fingerprint(project, section, workbook), names(project, section))
    return {**results, "changed": changed, "stale": stale}
