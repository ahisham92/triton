"""Project inputs: everything the design needs that is not in the Plaxis workbook.

Dimensions of sections are in mm, levels in m (same datum as the Plaxis model),
stresses in MPa. Each element in the workbook (e.g. ``Pile(1)``) gets one
element input whose ``kind`` matches its element type.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .durability import en1992_covers, en1993_5_corrosion
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
    links: Literal["unified", "zoned"] = Field(
        "unified",
        title="Links along the pile",
        description="Unified: one spacing over the whole pile, the closest one needed anywhere. "
        "Zoned: closer links only where shear, the slab connection or laps need them.",
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


class Materials(_Model):
    """Project grades. Each element uses these unless it sets its own."""

    concrete: ConcreteGrade = Field("C40/50", title="Concrete grade (piles, slabs, beams)")
    infill_concrete: ConcreteGrade = Field("C32/40", title="Combi wall infill concrete grade")
    structural_steel: SteelGrade = Field("S355", title="Tube and casing steel grade")
    sheet_pile_steel: SheetPileGrade = Field("S355GP", title="Sheet pile steel grade")


_EN_COVERS = en1992_covers(50)
_EN_CORROSION = en1993_5_corrosion(50)


class Covers(_Model):
    """Project covers to the outer bars' links. Each element uses these unless it sets its own."""

    piles: float = _mm("Piles", _EN_COVERS["piles"], gt=0, description="EN 1992: XS3, at least 75 mm.")
    combi_infill: float = _mm(
        "Combi wall infill", _EN_COVERS["combi_infill"], gt=0, description="EN 1992: XS2, inside the tube."
    )
    slab_top: float = _mm("Slab top", _EN_COVERS["slab_top"], gt=0, description="EN 1992: XS1.")
    slab_bottom: float = _mm("Slab bottom", _EN_COVERS["slab_bottom"], gt=0, description="EN 1992: XS3.")
    beams: float = _mm("Beams", _EN_COVERS["beams"], gt=0, description="EN 1992: XS3.")


class CorrosionAllowances(_Model):
    """Loss of steel thickness over the design life. Each element uses these unless it sets its own."""

    casing: float = _mm(
        "Pile casing", _EN_CORROSION["casing"], ge=0, description="EN 1993-5: sea water, zone of high attack."
    )
    combi_tube: float = _mm(
        "Combi wall tube",
        _EN_CORROSION["combi_tube"],
        ge=0,
        description="EN 1993-5: sea water, zone of high attack.",
    )
    sheet_pile_per_face: float = _mm(
        "Sheet piles, per face",
        _EN_CORROSION["sheet_pile_per_face"],
        ge=0,
        description="EN 1993-5: sea water, permanent immersion or intertidal.",
    )


class Durability(_Model):
    cover_code: Literal["en1992", "bs6349"] = Field(
        "en1992",
        title="Covers from",
        description="Choosing the code or changing the design life fills in the covers below; "
        "they can still be edited.",
    )
    corrosion_code: Literal["en1993_5", "bs6349"] = Field(
        "en1993_5",
        title="Corrosion allowances from",
        description="Choosing the code or changing the design life fills in the allowances below.",
    )
    covers: Covers = Field(default_factory=Covers, title="Covers")
    corrosion: CorrosionAllowances = Field(default_factory=CorrosionAllowances, title="Corrosion allowances")


class Cracking(_Model):
    """Crack widths under QP loads, and cracking from restrained temperature and shrinkage."""

    creep_coefficient: float = Field(
        2.0,
        title="Creep coefficient φ for QP stresses",
        ge=0,
        le=5,
        description="Cracked-section stresses under QP loads use Ec,eff = Ecm / (1 + φ).",
    )
    early_age_drop: float = Field(
        30.0,
        title="Early-age temperature drop T1",
        ge=0,
        json_schema_extra={"unit": "°C"},
        description="Peak hydration temperature to ambient (CIRIA C660). About 25 to 35 °C for 1 to 2 m "
        "sections with blended cements.",
    )
    seasonal_drop: float = Field(
        20.0, title="Seasonal temperature drop T2", ge=0, json_schema_extra={"unit": "°C"}
    )
    thermal_expansion: float = Field(
        10.0, title="Coefficient of thermal expansion", gt=0, json_schema_extra={"unit": "µε/°C"}
    )
    creep_factor: float = Field(
        0.65, title="K1 creep factor on restrained strain", gt=0, le=1, description="CIRIA C660: 0.65."
    )


