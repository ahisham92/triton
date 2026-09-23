"""Sheet pile wall straining actions for the design office's sheet pile program.

Triton does not design the sheet pile wall. It hands over the Plaxis plate
results, with the section's load multipliers applied, in the Plaxis sign
convention (steel element: N is not multiplied by -1):

* ``Governing``: the ten ULS rows of the wall, the maximum and minimum of N, M2,
  M3, Q1 and Q2 over all combinations, each with the other actions at the same
  node (see ``governing.steel_sets``);
* one ``Envelope`` sheet per combination: maximum and minimum of every action
  across the wall at each level;
* ``Durability`` (first sheet): the input tables of ArcelorMittal Durability (v4.2.1) per corrosion
  zone, as the office runs it: one row per zone bottom level, Z | M Ed | V Ed | N Ed | e, magnitudes
  per metre with N = 0 and e = 0, and the front and back losses of each zone. M and V are
  concurrent: one table takes each zone's largest |M_11| with the V at the same node and
  combination, the other its largest |V| with the M there.
"""

from __future__ import annotations

import io
import re
from typing import Any

import pandas as pd

from ..elements import PLATE_ACTIONS, combination_type
from ..importer import SheetData
from ..project import SheetPileInput
from .governing import STEEL_KEYS, steel_header, steel_sets, uls_frame


def _actions(frame: pd.DataFrame) -> list[str]:
    return [a for a in PLATE_ACTIONS if a in frame.columns]


def envelope(frame: pd.DataFrame) -> pd.DataFrame:
    acts = _actions(frame)
    g = frame.assign(level=frame["Z"].round(2)).groupby("level")[acts]
    out = pd.concat({"max": g.max(), "min": g.min()}, axis=1).sort_index(ascending=False)
    out.columns = [f"{a} {k}" for k, a in out.columns]
    return out[[f"{a} {k}" for a in acts for k in ("max", "min")]].round(2)


def durability(sheets: dict[str, SheetData], wall: SheetPileInput) -> dict[str, Any]:
    """Durability's action tables: per corrosion zone, the governing |M| (or |V|) with the other
    action at the same node and combination."""
    f = uls_frame(sheets)
    if f.empty or "M_11" not in f.columns or wall.shear not in f.columns:
        return {"top": None, "max_M": [], "max_V": []}
    top = round(float(f["Z"].max()), 2)
    zones = wall.corrosion_zones
    out: dict[str, Any] = {"top": top, "shear": wall.shear, "max_M": [], "max_V": []}
    upper = top
    for z in zones:
        part = f[(f["Z"] <= upper + 1e-6) & (f["Z"] >= z.bottom_level - 1e-6)]
        for key, col in (("max_M", "M_11"), ("max_V", wall.shear)):
            if part.empty:
                out[key].append({"z": z.bottom_level, "M": 0.0, "V": 0.0, "combination": None, "at": None})
                continue
            i = part[col].abs().idxmax()
            r = part.loc[i]
            out[key].append(
                {
                    "z": z.bottom_level,
                    "M": round(abs(float(r["M_11"])), 1),
                    "V": round(abs(float(r[wall.shear])), 1),
                    "combination": str(r["combination"]),
                    "at": round(float(r["Z"]), 2),
                }
            )
        upper = z.bottom_level
    return out


def _durability_sheet(ws, element: str, wall: SheetPileInput, tables: dict[str, Any]) -> None:
    from openpyxl.styles import Font

    bold = Font(bold=True)
    ws.append([f"{element}: ArcelorMittal Durability input, EN 1993-5"])
    ws.append(
        [
            f"Z top = {tables['top']} m. kN/m and kNm/m, magnitudes, load multipliers applied; "
            f"V = {wall.shear}."
        ]
    )
    ws.append([])
    ws.append(["Corrosion"])
    ws[ws.max_row][0].font = bold
    ws.append(["Zone", "Z bottom (m)", "Loss front (mm)", "Loss back (mm)"])
    for c in ws[ws.max_row]:
        c.font = bold
    for k, z in enumerate(wall.corrosion_zones, 1):
        ws.append([k, z.bottom_level, z.front, z.back])
    for key, title in (
        ("max_M", "Actions: largest |M| per zone, V at the same point"),
        ("max_V", "Actions: largest |V| per zone, M at the same point"),
    ):
        ws.append([])
        ws.append([title])
        ws[ws.max_row][0].font = bold
        ws.append(
            ["N°", "Z (m)", "M Ed (kNm/m)", "V Ed (kN/m)", "N Ed (kN/m)", "e (mm)", "Combination", "At Z (m)"]
        )
        for c in ws[ws.max_row]:
            c.font = bold
        ws.append([1, tables["top"], 0, 0, 0, 0, None, None])
        for k, r in enumerate(tables[key], 2):
            ws.append([k, r["z"], r["M"], r["V"], 0, 0, r["combination"], r["at"]])
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["G"].width = 16


def workbook(
    project: str, section: str, element: str, sheets: dict[str, SheetData], wall: SheetPileInput | None = None
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wall = wall or SheetPileInput()
    wb = Workbook()
    _durability_sheet(wb.active, element, wall, durability(sheets, wall))
    wb.active.title = "Durability"
    ws = wb.create_sheet("Governing")
    ws.append([f"{project} · {section} · {element}"])
    ws.append(["Plaxis signs (N not multiplied by -1), kN/m and kNm/m, load multipliers applied."])
    ws.append([])
    sets = steel_sets(uls_frame(sheets), "plate")
    cols = sets["columns"]
    ws.append(steel_header(cols))
    for c in ws[4]:
        c.font = Font(bold=True)
    for r in sets["rows"]:
        ws.append([f"ULS {r['case']}", *(r[k] for k in STEEL_KEYS), r["combination"], r["z"], r["node"]])
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["G"].width = 14
    for combo, sheet in sheets.items():
        env = envelope(sheet.frame)
        es = wb.create_sheet(_title(f"Envelope {combo}", wb.sheetnames))
        es.append([f"{element} · {combo} ({combination_type(combo).value}): max and min across the wall"])
        es.append([])
        es.append(["Z (m)", *env.columns])
        for c in es[3]:
            c.font = Font(bold=True)
        for z, vals in env.iterrows():
            es.append([float(z), *(float(v) for v in vals)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _title(name: str, taken: list[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "", name)[:31]
    out, i = base, 2
    while out in taken:
        out = f"{base[:28]} {i}"
        i += 1
    return out
