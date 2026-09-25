"""A deck's option matrix: every combination of the thicknesses, crack width limits, pile-face methods
and deck types (solid, or with PVC voids) the user lists, each designed with the whole section and
costed, then compared in a summary and a report.

Each combination is a whole-section variant of ``trials`` (the "matrix" set), so an element the
combination does not change (the piles) is designed once and shared, and a combination already
designed from the same inputs is not designed again. The crack width limit is the deck's own (top and
bottom faces); the other elements keep theirs.
"""

from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any

from . import trials
from .project import Project, Section, SlabInput

MAX_OPTIONS = 36
LAYER_NAMES = {
    "top_x": "Top, bars along X",
    "bottom_x": "Bottom, bars along X",
    "top_y": "Top, bars along Y",
    "bottom_y": "Bottom, bars along Y",
}


def _numbers(values: Any, what: str, lo: float, hi: float) -> list[float]:
    out: list[float] = []
    for v in values or []:
        if v in (None, ""):
            continue
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError(f"{what} {v:g} is outside {lo:g} to {hi:g}.")
        if v not in out:
            out.append(v)
    return out


def clean_spec(spec: dict[str, Any], section: Section, spacings: list[float] | None = None) -> dict[str, Any]:
    """The lists the user gave, checked: an empty list, or one switched off in ``use``, keeps the deck's
    own value. ``spacings``: the mesh spacings the slabs are designed with (Design settings)."""
    name = spec.get("element")
    element = section.elements.get(name) if name else None
    if not isinstance(element, SlabInput):
        raise ValueError("Pick a slab (deck) for the option matrix.")
    out: dict[str, Any] = {"element": name}
    out["thickness"] = _numbers(spec.get("thickness"), "A thickness (mm)", 150, 3000)
    out["crack_width_limit"] = _numbers(spec.get("crack_width_limit"), "A crack width limit (mm)", 0.05, 0.5)
    peaks = [p for p in spec.get("peaks") or [] if p]
    bad = [p for p in peaks if p not in trials.PEAKS]
    if bad:
        raise ValueError(f"No pile-face method '{bad[0]}'.")
    out["peaks"] = list(dict.fromkeys(peaks))
    decks = []
    for d in spec.get("decks") or []:
        kind = (d or {}).get("type")
        if kind not in trials.DECKS:
            raise ValueError(f"No deck type '{kind}'.")
        item: dict[str, Any] = {"type": kind}
        if kind == "voided":
            voids = element.voids
            for k, default in (
                ("diameter", voids.diameter if voids else 500.0),
                ("spacing", voids.spacing if voids else 700.0),
            ):
                v = d.get(k)
                item[k] = float(v) if v not in (None, "") else default
        if item not in decks:
            decks.append(item)
    out["decks"] = decks
    out["mesh"] = _numbers(spec.get("mesh"), "A mesh spacing (mm)", 75, 400)
    if spacings and any(m not in [float(x) for x in spacings] for m in out["mesh"]):
        raise ValueError(
            f"The slab is designed with meshes at {', '.join(f'{x:g}' for x in spacings)} mm only."
        )
    punching = [p for p in spec.get("punching_per") or [] if p]
    bad = [p for p in punching if p not in trials.PUNCHING]
    if bad:
        raise ValueError(f"No punching design '{bad[0]}'.")
    out["punching_per"] = list(dict.fromkeys(punching))
    use = spec.get("use") or {}
    out["use"] = {k: bool(use.get(k, True)) for k in AXES}
    n = len(options(out, section))
    if n > MAX_OPTIONS:
        raise ValueError(f"That is {n} combinations; Triton designs at most {MAX_OPTIONS} at a time.")
    return out


# Each list the matrix combines, with the key an option carries its value under. A list switched off
# ("use") is left out: the deck keeps its own value for it.
AXES = {
    "thickness": "thickness",
    "crack_width_limit": "crack_width_limit",
    "peaks": "peaks",
    "decks": "deck",
    "mesh": "mesh",
    "punching_per": "punching_per",
}


