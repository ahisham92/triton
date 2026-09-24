"""Trials: one element designed at several sizes, side by side with its reinforcement and cost.

A trial is the element designed by Triton at one size (slab thickness, beam width and depth, pile
diameter) with everything else as the section has it, except the bars the user set for the element,
which belong to its current size: each trial picks its own. Trials never change the section; "Use
this size" does, and puts that trial's design in the section's results.

Trials are kept in ``<section>/trials.json`` (sizes asked for and a summary of each run) with each
run's full design in ``<section>/trials/<key>.json.gz``. A run is keyed by everything it was designed
from, so a change to the workbook, settings or element makes it run again.
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Any

from . import fresh
from .alignment import combine_parts
from .costing import cost_section, model_length, slab_links
from .design.runner import run_section
from .materials import STEEL_DENSITY
from .project import ApproachSlabInput, BeamInput, PileInput, Project, Section, SlabInput

KINDS = {PileInput: "piles", BeamInput: "beams", SlabInput: "slabs"}


def kind_of(element: Any) -> str | None:
    return next((k for t, k in KINDS.items() if isinstance(element, t)), None)


def size_of(element: Any, designed: dict | None = None) -> dict[str, float]:
    """The element's current size, as a trial size."""
    if isinstance(element, SlabInput):
        voids = getattr(element, "voids", None)  # circular voids (PVC pipes), when the slab has them
        if voids is not None:
            return {
                "thickness": element.thickness,
                "void_diameter": voids.diameter,
                "void_spacing": voids.spacing,
            }
        return {"thickness": element.thickness}
    if isinstance(element, PileInput):
        return {"diameter": element.diameter}
    width = element.width or (designed or {}).get("width_mm")
    return {"width": width, "depth": element.depth} if width else {"depth": element.depth}


def size_key(size: dict[str, float]) -> str:
    return "x".join(f"{k}{float(v):g}" for k, v in sorted(size.items()) if v)


def size_label(size: dict[str, float]) -> str:
    if "thickness" in size:
        if size.get("void_diameter"):
            spacing = size.get("void_spacing") or 0
            return f"{size['thickness']:g} mm, voids Ø{size['void_diameter']:g} @ {spacing:g}"
        return f"{size['thickness']:g} mm"
    if "diameter" in size:
        return f"Ø{size['diameter']:g}"
    return f"{size['width']:g} × {size['depth']:g}" if size.get("width") else f"depth {size['depth']:g}"


def default_sizes(element: Any, designed: dict | None = None) -> list[dict[str, float]]:
    """The current size and a few steps either side."""
    now = size_of(element, designed)
    if "thickness" in now:
        t = now["thickness"]
        return [{**now, "thickness": t + d} for d in (-100, -50, 0, 50, 100) if t + d > 0]
    if "diameter" in now:
        D = now["diameter"]
        return [{"diameter": D + d} for d in (-200, 0, 200) if D + d > 0]
    return [{**now, "depth": now["depth"] + d} for d in (-200, -100, 0, 100, 200) if now["depth"] + d > 0]


SLAB_KEYS = ("thickness", "void_diameter", "void_spacing")


def clean_size(element: Any, size: dict[str, Any]) -> dict[str, float]:
    kind = kind_of(element)
    keys = {"slabs": SLAB_KEYS, "piles": ("diameter",), "beams": ("width", "depth")}[kind]
    voided = getattr(element, "voids", None) is not None
    out = {}
    for k in keys:
        v = size.get(k)
        if v in (None, "") or (k.startswith("void_") and not voided):
            continue
        v = float(v)
        if not 50 <= v <= 10000:
            raise ValueError(f"{k.replace('_', ' ')} {v:g} mm is not a size Triton can design.")
        out[k] = v
    if not out or {"slabs": "thickness", "piles": "diameter", "beams": "depth"}[kind] not in out:
        raise ValueError("Give each trial its size.")
    if voided:
        voids = element.voids
        dia, spacing = out.get("void_diameter", voids.diameter), out.get("void_spacing", voids.spacing)
        if dia >= out["thickness"] - 100:
            raise ValueError(f"Voids Ø{dia:g} leave too little concrete in a {out['thickness']:g} mm slab.")
        if spacing <= dia:
            raise ValueError(f"Voids Ø{dia:g} at {spacing:g} mm would touch: the spacing must be larger.")
    return out


def with_size(element: Any, size: dict[str, float]) -> Any:
    """The element at a trial size: its own dimensions and, for a voided slab, the voids'."""
    own = {k: v for k, v in size.items() if not k.startswith("void_")}
    voids = {k[len("void_") :]: v for k, v in size.items() if k.startswith("void_")}
    update: dict[str, Any] = dict(own)
    if voids and getattr(element, "voids", None) is not None:
        update["voids"] = element.voids.model_copy(update=voids)
    return element.model_copy(update=update)


