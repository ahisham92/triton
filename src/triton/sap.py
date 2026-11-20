"""Reading SAP2000 results for the results film.

SAP2000 writes its tables as text (File > Export > SAP2000 .s2k / .$2k, or the database tables
exported to a text file) or as a workbook (the tables exported to Excel). Results files are large:
every joint, member and plate in every load case. Only what the film shows is kept, folded as it
is read:

- the model: joints, frames, areas, their sections, supports;
- the loads put on it, per load pattern;
- joint reactions and displacements per output case (envelopes keep their larger value);
- frame and shell forces, the largest of each per member and output case;
- the design ratios SAP2000 reports (steel, concrete or other design summary tables).

The text is read a few seconds at a time (a host ends a request that runs too long), from the byte
where the last step stopped; a workbook a few sheets at a time. What has been gathered is kept
beside the upload between steps (``Gather`` pickles itself). ``Gather.summary()`` then gives the
compact picture the page draws.

The page reads the user's files itself, in the browser (static/sapread.js), so a results file of
hundreds of megabytes never reaches the site; this module is the same reader in Python, used for the
sample film and to check the page's reader in the tests.

Signs and axes are SAP2000's: global Z up, a reaction F3 > 0 pushes the structure up (the support
is in compression), frame P < 0 is compression.
"""

from __future__ import annotations

import gzip
import io
import math
import pickle
import re
import shutil
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from . import atomic

TEXT = {".s2k", ".$2k", ".txt", ".e2k", ".$et"}
BOOKS = {".xlsx", ".xlsm", ".xlsb"}
SUFFIXES = TEXT | BOOKS | {".zip", ".gz"}

STATE = "sap.pkl"

# How many items of one kind the summary carries at most (the film thins the rest out).
MAX_LOAD_ITEMS = 4000
MAX_FIELDS = 6  # displaced shapes kept in full


class NotSap(ValueError):
    """The file holds no SAP2000 tables Triton can use."""


# --- units ------------------------------------------------------------------------------------

FORCE = {
    "N": 1e-3,
    "KN": 1.0,
    "MN": 1e3,
    "KGF": 9.80665e-3,
    "TONF": 9.80665,
    "TON": 9.80665,
    "KIP": 4.448222,
    "LB": 4.448222e-3,
    "LBF": 4.448222e-3,
    "T": 9.80665,
}
LENGTH = {"MM": 1e-3, "CM": 1e-2, "M": 1.0, "IN": 0.0254, "FT": 0.3048, "INCH": 0.0254}


def _unit_factor(text: str) -> float | None:
    """kN and m factor of a unit as SAP2000 prints it in a workbook's units row ("KN-m", "KN/m2", "mm")."""
    t = str(text or "").strip().upper().replace(" ", "")
    if not t or t in {"UNITLESS", "RADIANS", "DEGREES", "SEC", "%"}:
        return None
    num, _, den = t.partition("/")
    out = 1.0
    for part, sign in ((num, 1), (den, -1)):
        if not part:
            continue
        for tok in re.split(r"[-*·]", part):
            m = re.fullmatch(r"([A-Z]+)(\d?)", tok)
            if not m:
                return None
            name, power = m.group(1), int(m.group(2) or 1)
            f = FORCE.get(name) if name in FORCE and name != "M" else LENGTH.get(name)
            if f is None:
                return None
            out *= f ** (power * sign)
    return out


def _curr_units(text: str) -> tuple[float, float]:
    """Force and length factors from PROGRAM CONTROL's CurrUnits ("KN, m, C")."""
    parts = [p.strip().upper() for p in str(text).split(",")]
    f = FORCE.get(parts[0], 1.0) if parts else 1.0
    length = LENGTH.get(parts[1], 1.0) if len(parts) > 1 else 1.0
    return f, length


# Each field's dimension: L length, F force, FL moment, F/L line force (or shell force), F/L2 pressure.
DIM = {
    **{
        k: "L"
        for k in (
            "XorR",
            "X",
            "Y",
            "Z",
            "GlobalX",
            "GlobalY",
            "GlobalZ",
            "U1",
            "U2",
            "U3",
            "t3",
            "t2",
            "Thickness",
            "Thick",
            "BendThick",
            "AbsDistA",
            "AbsDistB",
            "AbsDist",
            "Station",
            "Length",
        )
    },
    **{k: "F" for k in ("F1", "F2", "F3", "P", "V2", "V3", "Force", "GlobalFX", "GlobalFY", "GlobalFZ")},
    **{k: "FL" for k in ("M1", "M2", "M3", "T", "GlobalMX", "GlobalMY", "GlobalMZ", "Moment")},
    **{k: "F/L" for k in ("FOverLA", "FOverLB", "F11", "F22", "F12", "FMax", "FMin", "V13", "V23", "VMax")},
    **{k: "F" for k in ("M11", "M22", "M12", "MMax", "MMin")},  # shell moments are per metre: kN m/m = kN
    "UnifLoad": "F/L2",
}


def _dim_factor(dim: str, f: float, length: float) -> float:
    return {"L": length, "F": f, "FL": f * length, "F/L": f / length, "F/L2": f / length**2}[dim]


# --- the text format -------------------------------------------------------------------------

