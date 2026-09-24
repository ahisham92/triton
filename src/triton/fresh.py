"""Whether a section's results still match its inputs.

A design records a fingerprint of everything it was run from: the project's design settings, the
section's working zone and peak handling, each element, the load multipliers, the sheet mapping
and the workbook. When any of them changes afterwards, the results are flagged as out of date,
naming what changed, until the section is designed again.
"""

from __future__ import annotations

import hashlib
import json
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
        "workbook": _hash([(workbook or {}).get(k) for k in ("version", "uploaded_at")]),
    }
    for name, element in section.elements.items():
        parts[name] = _hash(element.model_dump(mode="json"))
    return parts


def changes(results: dict[str, Any], now: dict[str, str]) -> list[str] | None:
    """The inputs changed since the results were designed: [] when none, None for results designed
    before fingerprints were kept (nothing to compare)."""
    then = results.get("inputs")
    if not isinstance(then, dict):
        return None
    out = []
    for part in list(now) + [p for p in then if p not in now]:
        if then.get(part) == now.get(part):
            continue
        if part not in then:
            if part == "load combinations":
                continue  # designed before combinations were defined per section
            out.append(f"{part} (added)")
        elif part not in now:
            out.append(f"{part} (removed)")
        else:
            out.append(part)
    return out


def with_status(project: Project, section: Section, results: dict[str, Any], workbook: dict | None) -> dict:
    """The results with ``changed``: what changed since they were designed ([] = up to date)."""
    return {**results, "changed": changes(results, fingerprint(project, section, workbook))}
