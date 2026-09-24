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
from .costing import cost_section, model_length, slab_links
from .design.runner import run_section
from .materials import STEEL_DENSITY
from .project import BeamInput, PileInput, Project, Section, SlabInput

KINDS = {PileInput: "piles", BeamInput: "beams", SlabInput: "slabs"}


def kind_of(element: Any) -> str | None:
    return next((k for t, k in KINDS.items() if isinstance(element, t)), None)


def size_of(element: Any, designed: dict | None = None) -> dict[str, float]:
    """The element's current size, as a trial size."""
    if isinstance(element, SlabInput):
        return {"thickness": element.thickness}
    if isinstance(element, PileInput):
        return {"diameter": element.diameter}
    width = element.width or (designed or {}).get("width_mm")
    return {"width": width, "depth": element.depth} if width else {"depth": element.depth}


def size_key(size: dict[str, float]) -> str:
    return "x".join(f"{k}{float(v):g}" for k, v in sorted(size.items()) if v)


def size_label(size: dict[str, float]) -> str:
    if "thickness" in size:
        return f"{size['thickness']:g} mm"
    if "diameter" in size:
        return f"Ø{size['diameter']:g}"
    return f"{size['width']:g} × {size['depth']:g}" if size.get("width") else f"depth {size['depth']:g}"


def default_sizes(element: Any, designed: dict | None = None) -> list[dict[str, float]]:
    """The current size and a few steps either side."""
    now = size_of(element, designed)
    if "thickness" in now:
        t = now["thickness"]
        return [{"thickness": t + d} for d in (-100, -50, 0, 50, 100) if t + d > 0]
    if "diameter" in now:
        D = now["diameter"]
        return [{"diameter": D + d} for d in (-200, 0, 200) if D + d > 0]
    return [{**now, "depth": now["depth"] + d} for d in (-200, -100, 0, 100, 200) if now["depth"] + d > 0]


def clean_size(element: Any, size: dict[str, Any]) -> dict[str, float]:
    keys = {"slabs": ("thickness",), "piles": ("diameter",), "beams": ("width", "depth")}[kind_of(element)]
    out = {}
    for k in keys:
        v = size.get(k)
        if v in (None, ""):
            continue
        v = float(v)
        if not 50 <= v <= 10000:
            raise ValueError(f"{k} {v:g} mm is not a size Triton can design.")
        out[k] = v
    if not out or ("depth" not in out and kind_of(element) == "beams"):
        raise ValueError("Give each trial its size.")
    return out


def trial_section(section: Section, name: str, size: dict[str, float]) -> Section:
    """The section with the element at the trial size and without the bars set for its current size."""
    element = section.elements[name].model_copy(update=size)
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
    return fresh._hash(fresh.element_inputs(now, section.elements, [name])[name])


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
        h = (design.get("thickness_mm") or 0) / 1000
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
        res = run_section(project.design, trial, workbook, only=[name])
        design = next((e for e in res.get(kind) or [] if e["element"] == name), None)
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
    results = results or {}
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
