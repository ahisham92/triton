"""Project inputs: everything the design needs that is not in the Plaxis workbook.

Dimensions of sections are in mm, levels in m (same datum as the Plaxis model),
stresses in MPa. Each element in the workbook (e.g. ``Pile(1)``) gets one
element input whose ``kind`` matches its element type.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .elements import ElementType, parse_sheet_name
from .materials import (
    BAR_DIAMETERS,
    CONCRETE_GRADES,
    REINFORCEMENT_GRADES,
    SHEET_PILE_GRADES,
    STRUCTURAL_STEEL_GRADES,
)

ConcreteGrade = Literal[tuple(CONCRETE_GRADES)]  # type: ignore[valid-type]
RebarGrade = Literal[tuple(REINFORCEMENT_GRADES)]  # type: ignore[valid-type]
SteelGrade = Literal[tuple(STRUCTURAL_STEEL_GRADES)]  # type: ignore[valid-type]
SheetPileGrade = Literal[tuple(SHEET_PILE_GRADES)]  # type: ignore[valid-type]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _mm(title: str, default: float | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "mm"}, **kw)


def _m(title: str, default: float | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "m"}, **kw)


# --- Project-wide settings -------------------------------------------------


class ProjectInfo(_Model):
    name: str = Field("New project", title="Project name", min_length=1)
    number: str = Field("", title="Project number")
    client: str = Field("", title="Client")
    location: str = Field("", title="Location")
    section: str = Field("", title="Section / area", description="e.g. Section 01a")
    designer: str = Field("", title="Designed by")
    checker: str = Field("", title="Checked by")


class PartialFactors(_Model):
    gamma_c: float = Field(1.5, title="γc concrete", gt=1)
    gamma_s: float = Field(1.15, title="γs reinforcement", gt=1)
    gamma_c_accidental: float = Field(1.2, title="γc accidental / seismic", ge=1)
    gamma_s_accidental: float = Field(1.0, title="γs accidental / seismic", ge=1)
    gamma_m0: float = Field(1.0, title="γM0 steel cross-section", ge=1)
    gamma_m1: float = Field(1.0, title="γM1 steel buckling", ge=1)
    alpha_cc: float = Field(0.85, title="αcc long-term factor", gt=0, le=1)


class ReinforcementSettings(_Model):
    grade: RebarGrade = Field("B500B", title="Reinforcement grade")
    bar_diameters: list[int] = Field(
        default_factory=lambda: [16, 20, 25, 32, 40],
        title="Bar sizes to try",
        json_schema_extra={"unit": "mm"},
    )
    min_clear_spacing: float = _mm("Minimum clear spacing between bars", 50.0, gt=0)
    max_spacing: float = _mm("Maximum bar spacing", 250.0, gt=0)
    spacing_step: float = _mm("Spacing increment", 25.0, gt=0)
    max_layers: int = Field(2, title="Maximum bar layers per face", ge=1, le=3)
    objective: Literal["min_steel", "min_cost"] = Field(
        "min_steel", title="Choose arrangement by", description="Least steel ratio or lowest cost"
    )
    rebar_cost_per_tonne: float = Field(0.0, title="Reinforcement cost per tonne", ge=0)
    cost_per_bar_placed: float = Field(0.0, title="Placing cost per bar (per m)", ge=0)

    @field_validator("bar_diameters")
    @classmethod
    def _known_bars(cls, v: list[int]) -> list[int]:
        bad = [d for d in v if d not in BAR_DIAMETERS]
        if bad:
            raise ValueError(f"Unknown bar size(s): {bad}. Available: {BAR_DIAMETERS}")
        if not v:
            raise ValueError("Pick at least one bar size.")
        return sorted(set(v))


class DesignSettings(_Model):
    code: Literal["EN 1992 / EN 1993 + BS 6349"] = Field("EN 1992 / EN 1993 + BS 6349", title="Design code")
    design_life_years: int = Field(50, title="Design life", ge=1, json_schema_extra={"unit": "years"})
    partial_factors: PartialFactors = Field(default_factory=PartialFactors, title="Partial factors")
    reinforcement: ReinforcementSettings = Field(default_factory=ReinforcementSettings, title="Reinforcement")
    shear_check_distance: Literal["d", "2d"] = Field(
        "d", title="Shear checked at", description="Distance from the support face"
    )


# --- Element inputs ----------------------------------------------------------


class _ConcreteSection(_Model):
    concrete: ConcreteGrade = Field("C40/50", title="Concrete grade")
    crack_width_limit: float = _mm("Crack width limit wk (QP)", 0.3, gt=0, le=0.5)


class Casing(_Model):
    """Permanent steel casing around a pile over part of its length."""

    role: Literal["crack_only", "structural"] = Field(
        "crack_only",
        title="Casing role",
        description="Crack width only: the casing removes the crack width check, the concrete "
        "carries all forces. Structural: the casing works with the concrete and forces are "
        "shared by E·I, as in the combi wall.",
    )
    top_level: float = _m("Casing top level", 2.7)
    bottom_level: float = _m("Casing bottom level", -1.3)
    thickness: float = _mm("Casing wall thickness", 16.0, gt=0)
    corrosion_loss: float = _mm("Corrosion loss over design life", 2.0, ge=0)
    steel: SteelGrade = Field("S355", title="Casing steel grade")

    @model_validator(mode="after")
    def _levels(self) -> Casing:
        if self.bottom_level >= self.top_level:
            raise ValueError("Casing bottom level must be below its top level.")
        if self.corrosion_loss >= self.thickness:
            raise ValueError("Corrosion loss must be less than the casing thickness.")
        return self


class PileInput(_ConcreteSection):
    kind: Literal["pile"] = "pile"
    diameter: float = _mm("Pile diameter", 1200.0, gt=0)
    cover: float = _mm("Cover to links", 75.0, gt=0)
    link_diameter: float = _mm("Link diameter", 12.0, gt=0)
    head_level: float | None = _m(
        "Pile head level (slab soffit)",
        None,
        description="Results above this level are inside the slab and are ignored.",
    )
    casing: Casing | None = Field(
        None, title="Steel casing", description="Leave empty for a plain concrete pile."
    )


class CombiWallInput(_Model):
    kind: Literal["combi_wall"] = "combi_wall"
    tube_diameter: float = _mm("King pile tube diameter", 1626.0, gt=0)
    tube_thickness: float = _mm("Tube wall thickness", 18.0, gt=0)
    corrosion_loss: float = _mm("Corrosion loss over design life", 3.0, ge=0)
    steel: SteelGrade = Field("S355", title="Tube steel grade")
    concrete: ConcreteGrade = Field("C32/40", title="Infill concrete grade")
    concrete_bottom_level: float = _m("Concrete infill bottom level", -25.0)
    top_level_to_ignore: float | None = _m(
        "Front beam soffit level",
        None,
        description="Results above this level are inside the front beam and are ignored.",
    )
    cover: float = _mm("Cover to infill reinforcement", 75.0, gt=0)

    @model_validator(mode="after")
    def _tube(self) -> CombiWallInput:
        if self.tube_diameter <= 2 * self.tube_thickness:
            raise ValueError("Tube thickness must be less than half the diameter.")
        if self.corrosion_loss >= self.tube_thickness:
            raise ValueError("Corrosion loss must be less than the tube thickness.")
        return self


class SheetPileInput(_Model):
    kind: Literal["sheet_pile_wall"] = "sheet_pile_wall"
    section_name: str = Field("", title="Sheet pile section", description="e.g. AZ 26-700")
    steel: SheetPileGrade = Field("S355GP", title="Steel grade")
    area: float | None = Field(None, title="Area per m", gt=0, json_schema_extra={"unit": "cm²/m"})
    elastic_modulus: float | None = Field(
        None, title="Elastic section modulus Wel per m", gt=0, json_schema_extra={"unit": "cm³/m"}
    )
    plastic_modulus: float | None = Field(
        None, title="Plastic section modulus Wpl per m", gt=0, json_schema_extra={"unit": "cm³/m"}
    )
    section_class: Literal[1, 2, 3, 4] = Field(2, title="Section class")
    corrosion_loss_per_face: float = _mm("Corrosion loss per face", 2.0, ge=0)


class SlabInput(_ConcreteSection):
    kind: Literal["slab"] = "slab"
    thickness: float = _mm("Slab thickness", 1000.0, gt=0)
    cover_top: float = _mm("Top cover", 75.0, gt=0)
    cover_bottom: float = _mm("Bottom cover", 75.0, gt=0)
    strips: Literal["uniform", "column_and_field"] = Field(
        "uniform", title="Reinforcement layout", description="One uniform slab, or column and field strips"
    )


class BeamInput(_ConcreteSection):
    kind: Literal["front_beam", "rear_beam"] = "front_beam"
    width: float = _mm("Beam width", 2000.0, gt=0)
    depth: float = _mm("Beam depth", 2000.0, gt=0)
    cover: float = _mm("Cover", 75.0, gt=0)


ElementInput = Annotated[
    PileInput | CombiWallInput | SheetPileInput | SlabInput | BeamInput,
    Field(discriminator="kind"),
]

_KIND_FOR_TYPE = {
    ElementType.PILE: PileInput,
    ElementType.COMBI_WALL: CombiWallInput,
    ElementType.SHEET_PILE_WALL: SheetPileInput,
    ElementType.SLAB: SlabInput,
    ElementType.FRONT_BEAM: BeamInput,
    ElementType.REAR_BEAM: BeamInput,
}


def default_element(name: str) -> ElementInput | None:
    """Default inputs for an element found in the workbook (e.g. 'Pile(2)')."""
    parsed = parse_sheet_name(f"{name}-X")
    if parsed is None:
        return None
    cls = _KIND_FOR_TYPE.get(parsed.spec.type)
    if cls is None:
        return None
    if cls is BeamInput:
        return BeamInput(kind=parsed.spec.type.value)
    return cls()


# --- Project -------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Project(_Model):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    info: ProjectInfo = Field(default_factory=ProjectInfo, title="Project")
    design: DesignSettings = Field(default_factory=DesignSettings, title="Design settings")
    elements: dict[str, ElementInput] = Field(default_factory=dict, title="Elements")

    @field_validator("id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{6,32}", v):
            raise ValueError("Invalid project id.")
        return v

    @field_validator("elements")
    @classmethod
    def _names_match_kinds(cls, v: dict[str, ElementInput]) -> dict[str, ElementInput]:
        for name, element in v.items():
            parsed = parse_sheet_name(f"{name}-X")
            if parsed is None:
                raise ValueError(f"'{name}' is not a known element name (e.g. Pile(1), Deck, Combi Wall).")
            if parsed.spec.type.value != element.kind:
                raise ValueError(f"'{name}' is a {parsed.spec.type.value}, not a {element.kind}.")
        return v

    def add_elements(self, names: list[str]) -> list[str]:
        """Add default inputs for workbook elements not yet in the project."""
        added = []
        elements = dict(self.elements)
        for name in names:
            if name in elements:
                continue
            element = default_element(name)
            if element is not None:
                elements[name] = element
                added.append(name)
        self.elements = elements
        return added