def trial_section(section: Section, name: str, size: dict[str, float]) -> Section:
    """The section with the element at the trial size and without the bars set for its current size."""
    element = with_size(section.elements[name], size)
    update: dict[str, Any] = {"elements": {**section.elements, name: element}}
    for field in ("user_cages", "beam_cages"):
        cages = getattr(section, field)
        if name in cages:
            update[field] = {k: v for k, v in cages.items() if k != name}
    if name in section.slab_strips:  # keep the stations, pick bars and mesh spacing afresh
        strips = section.slab_strips[name].model_copy(update={"bars": {}, "spacing": None})
        update["slab_strips"] = {**section.slab_strips, name: strips}
    return section.model_copy(update=update)


def run_key(project: Project, section: Section, name: str, workbook: dict | None) -> str:
    now = fresh.fingerprint(project, section, workbook)
    return fresh._hash(fresh.element_inputs(now, fresh.names(project, section), [name])[name])


# --- Storage ---------------------------------------------------------------------------


def _file(d: Path) -> Path:
    return d / "trials.json"


def load(d: Path) -> dict[str, Any]:
    try:
        return json.loads(_file(d).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def save(d: Path, data: dict[str, Any]) -> None:
    tmp = _file(d).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str), "utf-8")
    tmp.replace(_file(d))


def load_design(d: Path, key: str) -> dict[str, Any] | None:
    path = d / "trials" / f"{key}.json.gz"
    if not path.exists():
        return None
    return json.loads(gzip.decompress(path.read_bytes()))


def _save_design(d: Path, key: str, design: dict[str, Any]) -> None:
    (d / "trials").mkdir(exist_ok=True)
    path = d / "trials" / f"{key}.json.gz"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(design, default=str).encode(), 5))
    tmp.replace(path)


def prune(d: Path, data: dict[str, Any]) -> None:
    """Delete full designs no trial refers to any more."""
    folder = d / "trials"
    if not folder.is_dir():
        return
    used = {r["key"] for el in data.values() for r in (el.get("runs") or {}).values()}
    for which in SETS:
        used |= {
            k for run in (load_scenarios(d, which).get("runs") or {}).values() for k in run.values() if k
        }
    for f in folder.glob("*.json.gz"):
        if f.name[: -len(".json.gz")] not in used:
            f.unlink(missing_ok=True)


# --- Running ---------------------------------------------------------------------------


def _summary(kind: str, design: dict[str, Any]) -> dict[str, Any]:
    """What a trial's table row shows, apart from the cost."""
    st = design.get("steel") or {}
    out: dict[str, Any] = {
        "utilisation": design.get("utilisation"),
        "passed": bool(design.get("passed")),
        "kg_per_m3": st.get("kg_per_m3") or design.get("steel_ratio_kg_m3"),
        "ratio_pct": st.get("ratio_pct") or design.get("reinforcement_ratio_pct"),
    }
    if kind == "piles":
        zones = (design.get("shear") or {}).get("zones") or []
        arrangement = design.get("arrangement") if isinstance(design.get("arrangement"), dict) else {}
        out |= {
            "failure": design.get("failure") or [],
            "links_kg": st.get("links_kg"),
            "links": ", ".join(dict.fromkeys(z["link"] for z in zones if z.get("link"))) or None,
            "bars": arrangement.get("label"),
        }
    elif kind == "beams":
        link = (design.get("shear") or {}).get("link") or {}
        out |= {"links": link.get("label"), "links_kg_per_m": link.get("kg_per_m")}
    else:
        links = slab_links(design)
        area = st.get("area_m2") or 0
        punching = design.get("punching") or []
        choice = design.get("mesh_choice") or {}
        out |= {
            "bars_kg_per_m2": st.get("kg_per_m2"),
            "shear_links_kg_per_m2": round(links["shear_kg"] / area, 1) if area else None,
            "punching_links_kg_per_m2": round(links["punching_kg"] / area, 1) if area else None,
            "shear_link_zones": sum(
                1 for z in (design.get("shear") or {}).get("links") or [] if z.get("phi")
            ),
            "punching_heads": len(punching),
            "punching_need_links": sum(1 for q in punching if q.get("needs_reinforcement")),
            "punching_fail": sum(1 for q in punching if not q.get("passed")),
            "mesh_mm": choice.get("chosen_mm"),
        }
        # The slab's own utilisation leaves out the shear links; a trial's includes them.
        su = (design.get("shear") or {}).get("utilisation")
        if su is not None and out["utilisation"] is not None:
            out["utilisation"] = max(out["utilisation"], round(su, 3))
        with_links = (
            (st.get("kg_per_m2") or 0) + (links["shear_kg"] + links["punching_kg"]) / area if area else None
        )
        # Per m³ of concrete actually cast: voids take their share out, as in the slab design.
        h = (design.get("thickness_mm") or 0) / 1000 * (st.get("concrete_share") or 1.0)
        if with_links is not None and h:
            out["kg_per_m3_with_links"] = round(with_links / h)
            out["ratio_pct_with_links"] = round(100 * with_links / h / STEEL_DENSITY, 2)
    out["why"] = _why(kind, design, out)
    return out


