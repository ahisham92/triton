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
        default_factory=lambda: [10, 12, 16, 20, 25, 32],
        title="Bars available on this project",
        description="Only these sizes are used in any design.",
        json_schema_extra={"unit": "mm"},
    )
    min_clear_spacing: float = _mm("Minimum clear spacing (slabs and beams)", 50.0, gt=0)
    max_spacing: float = _mm("Maximum bar spacing (slabs and beams)", 250.0, gt=0)
    spacing_step: float = _mm("Spacing increment (slabs and beams)", 25.0, gt=0)
    max_layers: int = Field(2, title="Maximum bar layers per face (slabs and beams)", ge=1, le=3)
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


RowOption = Literal[1, 1.5, 2, 2.5, 3]


class PileReinforcement(_Model):
    even_bar_count: bool = Field(True, title="Even number of bars in each row")
    aggregate_size: float = _mm("Maximum aggregate size dg", 20.0, gt=0)
    min_clear_spacing: float = _mm(
        "Minimum clear spacing between bars",
        80.0,
        gt=0,
        description="Project value, between neighbouring bars of every row, measured around the circle. "
        "Never less than EN 1992-1-1 8.2(2): max(φ, dg + 5 mm, 20 mm).",
    )
    max_clear_spacing: float = _mm(
        "Maximum clear spacing between bars",
        200.0,
        gt=0,
        le=200,
        description="Outer row, around the periphery. EN 1992-1-1 9.8.5(3): at most 200 mm.",
    )
    row_clear_spacing: float | None = _mm(
        "Clear gap between rows",
        None,
        gt=0,
        description="Radial gap between rows. Empty: the EN 1992-1-1 8.2(2) minimum "
        "max(φ, dg + 5 mm, 20 mm) for the larger bar.",
    )
    rows: list[RowOption] = Field(
        default_factory=lambda: [1, 1.5, 2, 2.5, 3],
        title="Rows allowed",
        description="1.5 rows is a full outer row plus half as many bars behind every second bar, "
        "e.g. 26Ø32 + 13Ø16.",
    )
    extra_rows_only_when_needed: bool = Field(
        True,
        title="Use extra rows only when one row is not enough",
        description="Off: pick the lightest cage even if it has more rows.",
    )
    splice: Literal["lap", "coupler"] = Field("lap", title="Bar splices")
    lap_factor: float = Field(
        45.0,
        title="Lap length",
        gt=0,
        description="Lap length as a multiple of the bar diameter (45 gives 45φ).",
        json_schema_extra={"unit": "φ"},
    )
    max_steel_ratio: float = Field(
        4.0,
        title="Maximum steel ratio",
        gt=0,
        le=8,
        description="EN 1992-1-1 9.5.2(3): 4% outside laps and 8% at laps (recommended values). "
        "Above 4% only with couplers.",
        json_schema_extra={"unit": "%"},
    )
    curtail: bool = Field(True, title="Reduce the reinforcement down the pile")
    curtailment: Literal["least_steel", "standard_lengths"] = Field(
        "least_steel",
        title="Choose the zones for",
        description="Least steel with zones of at least the minimum length, or bar lengths from the "
        "standard cut lengths where possible.",
    )
    min_zone_length: float = _m("Minimum zone length", 3.0, gt=0)
    standard_bar_lengths: list[float] = Field(
        default_factory=lambda: [6.0, 8.0, 9.0, 12.0],
        title="Standard cut lengths",
        json_schema_extra={"unit": "m"},
    )
    max_bar_length: float = _m("Longest bar", 12.0, gt=0)

    @field_validator("rows")
    @classmethod
    def _some_rows(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("Allow at least one row option.")
        return sorted(set(v))

    @field_validator("standard_bar_lengths")
    @classmethod
    def _lengths(cls, v: list[float]) -> list[float]:
        if not v or any(x <= 0 for x in v):
            raise ValueError("Give at least one positive cut length.")
        return sorted(set(v))

    @model_validator(mode="after")
    def _checks(self) -> PileReinforcement:
        if self.min_clear_spacing >= self.max_clear_spacing:
            raise ValueError("The minimum clear spacing must be less than the maximum.")
        if self.max_steel_ratio > 4.0 and self.splice != "coupler":
            raise ValueError("A steel ratio above 4% needs couplers (EN 1992-1-1 9.5.2(3)).")
        if self.max_bar_length < self.min_zone_length:
            raise ValueError("The longest bar must be at least the minimum zone length.")
        return self


class DesignSettings(_Model):
    code: Literal["EN 1992 / EN 1993 + BS 6349"] = Field("EN 1992 / EN 1993 + BS 6349", title="Design code")
    design_life_years: int = Field(50, title="Design life", ge=1, json_schema_extra={"unit": "years"})
    partial_factors: PartialFactors = Field(default_factory=PartialFactors, title="Partial factors")
    reinforcement: ReinforcementSettings = Field(default_factory=ReinforcementSettings, title="Reinforcement")
    piles: PileReinforcement = Field(default_factory=PileReinforcement, title="Pile reinforcement")
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
    count: int | None = Field(
        None,
        title="Number of piles",
        ge=1,
        description="Piles of this type in the section. Empty: counted from the workbook.",
    )
    bar_count: int | None = Field(
        None,
        title="Bars in the outer row",
        ge=6,
        description="Leave empty to let Triton choose, or fix it (e.g. 26 for a 1200 mm pile).",
    )
    head_level: float | None = _m(
        "Pile head level (slab soffit)",
        None,
        description="Results above this level are inside the slab and are ignored. "
        "Empty: the section's slab soffit level.",
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


class LoadFactor(_Model):
    factor: float = Field(1.35, title="Multiplier", gt=0)
    sheets: list[str] = Field(
        default_factory=list,
        title="Sheets",
        description="Workbook sheets whose straining actions are multiplied. X, Y and Z are not changed.",
    )
    note: str = Field("", title="Note", description="e.g. Set B actions to design values")


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class Section(_Model):
    """One part of the structure with its own Plaxis workbook, e.g. Section 01a."""

    id: str = Field(default_factory=_short_id)
    name: str = Field("Section 1", title="Section name", min_length=1, description="e.g. Section 01a")
    slab_soffit_level: float | None = _m(
        "Slab soffit level",
        None,
        description="Pile head level for every pile of this section that has none of its own.",
    )
    elements: dict[str, ElementInput] = Field(default_factory=dict, title="Elements")
    load_factors: list[LoadFactor] = Field(default_factory=list, title="Load multipliers")

    @field_validator("id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{6,32}", v):
            raise ValueError("Invalid section id.")
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

    @field_validator("load_factors")
    @classmethod
    def _each_sheet_once(cls, v: list[LoadFactor]) -> list[LoadFactor]:
        seen: set[str] = set()
        for rule in v:
            twice = seen & set(rule.sheets)
            if twice:
                raise ValueError(f"Sheet(s) in more than one multiplier: {', '.join(sorted(twice))}.")
            seen |= set(rule.sheets)
        return v

    def factor_for(self, sheet: str) -> float:
        return next((r.factor for r in self.load_factors if sheet in r.sheets), 1.0)

    def add_elements(self, names: list[str]) -> list[str]:
        """Add default inputs for workbook elements not yet in the section."""
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


class Project(_Model):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    info: ProjectInfo = Field(default_factory=ProjectInfo, title="Project")
    design: DesignSettings = Field(default_factory=DesignSettings, title="Design settings")
    sections: list[Section] = Field(default_factory=lambda: [Section()], title="Sections", min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _one_section_projects(cls, data):
        """Projects saved before sections existed keep their elements as their first section."""
        if (
            isinstance(data, dict)
            and "sections" not in data
            and ("elements" in data or "load_factors" in data)
        ):
            data = dict(data)
            info = dict(data.get("info") or {})
            name = info.pop("section", "") or "Section 1"
            data["info"] = info
            data["sections"] = [
                {
                    "name": name,
                    "elements": data.pop("elements", {}),
                    "load_factors": data.pop("load_factors", []),
                }
            ]
        elif isinstance(data, dict) and isinstance(data.get("info"), dict) and "section" in data["info"]:
            data = dict(data)
            data["info"] = {k: v for k, v in data["info"].items() if k != "section"}
        return data

    @field_validator("id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        if not re.fullmatch(r"[a-f0-9]{6,32}", v):
            raise ValueError("Invalid project id.")
        return v

    @field_validator("sections")
    @classmethod
    def _unique_sections(cls, v: list[Section]) -> list[Section]:
        ids = [s.id for s in v]
        names = [s.name.strip().lower() for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("Two sections have the same id.")
        if len(set(names)) != len(names):
            raise ValueError("Two sections have the same name.")
        return v

    def section(self, section_id: str) -> Section:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise KeyError(section_id)
