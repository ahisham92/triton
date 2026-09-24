"""The Method tab: how Triton designs each kind of element, and the options chosen in this project.

The method texts are the design modules' own descriptions (their docstrings), so the tab always
says what the code does. Each option that changes the method (a choice between named methods on
the design settings or on an element) is listed with every alternative and the one in use, so the
differences between methods sit side by side.
"""

from __future__ import annotations

import inspect
import typing
from typing import Any, Literal

from pydantic import BaseModel

from . import alignment, clashes, trials
from .design import (
    beams,
    bollard,
    circular,
    combi,
    crack,
    curtailment,
    ductility,
    governing,
    openings,
    peaks,
    pile_cracks,
    pile_shear,
    piles,
    rect,
    rooms,
    sheet_piles,
    slabs,
    spw_design,
    truss,
    tube,
    voids,
)
from .project import Project

KIND_LABEL = {
    "pile": "Piles",
    "combi_wall": "Combi wall",
    "sheet_pile_wall": "Sheet pile wall",
    "slab": "Slabs",
    "front_beam": "Beams",
    "rear_beam": "Beams",
    "transverse_beam": "Beams",
}

# Per kind of element: (heading, module whose description is the method), in reading order.
TOPICS: dict[str, list[tuple[str, Any]]] = {
    "Piles": [
        ("Bars for N and M", piles),
        ("N–M capacity of the circular section", circular),
        ("Reducing the bars down the pile", curtailment),
        ("Shear and links", pile_shear),
        ("Crack widths", pile_cracks),
        ("Isolated peaks in the results", peaks),
    ],
    "Combi wall": [
        ("Infill and tube share", combi),
        ("Steel tube", tube),
        ("N–M capacity of the infill", circular),
        ("Infill shear and links", pile_shear),
    ],
    "Sheet pile wall": [
        ("Actions checked", spw_design),
        ("Section checks (EN 1993-5)", sheet_piles),
    ],
    "Slabs": [
        ("Bars, strips, shear and punching", slabs),
        ("Circular voids (PVC pipes)", voids),
        ("Manholes and channels (Openings tab)", openings),
        ("Crack widths and restraint", crack),
    ],
    "Beams": [
        ("Section forces, cage and links", beams),
        ("N with biaxial bending", rect),
        ("Crack widths and restraint", crack),
        ("Bollard tie bars (front beam)", bollard),
        ("Truss model (front beam)", truss),
        ("Rooms cut into the beam", rooms),
    ],
}

GENERAL = [
    ("Corner berths: a quay that turns", alignment),
    ("Comparisons (trial sizes)", trials),
    ("Over-reinforced sections (slabs and beams)", ductility),
    ("Reinforcement clashes at the pile heads", clashes),
    ("AdSec load sets and signs", governing),
]

# Methods of the slab's moments at the pile faces, in the order the options are offered.
PEAK_ORDER = ["peak", "face_mean", "ring_mean", "envelope_face_mean"]
PEAK_LABEL = {
    "peak": "Peak",
    "face_mean": "Face mean",
    "ring_mean": "Ring mean",
    "envelope_face_mean": "Envelope then face mean",
}


def _doc(module: Any) -> str:
    return inspect.getdoc(module) or ""


def _choices(annotation: Any) -> list[str] | None:
    """The values of a Literal field (also inside ``X | None``), else None."""
    if typing.get_origin(annotation) is Literal:
        return [str(v) for v in typing.get_args(annotation)]
    for arg in typing.get_args(annotation):
        if typing.get_origin(arg) is Literal:
            return [str(v) for v in typing.get_args(arg)]
    return None


def options_of(model: BaseModel, path: str = "") -> list[dict[str, Any]]:
    """Every choice between named methods on ``model`` and its sub-models, with the one in use.

    Grades and section names are data, not methods, so choices with more than six values are left out.
    """
    out: list[dict[str, Any]] = []
    for name, field in type(model).model_fields.items():
        value = getattr(model, name)
        if name == "kind":
            continue
        if isinstance(value, BaseModel):
            out += options_of(value, f"{path}{field.title or name} › ")
            continue
        choices = _choices(field.annotation)
        if not choices or len(choices) < 2 or len(choices) > 6 or "grade" in (field.title or name).lower():
            continue
        out.append(
            {
                "field": name,
                "title": f"{path}{field.title or name}",
                "chosen": None if value is None else str(value),
                "choices": choices,
                "description": field.description or "",
            }
        )
    return out


def view(project: Project) -> dict[str, Any]:
    """Everything the Method tab shows for ``project``."""
    kinds: dict[str, dict[str, Any]] = {}
    for section in project.sections:
        for name, element in section.elements.items():
            label = KIND_LABEL.get(element.kind)
            if label is None:
                continue
            k = kinds.setdefault(label, {"kind": label, "elements": [], "options": []})
            k["elements"].append({"section": section.name, "element": name})
            for o in options_of(element):
                k["options"].append({**o, "section": section.name, "element": name})
    order = list(TOPICS)
    out_kinds = []
    for label in sorted(kinds, key=order.index):
        k = kinds[label]
        k["topics"] = [{"title": t, "text": _doc(m)} for t, m in TOPICS[label]]
        if label == "Slabs":
            k["pile_faces"] = {
                "methods": [
                    {"value": v, "label": PEAK_LABEL[v], "text": slabs.PEAK_METHODS[v]} for v in PEAK_ORDER
                ],
                "chosen": [
                    {"section": o["section"], "element": o["element"], "value": o["chosen"]}
                    for o in k["options"]
                    if o["field"] == "peaks"
                ],
            }
        out_kinds.append(k)
    return {
        "general": [{"title": t, "text": _doc(m)} for t, m in GENERAL],
        "project_options": options_of(project.design),
        "kinds": out_kinds,
        "empty": not out_kinds,
    }