def _why(kind: str, design: dict[str, Any], out: dict[str, Any]) -> str:
    """Why a trial is not safe, in a sentence or two."""
    if out["passed"]:
        return ""
    reasons = list(design.get("failure") or []) if kind == "piles" else []
    u = design.get("utilisation")
    if not reasons and u is not None and u > 1 + 1e-9:
        reasons.append(f"utilisation {u:.3f} is over 1.")
    if kind == "slabs" and (design.get("shear") or {}).get("passed") is False:
        su = (design.get("shear") or {}).get("utilisation")
        reasons.append(
            "shear links are not enough in some cells" + (f" (utilisation {su:.2f})." if su else ".")
        )
    if kind == "slabs" and out.get("punching_fail"):
        reasons.append(f"{out['punching_fail']} pile heads fail in punching, even with links.")
    if kind == "beams" and (design.get("shear") or {}).get("passed") is False:
        reasons.append("shear and torsion links are not enough.")
    return " ".join(r[0].upper() + r[1:] for r in reasons) or "a check fails; open the design for details."


def run(
    project: Project,
    section: Section,
    workbook: Any,
    summary: dict | None,
    d: Path,
    name: str,
    sizes: list[dict[str, float]],
    deadline: float | None = None,
    tell=None,
) -> dict[str, Any]:
    """Design ``name`` at each size not yet designed from the same inputs, until ``deadline``
    (at least one). Returns the sizes still to do in ``left``."""
    data = load(d)
    entry = data.setdefault(name, {})
    entry["sizes"] = sizes
    runs = entry.setdefault("runs", {})
    kind = kind_of(section.elements[name])
    done, left = [], []
    for i, size in enumerate(sizes):
        trial = trial_section(section, name, size)
        key = run_key(project, trial, name, summary)
        sk = size_key(size)
        if sk in runs and runs[sk]["key"] == key and (d / "trials" / f"{key}.json.gz").exists():
            continue
        if deadline is not None and done and time.monotonic() > deadline:
            left.append(size)
            continue
        if tell:
            tell(i / max(len(sizes), 1), f"Designing {name} at {size_label(size)}")
        res = run_section(project.design, trial, workbook, only=[name], approach=project.approach)
        # A corner berth's parts as one design: the worst utilisation, the steel over all of them.
        design = next((e for e in combine_parts(res).get(kind) or [] if e["element"] == name), None)
        if design is None:
            runs[sk] = {
                "size": size,
                "key": key,
                "error": "; ".join(res.get("skipped") or []) or "Not designed.",
            }
        else:
            _save_design(d, key, design)
            runs[sk] = {"size": size, "key": key, "run_at": res.get("run_at")}
        done.append(size)
    # Sizes taken off the list go.
    wanted = {size_key(s) for s in sizes}
    entry["runs"] = {k: v for k, v in runs.items() if k in wanted}
    save(d, data)
    prune(d, data)
    return {"done": done, "left": left}


def view(
    project: Project,
    section: Section,
    summary: dict | None,
    results: dict[str, Any] | None,
    d: Path,
) -> dict[str, Any]:
    """Every element that can have trials: its sizes, each run's summary and cost per metre of berth."""
    data = load(d)
    results = combine_parts(results or {})
    L = model_length(section, results)
    berth = section.costing.berth_length or L
    out = []
    for name, element in section.elements.items():
        kind = kind_of(element)
        if kind is None:
            continue
        designed = next((e for e in results.get(kind) or [] if e["element"] == name), None)
        entry = data.get(name) or {}
        sizes = entry.get("sizes") or default_sizes(element, designed)
        current = size_of(element, designed)
        rows = []
        for size in sizes:
            sk = size_key(size)
            r = (entry.get("runs") or {}).get(sk)
            row: dict[str, Any] = {
                "size": size,
                "label": size_label(size),
                "key": sk,
                "current": sk == size_key(current),
            }
            if r is None:
                row["state"] = "not run"
                rows.append(row)
                continue
            key = run_key(project, trial_section(section, name, size), name, summary)
            row |= {k: v for k, v in r.items() if k not in ("size", "key")}
            row["state"] = "error" if r.get("error") else ("done" if r["key"] == key else "out of date")
            if r.get("error"):
                rows.append(row)
                continue
            design = load_design(d, r["key"])
            if design is None:
                row["state"] = "not run"
                rows.append(row)
                continue
            row |= _summary(kind, design)
            length = L or model_length(section, {kind: [design]})  # not designed yet: the trial's own
            if berth or length:
                per = berth or length
                c = cost_section(project, section, {kind: [design]}, length=length, berth=per)
                if c.get("rows"):
                    cr = c["rows"][0]
                    row |= {
                        "concrete_m3_per_m": round(cr["concrete_m3"] / per, 3),
                        "rebar_t_per_m": round(cr["rebar_t"] / per, 4),
                        "cost_per_m": cr["cost_per_m"],
                        "cost": cr["cost"],
                        "missing": cr["missing"],
                        "cost_flags": cr["flags"],
                        "basis": cr["basis"],
                    }
            rows.append(row)
        # The cheapest trial that passes, and each trial against the current size.
        priced = [r for r in rows if r.get("state") == "done" and r.get("cost_per_m") is not None]
        best = min((r for r in priced if r.get("passed")), key=lambda r: r["cost_per_m"], default=None)
        base = next((r for r in priced if r["current"]), None)
        for r in priced:
            r["best"] = r is best
            if base is not None:
                r["saving_per_m"] = round(base["cost_per_m"] - r["cost_per_m"], 0)
                if section.costing.berth_length:
                    r["saving"] = round(r["saving_per_m"] * section.costing.berth_length, 0)
        out.append(
            {
                "element": name,
                "kind": kind,
                "current": current,
                "current_label": size_label(current),
                "sizes": sizes,
                "voided": getattr(element, "voids", None) is not None,
                "rows": rows,
            }
        )
    notes = []
    if not section.costing.berth_length:
        notes.append(
            "No berth length on the Costing tab: costs are per metre over the length the model covers"
            + (f" ({L:.1f} m)." if L else ".")
        )
    return {
        "currency": project.prices.currency,
        "berth_length_m": section.costing.berth_length,
        "model_length_m": round(L, 2) if L else None,
        "elements": out,
        "notes": notes,
    }


