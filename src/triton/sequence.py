"""The construction sequence of a section: the order its works are built in, as stages for the
Construction sequence tab's 3D player. Never a design input.

The default order (Ahmed, 2026-09-25): steel pipes of the combi wall, its cages, its concrete infill,
the sheet piles, the demolition of the existing front beam and slab (only with an existing structure),
the piles (cage in, then concrete cast about 1 m above the cut-off level, then the heads broken
down), the front and rear beams at the same time, the slab and approach slab, the furniture and
last the dredging. Each section can have its own steps (Section.sequence).

The furniture goes in item by item (Ahmed, 2026-09-26): crane rail, tie-downs, stow pins, crane
stoppers, fenders, bollards, and last the STS crane, brought by a ship that berths alongside and pushed
onto the quay over skid rails laid from its deck. A section with no STS crane has none of the crane's
items (furniture.for_section, the Furniture tab's own rule), so it has none of their steps.

A stage is a step with the steps set "at the same time as the step before" after it.
"""

from __future__ import annotations

from typing import Any

from .furniture import for_section
from .project import (
    BeamInput,
    CombiWallInput,
    PileInput,
    Project,
    Section,
    SequenceStep,
    SheetPileInput,
    SlabInput,
)

APPROACH = "Approach Slab"

NAMES = {
    "steel_pipes": "Drive the steel pipes (combi wall)",
    "combi_cages": "Place the cages in the pipes",
    "combi_infill": "Cast the combi wall's concrete infill",
    "sheet_piles": "Drive the sheet piles",
    "demolition": "Demolish the existing front beam and slab",
    "pile_cages": "Bore the piles and place their cages",
    "pile_concrete": "Cast the piles, {above} m above the cut-off level",
    "pile_heads": "Break down the pile heads to the cut-off level",
    "front_beam": "Cast the front beam",
    "rear_beam": "Cast the rear beam",
    "transverse_beam": "Cast the transverse beams",
    "slab": "Cast the slab",
    "approach_slab": "Cast the approach slab",
    "crane_rail": "Lay the crane rails",
    "tie_downs": "Fix the crane tie-downs",
    "stow_pins": "Fix the stow pin sockets",
    "crane_stoppers": "Fix the crane stoppers",
    "fenders": "Hang the fenders and ladders",
    "bollards": "Fix the bollards",
    "sts_crane": "Bring the STS crane by ship and push it onto the quay",
    "furniture": "Install the quay furniture",
    "dredging": "Dredge to the new seabed",
}

# What each work builds: element kind -> the state it leaves the element in.
BUILDS = {
    "steel_pipes": ("combi_wall", "pipe"),
    "combi_cages": ("combi_wall", "cage"),
    "combi_infill": ("combi_wall", "done"),
    "sheet_piles": ("sheet_pile_wall", "done"),
    "pile_cages": ("pile", "cage"),
    "pile_concrete": ("pile", "cast_high"),
    "pile_heads": ("pile", "done"),
    "front_beam": ("front_beam", "done"),
    "rear_beam": ("rear_beam", "done"),
    "transverse_beam": ("transverse_beam", "done"),
    "slab": ("slab", "done"),
}


# The furniture works in their order, the Furniture tab's item each one needs, and the kinds of the
# 3D view's furniture (triton/site3d.py) each one installs. "furniture" is every item at once.
FURNITURE = {
    "crane_rail": ("crane_rails", ["crane_rails"]),
    "tie_downs": ("tie_downs", ["tie_downs"]),
    "stow_pins": ("storm_pins", ["storm_pins"]),
    "crane_stoppers": ("crane_stoppers", ["crane_stoppers"]),
    "fenders": ("fenders", ["fenders", "fender_blocks", "ladders"]),
    "bollards": ("bollards", ["bollards"]),
    "sts_crane": ("sts_crane", ["sts_crane"]),
}
ALL_FURNITURE = sorted({k for _, kinds in FURNITURE.values() for k in kinds})


def furniture_works(project: Project, section: Section) -> list[str]:
    """The furniture works this section has: none without furniture, and none of the crane's items
    without an STS crane."""
    if not section.furniture.use:
        return []
    f = for_section(project.furniture, section.furniture)
    return [w for w, (item, _) in FURNITURE.items() if getattr(f, item) is not None]


def _kind(el: Any) -> str | None:
    if isinstance(el, PileInput | CombiWallInput | SheetPileInput | SlabInput):
        return el.kind
    if isinstance(el, BeamInput):
        return el.kind
    return None