class DesignSettings(_Model):
    code: Literal["EN 1992 / EN 1993 + BS 6349"] = Field("EN 1992 / EN 1993 + BS 6349", title="Design code")
    design_life_years: int = Field(50, title="Design life", ge=1, json_schema_extra={"unit": "years"})
    materials: Materials = Field(default_factory=Materials, title="Project grades")
    durability: Durability = Field(default_factory=Durability, title="Covers and corrosion")
    partial_factors: PartialFactors = Field(default_factory=PartialFactors, title="Partial factors")
    reinforcement: ReinforcementSettings = Field(default_factory=ReinforcementSettings, title="Reinforcement")
    piles: PileReinforcement = Field(default_factory=PileReinforcement, title="Pile reinforcement")
    cracking: Cracking = Field(default_factory=Cracking, title="Cracking and restraint")
    plate_positive_moment: Literal["sagging", "hogging"] = Field(
        "sagging",
        title="Positive plate moments (M11, M22) in the workbook",
        description="Sagging: positive M puts the bottom face of slabs and beams in tension. In the sample "
        "the deck's M11 peaks negative at every pile head, so positive is sagging there.",
    )
    shear_check_distance: Literal["d", "2d"] = Field(
        "d", title="Shear checked at", description="Distance from the support face"
    )
    results_into_connection: float = _mm(
        "Results taken into the slab or beam above",
        100.0,
        ge=0,
        le=1000,
        description="Piles and king piles use Plaxis results up to this far above their top level, "
        "taken at the top level. Results higher up are FE peaks inside the connection and are ignored.",
    )


# --- Element inputs ----------------------------------------------------------


_PROJECT_GRADE = "Empty: the project grade from Design settings."
_PROJECT_VALUE = "Empty: the project value from Design settings (Covers and corrosion)."


class _ConcreteSection(_Model):
    concrete: ConcreteGrade | None = Field(None, title="Concrete grade", description=_PROJECT_GRADE)
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
    corrosion_loss: float | None = _mm(
        "Corrosion loss over design life", None, ge=0, description=_PROJECT_VALUE
    )
    steel: SteelGrade | None = Field(None, title="Casing steel grade", description=_PROJECT_GRADE)
    connection_bar_count: int | None = Field(
        None,
        title="Bars welded to the casing at its top",
        ge=0,
        description="Structural casing only. Where the casing stops, these bars (cover 0, welded to the "
        "pipe) and the cage carry the forces with no help from the casing.",
    )
    connection_bar_diameter: int = Field(32, title="Welded bar diameter", json_schema_extra={"unit": "mm"})
    connection_length: float = _m(
        "Connection zone length",
        0.5,
        gt=0,
        description="Length checked without the casing, from the casing top upward (or the top of the pile "
        "downward when the casing reaches it).",
    )

    @model_validator(mode="after")
    def _levels(self) -> Casing:
        if self.bottom_level >= self.top_level:
            raise ValueError("Casing bottom level must be below its top level.")
        if self.corrosion_loss is not None and self.corrosion_loss >= self.thickness:
            raise ValueError("Corrosion loss must be less than the casing thickness.")
        return self


class PileInput(_ConcreteSection):
    kind: Literal["pile"] = "pile"
    diameter: float = _mm("Pile diameter", 1200.0, gt=0)
    cover: float | None = _mm("Cover to links", None, gt=0, description=_PROJECT_VALUE)
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
        "Top level of the pile (slab soffit)",
        None,
        description="Results more than the distance set in Design settings (default 10 cm) above it are "
        "inside the slab and ignored. Empty: every result is used.",
    )
    casing: Casing | None = Field(
        None, title="Steel casing", description="Leave empty for a plain concrete pile."
    )