# --- The whole section: a setting changed on every element ----------------------------
#
# A variant changes one thing on every element (for now the crack width limit, all faces) and the
# whole section is designed with it, each element on its own so the page can go in short steps.
# "As set" is the section as it is. Like a trial, every variant designs without the bars set by
# hand, so the variants differ only by the change. An element whose inputs a variant does not
# change (a sheet pile wall has no crack limit) is designed once and shared.

CRACK_FIELDS = ("crack_width_limit", "crack_width_limit_bottom")


META = ("label", "ideas")


def change_of(variant: dict[str, Any]) -> dict[str, Any]:
    """What a variant changes, without its label and the ideas it came from."""
    return {k: v for k, v in variant.items() if k not in META and v not in (None, {}, "")}


def variant_key(variant: dict[str, Any]) -> str:
    change = change_of(variant)
    if not change:
        return "as-set"
    if set(change) == {"crack_width_limit"}:
        return f"crack_width_limit{float(change['crack_width_limit']):g}"
    return fresh._hash(change)


def variant_label(variant: dict[str, Any], section: Section | None = None) -> str:
    if variant.get("label"):
        return str(variant["label"])
    change = change_of(variant)
    if not change:
        if section is None:
            return "As set"
        limits = sorted(
            {getattr(e, f) for e in section.elements.values() for f in CRACK_FIELDS if hasattr(e, f)}
        )
        return "As set" + (f" (wk {', '.join(f'{x:g}' for x in limits)} mm)" if limits else "")
    parts = []
    if change.get("crack_width_limit"):
        parts.append(f"wk {change['crack_width_limit']:g} mm")
    if change.get("approach"):
        a = change["approach"]
        bits = [
            f"{APPROACH_KEYS[k][0]} {v:g}{APPROACH_KEYS[k][1]}" for k, v in a.items() if k in APPROACH_KEYS
        ]
        parts.append(f"{fresh.APPROACH} {', '.join(bits)}")
    for name, c in (change.get("elements") or {}).items():
        bits = []
        size = {k: v for k, v in c.items() if k in SIZE_KEYS}
        if size:
            bits.append(" × ".join(f"{v:g}" for k, v in size.items() if not k.startswith("void_")) + " mm")
            if "void_diameter" in size or "void_spacing" in size:
                bits.append(
                    f"voids Ø{size.get('void_diameter', '')} @ {size.get('void_spacing', '')}".replace(
                        ".0", ""
                    )
                )
        if c.get("peaks"):
            bits.append(PEAKS.get(c["peaks"], c["peaks"]))
        if c.get("crack_width_limit"):
            bits.append(f"wk {c['crack_width_limit']:g}")
        parts.append(f"{name} {', '.join(bits)}")
    return "; ".join(parts)


SIZE_KEYS = ("thickness", "void_diameter", "void_spacing", "diameter", "width", "depth")
# What a variant can change on the approach slab: (label, unit, lowest, highest).
APPROACH_KEYS = {
    "thickness": ("thickness", " mm", 150.0, 1500.0),
    "length": ("length", " m", 1.0, 30.0),
    "ledge_depth": ("ledge depth", " mm", 200.0, 2000.0),
    "ledge_projection": ("ledge projection", " mm", 150.0, 1500.0),
    "crack_width_limit": ("crack width limit", " mm", 0.05, 0.5),
}
PEAKS = {
    "peak": "peaks as they are",
    "face_mean": "face mean",
    "ring_mean": "ring mean",
    "envelope_face_mean": "envelope then face mean",
}