def options(spec: dict[str, Any], section: Section) -> list[dict[str, Any]]:
    """Every combination, as the values it sets (None: the deck's own)."""
    element = section.elements[spec["element"]]
    use = spec.get("use") or {}
    axes = [(spec.get(k) if use.get(k, True) else None) or [None] for k in AXES]
    out = []
    for values in product(*axes):
        o = dict(zip(AXES.values(), values, strict=True))
        if o["thickness"] is None:
            o["thickness"] = element.thickness
        out.append(o)
    return out


def option_label(o: dict[str, Any], element: SlabInput) -> str:
    bits = [f"{o['thickness']:g} mm"]
    deck = o.get("deck")
    if deck:
        bits.append(
            "solid" if deck["type"] == "solid" else f"voids Ø{deck['diameter']:g} @ {deck['spacing']:g}"
        )
    elif element.voids is not None:
        bits.append(f"voids Ø{element.voids.diameter:g} @ {element.voids.spacing:g}")
    if o.get("crack_width_limit"):
        bits.append(f"wk {o['crack_width_limit']:g}")
    else:
        bits.append(f"wk {element.crack_width_limit:g}")
    bits.append(trials.PEAKS.get(o.get("peaks") or element.peaks, str(element.peaks)))
    if o.get("mesh"):
        bits.append(f"mesh @ {o['mesh']:g}")
    if o.get("punching_per"):
        bits.append(trials.PUNCHING[o["punching_per"]])
    return ", ".join(bits)


def variant_of(o: dict[str, Any], spec: dict[str, Any], section: Section) -> dict[str, Any]:
    name = spec["element"]
    element = section.elements[name]
    c: dict[str, Any] = {"thickness": o["thickness"]}
    if o.get("crack_width_limit"):
        c["crack_width_limit"] = o["crack_width_limit"]
    if o.get("peaks"):
        c["peaks"] = o["peaks"]
    for k in ("mesh", "punching_per"):
        if o.get(k):
            c[k] = o[k]
    deck = o.get("deck")
    if deck:
        c["deck"] = deck["type"]
        if deck["type"] == "voided":
            c["void_diameter"] = deck["diameter"]
            c["void_spacing"] = deck["spacing"]
    return {
        "elements": {name: c},
        "label": option_label(o, element),
        "option": {k: v for k, v in o.items() if v is not None},
    }


def variants(spec: dict[str, Any], section: Section) -> list[dict[str, Any]]:
    """The combinations as whole-section variants, checked like any other."""
    return [trials.clean_variant(variant_of(o, spec, section), section) for o in options(spec, section)]


def load_spec(d: Path) -> dict[str, Any] | None:
    return trials.load_scenarios(d, "matrix").get("spec")


def save_spec(d: Path, spec: dict[str, Any]) -> None:
    data = trials.load_scenarios(d, "matrix")
    data["spec"] = spec
    trials._save_scenarios(d, data, "matrix")


def default_spec(section: Section) -> dict[str, Any] | None:
    """A first matrix for the first slab: its thickness and 50 mm either side, 0.2 and 0.3 mm, peaks
    and face mean, and the deck as it is (solid, or solid and with its voids)."""
    name = next((n for n, e in section.elements.items() if isinstance(e, SlabInput)), None)
    if name is None:
        return None
    e = section.elements[name]
    decks: list[dict[str, Any]] = [{"type": "solid"}]
    if e.voids is not None:
        decks.append({"type": "voided", "diameter": e.voids.diameter, "spacing": e.voids.spacing})
    return {
        "element": name,
        "thickness": [e.thickness - 50, e.thickness, e.thickness + 50],
        "crack_width_limit": [0.2, 0.3],
        "peaks": ["peak", "face_mean"],
        "decks": decks,
        "mesh": [150.0, 200.0],
        "punching_per": ["type", "head"],
        "use": {
            "thickness": True,
            "crack_width_limit": True,
            "peaks": True,
            "decks": True,
            "mesh": False,
            "punching_per": False,
        },
    }


