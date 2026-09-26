"""Standard design: the Detailed design without its drawings, AdSec files and clash checks.

A Standard design is the same run as a Detailed one (Ahmed, 2026-09-26): the same bars, cages and
checks, shown in Triton, with an overview per element (workable or not, utilisation, what governs,
steel ratio). Only the drawings (DXF / Revit), the bars for Revit, the AdSec files and governing
sets, and the clash checks leave its elements out, until they are designed in Detailed mode.

The designs' ``standard`` arguments (quicker checks: coarser pile zones, one slab mesh spacing,
QP crack candidates) are not used by a Standard design any more.
"""

from __future__ import annotations

from typing import Any

MODES = ("detailed", "standard")
KEY = "design_mode"  # on each element's results; missing = detailed (every design before Standard)

NOTE = (
    "Standard design: the full design, without drawings, AdSec files or clash checks. Design it in "
    "Detailed mode for those."
)


def is_standard(entry: dict[str, Any]) -> bool:
    return entry.get(KEY) == "standard"


def _u(v: Any) -> float | None:
    try:
        return None if v is None else round(float(v), 3)
    except (TypeError, ValueError):
        return None


def _check(name: str, u: Any, passed: Any = None) -> dict[str, Any] | None:
    u = _u(u)
    if u is None and passed is None:
        return None
    ok = bool(passed) if passed is not None else u <= 1 + 1e-6
    return {"check": name, "utilisation": u, "passed": ok}


def _crack(name: str, c: dict | None, wk: str, limit: str) -> dict[str, Any] | None:
    if not isinstance(c, dict) or c.get(wk) is None or not c.get(limit):
        return None
    return _check(f"{name} ({c[wk]:g} / {c[limit]:g} mm)", c[wk] / c[limit], c.get("passed"))


def _pile(p: dict[str, Any], name: str = "") -> list:
    casing = (p.get("casing") or {}).get("tube") or {}
    return [
        _check(f"{name}Bending with axial force (N–M)", p.get("utilisation")),
        _check(
            f"{name}Shear", (p.get("shear") or {}).get("utilisation"), (p.get("shear") or {}).get("passed")
        ),
        _crack(f"{name}QP crack width", p.get("cracks"), "wk_mm", "limit_mm"),
        _check("Steel casing", casing.get("utilisation"), casing.get("passed")) if casing else None,
    ]


def _beam(b: dict[str, Any]) -> list:
    out = [
        _check("Bending with axial force", (b.get("bending") or {}).get("utilisation")),
        _check(
            "Shear and torsion",
            (b.get("shear") or {}).get("utilisation"),
            (b.get("shear") or {}).get("passed"),
        ),
    ]
    for face, c in (b.get("cracks") or {}).items():
        out.append(_crack(f"QP crack width, {face}", c, "wk", "limit"))
    for face, c in ((b.get("restraint") or {}).get("faces") or {}).items():
        out.append(_crack(f"Restraint crack width, {face}", c, "wk", "limit"))
    t = b.get("transverse") or {}
    out.append(_check("Transverse bars", t.get("utilisation"), t.get("passed")))
    return out


def _slab(s: dict[str, Any]) -> list:
    layers = s.get("layers") or {}
    worst = max((_u(v.get("utilisation")) or 0.0 for v in layers.values()), default=None)
    out = [
        _check("Bending with axial force and crack widths (worst bar layer)", worst) if layers else None,
        _check("Shear", (s.get("shear") or {}).get("utilisation"), (s.get("shear") or {}).get("passed")),
    ]
    for t in s.get("punching_types") or []:
        links = " with links" if t.get("needs_reinforcement") and t.get("passed") else ""
        out.append(_check(f"Punching at {t.get('pile')}{links}", t.get("utilisation"), t.get("passed")))
    for name, r in ((s.get("restraint") or {}).get("layers") or {}).items():
        if isinstance(r, dict) and r.get("wk") is not None and r.get("limit"):
            out.append(_crack(f"Restraint crack width, {name.replace('_', ' ')}", r, "wk", "limit"))
    return out


def checks(kind: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The element's checks, each with its utilisation and whether it passes."""
    if kind == "piles":
        found = _pile(entry)
    elif kind == "combi_walls":
        tube = entry.get("tube") or {}
        found = [_check("Steel tube (EN 1993)", tube.get("utilisation"), tube.get("passed"))]
        found += _pile(entry.get("infill") or {}, "Concrete infill: ")
    elif kind == "beams":
        found = _beam(entry)
    elif kind == "slabs":
        found = _slab(entry)
    else:
        found = []
    found = [c for c in found if c]
    if not found and entry.get("utilisation") is not None:
        found = [_check("Utilisation", entry.get("utilisation"), entry.get("passed"))]
    return [c for c in found if c]


def steel(entry: dict[str, Any]) -> tuple[float | None, float | None]:
    """Steel of the element in kg/m³ of concrete and % of the concrete."""
    st = entry.get("steel") or (entry.get("infill") or {}).get("steel") or {}
    kg = st.get("kg_per_m3", entry.get("steel_ratio_kg_m3"))
    pct = st.get("ratio_pct")
    if pct is None and kg is not None:
        pct = 100 * float(kg) / 7850
    return _u(kg), None if pct is None else round(float(pct), 2)


def summary(kind: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Workable or not, the utilisation, the steel ratio and what does not work."""
    found = checks(kind, entry)
    kg, pct = steel(entry)
    failing = [c["check"] for c in found if not c["passed"]]
    why = list(entry.get("failure") or []) or [f"{c} fails." for c in failing]
    governing = max(found, key=lambda c: c["utilisation"] or 0.0) if found else None
    return {
        "workable": bool(entry.get("passed", not failing)) and not failing,
        "utilisation": _u(entry.get("utilisation")),
        "governs": governing["check"] if governing else None,
        "kg_per_m3": kg,
        "ratio_pct": pct,
        "checks": found,
        "why": why,
    }


def mark(kind: str, entry: dict[str, Any]) -> dict[str, Any]:
    """An element's results as a Standard design: marked, with the overview and the note first."""
    entry[KEY] = "standard"
    entry["standard"] = summary(kind, entry)
    notes = entry.get("notes")
    if isinstance(notes, list):
        notes.insert(0, NOTE)
    return entry


def unmark(entry: dict[str, Any]) -> dict[str, Any]:
    """An element's Standard results taken as Detailed (the same design): the mark and note dropped."""
    entry.pop(KEY, None)
    entry.pop("standard", None)
    notes = entry.get("notes")
    if isinstance(notes, list):
        entry["notes"] = [n for n in notes if n != NOTE]
    return entry


def detailed_only(results: dict[str, Any], kinds: tuple[str, ...]) -> tuple[dict[str, Any], list[str]]:
    """The results without the elements designed in Standard mode, and those elements' names."""
    out = dict(results)
    left: list[str] = []
    for k in kinds:
        items = results.get(k) or []
        out[k] = [e for e in items if not is_standard(e)]
        left += [e["element"] for e in items if is_standard(e) and e["element"] not in left]
    return out, left