def _wk(v: Any) -> float:
    v = float(v)
    if not 0.05 <= v <= 0.5:
        raise ValueError(f"A crack width limit of {v:g} mm is outside 0.05 to 0.5 mm.")
    return v


def clean_variant(variant: dict[str, Any], section: Section | None = None) -> dict[str, Any]:
    """A variant checked against the section: a crack width limit on every element, and changes to
    single elements (size, a slab's pile-face method, the element's crack width limit)."""
    out: dict[str, Any] = {}
    if variant.get("crack_width_limit") not in (None, ""):
        out["crack_width_limit"] = _wk(variant["crack_width_limit"])
    for name, c in (variant.get("elements") or {}).items():
        if section is None or name not in section.elements:
            raise ValueError(f"{name} is not an element of this section.")
        element = section.elements[name]
        mine: dict[str, Any] = {}
        size = {k: v for k, v in c.items() if k in SIZE_KEYS and v not in (None, "")}
        if size:
            if kind_of(element) is None:
                raise ValueError(f"{name}: Triton cannot change its size.")
            full = clean_size(element, {**size_of(element), **size})
            mine |= {k: v for k, v in full.items() if k in size}
        if c.get("peaks"):
            if not isinstance(element, SlabInput) or c["peaks"] not in PEAKS:
                raise ValueError(f"{name}: no pile-face method '{c['peaks']}'.")
            mine["peaks"] = c["peaks"]
        if c.get("crack_width_limit") not in (None, ""):
            if not hasattr(element, "crack_width_limit"):
                raise ValueError(f"{name} has no crack width limit.")
            mine["crack_width_limit"] = _wk(c["crack_width_limit"])
        if mine:
            out.setdefault("elements", {})[name] = mine
    a = variant.get("approach") or {}
    mine = {}
    for k, v in a.items():
        if k not in APPROACH_KEYS or v in (None, ""):
            continue
        label, unit, lo, hi = APPROACH_KEYS[k]
        v = float(v)
        if not lo <= v <= hi:
            raise ValueError(f"Approach slab {label} {v:g}{unit} is outside {lo:g} to {hi:g}{unit}.")
        mine[k] = v
    if mine:
        out["approach"] = mine
    for k in META:
        if variant.get(k):
            out[k] = variant[k]
    return out


def variant_approach(project: Project, variant: dict[str, Any]) -> ApproachSlabInput | None:
    """The project's approach slab with the variant's changes (and its crack width limit on all)."""
    a = project.approach
    if a is None:
        return None
    change = change_of(variant)
    c = change.get("approach") or {}
    update: dict[str, Any] = {k: c[k] for k in ("thickness", "length") if k in c}
    wk = c.get("crack_width_limit") or change.get("crack_width_limit")
    if wk:
        update |= {f: wk for f in CRACK_FIELDS}
    ledge = {}
    if "ledge_depth" in c:
        ledge["depth"] = c["ledge_depth"]
    if "ledge_projection" in c:
        ledge["projection"] = c["ledge_projection"]
    if wk:
        ledge["crack_width_limit"] = wk
    if ledge:
        update["ledge"] = a.ledge.model_copy(update=ledge)
    return a.model_copy(update=update) if update else a


def variant_project(project: Project, variant: dict[str, Any]) -> Project:
    """The project with the variant's approach slab (its keys follow the approach's inputs)."""
    a = variant_approach(project, variant)
    return project if a is project.approach else project.model_copy(update={"approach": a})


def variant_section(section: Section, variant: dict[str, Any]) -> Section:
    """The section with the variant's changes, and no bars set by hand."""
    change = change_of(variant)
    wk = change.get("crack_width_limit")
    per = change.get("elements") or {}
    elements = {}
    for name, e in section.elements.items():
        update = {f: wk for f in CRACK_FIELDS if wk and hasattr(e, f)}
        c = per.get(name) or {}
        if c.get("crack_width_limit"):
            update |= {f: c["crack_width_limit"] for f in CRACK_FIELDS if hasattr(e, f)}
        if c.get("peaks"):
            update["peaks"] = c["peaks"]
        size = {k: v for k, v in c.items() if k in SIZE_KEYS}
        if size:
            e = with_size(e, size)
        elements[name] = e.model_copy(update=update) if update else e
    strips = {k: v.model_copy(update={"bars": {}, "spacing": None}) for k, v in section.slab_strips.items()}
    return section.model_copy(
        update={"elements": elements, "user_cages": {}, "beam_cages": {}, "slab_strips": strips}
    )


def _designable(section: Section, project: Project | None = None) -> list[str]:
    """Every element the Design tab designs, in its order (a sheet pile wall only when defined), and the
    project's approach slab last."""
    from .project import CombiWallInput, SheetPileInput

    order = (PileInput, CombiWallInput, SheetPileInput, BeamInput, SlabInput)
    out = [n for t in order for n, e in section.elements.items() if isinstance(e, t)]
    return out + ([fresh.APPROACH] if project is not None and project.approach is not None else [])


