"""Turn one raw Plaxis sheet into a clean table of node results.

This automates the clean-up the team does by hand: dropping blank rows,
removing the repeated header in the middle of combi wall sheets, ignoring the
inconsistent Plaxis element names, and removing duplicated node rows.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from openpyxl.utils import get_column_letter

from .elements import (
    BEAM_ACTIONS,
    EMBEDDED_BEAM_EXTRAS,
    PLATE_ACTIONS,
    ResultKind,
    SheetName,
    parse_sheet_name,
)
from .issues import Issue, Severity
from .reader import Row

LABEL = "plaxis_label"
LOCAL = "local_number"
EXCEL_ROW = "excel_row"

_SPECIAL_HEADERS = {"structural element": LABEL, "local number": LOCAL}
_HEADER_UNIT = re.compile(r"^(?P<name>.*?)\s*\[(?P<unit>[^\]]*)\]\s*$")

# Units Plaxis uses for each action, by result kind.
_EXPECTED_UNITS = {
    ResultKind.PLATE: {"N": "kN/m", "Q": "kN/m", "M": "kN m/m"},
    ResultKind.BEAM: {"N": "kN", "Q": "kN", "M": "kN m"},
}


@dataclass
class SheetData:
    name: str
    parsed: SheetName | None
    frame: pd.DataFrame
    units: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    raw_rows: int = 0
    blank_rows: int = 0
    duplicate_rows: int = 0
    empty: bool = False
    duplicates: pd.DataFrame | None = None  # the repeated rows, kept until their removal is accepted

    @property
    def action_columns(self) -> list[str]:
        """Phase-value action columns present in this sheet (no min/max)."""
        if self.parsed is None:
            return []
        wanted = _all_actions(self.parsed.spec.kind)
        return [c for c in wanted if c in self.frame.columns]


def _all_actions(kind: ResultKind) -> tuple[str, ...]:
    return PLATE_ACTIONS if kind is ResultKind.PLATE else BEAM_ACTIONS + EMBEDDED_BEAM_EXTRAS


def normalise_header(text: Any) -> tuple[str, str | None]:
    """'   N_1,min [kN/m]' -> ('N_1_min', 'kN/m');  ' Node' -> ('Node', None)."""
    s = re.sub(r"\s+", " ", str(text)).strip()
    unit = None
    m = _HEADER_UNIT.match(s)
    if m:
        s, unit = m.group("name").strip(), m.group("unit").strip()
    special = _SPECIAL_HEADERS.get(s.lower())
    if special:
        return special, unit
    s = s.replace(",", "_").replace(" ", "_")
    s = re.sub(r"_+", "_", s)
    return s, unit


# Text Plaxis writes where a value does not apply (e.g. F_foot away from the pile tip).
_NOT_APPLICABLE = {"", "n/a", "na", "-", "nan", "#n/a"}


def _is_empty_cell(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in _NOT_APPLICABLE)


def _is_blank(row: Row) -> bool:
    return all(_is_empty_cell(v) for v in row)


def is_header(row: Row) -> bool:
    cells = {str(v).strip().lower() for v in row if isinstance(v, str)}
    return "node" in cells and any(c.startswith("x") and "[" in c for c in cells)


def _to_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def clean_sheet(name: str, rows: list[Row]) -> SheetData:
    parsed = parse_sheet_name(name)
    element = parsed.element if parsed else None
    combination = parsed.combination if parsed else None
    issues: list[Issue] = []

    def issue(sev: Severity, code: str, msg: str, rows_: list[int] | None = None) -> None:
        issues.append(Issue(sev, code, msg, name, element, combination, rows_ or []))

    non_blank = [i for i, r in enumerate(rows) if not _is_blank(r)]
    if not non_blank:
        issue(Severity.INFO, "empty_sheet", "Sheet is empty and was ignored.")
        return SheetData(name, parsed, pd.DataFrame(), issues=issues, raw_rows=len(rows), empty=True)

    header_idx = [i for i in non_blank if is_header(rows[i])]
    if not header_idx:
        issue(
            Severity.ERROR,
            "no_header",
            "Could not find the Plaxis header row (a row with 'Node' and 'X [m]').",
        )
        return SheetData(name, parsed, pd.DataFrame(), issues=issues, raw_rows=len(rows))

    first = header_idx[0]
    header_set = set(header_idx)
    above = [i for i in non_blank if i < first]
    if above:
        issue(
            Severity.WARNING,
            "content_above_header",
            f"{len(above)} row(s) above the header were ignored.",
            [i + 1 for i in above],
        )

    # Build the column map from every header row. Later headers (the combi wall
    # sheets have a second table stacked under the first) may add columns.
    columns: dict[int, str] = {}
    units: dict[str, str] = {}
    for h in header_idx:
        for col, text in enumerate(rows[h]):
            if text is None or (isinstance(text, str) and not text.strip()):
                continue
            col_name, unit = normalise_header(text)
            if col in columns and columns[col] != col_name:
                issue(
                    Severity.ERROR,
                    "header_mismatch",
                    f"Header at row {h + 1} names column {col + 1} '{col_name}', "
                    f"but the first header names it '{columns[col]}'.",
                    [h + 1],
                )
                continue
            columns.setdefault(col, col_name)
            if unit is not None:
                units.setdefault(col_name, unit)
    if len(header_idx) > 1:
        added = sorted(set(columns.values()) - {normalise_header(t)[0] for t in rows[first] if t})
        extra = f" It also named extra columns: {', '.join(added)}." if added else ""
        issue(
            Severity.INFO,
            "repeated_header_removed",
            f"Removed {len(header_idx) - 1} repeated header row(s) so the tables form one list.{extra}",
            [h + 1 for h in header_idx[1:]],
        )

    records: list[dict[str, Any]] = []
    bad_rows: list[int] = []
    unnamed_rows: list[int] = []
    unnamed: dict[int, Any] = {}  # column -> the first value found in it
    blank = 0
    numeric_cols = [c for c in columns.values() if c not in (LABEL,)]
    for i in range(first + 1, len(rows)):
        row = rows[i]
        if i in header_set:
            continue
        if _is_blank(row):
            blank += 1
            continue
        rec: dict[str, Any] = {EXCEL_ROW: i + 1}
        ok = True
        for col, value in enumerate(row):
            col_name = columns.get(col)
            if col_name is None:
                if not _is_empty_cell(value):
                    unnamed_rows.append(i + 1)
                    unnamed.setdefault(col, value)
                continue
            if col_name == LABEL:
                rec[LABEL] = str(value).strip().replace("\\_", "_") if value is not None else ""
                continue
            num = _to_number(value)
            if num is None and not _is_empty_cell(value):
                ok = False
            rec[col_name] = num
        if not ok:
            bad_rows.append(i + 1)
            continue
        records.append(rec)

    if blank:
        issue(Severity.INFO, "blank_rows_removed", f"Removed {blank} blank row(s).")
    if bad_rows:
        issue(
            Severity.ERROR,
            "non_numeric",
            f"{len(bad_rows)} row(s) contain text where a number is expected and were left out.",
            bad_rows,
        )
    if unnamed_rows:
        # Values beside the table, under no header (notes, a helper column): not Plaxis results.
        found = sorted(unnamed.items())[:4]
        letters = ", ".join(get_column_letter(c + 1) for c, _ in found)
        samples = ", ".join(repr(str(v).strip()[:20]) for _, v in found)
        issue(
            Severity.INFO,
            "unnamed_columns",
            f"Extra column{'s' if len(found) > 1 else ''} {letters} beside the Plaxis table (no header above "
            f"{'them' if len(found) > 1 else 'it'}): not read. {len(set(unnamed_rows))} row(s) have values "
            f"there, e.g. {samples}.",
            unnamed_rows,
        )

    frame = pd.DataFrame.from_records(records)
    for c in numeric_cols:
        if c not in frame.columns:
            frame[c] = np.nan
    if "Node" in frame.columns:
        frame["Node"] = frame["Node"].astype("Int64")

    sheet = SheetData(name, parsed, frame, units, issues, raw_rows=len(rows), blank_rows=blank)
    if frame.empty:
        issue(Severity.ERROR, "no_data", "The header was found but there are no data rows under it.")
        return sheet

    _check_required(sheet, issue)
    _check_missing_values(sheet, issue)
    _drop_duplicates(sheet, issue)
    _check_nodes(sheet, issue)
    _check_envelope(sheet, issue)
    _check_units(sheet, issue)
    return sheet


def _check_required(sheet: SheetData, issue) -> None:
    required = ["Node", "X", "Y", "Z"]
    if sheet.parsed is not None:
        required += list(sheet.parsed.spec.required_actions)
    missing = [c for c in required if c not in sheet.frame.columns]
    if missing:
        issue(Severity.ERROR, "missing_columns", f"Missing required column(s): {', '.join(missing)}.")


def _check_missing_values(sheet: SheetData, issue) -> None:
    f = sheet.frame
    cols = [c for c in ["Node", "X", "Y", "Z", *sheet.action_columns] if c in f.columns]
    if sheet.parsed is not None and sheet.parsed.spec.kind is ResultKind.BEAM:
        # Only embedded beams report skin and foot forces; plain beams leave them empty.
        cols = [c for c in cols if c not in EMBEDDED_BEAM_EXTRAS]
    holes = f[f[cols].isna().any(axis=1)]
    if not holes.empty:
        issue(
            Severity.ERROR,
            "missing_values",
            f"{len(holes)} row(s) have empty cells in node, coordinate or force columns.",
            holes[EXCEL_ROW].astype(int).tolist(),
        )


def _design_columns(sheet: SheetData) -> list[str]:
    """Node, coordinates and the structural actions with their min/max.

    Embedded-beam skin and foot forces are left out: they differ between the
    two elements that share a node but are not used in structural design.
    """
    f = sheet.frame
    actions = sheet.parsed.spec.required_actions if sheet.parsed else ()
    cols = ["Node", "X", "Y", "Z"]
    for a in actions:
        cols += [a, f"{a}_min", f"{a}_max"]
    return [c for c in cols if c in f.columns]


def _drop_duplicates(sheet: SheetData, issue) -> None:
    f = sheet.frame
    before = len(f)
    repeated = f.duplicated(subset=_design_columns(sheet), keep="first")
    sheet.duplicates = f[repeated].reset_index(drop=True) if repeated.any() else None
    f = f[~repeated].reset_index(drop=True)
    sheet.duplicate_rows = before - len(f)
    sheet.frame = f
    if sheet.duplicate_rows:
        # Each repeat and the row it repeats (Plaxis lists a node shared by two elements twice).
        cols = _design_columns(sheet)
        first = sheet.duplicates.merge(
            pd.DataFrame(f[cols + [EXCEL_ROW]]).rename(columns={EXCEL_ROW: "_first"}), on=cols, how="left"
        )
        twins = dict(zip(first[EXCEL_ROW].astype(int), first["_first"], strict=True))
        rows = sheet.duplicates[EXCEL_ROW].astype(int).tolist()
        shown = [f"row {r} repeats row {int(twins[r])}" for r in rows[:3] if pd.notna(twins.get(r))]
        issues_before = len(sheet.issues)
        issue(
            Severity.INFO,
            "duplicate_rows_removed",
            f"{sheet.duplicate_rows} repeated row(s): the same node with the same forces as an earlier row"
            + (f" ({', '.join(shown)})" if shown else "")
            + ".",
            rows,
        )
        sheet.issues[issues_before].notes = {
            r: f"Repeats row {int(twins[r])}." for r in rows[: Issue.MAX_ROWS] if pd.notna(twins.get(r))
        }


def _check_nodes(sheet: SheetData, issue) -> None:
    f = sheet.frame
    if "Node" not in f.columns:
        return
    coords = f.groupby("Node")[["X", "Y", "Z"]].nunique(dropna=False)
    moved = coords[(coords > 1).any(axis=1)].index
    if len(moved):
        rows = f[f["Node"].isin(moved)][EXCEL_ROW].astype(int).tolist()
        issue(
            Severity.ERROR,
            "node_coordinates_differ",
            f"{len(moved)} node(s) appear with different coordinates.",
            rows,
        )
    counts = f.groupby("Node").size()
    conflicting = counts[counts > 1].index.difference(moved)
    if len(conflicting):
        rows = f[f["Node"].isin(conflicting)][EXCEL_ROW].astype(int).tolist()
        issue(
            Severity.WARNING,
            "node_values_differ",
            f"{len(conflicting)} node(s) appear more than once with different force values; all are kept.",
            rows,
        )


def _check_envelope(sheet: SheetData, issue) -> None:
    f = sheet.frame
    rows: list[int] = []
    for a in sheet.action_columns:
        lo, hi = f"{a}_min", f"{a}_max"
        if lo in f.columns and hi in f.columns:
            tol = 1e-6 * np.maximum(1.0, f[a].abs())
            bad = (f[a] < f[lo] - tol) | (f[a] > f[hi] + tol)
            rows += f.loc[bad, EXCEL_ROW].astype(int).tolist()
    if rows:
        issue(
            Severity.WARNING,
            "outside_envelope",
            f"{len(set(rows))} row(s) have a value outside its own min/max, which Plaxis never exports.",
            rows,
        )


def _check_units(sheet: SheetData, issue) -> None:
    if sheet.parsed is None:
        return
    expected = _EXPECTED_UNITS[sheet.parsed.spec.kind]
    wrong = []
    for col, unit in sheet.units.items():
        want = expected.get(col[:1]) if col[:1] in "NQM" and col not in ("Node",) else None
        if want and unit.replace(" ", "") != want.replace(" ", ""):
            wrong.append(f"{col} [{unit}], expected [{want}]")
    if wrong:
        issue(Severity.WARNING, "unexpected_units", "Unexpected units: " + "; ".join(wrong) + ".")
