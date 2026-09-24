"""Workbook-level checks across sheets: coverage, copy-paste errors, mesh consistency."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .axes import infer_axes
from .elements import CombinationType, combination_type, mapped_sheet_name
from .importer import SheetData, clean_sheet
from .issues import Issue, Severity
from .reader import Row, read_workbook
from .suggest import combination_key, split_name, squash, suggest


@dataclass
class ImportResult:
    sheets: list[SheetData]
    issues: list[Issue] = field(default_factory=list)
    axes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def usable(self) -> list[SheetData]:
        return [
            s
            for s in self.sheets
            if s.parsed is not None and not s.empty and not s.frame.empty and not _has_error(s)
        ]

    def elements(self) -> dict[str, dict[str, SheetData]]:
        """element -> combination -> sheet, for usable sheets."""
        out: dict[str, dict[str, SheetData]] = defaultdict(dict)
        for s in self.usable:
            out[s.parsed.element][s.parsed.combination] = s
        return dict(out)

    def all_issues(self) -> list[Issue]:
        return [i for s in self.sheets for i in s.issues] + self.issues

    def summary(self) -> dict[str, Any]:
        issues = self.all_issues()
        counts = {sev.value: sum(1 for i in issues if i.severity is sev) for sev in Severity}
        combos = sorted({s.parsed.combination for s in self.sheets if s.parsed and not s.empty})
        elements = sorted(
            {s.parsed.element for s in self.sheets if s.parsed and not s.empty}, key=_element_sort_key
        )
        by_key = {(s.parsed.element, s.parsed.combination): s for s in self.sheets if s.parsed}
        coverage = {e: {c: _cell_state(by_key.get((e, c))) for c in combos} for e in elements}
        sheets = [_sheet_summary(s) for s in self.sheets]
        return {
            "counts": counts,
            "elements": elements,
            "combinations": [{"name": c, "type": combination_type(c).value} for c in combos],
            "coverage": coverage,
            "sheets": sheets,
            "suggestions": suggest(sheets),
            "issues": [i.to_dict() for i in sorted(issues, key=_issue_sort_key)],
            "axes": sorted(getattr(self, "axes", []), key=lambda a: _element_sort_key(a["element"])),
        }


def _has_error(s: SheetData) -> bool:
    return any(i.severity is Severity.ERROR for i in s.issues)


def _cell_state(s: SheetData | None) -> str:
    if s is None or s.empty:
        return "missing"
    if _has_error(s):
        return "error"
    if any(i.severity is Severity.WARNING for i in s.issues):
        return "warning"
    return "ok"


def _sheet_summary(s: SheetData) -> dict[str, Any]:
    return {
        "name": s.name,
        "element": s.parsed.element if s.parsed else None,
        "element_type": s.parsed.spec.type.value if s.parsed else None,
        "combination": s.parsed.combination if s.parsed else None,
        "combination_type": combination_type(s.parsed.combination).value if s.parsed else None,
        "raw_rows": s.raw_rows,
        "rows": 0 if s.frame.empty else len(s.frame),
        "nodes": 0 if s.frame.empty or "Node" not in s.frame else int(s.frame["Node"].nunique()),
        "blank_rows": s.blank_rows,
        "duplicate_rows": s.duplicate_rows,
        "empty": s.empty,
        "state": _cell_state(s),
    }


def _element_sort_key(e: str) -> tuple[int, str]:
    order = ["SPW", "Combi Wall", "Pile", "Deck", "Front Beam", "Rear Beam", "Trans", "Portal Frame"]
    for i, prefix in enumerate(order):
        if e.startswith(prefix):
            return i, e
    return len(order), e


def _issue_sort_key(i: Issue) -> tuple[int, str, str]:
    rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}[i.severity]
    return rank, i.sheet or "", i.code


def import_workbook(path: str | Path, progress: Callable[[float, str], None] | None = None) -> ImportResult:
    """``progress(fraction, step)`` is told how far it has got: reading is most of the time, the
    checks the rest."""
    if progress is None:
        return import_sheets(read_workbook(path))
    raw = read_workbook(path, lambda done, sheet: progress(0.85 * done, f"Reading {sheet}"))
    progress(0.85, "Checking the sheets")
    return import_sheets(raw)


def import_sheets(raw: dict[str, list[Row]]) -> ImportResult:
    return _checked(ImportResult([clean_sheet(name, rows) for name, rows in raw.items()]))


def apply_mapping(result: ImportResult, mapping: dict[str, Any]) -> ImportResult:
    """The workbook with sheets assigned by hand: each entry maps a sheet name to an element and
    combination (``element``, ``combination``) or leaves it out (``ignore``). Checks run again."""
    if not mapping:
        return result
    sheets = []
    ignored = set()
    for s in result.sheets:
        m = mapping.get(s.name)
        if m is None:
            sheets.append(s)
            continue
        get = m.get if isinstance(m, dict) else lambda k, d=None, m=m: getattr(m, k, d)
        if get("ignore", False):
            ignored.add(s.name)
            sheets.append(replace(s, parsed=None))
            continue
        parsed = mapped_sheet_name(s.name, get("element", ""), get("combination", ""))
        sheets.append(replace(s, parsed=parsed) if parsed else s)
    return _checked(ImportResult(sheets), ignored)


# Issues the workbook checks add to single sheets; a sheet moved into another workbook drops them
# and is checked again there.
_SHEET_CHECKS = {
    "duplicate_sheet_name",
    "identical_combinations",
    "node_set_differs",
    "undefined_combination",
}

MERGE_MODES = ("replace", "update", "add")


def defined_match(combination: str, defined: list[str]) -> str | None:
    """The defined combination a workbook spelling is, when it is the same one written differently
    (case, separators, ``Seismic2`` for ``Seismic 2``), or None."""
    if combination in defined:
        return combination
    same = [d for d in defined if squash(d) == squash(combination)]
    if len(same) == 1:
        return same[0]
    key = combination_key(combination)
    if key:
        same = [d for d in defined if combination_key(d) == key]
        if len(same) == 1:
            return same[0]
    return None


def combination_check(result: ImportResult, defined: list[str], aliases: dict[str, str]) -> dict[str, Any]:
    """The workbook's combinations against the section's list, before any sheet is designed: each
    one found with its sheets, what it is read as, and whether that still needs a decision."""
    counts: dict[str, int] = defaultdict(int)
    for s in result.sheets:
        if s.parsed and not s.empty:
            counts[s.parsed.combination] += 1
    found = []
    for name, n in sorted(counts.items(), key=lambda kv: _combo_order(kv[0], defined)):
        if name in aliases:
            status, target = ("left_out" if not aliases[name] else "read_as"), aliases[name]
            if target and target not in defined:
                status, target = "undefined", None
        elif name in defined:
            status, target = "defined", name
        elif defined_match(name, defined):
            status, target = "matched", defined_match(name, defined)
        else:
            status, target = "undefined", None
        found.append({"name": name, "sheets": n, "status": status, "target": target})
    used = {f["target"] for f in found if f["target"]}
    return {
        "defined": defined,
        "found": found,
        "missing": [d for d in defined if d not in used],
        "unresolved": [f["name"] for f in found if f["status"] == "undefined"],
    }


def _combo_order(name: str, defined: list[str]) -> tuple[int, str]:
    target = defined_match(name, defined)
    return (defined.index(target) if target else len(defined), name)


def apply_section(
    result: ImportResult,
    sheet_map: dict[str, Any] | None = None,
    defined: list[str] | None = None,
    aliases: dict[str, str] | None = None,
) -> ImportResult:
    """The workbook as a section reads it: sheets assigned by hand, then each combination read as
    one of the section's defined combinations. A sheet whose combination is not defined (and not
    said to be one) is marked as an error and not designed; one left out is ignored."""
    mapped = apply_mapping(result, sheet_map or {})
    if defined is None:
        return mapped
    aliases = aliases or {}
    sheets, ignored = [], set()
    for s in mapped.sheets:
        issues = [i for i in s.issues if i.code not in _SHEET_CHECKS]
        if s.parsed is None or s.empty:
            sheets.append(replace(s, issues=issues))
            continue
        c = s.parsed.combination
        target = aliases[c] if c in aliases else defined_match(c, defined)
        if c in aliases and not target:
            ignored.add(s.name)
            sheets.append(replace(s, parsed=None, issues=issues))
            continue
        if target is None or target not in defined:
            issues.append(
                Issue(
                    Severity.ERROR,
                    "undefined_combination",
                    f"'{c}' is not one of this section's load combinations, so the sheet is not used. "
                    "Say which combination it is, add it to the list, or leave it out.",
                    sheet=s.name,
                    element=s.parsed.element,
                    combination=c,
                )
            )
            sheets.append(replace(s, issues=issues))
            continue
        sheets.append(replace(s, parsed=replace(s.parsed, combination=target), issues=issues))
    ignored |= {i.sheet for i in mapped.issues if i.code == "ignored_sheet" and i.sheet}
    return _checked(ImportResult(sheets), ignored)


def merge_workbooks(
    old: ImportResult, new: ImportResult, mode: str, mapping: dict[str, Any] | None = None
) -> tuple[ImportResult, dict[str, Any]]:
    """A section's workbook with a second upload brought in.

    ``replace``: the new workbook replaces the old one. ``update``: each new sheet replaces the old
    sheet of the same name, or else the old sheet holding the same element and combination (as
    mapped); the rest are added. ``add``: only sheets whose names are not there yet are added.
    Returns the combined (unmapped) workbook and what was replaced, added and left out.
    """
    if mode not in MERGE_MODES:
        raise ValueError(f"mode is one of {', '.join(MERGE_MODES)}")
    if mode == "replace":
        return new, {"mode": mode, "replaced": [], "added": [s.name for s in new.sheets], "skipped": []}
    mapped = apply_mapping(old, mapping or {}) if mapping else old

    def key(s: SheetData):
        """What a sheet holds, whatever its spelling: from its name or mapping, else as suggested."""
        if s.empty:
            return None
        if s.parsed:
            return (squash(s.parsed.element), combination_key(s.parsed.combination) or s.parsed.combination)
        found = split_name(s.name)
        return (squash(found[0]), found[2]) if found else None

    old_keys = {key(s): s.name for s in mapped.sheets if key(s)}
    sheets = {s.name: s for s in old.sheets}
    replaced, added, skipped = [], [], []
    for s in new.sheets:
        if s.empty:
            continue
        if s.name in sheets:
            if mode == "add":
                skipped.append(s.name)
                continue
            replaced.append(s.name)
            sheets[s.name] = s
            continue
        twin = old_keys.get(key(s)) if mode == "update" and key(s) else None
        if twin and twin in sheets:
            replaced.append(f"{twin} → {s.name}")
            sheets = {(s.name if n == twin else n): (s if n == twin else v) for n, v in sheets.items()}
            continue
        added.append(s.name)
        sheets[s.name] = s
    fresh = [replace(s, issues=[i for i in s.issues if i.code not in _SHEET_CHECKS]) for s in sheets.values()]
    merged = _checked(ImportResult(fresh))
    return merged, {"mode": mode, "replaced": replaced, "added": added, "skipped": skipped}


def _checked(result: ImportResult, ignored: set[str] | frozenset = frozenset()) -> ImportResult:
    _check_names(result, ignored)
    _check_duplicate_sheets(result)
    _check_coverage(result)
    _check_identical_combinations(result)
    _check_mesh_consistency(result)
    result.axes, issues = infer_axes(result.elements())
    result.issues.extend(issues)
    return result


def _check_names(result: ImportResult, ignored: set[str] | frozenset = frozenset()) -> None:
    for s in result.sheets:
        if s.name in ignored and not s.empty:
            result.issues.append(
                Issue(Severity.INFO, "ignored_sheet", "Left out by the sheet mapping.", sheet=s.name)
            )
            continue
        if s.parsed is None and not s.empty:
            result.issues.append(
                Issue(
                    Severity.WARNING,
                    "unknown_sheet",
                    "Sheet name does not match '<Element>-<Combination>' for a known element; "
                    "the sheet was ignored. Assign it under Sheet mapping to use it.",
                    sheet=s.name,
                )
            )


def _check_duplicate_sheets(result: ImportResult) -> None:
    seen: dict[tuple[str, str], str] = {}
    for s in result.sheets:
        if s.parsed is None or s.empty:
            continue
        key = (s.parsed.element, s.parsed.combination.upper())
        if key in seen:
            s.issues.append(
                Issue(
                    Severity.ERROR,
                    "duplicate_sheet_name",
                    f"'{s.name}' and '{seen[key]}' are the same element and combination.",
                    sheet=s.name,
                    element=s.parsed.element,
                    combination=s.parsed.combination,
                )
            )
        else:
            seen[key] = s.name


def _check_coverage(result: ImportResult) -> None:
    """An element should have every combination its siblings (same family) have."""
    family_combos: dict[str, set[str]] = defaultdict(set)
    element_combos: dict[str, set[str]] = defaultdict(set)
    element_family: dict[str, str] = {}
    for s in result.sheets:
        if s.parsed is None or s.empty:
            continue
        fam = s.parsed.spec.family
        family_combos[fam].add(s.parsed.combination)
        element_combos[s.parsed.element].add(s.parsed.combination)
        element_family[s.parsed.element] = fam
    for element, have in element_combos.items():
        missing = sorted(family_combos[element_family[element]] - have)
        for combo in missing:
            result.issues.append(
                Issue(
                    Severity.WARNING,
                    "missing_combination",
                    f"{element} has no {combo} sheet, but other elements of the same kind do.",
                    element=element,
                    combination=combo,
                )
            )
        if not any(combination_type(c) is CombinationType.SLS_QP for c in have):
            result.issues.append(
                Issue(
                    Severity.WARNING,
                    "missing_qp",
                    f"{element} has no QP sheet, so crack width cannot be checked.",
                    element=element,
                )
            )


def _node_values(s: SheetData) -> pd.DataFrame:
    cols = [c for c in s.parsed.spec.required_actions if c in s.frame.columns]
    return s.frame.groupby("Node")[cols].first()


def _check_identical_combinations(result: ImportResult) -> None:
    for element, combos in result.elements().items():
        for (ca, a), (cb, b) in combinations(sorted(combos.items()), 2):
            va, vb = _node_values(a), _node_values(b)
            common = va.index.intersection(vb.index)
            cols = [c for c in va.columns if c in vb.columns]
            if len(common) == 0 or not cols:
                continue
            x = va.loc[common, cols].to_numpy(dtype=float)
            y = vb.loc[common, cols].to_numpy(dtype=float)
            same = np.isclose(x, y, rtol=0, atol=1e-9, equal_nan=True).all(axis=1)
            if same.all():
                b.issues.append(
                    Issue(
                        Severity.WARNING,
                        "identical_combinations",
                        f"{element}: {ca} and {cb} have identical forces at all {len(common)} common "
                        "nodes. One of them was probably pasted twice.",
                        sheet=b.name,
                        element=element,
                        combination=cb,
                    )
                )


def _check_mesh_consistency(result: ImportResult) -> None:
    for element, combos in result.elements().items():
        node_sets = {c: set(s.frame["Node"].astype(int)) for c, s in combos.items()}
        if len({frozenset(n) for n in node_sets.values()}) <= 1:
            continue
        reference_combo = max(node_sets, key=lambda c: len(node_sets[c]))
        reference = node_sets[reference_combo]
        for combo, nodes in sorted(node_sets.items()):
            if nodes == reference:
                continue
            combos[combo].issues.append(
                Issue(
                    Severity.WARNING,
                    "node_set_differs",
                    f"{element} {combo} has {len(nodes - reference)} node(s) not in {reference_combo} "
                    f"and is missing {len(reference - nodes)} of its nodes. The sheets may come from "
                    "different models or be cut short.",
                    sheet=combos[combo].name,
                    element=element,
                    combination=combo,
                )
            )