def default_steps(project: Project, section: Section) -> list[SequenceStep]:
    """The default order, keeping only the works this section has."""
    kinds = {_kind(e) for e in section.elements.values()}
    ex = section.existing
    order: list[tuple[str, bool]] = [
        ("steel_pipes", False),
        ("combi_cages", False),
        ("combi_infill", False),
        ("sheet_piles", False),
        ("demolition", False),
        ("pile_cages", False),
        ("pile_concrete", False),
        ("pile_heads", False),
        ("front_beam", False),
        ("rear_beam", True),
        ("transverse_beam", True),
        ("slab", False),
        ("approach_slab", True),
        *((w, False) for w in furniture_works(project, section)),
        ("dredging", False),
    ]
    out = []
    for work, together in order:
        if work in BUILDS and BUILDS[work][0] not in kinds:
            continue
        if work == "demolition" and not ex.use:
            continue
        if work == "approach_slab" and project.approach is None:
            continue
        # "At the same time" only when the step before it is kept.
        out.append(SequenceStep(work=work, with_previous=together and bool(out)))
    return out


def steps(project: Project, section: Section) -> tuple[list[SequenceStep], bool]:
    """The section's steps, and whether they are the default."""
    own = section.sequence.steps
    return (list(own), False) if own else (default_steps(project, section), True)


def step_name(step: SequenceStep, project: Project, section: Section) -> str:
    if step.name.strip():
        return step.name.strip()
    name = NAMES[step.work].format(above=f"{section.sequence.cast_above:g}")
    if step.work == "demolition" and section.existing.system == "gravity_wall":
        name = "Demolish the existing front beam (the blocks and quarry run stay)"
    return name


def stages(project: Project, section: Section, geometry: list[dict[str, Any]]) -> dict[str, Any]:
    """The stages to play, each with the state of every element after it."""
    own, default = steps(project, section)
    by_kind: dict[str, list[str]] = {}
    for g in geometry:
        k = _kind(section.elements.get(g["element"]))
        if k:
            by_kind.setdefault(k, []).append(g["element"])
    ex = section.existing
    before = ex.dredge_level
    after = section.site.seabed_level
    state: dict[str, str] = {}
    demolished = False
    installed: set[str] = set()
    approach = False
    seabed = before
    groups: list[list[int]] = []
    for i, st in enumerate(own):
        if st.with_previous and groups:
            groups[-1].append(i)
        else:
            groups.append([i])
    out = []
    notes: list[str] = []
    for n, group in enumerate(groups, 1):
        active: list[str] = []
        names = []
        for i in group:
            st = own[i]
            names.append(step_name(st, project, section))
            if st.work in BUILDS:
                kind, to = BUILDS[st.work]
                for el in by_kind.get(kind, []):
                    state[el] = to
                    active.append(el)
            elif st.work == "demolition":
                demolished = True
            elif st.work == "furniture":
                installed.update(ALL_FURNITURE)
            elif st.work in FURNITURE:
                installed.update(FURNITURE[st.work][1])
            elif st.work == "approach_slab":
                approach = True
            elif st.work == "dredging":
                seabed = after
        out.append(
            {
                "stage": n,
                "steps": [
                    {"index": i, "work": own[i].work, "name": nm} for i, nm in zip(group, names, strict=True)
                ],
                "elements": dict(state),
                "active": active,
                "demolished": demolished,
                "furniture": sorted(installed),
                "approach_slab": approach,
                "seabed": seabed,
            }
        )
    missing = sorted(
        g["element"]
        for g in geometry
        if _kind(section.elements.get(g["element"])) and g["element"] not in state
    )
    if missing:
        notes.append(f"Not built by any step: {', '.join(missing)}.")
    if not any(s.work == "dredging" for s in own):
        notes.append("No dredging step: the seabed stays at the existing dredge level.")
    notes.append(
        f"Seabed {before:g} m (existing dredge level) until the dredging, then {after:g} m. Piles are cast "
        f"{section.sequence.cast_above:g} m above their cut-off level and broken down to it in "
        "their own step."
    )
    if project.approach is not None and not any(g["element"] == APPROACH for g in geometry):
        notes.append("The approach slab is not in the Plaxis model: it is listed but not drawn.")
    return {
        "default": default,
        "steps": [s.model_dump(mode="json") | {"name": step_name(s, project, section)} for s in own],
        "works": {k: NAMES[k].format(above=f"{section.sequence.cast_above:g}") for k in NAMES},
        "stages": out,
        "cast_above": section.sequence.cast_above,
        "notes": notes,
    }