# --- What each option's deck looks like ------------------------------------------------


def _where(row: dict[str, Any]) -> str:
    if row.get("station"):
        a, b = row["station"]
        strip = row.get("strip")
        return f"{'whole width' if strip in (None, 'all') else f'{strip} strip'}, {a:g} to {b:g} m"
    z = row.get("zone")
    if z:
        return f"zone X {z[0][0]:g} to {z[0][1]:g}, Y {z[1][0]:g} to {z[1][1]:g}"
    return ""


def _util(row: dict[str, Any]) -> float:
    wk, lim = row.get("wk_mm"), row.get("wk_limit_mm")
    crack = wk / lim if wk is not None and lim else 0.0
    return max(row.get("ratio") or 0.0, crack)


def governing(design: dict[str, Any]) -> list[dict[str, Any]]:
    """Per face and direction, the station (or zone) that governs: its bars, moment and checks."""
    rows = (design.get("strip_design") or {}).get("rows") or []
    out = []
    for layer, name in LAYER_NAMES.items():
        mine = [r for r in rows if r.get("layer") == layer]
        if not mine:
            continue
        worst = max(mine, key=_util)
        heavy = max(mine, key=lambda r: r.get("as_mm2_per_m") or 0)
        out.append(
            {
                "layer": layer,
                "name": name,
                "mesh": ((design.get("layers") or {}).get(layer) or {}).get("basic", {}).get("label"),
                "where": _where(worst),
                "moment": worst.get("moment"),
                "bars": worst.get("bars"),
                "M_kNm_per_m": worst.get("M_kNm_per_m"),
                "N_kN_per_m": worst.get("N_kN_per_m"),
                "combination": worst.get("combination"),
                "ratio": worst.get("ratio"),
                "wk_mm": worst.get("wk_mm"),
                "wk_limit_mm": worst.get("wk_limit_mm"),
                "set_by": worst.get("set_by"),
                "utilisation": round(_util(worst), 3),
                "heaviest_bars": heavy.get("bars"),
                "heaviest_where": _where(heavy),
                "heaviest_as_mm2_per_m": heavy.get("as_mm2_per_m"),
            }
        )
    return out


def punching(design: dict[str, Any]) -> list[dict[str, Any]]:
    """Each pile type's punching: whether links are needed and how many perimeters."""
    types = [t for t in design.get("punching_types") or [] if t.get("unified")]
    if not types:
        types = design.get("punching") or []
    out = []
    for t in types:
        out.append(
            {
                "pile": t.get("pile"),
                "heads": t.get("heads", 1),
                "utilisation": t.get("utilisation"),
                "needs_links": bool(t.get("needs_reinforcement")),
                "perimeters": t.get("perimeters"),
                "links": (
                    f"{t['perimeters']} perimeters @ {t.get('radial_spacing_mm', 0):g} mm, "
                    f"{t.get('asw_mm2_per_perimeter', 0):g} mm² each"
                    if t.get("perimeters")
                    else None
                ),
                "passed": bool(t.get("passed")),
                "fix": t.get("fix"),
                "combination": t.get("combination"),
            }
        )
    return out