_TABLE = re.compile(r'^\s*TABLE:\s*"?([^"]+?)"?\s*$', re.I)
_PAIR = re.compile(r'(\w+)=("([^"]*)"|\S*)')


def _record(line: str) -> dict[str, str]:
    """One record of a text table: ``Joint=1   CoordSys=GLOBAL   Name="A B"``."""
    if '"' not in line:
        out = {}
        for tok in line.split():
            k, _, v = tok.partition("=")
            out[k] = v
        return out
    return {m.group(1): (m.group(3) if m.group(3) is not None else m.group(2)) for m in _PAIR.finditer(line)}


def _num(v: Any) -> float:
    if v is None or v == "":
        return math.nan
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return math.nan


def _key(v: Any) -> str:
    """A joint, frame or case name as text ("12" for a workbook's 12.0)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


# --- what is gathered ------------------------------------------------------------------------


class _PerCase:
    """Values per item and output case, in arrays that grow; ``mode`` "abs" keeps the value of
    larger size (signed), "max"/"min" the larger or smaller."""

    def __init__(self, width: int) -> None:
        self.width = width
        self.data: dict[int, np.ndarray] = {}

    def get(self, case: int, n: int) -> np.ndarray:
        a = self.data.get(case)
        if a is None:
            a = np.full((max(64, n * 2), self.width), np.nan, dtype=np.float32)
            self.data[case] = a
        elif a.shape[0] < n:
            b = np.full((max(n, a.shape[0] * 2), self.width), np.nan, dtype=np.float32)
            b[: a.shape[0]] = a
            a = self.data[case] = b
        return a


def _fold_abs(row: np.ndarray, vals: list[float]) -> None:
    for k, v in enumerate(vals):
        if v == v:  # not NaN
            o = row[k]
            if not (o == o) or abs(v) > abs(o):
                row[k] = v


class Gather:
    """Everything kept from the tables read so far."""

    def __init__(self) -> None:
        self.f, self.l = 1.0, 1.0  # force and length factors to kN and m (text files)
        self.program = ""
        self.tables: dict[str, int] = {}
        self.ignored: dict[str, int] = {}
        self.joint_ix: dict[str, int] = {}
        self.xyz: list[tuple[float, float, float]] = []
        self.frame_ix: dict[str, int] = {}
        self.frames: list[list[int]] = []  # [i, j]
        self.frame_sect: dict[int, str] = {}
        self.area_ix: dict[str, int] = {}
        self.areas: list[list[int]] = []
        self.area_sect: dict[int, str] = {}
        self.sections: dict[str, dict] = {}
        self.area_sections: dict[str, dict] = {}
        self.restraints: dict[int, str] = {}
        self.springs: set[int] = set()
        self.patterns: dict[str, dict] = {}
        self.cases: dict[str, dict] = {}
        self.combos: dict[str, dict] = {}
        self.case_ix: dict[str, int] = {}
        self.case_types: dict[int, str] = {}
        self.loads: dict[str, dict[str, list]] = {}
        self.load_counts: dict[str, int] = {}
        self.react = _PerCase(12)  # F1..M3 max, then F1..M3 min
        self.disp = _PerCase(3)
        self.fforce = _PerCase(6)  # P V2 V3 T M2 M3, the larger in size over the stations
        self.sforce = _PerCase(5)  # M11 M22 V13 V23 (or VMax) F11..F22 larger
        self.design: dict[int, dict] = {}
        self.base: dict[str, list[float]] = {}
        self.rows = 0

    # indices ---------------------------------------------------------------------------------
    def _joint(self, name: Any) -> int:
        k = _key(name)
        i = self.joint_ix.get(k)
        if i is None:
            i = self.joint_ix[k] = len(self.xyz)
            self.xyz.append((math.nan, math.nan, math.nan))
        return i

    def _frame(self, name: Any) -> int:
        k = _key(name)
        i = self.frame_ix.get(k)
        if i is None:
            i = self.frame_ix[k] = len(self.frames)
            self.frames.append([-1, -1])
        return i

    def _area(self, name: Any) -> int:
        k = _key(name)
        i = self.area_ix.get(k)
        if i is None:
            i = self.area_ix[k] = len(self.areas)
            self.areas.append([])
        return i

    def _case(self, name: Any, kind: str = "") -> int:
        k = _key(name)
        i = self.case_ix.get(k)
        if i is None:
            i = self.case_ix[k] = len(self.case_ix)
        if kind and i not in self.case_types:
            self.case_types[i] = kind
        return i

    # one record of a table ------------------------------------------------------------------
    def add(self, table: str, rec: dict[str, Any], units: dict[str, float] | None = None) -> None:
        """Fold one record of ``table`` in. ``units`` gives a workbook column's factor to kN and m;
        text files use the PROGRAM CONTROL units."""
        t = table.upper()
        self.rows += 1

        def n(k: str) -> float:
            v = _num(rec.get(k))
            if units is not None:
                fac = units.get(k)
                return v * fac if fac is not None else v
            dim = DIM.get(k)
            return v * _dim_factor(dim, self.f, self.l) if dim else v

        handler = _handler(t)
        if handler is None:
            self.ignored[table] = self.ignored.get(table, 0) + 1
            return
        self.tables[table] = self.tables.get(table, 0) + 1
        handler(self, rec, n)

    # the handlers ---------------------------------------------------------------------------
    def _program(self, rec: dict, n: Any) -> None:
        self.program = f"{rec.get('ProgramName', 'SAP2000')} {rec.get('Version', '')}".strip()
        if rec.get("CurrUnits"):
            self.f, self.l = _curr_units(rec["CurrUnits"])

    def _coords(self, rec: dict, n: Any) -> None:
        i = self._joint(rec.get("Joint"))
        if "GlobalX" in rec:
            x, y, z = n("GlobalX"), n("GlobalY"), n("GlobalZ")
        else:
            x, y, z = n("XorR") if "XorR" in rec else n("X"), n("Y"), n("Z")
        self.xyz[i] = (x, y, z)

    def _conn_frame(self, rec: dict, n: Any) -> None:
        i = self._frame(rec.get("Frame"))
        self.frames[i] = [self._joint(rec.get("JointI")), self._joint(rec.get("JointJ"))]

    def _conn_area(self, rec: dict, n: Any) -> None:
        i = self._area(rec.get("Area"))
        count = int(_num(rec.get("NumJoints")) or 0) if rec.get("NumJoints") not in (None, "") else 0
        keys = [f"Joint{k}" for k in range(1, (count or 9) + 1)]
        self.areas[i] = [self._joint(rec[k]) for k in keys if rec.get(k) not in (None, "")]

    def _frame_sect(self, rec: dict, n: Any) -> None:
        i = self._frame(rec.get("Frame"))
        self.frame_sect[i] = str(rec.get("AnalSect") or rec.get("DesignSect") or rec.get("Section") or "")

    def _sect_props(self, rec: dict, n: Any) -> None:
        name = str(rec.get("SectionName") or rec.get("Name") or "")
        if name:
            self.sections[name] = {
                "shape": str(rec.get("Shape") or ""),
                "d": n("t3"),
                "b": n("t2"),
                "material": str(rec.get("Material") or ""),
            }

    def _area_sect(self, rec: dict, n: Any) -> None:
        i = self._area(rec.get("Area"))
        self.area_sect[i] = str(rec.get("Section") or "")

    def _area_props(self, rec: dict, n: Any) -> None:
        name = str(rec.get("Section") or "")
        if name:
            t = n("Thickness") if "Thickness" in rec else n("Thick")
            self.area_sections[name] = {"t": t, "material": str(rec.get("Material") or "")}

    def _restraint(self, rec: dict, n: Any) -> None:
        i = self._joint(rec.get("Joint"))
        self.restraints[i] = "".join(
            "1" if str(rec.get(k, "")).lower() in {"yes", "true", "1"} else "0"
            for k in ("U1", "U2", "U3", "R1", "R2", "R3")
        )

    def _spring(self, rec: dict, n: Any) -> None:
        self.springs.add(self._joint(rec.get("Joint")))

    def _pattern(self, rec: dict, n: Any) -> None:
        name = _key(rec.get("LoadPat"))
        self.patterns[name] = {
            "type": str(rec.get("DesignType") or rec.get("Type") or ""),
            "self": _num(rec.get("SelfWtMult")) or 0.0,
        }

    def _case_def(self, rec: dict, n: Any) -> None:
        name = _key(rec.get("Case"))
        self.cases[name] = {"type": str(rec.get("Type") or ""), "design": str(rec.get("DesignType") or "")}

    def _combo(self, rec: dict, n: Any) -> None:
        name = _key(rec.get("ComboName"))
        c = self.combos.setdefault(name, {"type": "", "parts": []})
        if rec.get("ComboType"):
            c["type"] = str(rec["ComboType"])
        if rec.get("CaseName") not in (None, ""):
            c["parts"].append([_key(rec["CaseName"]), _num(rec.get("ScaleFactor"))])

    def _keep_load(self, pat: str, kind: str, item: list) -> None:
        key = f"{pat}|{kind}"
        self.load_counts[key] = self.load_counts.get(key, 0) + 1
        bucket = self.loads.setdefault(pat, {}).setdefault(kind, [])
        if len(bucket) < MAX_LOAD_ITEMS:
            bucket.append(item)

    def _joint_load(self, rec: dict, n: Any) -> None:
        vals = [n(k) for k in ("F1", "F2", "F3")]
        self._keep_load(
            _key(rec.get("LoadPat")),
            "joint",
            [self._joint(rec.get("Joint")), *[0.0 if v != v else v for v in vals]],
        )

    def _frame_dist(self, rec: dict, n: Any) -> None:
        a, b = n("FOverLA"), n("FOverLB")
        v = (0.0 if a != a else a) + (0.0 if b != b else b)
        self._keep_load(
            _key(rec.get("LoadPat")),
            "frame",
            [self._frame(rec.get("Frame")), str(rec.get("Dir") or "Gravity"), v / 2],
        )

    def _frame_point(self, rec: dict, n: Any) -> None:
        f, rel = n("Force"), _num(rec.get("RelDist"))
        self._keep_load(
            _key(rec.get("LoadPat")),
            "point",
            [
                self._frame(rec.get("Frame")),
                str(rec.get("Dir") or "Gravity"),
                0.0 if f != f else f,
                0.5 if rel != rel else rel,
            ],
        )

    def _area_load(self, rec: dict, n: Any) -> None:
        v = n("UnifLoad")
        self._keep_load(
            _key(rec.get("LoadPat")),
            "area",
            [self._area(rec.get("Area")), str(rec.get("Dir") or "Gravity"), 0.0 if v != v else v],
        )

    def _results_case(self, rec: dict) -> tuple[int, str]:
        c = self._case(rec.get("OutputCase"), str(rec.get("CaseType") or ""))
        return c, str(rec.get("StepType") or "").lower()

    def _reaction(self, rec: dict, n: Any) -> None:
        c, step = self._results_case(rec)
        i = self._joint(rec.get("Joint"))
        a = self.react.get(c, len(self.xyz))
        row = a[i]
        for k, name in enumerate(("F1", "F2", "F3", "M1", "M2", "M3")):
            v = n(name)
            if v != v:
                continue
            if step != "min" and (row[k] != row[k] or v > row[k]):
                row[k] = v
            if step != "max" and (row[6 + k] != row[6 + k] or v < row[6 + k]):
                row[6 + k] = v

    def _displacement(self, rec: dict, n: Any) -> None:
        c, _ = self._results_case(rec)
        i = self._joint(rec.get("Joint"))
        _fold_abs(self.disp.get(c, len(self.xyz))[i], [n("U1"), n("U2"), n("U3")])

    def _frame_force(self, rec: dict, n: Any) -> None:
        c, _ = self._results_case(rec)
        i = self._frame(rec.get("Frame"))
        _fold_abs(self.fforce.get(c, len(self.frames))[i], [n(k) for k in ("P", "V2", "V3", "T", "M2", "M3")])

    def _shell_force(self, rec: dict, n: Any) -> None:
        c, _ = self._results_case(rec)
        i = self._area(rec.get("Area"))
        v13 = n("V13") if "V13" in rec else n("VMax")
        _fold_abs(
            self.sforce.get(c, len(self.areas))[i],
            [n("M11"), n("M22"), v13, n("V23"), max(abs(n("F11")), abs(n("F22")))],
        )

    def _design(self, rec: dict, n: Any) -> None:
        name = rec.get("Frame")
        if name in (None, ""):
            return
        ratios = {k: _num(v) for k, v in rec.items() if k.lower().endswith("ratio") and _num(v) == _num(v)}
        if not ratios:
            return
        key, ratio = max(ratios.items(), key=lambda kv: kv[1])
        i = self._frame(name)
        old = self.design.get(i)
        if old is None or ratio > old["ratio"]:
            self.design[i] = {
                "ratio": ratio,
                "what": str(rec.get("RatioType") or key),
                "combo": str(rec.get("Combo") or rec.get("PMMCombo") or rec.get("DesignCombo") or ""),
                "section": str(rec.get("DesignSect") or ""),
                "status": str(rec.get("Status") or ""),
            }

    def _base(self, rec: dict, n: Any) -> None:
        case = _key(rec.get("OutputCase"))
        vals = [n(k) for k in ("GlobalFX", "GlobalFY", "GlobalFZ")]
        old = self.base.get(case)
        if old is None:
            self.base[case] = vals
        else:
            self.base[case] = [v if abs(v) > abs(o) else o for v, o in zip(vals, old, strict=True)]

    # the summary the page draws -------------------------------------------------------------
    def summary(self) -> dict:
        return summarise(self)


def _handler(t: str) -> Any:
    if t == "PROGRAM CONTROL":
        return Gather._program
    if t == "JOINT COORDINATES":
        return Gather._coords
    if t == "CONNECTIVITY - FRAME":
        return Gather._conn_frame
    if t in {"CONNECTIVITY - AREA", "CONNECTIVITY - SHELL"}:
        return Gather._conn_area
    if t == "FRAME SECTION ASSIGNMENTS":
        return Gather._frame_sect
    if t.startswith("FRAME SECTION PROPERTIES 01"):
        return Gather._sect_props
    if t == "AREA SECTION ASSIGNMENTS":
        return Gather._area_sect
    if t.startswith("AREA SECTION PROPERTIES"):
        return Gather._area_props
    if t == "JOINT RESTRAINT ASSIGNMENTS":
        return Gather._restraint
    if t.startswith("JOINT SPRING ASSIGNMENTS"):
        return Gather._spring
    if t == "LOAD PATTERN DEFINITIONS":
        return Gather._pattern
    if t == "LOAD CASE DEFINITIONS":
        return Gather._case_def
    if t == "COMBINATION DEFINITIONS":
        return Gather._combo
    if t == "JOINT LOADS - FORCE":
        return Gather._joint_load
    if t == "FRAME LOADS - DISTRIBUTED":
        return Gather._frame_dist
    if t == "FRAME LOADS - POINT":
        return Gather._frame_point
    if t.startswith("AREA LOADS - UNIFORM"):
        return Gather._area_load
    if t == "JOINT REACTIONS":
        return Gather._reaction
    if t in {"JOINT DISPLACEMENTS", "JOINT DISPLACEMENTS - ABSOLUTE"}:
        return Gather._displacement
    if t in {"ELEMENT FORCES - FRAMES", "FRAME FORCES"}:
        return Gather._frame_force
    if t in {"ELEMENT FORCES - AREA SHELLS", "ELEMENT FORCES - AREA PLANES"}:
        return Gather._shell_force
    if "DESIGN" in t and ("SUMMARY" in t or "RATIO" in t):
        return Gather._design
    if t == "BASE REACTIONS":
        return Gather._base
    return None


# --- reading in steps ------------------------------------------------------------------------


def _save(work: Path, state: dict) -> None:
    tmp = atomic.tmp_for(work / STATE)
    with tmp.open("wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(work / STATE)


def _load(work: Path) -> dict | None:
    p = work / STATE
    if not p.exists():
        return None
    # Only this app writes this pickle, beside an upload of the user's own file.
    with p.open("rb") as f:
        return pickle.load(f)


def _unpack(path: Path, work: Path) -> Path:
    """The SAP2000 file inside a .zip or .gz, written beside the upload."""
    if path.suffix.lower() == ".gz":
        inner = Path(path.name[: -len(".gz")] if path.name.lower().endswith(".gz") else "model.s2k")
        out = work / f"inner{inner.suffix.lower() or '.s2k'}"
        with gzip.open(path, "rb") as src, out.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        return out
    with zipfile.ZipFile(path) as z:
        names = [
            i for i in z.infolist() if not i.is_dir() and Path(i.filename).suffix.lower() in TEXT | BOOKS
        ]
        if not names:
            raise NotSap("The zip holds no SAP2000 tables (.s2k, .$2k, .txt or an Excel export).")
        best = max(names, key=lambda i: i.file_size)
        out = work / f"inner{Path(best.filename).suffix.lower()}"
        with z.open(best) as src, out.open("wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    return out


def read_step(work: Path, path: Path, budget_s: float = 4.0) -> dict:
    """Read the next part of the SAP2000 file at ``path`` for about ``budget_s`` seconds, keeping
    what is gathered in ``work``. Returns how far it has got; ``done`` once the whole file is read."""
    started = time.monotonic()
    state = _load(work)
    if state is None:
        src = path
        if path.suffix.lower() in {".zip", ".gz"}:
            src = _unpack(path, work)
        kind = "book" if src.suffix.lower() in BOOKS else "text"
        state = {
            "src": str(src),
            "kind": kind,
            "offset": 0,
            "table": "",
            "sheets": None,
            "read": [],
            "gather": Gather(),
            "size": src.stat().st_size,
            "done": False,
        }
        if kind == "book":
            from .reader import sheet_names

            state["sheets"] = sheet_names(src)
        if src is not path:
            _save(work, state)
            return _said(state, "Unpacked the file")
    if state["done"]:
        return _said(state, "Read")
    if state["kind"] == "text":
        _text_step(state, started, budget_s)
    else:
        _book_step(state, started, budget_s)
    _save(work, state)
    return _said(state, f"Reading {state['table'].title()}" if state["table"] else "Reading")


def _said(state: dict, step: str) -> dict:
    g: Gather = state["gather"]
    if state["kind"] == "text":
        fraction = state["offset"] / max(1, state["size"])
    else:
        fraction = len(state["read"]) / max(1, len(state["sheets"] or []))
    return {
        "done": state["done"],
        "fraction": 1.0 if state["done"] else round(min(0.999, fraction), 4),
        "step": step,
        "rows": g.rows,
        "tables": len(g.tables),
    }


def _text_step(state: dict, started: float, budget_s: float) -> None:
    g: Gather = state["gather"]
    table = state["table"]
    keep = table and _handler(table.upper()) is not None
    with open(state["src"], "rb") as f:
        f.seek(state["offset"])
        offset = state["offset"]
        pending = ""
        count = 0
        for raw in f:
            offset += len(raw)
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if pending:
                line = pending + " " + line.lstrip()
                pending = ""
            if line.endswith(" _"):
                pending = line[:-2]
                continue
            s = line.strip()
            if not s or s.startswith("$") or s.startswith("File "):
                pass
            elif s.upper().startswith("TABLE:"):
                m = _TABLE.match(s)
                table = m.group(1).strip() if m else s[6:].strip().strip('"')
                keep = _handler(table.upper()) is not None
                if not keep:
                    g.ignored.setdefault(table, 0)
            elif s.upper().startswith("END TABLE DATA"):
                table, keep = "", False
            elif keep:
                g.add(table, _record(s))
            elif table:
                g.ignored[table] = g.ignored.get(table, 0) + 1
            count += 1
            if count % 20000 == 0 and time.monotonic() - started > budget_s:
                if not pending:
                    break
        else:
            state["done"] = True
        state["offset"] = offset
        state["table"] = table


def _book_step(state: dict, started: float, budget_s: float) -> None:
    from .reader import read_sheets

    g: Gather = state["gather"]
    todo = [s for s in state["sheets"] if s not in state["read"]]
    if not todo:
        state["done"] = True
        return
    for name, rows in read_sheets(state["src"], todo[:1] if True else todo):
        _book_sheet(g, name, rows)
        state["read"].append(name)
        state["table"] = name
        if time.monotonic() - started > budget_s:
            break
    if len(state["read"]) >= len(state["sheets"]):
        state["done"] = True


def _book_sheet(g: Gather, name: str, rows: list[list[Any]]) -> None:
    """A sheet of SAP2000's Excel export: "TABLE:  Joint Coordinates" in A1, the field names in
    row 2, their units in row 3, the records below. A sheet without the title line is taken by its
    name, with the field names in row 1."""
    if not rows:
        return
    head = 0
    table = name
    first = str(rows[0][0] or "") if rows[0] else ""
    if first.upper().startswith("TABLE:"):
        table = first.split(":", 1)[1].strip().strip('"')
        head = 1
    if len(rows) <= head:
        return
    fields = [str(c).strip() if c is not None else "" for c in rows[head]]
    units_row = rows[head + 1] if len(rows) > head + 1 else []
    # SAP2000's export: the units always in the row under the field names (geometry tables say
    # only "Text" there); elsewhere only if it reads as units
    has_units = bool(units_row) and (
        head == 1
        or (
            not any(isinstance(c, (int, float)) for c in units_row[:3])
            and any(_unit_factor(c) is not None for c in units_row if c is not None)
        )
    )
    units: dict[str, float] = {}
    if has_units:
        for k, u in zip(fields, units_row, strict=False):
            fac = _unit_factor(u) if u is not None else None
            if k and fac is not None:
                units[k] = fac
    if _handler(table.upper()) is None:
        g.ignored[table] = g.ignored.get(table, 0) + max(0, len(rows) - head - 2)
        return
    start = head + (2 if has_units else 1)
    for r in rows[start:]:
        if not r or all(c is None for c in r):
            continue
        g.add(table, {k: v for k, v in zip(fields, r, strict=False) if k and v is not None}, units)


def read_all(path: Path, work: Path) -> Gather:
    """Read the whole file at once (scripts and tests)."""
    while True:
        said = read_step(work, path, budget_s=1e9)
        if said["done"]:
            return _load(work)["gather"]


# --- the summary -----------------------------------------------------------------------------


def _r(v: float, d: int = 3) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), d)


def _kind_of(g: Gather, case: str) -> str:
    """ "combo", "case", with its design type where SAP2000 gives one."""
    return "combo" if case in g.combos else "case"


def _is_service(name: str, info: dict) -> bool:
    t = f"{name} {info.get('type', '')} {info.get('design', '')}".upper()
    return any(w in t for w in ("SLS", "SERV", "QP", "CHAR", "FREQ"))


def summarise(g: Gather) -> dict:
    """The compact picture of the model and its results that the film is made from."""
    xyz = np.array(g.xyz, dtype=np.float64) if g.xyz else np.zeros((0, 3))
    nj = len(xyz)
    known = np.isfinite(xyz).all(axis=1) if nj else np.zeros(0, bool)
    if not known.any():
        raise NotSap(
            "No joint coordinates were found. "
            "Export the Joint Coordinates and Connectivity tables with the results."
        )
    frames = [f for f in g.frames]
    good_f = [i for i, (a, b) in enumerate(frames) if a >= 0 and b >= 0 and known[a] and known[b]]
    good_a = [i for i, js in enumerate(g.areas) if len(js) >= 3 and all(known[j] for j in js)]
    case_names = sorted(g.case_ix, key=g.case_ix.get)
    lo, hi = xyz[known].min(axis=0), xyz[known].max(axis=0)

    sect_names = sorted({g.frame_sect.get(i, "") for i in good_f} | {g.area_sect.get(i, "") for i in good_a})
    sect_ix = {s: k for k, s in enumerate(sect_names)}

    def sect_info(s: str) -> dict:
        if s in g.sections:
            p = g.sections[s]
            return {
                "name": s,
                "shape": p["shape"],
                "d": _r(p["d"]),
                "b": _r(p["b"]),
                "material": p["material"],
            }
        if s in g.area_sections:
            p = g.area_sections[s]
            return {"name": s, "shape": "Shell", "t": _r(p["t"]), "material": p["material"]}
        return {"name": s}

    supports = sorted(
        set(g.restraints)
        | g.springs
        | {
            i
            for a in g.react.data.values()
            for i in np.nonzero(np.isfinite(a[:nj, 2]) | np.isfinite(a[:nj, 8]))[0].tolist()
        }
    )
    supports = [i for i in supports if i < nj and known[i]]

    cases_out = []
    for name in case_names:
        c = g.case_ix[name]
        info = g.combos.get(name) or g.cases.get(name) or {}
        cases_out.append(
            {
                "name": name,
                "kind": _kind_of(g, name),
                "type": info.get("type") or g.case_types.get(c, ""),
                "service": _is_service(name, info),
            }
        )

    # what the reactions do: per support, the extremes over the output cases
    # (combinations where there are some)
    use = [g.case_ix[n] for n in case_names if n in g.combos] or [g.case_ix[n] for n in case_names]
    react = {"supports": supports, "cases": []}
    if g.react.data and supports:
        sup = np.array(supports)
        best = {"down": (-math.inf, None, None), "up": (math.inf, None, None), "h": (-math.inf, None, None)}
        per_case = []
        for name in case_names:
            c = g.case_ix[name]
            a = g.react.data.get(c)
            if a is None:
                continue
            a = (
                a[:nj][sup]
                if a.shape[0] >= nj
                else np.vstack([a, np.full((nj - a.shape[0], 12), np.nan, np.float32)])[sup]
            )
            fmax, fmin = a[:, :6], a[:, 6:]
            fz = np.where(np.isfinite(fmax[:, 2]), fmax[:, 2], fmin[:, 2])
            fzmin = np.where(np.isfinite(fmin[:, 2]), fmin[:, 2], fmax[:, 2])
            h = np.hypot(
                np.nan_to_num(
                    np.where(
                        np.abs(np.nan_to_num(fmax[:, 0])) >= np.abs(np.nan_to_num(fmin[:, 0])),
                        fmax[:, 0],
                        fmin[:, 0],
                    )
                ),
                np.nan_to_num(
                    np.where(
                        np.abs(np.nan_to_num(fmax[:, 1])) >= np.abs(np.nan_to_num(fmin[:, 1])),
                        fmax[:, 1],
                        fmin[:, 1],
                    )
                ),
            )
            per_case.append(
                {
                    "case": name,
                    "fz": [_r(v, 1) for v in fz],
                    "fz_min": [_r(v, 1) for v in fzmin],
                    "h": [_r(v, 1) for v in h],
                    "sum_fz": _r(float(np.nansum(fz)), 1),
                }
            )
            if c in use:
                k = int(np.nanargmax(np.nan_to_num(fz, nan=-np.inf))) if np.isfinite(fz).any() else None
                if k is not None and fz[k] > best["down"][0]:
                    best["down"] = (float(fz[k]), supports[k], name)
                k = int(np.nanargmin(np.nan_to_num(fzmin, nan=np.inf))) if np.isfinite(fzmin).any() else None
                if k is not None and fzmin[k] < best["up"][0]:
                    best["up"] = (float(fzmin[k]), supports[k], name)
                k = int(np.argmax(h)) if len(h) else None
                if k is not None and h[k] > best["h"][0]:
                    best["h"] = (float(h[k]), supports[k], name)
        react["cases"] = per_case
        react["max"] = {
            k: {"value": _r(v[0], 1), "joint": v[1], "case": v[2]}
            for k, v in best.items()
            if v[2] is not None
        }

    # movements: the cases that move the structure most, each kept whole
    disp = {"cases": [], "max": {}}
    if g.disp.data:
        scored = []
        for name in case_names:
            a = g.disp.data.get(g.case_ix[name])
            if a is None:
                continue
            a = a[:nj]
            hz = np.hypot(np.nan_to_num(a[:, 0]), np.nan_to_num(a[:, 1]))
            vz = np.abs(np.nan_to_num(a[:, 2]))
            scored.append(
                (
                    name,
                    float(hz.max(initial=0)),
                    int(hz.argmax()) if len(hz) else 0,
                    float(vz.max(initial=0)),
                    int(vz.argmax()) if len(vz) else 0,
                    a,
                )
            )
        if scored:
            pick: list[str] = []
            for key in (1, 3):
                for service in (True, False):
                    pool = [
                        s
                        for s in scored
                        if _is_service(s[0], g.combos.get(s[0]) or g.cases.get(s[0]) or {}) == service
                    ]
                    if pool:
                        top = max(pool, key=lambda s: s[key])
                        if top[0] not in pick:
                            pick.append(top[0])
            for s in sorted(scored, key=lambda s: -max(s[1], s[3])):
                if len(pick) >= MAX_FIELDS:
                    break
                if s[0] not in pick:
                    pick.append(s[0])
            by = {s[0]: s for s in scored}
            for name in pick[:MAX_FIELDS]:
                s = by[name]
                u = np.nan_to_num(s[5]).astype(np.float64)
                disp["cases"].append(
                    {
                        "case": name,
                        "u": [[_r(x, 5), _r(y, 5), _r(z, 5)] for x, y, z in u.tolist()],
                        "h": {"value": _r(s[1], 5), "joint": s[2]},
                        "v": {"value": _r(s[3], 5), "joint": s[4]},
                    }
                )
            hz = max(scored, key=lambda s: s[1])
            vz = max(scored, key=lambda s: s[3])
            disp["max"] = {
                "h": {"value": _r(hz[1], 5), "joint": hz[2], "case": hz[0]},
                "v": {"value": _r(vz[3], 5), "joint": vz[4], "case": vz[0]},
            }

    # member forces: the envelope over the cases, per frame, and the case that gives it
    forces: dict[str, Any] = {}
    nf = len(g.frames)
    if g.fforce.data and nf:
        env = np.zeros((nf, 4))
        which = np.full((nf, 4), -1, dtype=np.int64)
        for name in case_names:
            c = g.case_ix[name]
            a = g.fforce.data.get(c)
            if a is None:
                continue
            a = np.nan_to_num(a[:nf].astype(np.float64))
            if a.shape[0] < nf:
                a = np.vstack([a, np.zeros((nf - a.shape[0], 6))])
            vals = np.stack(
                [np.abs(a[:, 0]), np.hypot(a[:, 1], a[:, 2]), np.hypot(a[:, 4], a[:, 5]), np.abs(a[:, 3])],
                axis=1,
            )
            more = vals > env
            env = np.where(more, vals, env)
            which = np.where(more, c, which)
        comp = np.zeros(nf)
        for name in case_names:
            a = g.fforce.data.get(g.case_ix[name])
            if a is not None:
                p = np.nan_to_num(a[:nf, 0].astype(np.float64))
                comp[: len(p)] = np.minimum(comp[: len(p)], p)
        idx = {c: n for n, c in g.case_ix.items()}
        forces = {
            "N": [_r(v, 1) for v in env[:, 0]],
            "V": [_r(v, 1) for v in env[:, 1]],
            "M": [_r(v, 1) for v in env[:, 2]],
            "T": [_r(v, 1) for v in env[:, 3]],
            "compression": [_r(-v, 1) for v in comp],
            "case": {k: [idx.get(int(c), "") for c in which[:, j]] for j, k in enumerate("NVMT")},
        }
    shells: dict[str, Any] = {}
    na = len(g.areas)
    if g.sforce.data and na:
        env = np.zeros((na, 3))
        for a in g.sforce.data.values():
            a = np.nan_to_num(a[:na].astype(np.float64))
            if a.shape[0] < na:
                a = np.vstack([a, np.zeros((na - a.shape[0], 5))])
            env = np.maximum(
                env, np.stack([np.abs(a[:, 0]), np.abs(a[:, 1]), np.hypot(a[:, 2], a[:, 3])], axis=1)
            )
        shells = {
            "M11": [_r(v, 1) for v in env[:, 0]],
            "M22": [_r(v, 1) for v in env[:, 1]],
            "V": [_r(v, 1) for v in env[:, 2]],
        }

    design = {str(i): {**d, "ratio": _r(d["ratio"], 3)} for i, d in g.design.items() if i in set(good_f)}

    loads = {}
    for pat, kinds in g.loads.items():
        out = {}
        for kind, items in kinds.items():
            if kind == "joint":
                out[kind] = [[j, _r(a, 2), _r(b, 2), _r(c, 2)] for j, a, b, c in items if j < nj and known[j]]
            elif kind in ("frame", "point"):
                out[kind] = [
                    [i, *([_r(v, 3) if isinstance(v, float) else v for v in rest])]
                    for i, *rest in items
                    if i in set(good_f)
                ]
            else:
                out[kind] = [[i, d, _r(v, 3)] for i, d, v in items if i in set(good_a)]
        loads[pat] = {
            "items": out,
            "counts": {k.split("|")[1]: v for k, v in g.load_counts.items() if k.split("|")[0] == pat},
        }

    return {
        "program": g.program,
        "units": "kN, m",
        "bbox": [[_r(v, 3) for v in lo], [_r(v, 3) for v in hi]],
        "joints": [
            [_r(x, 3), _r(y, 3), _r(z, 3)] if k else None
            for k, (x, y, z) in zip(known.tolist(), xyz.tolist(), strict=True)
        ],
        "frames": {
            "ids": good_f,
            "ends": [frames[i] for i in good_f],
            "section": [sect_ix[g.frame_sect.get(i, "")] for i in good_f],
            "names": [n for n, i in sorted(g.frame_ix.items(), key=lambda kv: kv[1]) if i in set(good_f)],
        },
        "areas": {
            "ids": good_a,
            "joints": [g.areas[i] for i in good_a],
            "section": [sect_ix[g.area_sect.get(i, "")] for i in good_a],
        },
        "sections": [sect_info(s) for s in sect_names],
        "supports": supports,
        "restraints": {str(i): v for i, v in g.restraints.items() if i < nj},
        "patterns": [{"name": k, **v} for k, v in g.patterns.items()],
        "combos": [{"name": k, "type": v["type"], "parts": v["parts"]} for k, v in g.combos.items()],
        "cases": cases_out,
        "loads": loads,
        "reactions": react,
        "displacements": disp,
        "forces": forces,
        "frame_count": nf,
        "shells": shells,
        "area_count": na,
        "design": design,
        # Mode shapes and frame stresses are read by the page's reader (static/sapread.js) only.
        "modes": [],
        "stress": {},
        "base": {k: [_r(v, 1) for v in vals] for k, vals in g.base.items()},
        "tables": g.tables,
        "ignored": {k: v for k, v in g.ignored.items()},
        "rows": g.rows,
    }


def counts(summary: dict) -> dict:
    """The headline numbers of a summary, for the list of films."""
    return {
        "joints": sum(1 for j in summary["joints"] if j),
        "frames": len(summary["frames"]["ids"]),
        "areas": len(summary["areas"]["ids"]),
        "supports": len(summary["supports"]),
        "patterns": len(summary["patterns"]) or len(summary["loads"]),
        "cases": len(summary["cases"]),
        "combos": len(summary["combos"]),
        "designed": len(summary["design"]),
        "max_ratio": max(
            (d["ratio"] for d in summary["design"].values() if d["ratio"] is not None), default=None
        ),
    }


def gz_json(data: Any) -> bytes:
    import json

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as f:
        f.write(json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    return buf.getvalue()


def table_names(g: Gather) -> Iterable[str]:
    return sorted(g.tables)
