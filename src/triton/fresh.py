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

from .project import Project, Section

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
}


def _hash(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def fingerprint(project: Project, section: Section, workbook: dict[str, Any] | None) -> dict[str, str]:
    """What the design of a section depends on, part by part, as short hashes."""
    parts = {
        "design settings": _hash(project.design.model_dump(mode="json")),
        "working zone and peaks": _hash(section.model_dump(mode="json", exclude=_SECTION_OWN)),
        "load multipliers": _hash([f.model_dump(mode="json") for f in section.load_factors]),
        "sheet mapping": _hash({k: v.model_dump(mode="json") for k, v in section.sheet_map.items()}),
        "load combinations": _hash([section.combinations, section.combination_map]),
        "reviewed warnings": _hash(sorted(section.review.items())),
        "workbook": _hash([(workbook or {}).get(k) for k in ("version", "uploaded_at")]),
    }
    for name, element in section.elements.items():
        cage = section.user_cages.get(name)
        own = element.model_dump(mode="json")
        parts[name] = _hash(own if cage is None else [own, cage.model_dump(mode="json")])
    return parts


def _diff(then: dict[str, str], now: dict[str, str]) -> list[str]:
    out = []
    for part in list(now) + [p for p in then if p not in now]:
        if then.get(part) == now.get(part):
            continue
        if part not in then:
            if part in ("load combinations", "reviewed warnings"):
                continue  # designed before combinations were defined per section
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


KINDS = ("piles", "combi_walls", "beams", "slabs", "sheet_pile_walls")


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
    changed, stale = status(results, fingerprint(project, section, workbook), section.elements)
    return {**results, "changed": changed, "stale": stale}
