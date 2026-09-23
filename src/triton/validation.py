"""Workbook-level checks across sheets: coverage, copy-paste errors, mesh consistency."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .elements import CombinationType, combination_type
from .importer import SheetData, clean_sheet
from .issues import Issue, Severity
from .reader import Row, read_workbook


@dataclass
class ImportResult:
    sheets: list[SheetData]
    issues: list[Issue] = field(default_factory=list)

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
        return {
            "counts": counts,
            "elements": elements,
            "combinations": [{"name": c, "type": combination_type(c).value} for c in combos],
            "coverage": coverage,
            "sheets": [_sheet_summary(s) for s in self.sheets],
            "issues": [i.to_dict() for i in sorted(issues, key=_issue_sort_key)],
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


def import_workbook(path: str | Path) -> ImportResult:
    return import_sheets(read_workbook(path))


def import_sheets(raw: dict[str, list[Row]]) -> ImportResult:
    sheets = [clean_sheet(name, rows) for name, rows in raw.items()]
    result = ImportResult(sheets)
    _check_names(result)
    _check_duplicate_sheets(result)
    _check_coverage(result)
    _check_identical_combinations(result)
    _check_mesh_consistency(result)
    return result


def _check_names(result: ImportResult) -> None:
    for s in result.sheets:
        if s.parsed is None and not s.empty:
            result.issues.append(
                Issue(
                    Severity.WARNING,
                    "unknown_sheet",
                    "Sheet name does not match '<Element>-<Combination>' for a known element; "
                    "the sheet was ignored.",
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
