"""Read raw cell values from .xlsb / .xlsx / .xlsm workbooks.

The reader does no interpretation: each sheet becomes a list of rows, each row
a list of cell values (``None`` for empty cells). Row index 0 is Excel row 1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

Row = list[Any]


class UnsupportedWorkbook(ValueError):
    pass


def read_workbook(path: str | Path) -> dict[str, list[Row]]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xlsb":
        return _read_xlsb(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _read_openpyxl(path)
    raise UnsupportedWorkbook(f"Unsupported file type '{path.suffix}'. Use .xlsb, .xlsx or .xlsm.")


def _read_xlsb(path: Path) -> dict[str, list[Row]]:
    from pyxlsb import open_workbook

    sheets: dict[str, list[Row]] = {}
    with open_workbook(str(path)) as wb:
        for name in wb.sheets:
            rows: list[Row] = []
            with wb.get_sheet(name) as sheet:
                for cells in sheet.rows(sparse=True):
                    if not cells:
                        continue
                    r = cells[0].r
                    while len(rows) < r:
                        rows.append([])
                    values: Row = []
                    for cell in cells:
                        while len(values) < cell.c:
                            values.append(None)
                        values.append(cell.v)
                    rows.append(values)
            sheets[name] = rows
    return sheets


def _read_openpyxl(path: Path) -> dict[str, list[Row]]:
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
    finally:
        wb.close()
