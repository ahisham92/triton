"""Issued revisions: a copy of the project as issued, and what changed since.

Issue revision (Project tab) keeps the project as a .trt file in the project's ``revisions`` folder,
adds the revision to the project's list with who prepared, checked and approved it, and moves the
revision in work on (P01 to P02, A to B). "What changed since" compares that copy with the project
now: the inputs that differ, and for each designed element its bars, utilisation and steel.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from . import package
from .project import Project, Revision
from .store import ProjectStore

KINDS = ("piles", "combi_walls", "sheet_pile_walls", "beams", "slabs")
_SKIP = {"created_at", "updated_at", "revisions", "revision", "locked", "checks", "clashes", "id"}


def folder(store: ProjectStore, project_id: str) -> Path:
    store._path(project_id)  # validates the id
    return store.root / project_id / "revisions"


def safe(rev: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", rev).strip("._") or "rev"


def next_revision(rev: str) -> str:
    """P01 -> P02, C09 -> C10, A -> B, 2 -> 3; anything else gets '.1'."""
    m = re.fullmatch(r"(.*?)(\d+)", rev)
    if m:
        return f"{m[1]}{int(m[2]) + 1:0{len(m[2])}d}"
    if re.fullmatch(r"[A-Ya-y]", rev):
        return chr(ord(rev) + 1)
    return f"{rev}.1"


def issue(store: ProjectStore, project: Project, description: str) -> Project:
    """Keep the project as issued under its revision in work, then move the revision on."""
    rev = project.info.revision.strip() or "P01"
    if any(v.rev == rev for v in project.revisions):
        raise ValueError(f"Revision {rev} was issued already: set the revision in work on the Project tab.")
    d = folder(store, project.id)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{safe(rev)}.trt"
    record = Revision(
        rev=rev,
        description=description,
        prepared=project.info.designer,
        checked=project.info.checker,
        approved=project.info.approver,
        snapshot=name,
    )
    project.revisions.append(record)
    tmp = d / (name + ".tmp")
    with tmp.open("wb") as f:
        package.write(store, project, f)
    tmp.replace(d / name)
    project.info.revision = next_revision(rev)
    return store.save(project)


def snapshot_path(store: ProjectStore, project: Project, rev: str) -> Path | None:
    v = next((v for v in project.revisions if v.rev == rev), None)
    if v is None or not v.snapshot:
        return None
    path = folder(store, project.id) / v.snapshot
    return path if path.is_file() else None


def _flat(value: Any, path: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _SKIP:
                continue
            out.update(_flat(v, f"{path} › {k}" if path else str(k)))
        return out
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        out = {}
        for i, v in enumerate(value):
            label = v.get("name") or v.get("rev") or str(i + 1)
            out.update(_flat({k: x for k, x in v.items() if k != "name"}, f"{path} › {label}"))
        return out
    return {path: value}


def _elements(results: dict | None) -> dict[str, dict[str, Any]]:
    """What a reader compares for each designed element: bars, utilisation, pass, steel."""
    out: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        for e in (results or {}).get(kind, []):
            steel = e.get("steel") or (e.get("infill") or {}).get("steel") or {}
            arr = e.get("arrangement") or (e.get("infill") or {}).get("arrangement")
            label = (arr or {}).get("label") if isinstance(arr, dict) else None
            label = label or e.get("label") or ((e.get("design") or {}).get("section"))
            util = e.get("utilisation")
            if util is None:
                util = (e.get("design") or {}).get("utilisation")
            out[e["element"]] = {
                "bars": label,
                "utilisation": round(float(util), 3) if isinstance(util, (int, float)) else None,
                "passed": e.get("passed"),
                "kg_per_m3": steel.get("kg_per_m3"),
            }
    return out


def changes(store: ProjectStore, project: Project, rev: str) -> dict[str, Any]:
    """The inputs and element results that differ between revision ``rev`` as issued and now."""
    path = snapshot_path(store, project, rev)
    if path is None:
        raise FileNotFoundError(f"The copy of revision {rev} is not on this server.")
    with zipfile.ZipFile(path) as z:
        then = json.loads(z.read("project.json"))
        names = set(z.namelist())
        then_results = {
            s["id"]: json.loads(z.read(f"sections/{s['id']}/results.json"))
            if f"sections/{s['id']}/results.json" in names
            else None
            for s in then["sections"]
        }
    now = project.model_dump(mode="json")
    a, b = _flat(then), _flat(now)
    inputs = [
        {"what": k, "was": a.get(k), "now": b.get(k)}
        for k in list(a) + [k for k in b if k not in a]
        if a.get(k) != b.get(k)
    ]
    sections = []
    for s in project.sections:
        old = _elements(then_results.get(s.id))
        new = _elements(store.load_results(project.id, s.id))
        rows = []
        for name in list(new) + [n for n in old if n not in new]:
            o, n = old.get(name), new.get(name)
            if o == n:
                continue
            rows.append({"element": name, "was": o, "now": n})
        sections.append({"section": s.name, "elements": rows})
    return {
        "rev": rev,
        "inputs": inputs[:300],
        "more_inputs": max(0, len(inputs) - 300),
        "sections": sections,
    }