def deck_summary(design: dict[str, Any]) -> dict[str, Any]:
    """What the summary and the report show of one option's deck."""
    s = trials._summary("slabs", design)
    sh = design.get("shear") or {}
    punch = punching(design)
    gov = sh.get("governing") or {}
    return {
        **s,
        "thickness_mm": design.get("thickness_mm"),
        "voided": bool(design.get("voids")),
        "meshes": {
            k: ((design.get("layers") or {}).get(k) or {}).get("basic", {}).get("label") for k in LAYER_NAMES
        },
        "governing": governing(design),
        "shear": {
            "utilisation": sh.get("utilisation"),
            "passed": sh.get("passed"),
            "cells_needing_links": sh.get("cells_needing_links"),
            "heaviest": (sh.get("heaviest") or {}).get("label"),
            "governing": (
                f"{gov.get('combination')}, at X {gov.get('x'):g}, Y {gov.get('y'):g}: "
                f"V {gov.get('V_kN_per_m'):g} kN/m"
                if gov.get("x") is not None and gov.get("V_kN_per_m") is not None
                else None
            ),
        },
        "punching": punch,
        "punching_needed": any(p["needs_links"] for p in punch),
        "ductility": [
            f"{q.get('where')}: {'; '.join(q.get('warnings') or [])}" for q in design.get("ductility") or []
        ],
    }


def view(
    project: Project,
    section: Section,
    summary: dict | None,
    results: dict[str, Any] | None,
    d: Path,
) -> dict[str, Any]:
    """The matrix as set, and every option run so far with its deck and whole-section cost."""
    spec = load_spec(d) or default_spec(section)
    slabs = [n for n, e in section.elements.items() if isinstance(e, SlabInput)]
    if spec is None or spec.get("element") not in section.elements:
        return {"spec": spec, "slabs": slabs, "options": [], "peaks": trials.PEAKS, "decks": trials.DECKS}
    element = section.elements[spec["element"]]
    whole = trials.scenarios_view(project, section, summary, results, d, "matrix")
    name = spec["element"]
    rows = []
    for col in whole["variants"]:
        variant = col["variant"]
        vs = trials.variant_section(section, variant)
        vp = trials.variant_project(project, variant)
        e = col["elements"].get(name) or {}
        row = {k: v for k, v in col.items() if k not in ("elements", "variant")}
        row["option"] = variant.get("option") or {}
        row["deck_state"] = e.get("state")
        row["deck_cost_per_m"] = e.get("cost_per_m")
        if e.get("state") == "done":
            key = trials.scenario_key(vp, vs, name, summary)
            design = trials.load_design(d, key)
            if design is not None:
                row["deck"] = deck_summary(design)
                row["design_key"] = key
        row["others_unsafe"] = [n for n in col.get("unsafe") or [] if n != name]
        rows.append(row)
    # The cheapest option whose deck is safe, and each option against it.
    priced = [r for r in rows[1:] if r.get("cost_per_m") is not None and (r.get("deck") or {}).get("passed")]
    best = min(priced, key=lambda r: r["cost_per_m"], default=None)
    for r in rows:
        r["best"] = r is best
        if best is not None and r.get("cost_per_m") is not None:
            r["over_best_per_m"] = round(r["cost_per_m"] - best["cost_per_m"], 0)
    return {
        "spec": spec,
        "slabs": slabs,
        "element": name,
        "current": {
            "thickness": element.thickness,
            "crack_width_limit": element.crack_width_limit,
            "crack_width_limit_bottom": getattr(element, "crack_width_limit_bottom", None),
            "peaks": element.peaks,
            "voids": element.voids.model_dump() if element.voids is not None else None,
            "punching_per": element.punching_per,
            "mesh": (section.slab_strips.get(name).spacing if section.slab_strips.get(name) else None),
        },
        "spacings": project.design.reinforcement.slab_spacings,
        "punching": trials.PUNCHING,
        "count": len(options(spec, section)),
        "max": MAX_OPTIONS,
        "peaks": trials.PEAKS,
        "decks": trials.DECKS,
        "currency": whole["currency"],
        "berth_length_m": whole["berth_length_m"],
        "options": rows,
    }


def design_of(d: Path, key: str) -> dict[str, Any] | None:
    """One option's stored deck design (for its bar figures)."""
    if not key or not all(c.isalnum() or c in "-_" for c in key):
        return None
    return trials.load_design(d, key)