# Two sets of whole-section runs: "scenarios" (Comparisons, all elements) and "ve" (Value engineering).
SETS = ("scenarios", "ve")


def _scenario_file(d: Path, which: str) -> Path:
    return d / f"{which}.json"


def load_scenarios(d: Path, which: str = "scenarios") -> dict[str, Any]:
    try:
        return json.loads(_scenario_file(d, which).read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save_scenarios(d: Path, data: dict[str, Any], which: str) -> None:
    tmp = _scenario_file(d, which).with_suffix(".stmp")
    tmp.write_text(json.dumps(data, default=str), "utf-8")
    tmp.replace(_scenario_file(d, which))


DESIGN_KINDS = ("piles", "combi_walls", "sheet_pile_walls", "beams", "slabs", "approach_slabs")


def run_scenarios(
    project: Project,
    section: Section,
    workbook: Any,
    summary: dict | None,
    d: Path,
    variants: list[dict[str, Any]],
    deadline: float | None = None,
    tell=None,
    which: str = "scenarios",
) -> dict[str, Any]:
    """Design every element for each variant ("as set" first), skipping what is already designed from
    the same inputs, until ``deadline`` (at least one element). ``left`` counts what is still to do."""
    unique = {variant_key(v): v for v in variants if change_of(v)}
    variants = [{}] + list(unique.values())
    data = load_scenarios(d, which)
    data["variants"] = variants[1:]
    runs = data.setdefault("runs", {})
    names = _designable(section, project)
    todo = [(v, n) for v in variants for n in names]
    done, left = 0, 0
    for i, (variant, name) in enumerate(todo):
        vs = variant_section(section, variant)
        vp = variant_project(project, variant)
        key = scenario_key(vp, vs, name, summary)
        vk = variant_key(variant)
        mine = runs.setdefault(vk, {})
        if mine.get(name) == key and (d / "trials" / f"{key}.json.gz").exists():
            continue
        if (d / "trials" / f"{key}.json.gz").exists() or _no_results(d, key):
            mine[name] = key  # the same inputs in another variant: shared
            continue
        if deadline is not None and done and time.monotonic() > deadline:
            left += 1
            continue
        if tell:
            tell(i / max(len(todo), 1), f"Designing {name} ({variant_label(variant, section)})")
        res = run_section(vp.design, vs, workbook, only=[name], approach=vp.approach)
        design = next((e for k in DESIGN_KINDS for e in res.get(k) or [] if e["element"] == name), None)
        if design is None:
            _mark_no_results(d, key)
        else:
            _save_design(d, key, design)
        mine[name] = key
        done += 1
    wanted = {variant_key(v) for v in variants}
    data["runs"] = {k: v for k, v in runs.items() if k in wanted}
    _save_scenarios(d, data, which)
    prune(d, load(d))
    return {"done": done, "left": left}


def scenario_key(project: Project, section: Section, name: str, workbook: dict | None) -> str:
    """``run_key``, and for beams and slabs the sizes of the elements they are designed with (the piles
    they sit on and each other), which a whole-section variant can change too."""
    key = run_key(project, section, name, workbook)
    if name not in section.elements or kind_of(section.elements[name]) not in ("beams", "slabs"):
        return key
    others = {n: size_of(e) for n, e in section.elements.items() if n != name and kind_of(e)}
    return fresh._hash([key, others])


def _no_results(d: Path, key: str) -> bool:
    return (d / "trials" / f"{key}.none").exists()


def _mark_no_results(d: Path, key: str) -> None:
    (d / "trials").mkdir(exist_ok=True)
    (d / "trials" / f"{key}.none").write_text("", "utf-8")


def design_kind(element: Any) -> str:
    from .project import CombiWallInput, SheetPileInput

    if element is None:
        return "approach_slabs"
    if isinstance(element, CombiWallInput):
        return "combi_walls"
    if isinstance(element, SheetPileInput):
        return "sheet_pile_walls"
    return kind_of(element)


def _element_summary(kind: str, design: dict[str, Any]) -> dict[str, Any]:
    if kind == "sheet_pile_walls":
        uf = (design.get("design") or {}).get("uf")
        return {"utilisation": uf, "passed": uf is not None and uf <= 1}
    if kind == "combi_walls":
        return {"utilisation": design.get("utilisation"), "passed": bool(design.get("passed"))}
    return _summary(kind, design)


def scenarios_view(
    project: Project,
    section: Section,
    summary: dict | None,
    results: dict[str, Any] | None,
    d: Path,
    which: str = "scenarios",
) -> dict[str, Any]:
    """Each variant's whole-section cost per metre of berth, and each element's part in it."""
    data = load_scenarios(d, which)
    start = [{"crack_width_limit": 0.3}] if which == "scenarios" else []
    variants = [{}] + [v for v in (data.get("variants") if "variants" in data else start) if change_of(v)]
    names = _designable(section, project)
    L0 = model_length(section, results or {})
    cols = []
    for variant in variants:
        vk = variant_key(variant)
        vs = variant_section(section, variant)
        vp = variant_project(project, variant)
        mine = (data.get("runs") or {}).get(vk) or {}
        designs: dict[str, list] = {}
        elements: dict[str, dict] = {}
        missing = 0
        for name in names:
            key = scenario_key(vp, vs, name, summary)
            if mine.get(name) != key:
                missing += 1
                elements[name] = {"state": "out of date" if name in mine else "not run"}
                continue
            if _no_results(d, key):
                elements[name] = {"state": "no results"}
                continue
            design = load_design(d, key)
            if design is None:
                missing += 1
                elements[name] = {"state": "not run"}
                continue
            kind = design_kind(section.elements.get(name))
            designs.setdefault(kind, []).append(design)
            elements[name] = {"state": "done", **_element_summary(kind, design)}
        col: dict[str, Any] = {
            "variant": variant,
            "key": vk,
            "label": variant_label(variant, section),
            "ideas": variant.get("ideas") or [],
            "base": not variant,
            "complete": missing == 0,
            "missing": missing,
            "elements": elements,
        }
        if missing == 0:
            L = L0 or model_length(section, designs)
            berth = section.costing.berth_length or L
            if berth:
                c = cost_section(project, section, designs, length=L, berth=berth)
                for r in c.get("rows") or []:
                    # The ledge's row counts with the approach slab it carries.
                    e = elements.get(fresh.APPROACH if r["kind"] == "ledge" else r["element"])
                    if e is None:
                        continue
                    if r["kind"] == "ledge" and "cost_per_m" in e:
                        both = (
                            None
                            if e["cost_per_m"] is None or r["cost_per_m"] is None
                            else e["cost_per_m"] + r["cost_per_m"]
                        )
                        e |= {
                            "cost_per_m": both,
                            "rebar_t_per_m": round(e["rebar_t_per_m"] + r["rebar_t"] / berth, 4),
                        }
                        continue
                    e |= {"cost_per_m": r["cost_per_m"], "rebar_t_per_m": round(r["rebar_t"] / berth, 4)}
                t = c.get("totals") or {}
                col |= {
                    "cost_per_m": c.get("per_m", {}).get("cost"),
                    "cost": t.get("cost") if section.costing.berth_length else None,
                    "concrete_m3_per_m": c.get("per_m", {}).get("concrete_m3"),
                    "rebar_t_per_m": c.get("per_m", {}).get("rebar_t"),
                    "steel_t_per_m": c.get("per_m", {}).get("steel_t"),
                    "prices_complete": t.get("complete"),
                    "missing_prices": sorted({m for r in c.get("rows") or [] for m in r["missing"] if m}),
                }
            done = [e for e in elements.values() if e.get("state") == "done"]
            col["unsafe"] = [
                n for n, e in elements.items() if e.get("state") == "done" and not e.get("passed")
            ]
            col["safe_count"] = len(done) - len(col["unsafe"])
        cols.append(col)
    base = cols[0]
    for c in cols:
        if base.get("cost_per_m") is not None and c.get("cost_per_m") is not None:
            c["saving_per_m"] = round(base["cost_per_m"] - c["cost_per_m"], 0)
            if section.costing.berth_length:
                c["saving"] = round(c["saving_per_m"] * section.costing.berth_length, 0)
    return {
        "currency": project.prices.currency,
        "berth_length_m": section.costing.berth_length,
        "elements": names,
        "variants": cols,
    }


# --- Value engineering: the ideas Triton can cost ----------------------------------------


def ideas(
    section: Section, results: dict[str, Any] | None = None, approach: ApproachSlabInput | None = None
) -> list[dict[str, Any]]:
    """Every change Triton can design and cost for this section, one idea each, with a suggested value
    the user can edit. ``change`` is a variant; ideas with the same ``group`` change the same input
    and cannot be mixed."""
    results = results or {}
    out: list[dict[str, Any]] = []

    def add(
        group: str, element: str | None, label: str, change: dict, what: str, note: str = "", unit: str = "mm"
    ) -> None:
        out.append(
            {
                "id": fresh._hash([group, change]),
                "group": group,
                "element": element,
                "label": label,
                "what": what,
                "change": change,
                "note": note,
                "unit": unit,
            }
        )

    limits = sorted({getattr(e, f) for e in section.elements.values() for f in CRACK_FIELDS if hasattr(e, f)})
    if approach is not None:
        limits = sorted({*limits, approach.crack_width_limit, approach.crack_width_limit_bottom})
    now = max(limits) if limits else 0.2
    for wk in (0.25, 0.3):
        if wk > now:
            add(
                "crack:all",
                None,
                f"Crack width limit {wk:g} mm on every element",
                {"crack_width_limit": wk},
                "crack_width_limit",
                f"Now {', '.join(f'{x:g}' for x in limits)} mm. Check the exposure class and the client's "
                "specification allow it (BS 6349 asks 0.2 mm in the splash zone).",
            )
    for name, e in section.elements.items():
        kind = kind_of(e)
        if kind is None:
            continue
        designed = next((r for r in results.get(kind) or [] if r["element"] == name), None)
        size = size_of(e, designed)
        el = lambda c, n=name: {"elements": {n: c}}  # noqa: E731
        if kind == "slabs":
            t = size["thickness"]
            for dt in (-50, 50):
                add(
                    f"size:{name}",
                    name,
                    f"{name} {t + dt:g} mm thick ({dt:+g})",
                    el({"thickness": t + dt}),
                    "thickness",
                    "Thinner: less concrete, more steel and punching links. Thicker: the reverse.",
                )
            if "void_diameter" in size:
                d, sp = size["void_diameter"], size["void_spacing"]
                add(
                    f"voids:{name}",
                    name,
                    f"{name} voids Ø{d + 100:g} @ {sp:g}",
                    el({"void_diameter": d + 100}),
                    "void_diameter",
                    "Bigger voids: less concrete and weight, thinner webs for shear.",
                )
                add(
                    f"voids:{name}",
                    name,
                    f"{name} voids Ø{d:g} @ {sp + 100:g}",
                    el({"void_spacing": sp + 100}),
                    "void_spacing",
                    "Wider spacing: more concrete, wider webs.",
                )
            for m, text in PEAKS.items():
                if m != getattr(e, "peaks", None):
                    add(
                        f"peaks:{name}",
                        name,
                        f"{name} moments at the pile faces: {text}",
                        el({"peaks": m}),
                        "peaks",
                        "A method choice: see the Method tab for what each one takes.",
                    )
        elif kind == "beams":
            d = size["depth"]
            for dd in (-200, 200):
                add(
                    f"size:{name}",
                    name,
                    f"{name} {d + dd:g} mm deep ({dd:+g})",
                    el({"depth": d + dd}),
                    "depth",
                )
        else:
            D = size["diameter"]
            for dD in (-200, 200):
                add(
                    f"size:{name}",
                    name,
                    f"{name} Ø{D + dD:g} ({dD:+g})",
                    el({"diameter": D + dD}),
                    "diameter",
                    "The deck's punching and the beams' supports are designed with the new diameter too.",
                )
        if hasattr(e, "crack_width_limit") and e.crack_width_limit < 0.3:
            add(
                f"crack:{name}",
                name,
                f"{name} crack width limit 0.3 mm",
                el({"crack_width_limit": 0.3}),
                "crack_width_limit",
                "Only this element.",
            )
    if approach is not None:
        name = fresh.APPROACH
        t, L, led = approach.thickness, approach.length, approach.ledge
        ap = lambda c: {"approach": c}  # noqa: E731
        for dt in (-50, 50):
            if t + dt >= 150:
                add(
                    f"approach:{name}:thickness",
                    name,
                    f"{name} {t + dt:g} mm thick ({dt:+g})",
                    ap({"thickness": t + dt}),
                    "thickness",
                    "Thinner: less concrete, but more bars and shear links near the ledge. "
                    "Thicker: the reverse, and a little more load on the ledge and the rear beam.",
                )
        for dL in (-1.0, 1.0):
            if L + dL >= 2:
                add(
                    f"approach:{name}:length",
                    name,
                    f"{name} {L + dL:g} m long ({dL:+g} m)",
                    ap({"length": L + dL}),
                    "length",
                    "Only where the settlement behind the quay allows: the slab must still bridge it.",
                    unit="m",
                )
        for dd in (-100, 100):
            if led.depth + dd >= 250:
                add(
                    f"approach:{name}:ledge_depth",
                    name,
                    f"Rear beam ledge {led.depth + dd:g} mm deep ({dd:+g})",
                    ap({"ledge_depth": led.depth + dd}),
                    "ledge_depth",
                    "The rear beam is designed with it too (the ledge's load and torque).",
                )
        for dp in (-50, 50):
            if led.projection + dp >= led.bearing_width + led.edge_distance + 50:
                add(
                    f"approach:{name}:ledge_projection",
                    name,
                    f"Rear beam ledge projecting {led.projection + dp:g} mm ({dp:+g})",
                    ap({"ledge_projection": led.projection + dp}),
                    "ledge_projection",
                    "A shorter ledge: a shorter lever arm for its tie and less torque on the rear beam.",
                )
        if approach.crack_width_limit < 0.3 or approach.crack_width_limit_bottom < 0.3:
            add(
                f"approach:{name}:crack",
                name,
                f"{name} and ledge crack width limit 0.3 mm",
                ap({"crack_width_limit": 0.3}),
                "crack_width_limit",
                "Only the approach slab and its ledge.",
            )
    return out