class CombiWallInput(_Model):
    kind: Literal["combi_wall"] = "combi_wall"
    tube_diameter: float = _mm("King pile tube diameter", 1626.0, gt=0)
    tube_thickness: float = _mm("Tube wall thickness", 18.0, gt=0)
    corrosion_loss: float | None = _mm(
        "Corrosion loss over design life", None, ge=0, description=_PROJECT_VALUE
    )
    steel: SteelGrade | None = Field(None, title="Tube steel grade", description=_PROJECT_GRADE)
    concrete: ConcreteGrade | None = Field(None, title="Infill concrete grade", description=_PROJECT_GRADE)
    concrete_bottom_level: float = _m("Concrete infill bottom level", -25.0)
    top_level_to_ignore: float | None = _m(
        "Top level of the king pile (front beam soffit)",
        None,
        description="Results more than the distance set in Design settings (default 10 cm) above it are "
        "inside the front beam and ignored. Empty: every result is used.",
    )
    cover: float | None = _mm("Cover to infill links", None, gt=0, description=_PROJECT_VALUE)
    link_diameter: float = _mm("Infill link diameter", 12.0, gt=0)
    bar_count: int | None = Field(
        None,
        title="Infill bars in the outer row",
        ge=6,
        description="Leave empty to let Triton choose, or fix it.",
    )
    count: int | None = Field(
        None,
        title="Number of king piles",
        ge=1,
        description="King piles in the section. Empty: counted from the workbook.",
    )
    fabrication_class: Literal["A", "B", "C"] = Field(
        "B",
        title="Tube fabrication quality class",
        description="EN 1993-1-6 Table D.1, for local buckling below the infill (A excellent, B high, "
        "C normal).",
    )

    @model_validator(mode="after")
    def _tube(self) -> CombiWallInput:
        if self.tube_diameter <= 2 * self.tube_thickness:
            raise ValueError("Tube thickness must be less than half the diameter.")
        if self.corrosion_loss is not None and self.corrosion_loss >= self.tube_thickness:
            raise ValueError("Corrosion loss must be less than the tube thickness.")
        return self


class SheetPileInput(_Model):
    kind: Literal["sheet_pile_wall"] = "sheet_pile_wall"
    section_name: str = Field("", title="Sheet pile section", description="e.g. AZ 26-700")
    steel: SheetPileGrade | None = Field(None, title="Steel grade", description=_PROJECT_GRADE)
    area: float | None = Field(None, title="Area per m", gt=0, json_schema_extra={"unit": "cm²/m"})
    elastic_modulus: float | None = Field(
        None, title="Elastic section modulus Wel per m", gt=0, json_schema_extra={"unit": "cm³/m"}
    )
    plastic_modulus: float | None = Field(
        None, title="Plastic section modulus Wpl per m", gt=0, json_schema_extra={"unit": "cm³/m"}
    )
    section_class: Literal[1, 2, 3, 4] = Field(2, title="Section class")
    corrosion_loss_per_face: float | None = _mm(
        "Corrosion loss per face", None, ge=0, description=_PROJECT_VALUE
    )


class SlabMesh(_Model):
    diameter: int = Field(16, title="Bar diameter", json_schema_extra={"unit": "mm"})
    spacing: float = _mm("Spacing", 150.0, gt=0)
    layers: int = Field(1, title="Layers", ge=1, le=2)


class PunchingDepth(_Model):
    x: float = _m("Pile X", 0.0)
    y: float = _m("Pile Y", 0.0)
    thickness: float = _mm("Slab thickness at the pile", 700.0, gt=0)


class CraneArea(_Model):
    """Factored mobile crane minus factored live load from the SAP model, per metre, over a plan area."""

    x_from: float = _m("X from", 0.0)
    x_to: float = _m("X to", 0.0)
    y_from: float = _m("Y from", 0.0)
    y_to: float = _m("Y to", 0.0)
    mx: float = Field(0.0, title="Mx", json_schema_extra={"unit": "kNm/m"})
    my: float = Field(0.0, title="My", json_schema_extra={"unit": "kNm/m"})
    mxy: float = Field(0.0, title="Mxy", json_schema_extra={"unit": "kNm/m"})
    vx: float = Field(0.0, title="Vx", json_schema_extra={"unit": "kN/m"})
    vy: float = Field(0.0, title="Vy", json_schema_extra={"unit": "kN/m"})
    nx: float = Field(0.0, title="Nx (compression +)", json_schema_extra={"unit": "kN/m"})
    ny: float = Field(0.0, title="Ny (compression +)", json_schema_extra={"unit": "kN/m"})


