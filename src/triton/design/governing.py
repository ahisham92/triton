"""Governing load sets per station, as the design office enters them in AdSec.

A station is a length of the element with one reinforcement cage (a run of the
curtailment, or the whole element when it is not reduced). For each station
and for ULS and QP separately there are seven sets:

    max N, min N     with the M2 and M3 at the same point
    max M2, min M2   with the N and M3 at the same point
    max M3, min M3   with the N and M2 at the same point
    most utilised    ULS: highest N–M utilisation with the station's cage
                     QP: largest resultant moment, until crack width is checked

Where there is no crack width check (a station inside a steel casing, the combi
wall infill), the seven QP sets are 1s so the office's AdSec template still has
its 14 rows.

N follows the concrete (AdSec) sign convention: compression positive, i.e. the
Plaxis N multiplied by -1. M2 and M3 are the Plaxis values.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from ..importer import SheetData
from ..project import DesignSettings, PileInput

EXTREMES = (
    ("max N", "N", True),
    ("min N", "N", False),
    ("max M2", "M_2", True),
    ("min M2", "M_2", False),
    ("max M3", "M_3", True),
    ("min M3", "M_3", False),
)


def _row(case: str, p: pd.Series, util: float | None) -> dict[str, Any]:
    return {
        "case": case,
        "combination": str(p["combination"]),
        "node": int(p["Node"]) if pd.notna(p.get("Node")) else None,
        "z": round(float(p["Z"]), 2),
        "N_kN": round(float(p["N"]), 1),
        "M2_kNm": round(float(p["M_2"]), 1),
        "M3_kNm": round(float(p["M_3"]), 1),
        "utilisation": None if util is None or not np.isfinite(util) else round(float(util), 3),
    }


def pick_sets(frame: pd.DataFrame, util: np.ndarray | None, seventh: str) -> list[dict[str, Any]]:
    """The six extremes and the ``seventh`` case of one station and limit state."""
    if frame.empty:
        return []
    frame = frame.reset_index(drop=True)
    rows = []
    for case, col, is_max in EXTREMES:
        i = int(frame[col].idxmax() if is_max else frame[col].idxmin())
        rows.append(_row(case, frame.iloc[i], None if util is None else util[i]))
    if util is not None:
        i = int(np.nanargmax(util))
    else:
        i = int(np.hypot(frame["M_2"], frame["M_3"]).idxmax())
    rows.append(_row(seventh, frame.iloc[i], None if util is None else util[i]))
    return rows


def placeholder_sets() -> list[dict[str, Any]]:
    """Seven QP rows of 1s, for stations without a crack width check."""
    cases = [c for c, _, _ in EXTREMES] + ["largest resultant M"]
    return [
        {
            "case": c,
            "combination": "no crack check",
            "node": None,
            "z": None,
            "N_kN": 1.0,
            "M2_kNm": 1.0,
            "M3_kNm": 1.0,
            "utilisation": None,
        }
        for c in cases
    ]


def cased(pile: PileInput, top: float, bottom: float) -> bool:
    """Whether a station lies wholly inside the pile's steel casing (no crack width check)."""
    c = pile.casing
    return c is not None and c.bottom_level <= bottom + 1e-9 and top <= c.top_level + 1e-9


def station_sets(
    pile: PileInput,
    settings: DesignSettings,
    uls: pd.DataFrame,
    qp: pd.DataFrame,
    stations: list[tuple[float, float, dict]],
) -> list[dict[str, Any]]:
    """Seven ULS and seven QP sets for each (top, bottom, cage dict) station."""
    from .piles import _utilisation

    out = []
    for top, bottom, cage in stations:
        arrangement = SimpleNamespace(rings=[SimpleNamespace(**r) for r in cage["rings"]])
        u_rows = uls[(uls["Z"] <= top + 1e-9) & (uls["Z"] >= bottom - 1e-9)]
        q_rows = qp[(qp["Z"] <= top + 1e-9) & (qp["Z"] >= bottom - 1e-9)] if not qp.empty else qp
        util = _utilisation(pile, arrangement, settings, u_rows) if len(u_rows) else None
        out.append(
            {
                "top": top,
                "bottom": bottom,
                "cage": cage["label"],
                "uls": pick_sets(u_rows, util, "most utilised"),
                "qp": (
                    placeholder_sets()
                    if cased(pile, top, bottom)
                    else pick_sets(q_rows, None, "largest resultant M")
                ),
            }
        )
    return out


def qp_loads(sheets: dict[str, SheetData], head_level: float | None, above: float) -> pd.DataFrame:
    from .piles import PileLoads

    return PileLoads.from_sheets(sheets, head_level, above, qp=True).frame


HEADER = [
    "Station top (m)",
    "Station bottom (m)",
    "Cage",
    "Limit state",
    "Case",
    "Combination",
    "Node",
    "z (m)",
    "N (kN, compression +)",
    "M2 (kNm)",
    "M3 (kNm)",
    "N–M utilisation",
]
FIELDS = ("case", "combination", "node", "z", "N_kN", "M2_kNm", "M3_kNm", "utilisation")


def workbook(project: str, section: str, results: dict[str, Any]) -> bytes:
    """One sheet per pile element and combi wall infill with its governing sets."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    designs = [(p["element"], p) for p in results.get("piles", [])]
    designs += [(f"{w['element']} infill", w["infill"]) for w in results.get("combi_walls", [])]
    for name, d in designs:
        ws = wb.create_sheet(_sheet_name(name, wb.sheetnames))
        ws.append([f"{project} · {section} · {name}"])
        ws.append(["N in the concrete (AdSec) sign convention: Plaxis N × −1. M2, M3 as in Plaxis."])
        ws.append([])
        ws.append(HEADER)
        for c in ws[4]:
            c.font = Font(bold=True)
        for st in d.get("governing_sets") or []:
            for state, rows in (("ULS", st["uls"]), ("QP", st["qp"])):
                for r in rows:
                    ws.append([st["top"], st["bottom"], st["cage"], state, *(r[f] for f in FIELDS)])
        for col, width in zip("ABCDEFGHIJKL", (10, 10, 26, 8, 16, 14, 9, 8, 12, 10, 10, 10), strict=True):
            ws.column_dimensions[col].width = width
    if not designs:
        wb.create_sheet("No results")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet_name(name: str, taken: list[str]) -> str:
    base = "".join(c for c in name if c not in "[]:*?/\\")[:31] or "Element"
    out, i = base, 2
    while out in taken:
        out = f"{base[:28]} {i}"
        i += 1
    return out