class SlabInput(_ConcreteSection):
    kind: Literal["slab"] = "slab"
    thickness: float = _mm("Slab thickness", 1000.0, gt=0)
    cover_top: float | None = _mm("Top cover", None, gt=0, description=_PROJECT_VALUE)
    cover_bottom: float | None = _mm("Bottom cover", None, gt=0, description=_PROJECT_VALUE)
    strips: Literal["uniform", "column_and_field"] = Field(
        "uniform", title="Reinforcement layout", description="One uniform slab, or column and field strips"
    )
    zone_size: float = _m(
        "Zone grid",
        1.0,
        gt=0.2,
        le=5,
        description="Cells of this size carry either the basic mesh or heavier bars in a zone.",
    )
    peaks: Literal["design", "average"] = Field(
        "design",
        title="Moments at the pile faces",
        description="Design the peaks at the pile faces as they are, or average them over a ring one "
        "pile diameter wide round each pile.",
    )
    min_zone_length: float = _m(
        "Shortest zone", 2.5, gt=0, description="Shortest length of a zone of heavier bars along its bars."
    )
    mesh_bottom_x: SlabMesh | None = Field(
        None, title="Basic mesh, bottom along X", description="Empty: the mesh with the least steel."
    )
    mesh_bottom_y: SlabMesh | None = Field(
        None, title="Basic mesh, bottom along Y", description="Empty: the mesh with the least steel."
    )
    mesh_top_x: SlabMesh | None = Field(
        None, title="Basic mesh, top along X", description="Empty: the mesh with the least steel."
    )
    mesh_top_y: SlabMesh | None = Field(
        None, title="Basic mesh, top along Y", description="Empty: the mesh with the least steel."
    )
    punching_thickness: float | None = _mm(
        "Thickness for punching",
        None,
        gt=0,
        description="Sloped slab: the depth at the piles when it differs (e.g. 720 mm with 700 mm for "
        "bending). Empty: the slab thickness. Single piles can be set in the punching results.",
    )
    punching_depths: list[PunchingDepth] = Field(
        default_factory=list,
        title="Slab thickness at single piles",
        description="For punching only, e.g. on a slope.",
    )
    crane: list[CraneArea] = Field(
        default_factory=list,
        title="Mobile crane additions",
        description="From the SAP model: factored crane minus factored live load, per metre, added to every "
        "ULS combination over each area.",
    )
    joint_spacing: float = _m(
        "Length between movement joints",
        30.0,
        gt=0,
        description="For the temperature and shrinkage restraint check (restraint from length / thickness).",
    )
    restraint_factor: float | None = Field(
        None,
        title="Restraint factor R",
        ge=0,
        le=1,
        description="Empty: from the length between joints and the thickness (ACI 207.2R).",
    )
    crack_width_limit: float = _mm("Crack width limit wk (QP), top face", 0.3, gt=0, le=0.5)
    crack_width_limit_bottom: float = _mm(
        "Crack width limit wk (QP), bottom face",
        0.3,
        gt=0,
        le=0.5,
        description="e.g. tighter where the soffit is in the splash zone",
    )


class BeamInput(_ConcreteSection):
    kind: Literal["front_beam", "rear_beam", "transverse_beam"] = "front_beam"
    width: float | None = _mm(
        "Beam width", None, gt=0, description="Empty: the beam's width in the Plaxis model."
    )
    depth: float = _mm("Beam depth", 2000.0, gt=0)
    cover: float | None = _mm("Cover to links", None, gt=0, description=_PROJECT_VALUE)
    link_diameter: float = _mm("Link diameter", 16.0, gt=0)
    joint_spacing: float = _m(
        "Length between movement joints",
        30.0,
        gt=0,
        description="For the temperature and shrinkage restraint check (restraint from length / depth).",
    )
    restraint_factor: float | None = Field(
        None,
        title="Restraint factor R",
        ge=0,
        le=1,
        description="Empty: from the length between joints and the depth (edge restraint, ACI 207.2R).",
    )
    crack_width_limit: float = _mm("Crack width limit wk (QP), top face", 0.3, gt=0, le=0.5)
    crack_width_limit_bottom: float = _mm(
        "Crack width limit wk (QP), bottom face",
        0.3,
        gt=0,
        le=0.5,
        description="e.g. tighter where the soffit is in the splash zone",
    )


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
    ElementType.TRANSVERSE_BEAM: BeamInput,
}


def with_project_grades(element: Any, materials: Materials, durability: Durability | None = None) -> Any:
    """A copy of ``element`` with every unset grade, cover and corrosion allowance taken from the project."""
    d = durability or Durability()
    cv, co = d.covers, d.corrosion
    update: dict[str, Any] = {}
    if isinstance(element, CombiWallInput):
        update = {"concrete": element.concrete or materials.infill_concrete}
        update["steel"] = element.steel or materials.structural_steel
        update["cover"] = _or(element.cover, cv.combi_infill)
        update["corrosion_loss"] = _or(element.corrosion_loss, co.combi_tube)
    elif isinstance(element, SheetPileInput):
        update = {"steel": element.steel or materials.sheet_pile_steel}
        update["corrosion_loss_per_face"] = _or(element.corrosion_loss_per_face, co.sheet_pile_per_face)
    elif isinstance(element, _ConcreteSection):
        update = {"concrete": element.concrete or materials.concrete}
        if isinstance(element, PileInput):
            update["cover"] = _or(element.cover, cv.piles)
        elif isinstance(element, SlabInput):
            update["cover_top"] = _or(element.cover_top, cv.slab_top)
            update["cover_bottom"] = _or(element.cover_bottom, cv.slab_bottom)
        elif isinstance(element, BeamInput):
            update["cover"] = _or(element.cover, cv.beams)
        casing = getattr(element, "casing", None)
        if casing is not None:
            update["casing"] = casing.model_copy(
                update={
                    "steel": casing.steel or materials.structural_steel,
                    "corrosion_loss": _or(casing.corrosion_loss, co.casing),
                }
            )
    return element.model_copy(update=update)


def pile_cover(pile: PileInput, settings: DesignSettings) -> float:
    """The pile's cover to links, or the project cover when it has none of its own."""
    return settings.durability.covers.piles if pile.cover is None else pile.cover


def _or(value: float | None, default: float) -> float:
    return default if value is None else value


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
    x_min: float | None = _m(
        "Working zone: X from", None, description="Results outside the working zone are not used (FE edges)."
    )
    x_max: float | None = _m("Working zone: X to", None)
    y_min: float | None = _m("Working zone: Y from", None)
    y_max: float | None = _m("Working zone: Y to", None)
    peaks: Literal["raw", "average"] = Field(
        "raw",
        title="Isolated peaks",
        description="Raw: use the values as they are. Average: replace a peak by the mean of the nodes "
        "above and below it.",
    )
    peak_ratio: float = Field(
        1.5,
        title="A peak is a moment above both neighbours by",
        gt=1,
        le=10,
        json_schema_extra={"unit": "×"},
    )
    excluded_peaks: list[str] = Field(
        default_factory=list, title="Peaks left out", description="element|combination|node"
    )
    elements: dict[str, ElementInput] = Field(default_factory=dict, title="Elements")
    load_factors: list[LoadFactor] = Field(default_factory=list, title="Load multipliers")

    @model_validator(mode="after")
    def _zone(self) -> Section:
        for lo, hi, axis in ((self.x_min, self.x_max, "X"), (self.y_min, self.y_max, "Y")):
            if lo is not None and hi is not None and lo >= hi:
                raise ValueError(f"Working zone: {axis} from must be less than {axis} to.")
        return self

    def in_zone(self, x: Any, y: Any) -> Any:
        """Boolean mask (or bool) of points inside the working zone."""
        ok = True
        for v, lo, hi in ((x, self.x_min, self.x_max), (y, self.y_min, self.y_max)):
            if lo is not None:
                ok = ok & (v >= lo - 1e-9)
            if hi is not None:
                ok = ok & (v <= hi + 1e-9)
        return ok

    @property
    def has_zone(self) -> bool:
        return any(v is not None for v in (self.x_min, self.x_max, self.y_min, self.y_max))

    @model_validator(mode="before")
    @classmethod
    def _drop_slab_soffit(cls, data: Any) -> Any:
        """Sections saved with a shared slab soffit level: it becomes the top level of their piles."""
        if not isinstance(data, dict) or "slab_soffit_level" not in data:
            return data
        data = dict(data)
        soffit = data.pop("slab_soffit_level")
        if soffit is not None:
            elements = {}
            for name, el in (data.get("elements") or {}).items():
                if isinstance(el, dict) and el.get("kind") == "pile" and el.get("head_level") is None:
                    el = {**el, "head_level": soffit}
                elements[name] = el
            data["elements"] = elements
        return data

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
