"""Project inputs: everything the design needs that is not in the Plaxis workbook.

Dimensions of sections are in mm, levels in m (same datum as the Plaxis model),
stresses in MPa. Each element in the workbook (e.g. ``Pile(1)``) gets one
element input whose ``kind`` matches its element type.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import clock
from .design.sheet_piles import DEFAULT_SECTION as DEFAULT_SHEET_PILE
from .design.sheet_piles import SECTION_NAMES as SHEET_PILE_SECTIONS
from .design.sheet_piles import normalise as _normalise_sheet_pile
from .durability import bs6349_corrosion, bs6349_covers
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
SheetPileSection = Literal[tuple(SHEET_PILE_SECTIONS)]  # type: ignore[valid-type]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _mm(title: str, default: float | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "mm"}, **kw)


def _m(title: str, default: float | None = None, extra: dict | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "m", **(extra or {})}, **kw)


# A field shown on the form only while another field of the same object has one of these values.
STRUCTURAL_CASING = {"show_when": {"role": ["structural"]}}


# --- Project-wide settings -------------------------------------------------


class ProjectInfo(_Model):
    name: str = Field("New project", title="Project name", min_length=1)
    number: str = Field("", title="Project number")
    client: str = Field("", title="Client")
    location: str = Field("", title="Location")
    designer: str = Field("", title="Designed by")
    checker: str = Field("", title="Checked by")
    approver: str = Field("", title="Approved by")
    document_number: str = Field("", title="Calculation document number")
    revision: str = Field(
        "P01",
        title="Revision in work",
        description="Printed on the reports. Issue revision on the Project tab keeps a copy of it, dated.",
    )


class PartialFactors(_Model):
    gamma_c: float = Field(1.5, title="γc concrete", gt=1)
    gamma_s: float = Field(1.15, title="γs reinforcement", gt=1)
    gamma_c_accidental: float = Field(1.2, title="γc accidental / seismic", ge=1)
    gamma_s_accidental: float = Field(1.0, title="γs accidental / seismic", ge=1)
    gamma_m0: float = Field(
        1.1, title="γM0 steel cross-section", ge=1, description="The office sheets use 1.10 for king piles."
    )
    gamma_m1: float = Field(
        1.1, title="γM1 steel buckling", ge=1, description="EN 1993-5 and the office sheets: 1.1."
    )
    alpha_cc: float = Field(
        1.0,
        title="αcc long-term factor",
        gt=0,
        le=1,
        description="1.0 as in the AdSec files and capacity sheets.",
    )
    deduct_bar_area: bool = Field(
        False,
        title="Deduct the concrete displaced by the bars",
        description="Off (default): the gross concrete area, as AdSec and the capacity sheets take it.",
    )


class ReinforcementSettings(_Model):
    grade: RebarGrade = Field("B500B", title="Reinforcement grade")
    bar_diameters: list[int] = Field(
        default_factory=lambda: [10, 12, 16, 20, 25, 32],
        title="Bars available on this project",
        description="Only these sizes are used in any design.",
        json_schema_extra={"unit": "mm"},
    )
    min_clear_spacing: float = _mm("Minimum clear spacing (slabs and beams)", 50.0, gt=0)
    slab_min_clear_spacing: float = _mm(
        "Minimum clear spacing in slabs",
        32.0,
        gt=0,
        description="EC2 8.2(2): the larger bar's Ø, aggregate + 5 mm and 20 mm, at least; 32 mm lets Ø32 "
        "additional bars sit between Ø20 @ 150 mesh bars, as on the issued drawings.",
    )
    max_spacing: float = _mm("Maximum bar spacing (slabs and beams)", 250.0, gt=0)
    spacing_step: float = _mm("Spacing increment (slabs and beams)", 25.0, gt=0)
    slab_spacings: list[float] = Field(
        default_factory=lambda: [150.0, 200.0],
        title="Slab mesh spacings",
        description="The only spacings of slab meshes (additional bars go between the mesh bars). "
        "Empty: from the maximum spacing down in the spacing increment.",
        json_schema_extra={"unit": "mm"},
    )
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
    head_anchorage_factor: float = Field(
        45.0,
        title="Bars into the element above",
        ge=0,
        description="The pile bars run on above the pile head into the beam or slab over it, as a multiple "
        "of the bar diameter (45 gives 45φ); counted in bar lengths, weights and costs. 0 for none.",
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
    shear_depth: Literal["0.8D", "feltham"] = Field(
        "0.8D",
        title="Effective depth for shear",
        description="0.8D: d = 0.8 × diameter, as in the office pile shear sheets. "
        "Feltham: d = r + 2rs/π (about 5% less).",
    )
    hoop_legs: Literal["two_legs", "feltham"] = Field(
        "two_legs",
        title="A circular link counts as",
        description="Two legs: 2 × the bar area, as in the office pile shear sheets. "
        "Feltham: π/2 × the bar area (the hoop's component across the shear).",
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


_COVERS = bs6349_covers(50)
_CORROSION = bs6349_corrosion(50)


class Covers(_Model):
    """Project covers to the outer bars' links. Each element uses these unless it sets its own."""

    piles: float = _mm(
        "Piles", _COVERS["piles"], gt=0, description="BS 6349: 75 mm; EN 1992: XS3, at least 75 mm."
    )
    combi_infill: float = _mm(
        "Combi wall infill", _COVERS["combi_infill"], gt=0, description="BS 6349: 75 mm; EN 1992: XS2."
    )
    slab_top: float = _mm("Slab top", _COVERS["slab_top"], gt=0, description="BS 6349: 50 mm; EN 1992: XS1.")
    slab_bottom: float = _mm(
        "Slab bottom", _COVERS["slab_bottom"], gt=0, description="BS 6349: 50 mm; EN 1992: XS3."
    )
    beams: float = _mm("Beams", _COVERS["beams"], gt=0, description="BS 6349: 50 mm; EN 1992: XS3.")


class CorrosionAllowances(_Model):
    """Loss of steel thickness over the design life. Each element uses these unless it sets its own."""

    casing: float = _mm(
        "Pile casing",
        _CORROSION["casing"],
        ge=0,
        description="BS 6349-1-4:2021: splash zone. EN 1993-5: zone of high attack.",
    )
    combi_tube: float = _mm(
        "Combi wall tube",
        _CORROSION["combi_tube"],
        ge=0,
        description="BS 6349-1-4:2021: splash zone. EN 1993-5: zone of high attack.",
    )
    sheet_pile_per_face: float = _mm(
        "Sheet piles, per face",
        _CORROSION["sheet_pile_per_face"],
        ge=0,
        description="BS 6349-1-4:2021: continuous immersion. EN 1993-5: permanent immersion or intertidal.",
    )


class Durability(_Model):
    cover_code: Literal["en1992", "bs6349"] = Field(
        "bs6349",
        title="Covers from",
        description="Choosing the code or changing the design life fills in the covers below; "
        "they can still be edited.",
    )
    corrosion_code: Literal["en1993_5", "bs6349"] = Field(
        "bs6349",
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
    plate_positive_moment: Literal["auto", "sagging", "hogging"] = Field(
        "auto",
        title="Positive plate moments (M11, M22) in the workbook",
        description="Auto reads it from each workbook: away from the piles and walls the shears follow the "
        "slope of the moments, and the load the deck carries between them sets which way, whatever sign "
        "Plaxis gives the shears (Workbook tab, directions check). Sagging: positive M puts the bottom face "
        "of slabs and beams in tension; hogging: the top face. The sample model reads as hogging.",
    )
    beam_actions: Literal["peak_width", "integrated"] = Field(
        "peak_width",
        title="Beam bending and shear from the plates",
        description="Peak × width: the largest nodal M and Q per metre near each station times the beam "
        "width, as the calc report takes them. Integrated: the plate results fitted and integrated across "
        "the model's width (smaller where the mesh is coarse and noisy).",
    )
    beam_support_results: Literal["all", "faces"] = Field(
        "all",
        title="Beam results over piles and king piles",
        description="All: every Plaxis result along the beam is designed, over the supports too, as the "
        "office's beam designs take them. Faces: results inside a pile or king pile are left out as FE "
        "peaks in the connection; bending is taken at its faces and shear at d (or 2d) from them.",
    )
    shear_check_distance: Literal["d", "2d"] = Field(
        "d", title="Shear checked at", description="Distance from the support face"
    )
    results_into_connection: float = _mm(
        "Results taken into the slab or beam above",
        100.0,
        ge=0,
        le=1000,
        description="Piles and king piles are designed up to this far above their top level, at the face "
        "inside the slab or beam, with the results there at their own level. Results higher up are FE "
        "peaks inside the connection and are ignored.",
    )


# --- Element inputs ----------------------------------------------------------


_PROJECT_GRADE = "Empty: the project grade from Design settings."
_PROJECT_VALUE = "Empty: the project value from Design settings (Covers and corrosion)."


class _ConcreteSection(_Model):
    concrete: ConcreteGrade | None = Field(None, title="Concrete grade", description=_PROJECT_GRADE)
    crack_width_limit: float = _mm(
        "Crack width limit wk (QP)", 0.2, gt=0, le=0.5, description="0.2 mm in the BS 6349 design reports."
    )


class Casing(_Model):
    """Permanent steel casing around a pile over part of its length."""

    role: Literal["crack_only", "structural"] = Field(
        "crack_only",
        title="Is the casing designed?",
        description="No: the casing only removes the crack width check between its levels, and the "
        "concrete carries all forces. Yes: the casing works with the concrete; between its levels the "
        "forces are shared by E·I, as in the combi wall, and the casing itself is checked.",
    )
    top_level: float = _m(
        "Casing top level",
        2.7,
        description="At or above the pile's top level (slab soffit): the casing runs into the slab and "
        "covers the design top too (soffit + 10 cm), so no crack check there either.",
    )
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
        description="Where the casing stops, these bars (cover 0, welded to the pipe) and the cage carry "
        "the forces with no help from the casing.",
        json_schema_extra=STRUCTURAL_CASING,
    )
    connection_bar_diameter: int = Field(
        32, title="Welded bar diameter", json_schema_extra={"unit": "mm", **STRUCTURAL_CASING}
    )
    connection_length: float = _m(
        "Connection zone length",
        0.5,
        gt=0,
        description="Length checked without the casing, from the casing top upward (or the top of the pile "
        "downward when the casing reaches it).",
        extra=STRUCTURAL_CASING,
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
    link_diameter: float = _mm(
        "Smallest link diameter",
        10.0,
        gt=0,
        description="The links are designed: from this size up, the first that carries the shear at a "
        "pitch of 100 mm or more.",
    )
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
        description="The pile is designed up to the design top, 10 cm above this (the distance is in "
        "Design settings); results higher up are inside the slab and ignored. Empty: every result is used.",
    )
    casing: Casing | None = Field(
        None,
        title="This pile has a permanent steel casing",
        description="Tick it and give the casing's top and bottom levels: there is no crack width check "
        "between them. Leave it unticked for a plain concrete pile.",
    )


class CorrosionZone(_Model):
    """Loss of tube wall over one length, from the zone above (or the top) down to ``bottom_level``."""

    name: str = Field("", title="Zone name", description="As the office sheet's columns, e.g. Splash.")
    bottom_level: float = _m("Zone bottom level", 0.0)
    outside: float = _mm("Loss on the outside face", 0.0, ge=0)
    inside: float = _mm("Loss on the inside face", 0.0, ge=0, description="e.g. below the infill")


def _office_tube_zones() -> list[CorrosionZone]:
    # The office's king pile sheets: splash, immersion, immersion with soil, soil (filled), then the
    # steel-only length below the infill with 1.75 mm lost inside as well. BS 6349-1-4 mean losses.
    return [
        CorrosionZone(name="Splash", bottom_level=-0.5, outside=4.5),
        CorrosionZone(name="Submerged", bottom_level=-14.5, outside=2.5),
        CorrosionZone(name="Submerged & soil", bottom_level=-16.12, outside=2.5),
        CorrosionZone(name="Soil", bottom_level=-25.0, outside=1.75),
        CorrosionZone(name="Soil (steel only)", bottom_level=-39.0, outside=1.75, inside=1.75),
    ]


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
    link_diameter: float = _mm(
        "Smallest infill link diameter",
        10.0,
        gt=0,
        description="The links are designed: from this size up, the first that carries the shear.",
    )
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
    tube_share: Literal["ei_split", "all"] = Field(
        "ei_split",
        title="Actions on the tube",
        description="E·I split: where filled, the tube takes its E·I share of the actions and the infill "
        "the rest; below the infill the tube takes everything. All: the tube carries every action along "
        "its length (the infill is still designed for its share).",
    )
    tube_check: Literal["office", "ec3"] = Field(
        "office",
        title="Tube check",
        description="Office sheets: elastic, class 4 effective properties wherever d/t > 90ε², filled or "
        "not. EN 1993: plastic where filled (EN 1993-5 5.5.4(9)), shell buckling to EN 1993-1-6 where empty.",
    )
    corrosion_zones: list[CorrosionZone] = Field(
        default_factory=_office_tube_zones,
        title="Corrosion by zone",
        description="From the top down; the last zone runs on to the toe. The values are the office's "
        "king pile sheets: splash 4.5 to −0.5, immersion 2.5 to −16.12, soil 1.75 to −25, then 1.75 "
        "outside and inside below the infill. Empty: the single loss above, outside only.",
    )
    buckling_length_factor: float = Field(
        0.7,
        title="Column buckling length factor",
        gt=0,
        le=2,
        description="Lcr = factor × L, L from the king pile top level down to the firm soil level "
        "(or the toe).",
    )
    firm_soil_level: float | None = _m(
        "Firm soil level (column buckling)",
        None,
        description="L of the column buckling check runs from the king pile top level down to here, as the "
        "office sheet's 'length between the pile head and the firm soil'. Empty: down to the toe.",
    )
    column_ei: float | None = Field(
        None,
        title="Equivalent EI of the whole king pile",
        gt=0,
        json_schema_extra={"unit": "kN·m²"},
        description="For N_cr = π²·EI/Lcr², e.g. from a SAP frame model of the king pile (the office sheet: "
        "7.14E+15 N·mm² = 7.14E+06 kN·m²). Empty: the zones' E·I_eff averaged over the length.",
    )
    tube_fy: float | None = Field(
        None,
        title="Tube yield strength fy",
        gt=0,
        json_schema_extra={"unit": "MPa"},
        description="Empty: from the grade and the wall thickness (EN 10025-2: S355 is 345 MPa over 16 mm). "
        "The office sheet takes 355 MPa for the 18 mm tube.",
    )
    buckling_curve: Literal["a", "b", "c"] = Field(
        "c",
        title="Column buckling curve",
        description="c as the office sheets; EN 1994 gives a for filled tubes.",
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
        for z in self.corrosion_zones:
            if z.outside + z.inside >= self.tube_thickness:
                raise ValueError(f"Corrosion to {z.bottom_level:g} m must be less than the tube thickness.")
        levels = [z.bottom_level for z in self.corrosion_zones]
        if levels != sorted(levels, reverse=True):
            raise ValueError("Corrosion zones go from the top down: each bottom level below the one before.")
        return self


class SheetPileZone(_Model):
    """A corrosion zone of the sheet pile wall, from the zone above down to its bottom level."""

    bottom_level: float = _m("Zone bottom level", 0.0)
    front: float = _mm("Loss, front face", 0.0, ge=0)
    back: float = _mm("Loss, back face", 0.0, ge=0)


def _office_spw_zones() -> list[SheetPileZone]:
    # The office's Durability run: splash, immersion, immersion with soil behind, soil; no loss at the
    # back above −14.5 where the fill is cement stabilised sand.
    return [
        SheetPileZone(bottom_level=-0.5, front=4.5, back=0.0),
        SheetPileZone(bottom_level=-14.5, front=2.5, back=0.0),
        SheetPileZone(bottom_level=-16.12, front=2.5, back=1.75),
        SheetPileZone(bottom_level=-19.0, front=1.75, back=1.75),
    ]


class SheetPileIgnore(_Model):
    """Leave N or V out of the sheet pile checks, for one combination or all of them, where Plaxis
    gives values the sheet pile does not carry. The result shows both: as Plaxis and as designed."""

    combination: str = Field(
        "All combinations",
        title="Combination",
        description="As in the workbook (e.g. PT-B-Apron), or All combinations.",
    )
    ignore_n: bool = Field(False, title="Ignore N")
    ignore_q: bool = Field(False, title="Ignore Q (shear)")


_HIDDEN = {"hidden": True}


class SheetPileInput(_Model):
    kind: Literal["sheet_pile_wall"] = "sheet_pile_wall"
    section_name: SheetPileSection = Field(
        DEFAULT_SHEET_PILE, title="Sheet pile section", description="ArcelorMittal AZ range"
    )
    steel: SheetPileGrade | None = Field(None, title="Steel grade", description=_PROJECT_GRADE)
    # Kept so saved projects still open; the section's catalogue values are used.
    area: float | None = Field(None, gt=0, json_schema_extra=_HIDDEN)
    elastic_modulus: float | None = Field(None, gt=0, json_schema_extra=_HIDDEN)
    plastic_modulus: float | None = Field(None, gt=0, json_schema_extra=_HIDDEN)
    section_class: Literal[1, 2, 3, 4] = Field(2, json_schema_extra=_HIDDEN)
    class_from: Literal["auto", "catalogue", "flange"] = Field(
        "auto",
        title="Section class",
        description="flange: from b / tf / ε of the corroded flange (EN 1993-5 Table 5.1), as "
        "Durability; catalogue: never better than the class ArcelorMittal lists; auto: flange where "
        "the real flange width is known (AZ 14-770, or given), else catalogue.",
    )
    use_wel_only: bool = Field(
        False, title="Use Wel only", description="No plastic modulus for class 1 and 2."
    )
    flange_width: float | None = _mm(
        "Flange width b",
        None,
        gt=0,
        description="For the class and the water pressure factor. Empty: the real b for AZ 14-770 "
        "(351 mm, as Durability), else Triton's estimate (Durability's Sheet pile tab shows b).",
    )
    web_angle: float | None = Field(
        None,
        title="Web angle α",
        gt=0,
        lt=90,
        description="Empty: Triton's estimate (see flange width).",
        json_schema_extra={"unit": "°"},
    )
    gamma_m0: float = Field(1.0, title="γM0", gt=0, description="EN 1993-5 5.1.1 (4)")
    gamma_m1: float = Field(1.1, title="γM1", gt=0, description="EN 1993-5 5.1.1 (4); UK NA 1.0")
    buckling_length: float | None = _m(
        "Buckling length",
        None,
        gt=0,
        description="EN 1993-5 Figure 5.8; only matters where N is checked. Empty: 0.7 L, L from the "
        "top of the wall to the firm soil level, as the office's combi sheet.",
    )
    firm_soil_level: float | None = _m(
        "Firm soil level",
        None,
        description="For the assumed buckling length. Empty: the toe of the wall in the results.",
    )
    eccentricity: float = _mm("Eccentricity of N", 0.0, ge=0, description="Adds N e to M, as Durability.")
    differential_head: float = _m(
        "Differential water head",
        0.0,
        ge=0,
        description="Over 5 m, fy for bending is reduced by ρP (EN 1993-5 5.2.4, Table 5.2).",
    )
    welded_interlocks: bool = Field(False, title="Welded interlocks", description="ρP = 1.0")
    top_level: float | None = _m(
        "Top level of the wall (capping beam soffit)",
        None,
        description="Straining actions above this level are ignored, in the design and in the exports. "
        "Empty: every result is used.",
    )
    # The project's single allowance; the design uses the zones.
    corrosion_loss_per_face: float | None = Field(
        None, ge=0, title="Corrosion loss per face", json_schema_extra={"unit": "mm", **_HIDDEN}
    )
    corrosion_zones: list[SheetPileZone] = Field(
        default_factory=_office_spw_zones,
        title="Corrosion zones",
        description="Top down, front and back loss of each zone down to its bottom level; the design "
        "takes front + back off every plate. The values are the office's sample run.",
    )
    shear: Literal["Q_13", "Q_23"] = Field(
        "Q_13", title="Shear", description="Q_13: the shear of the vertical bending (M_11)."
    )
    ignore: list[SheetPileIgnore] = Field(
        default_factory=list,
        title="Ignore N or Q",
        description="Where Plaxis values are suspect: the result shows the check with every action "
        "and the one with these left out.",
    )

    @field_validator("section_name", mode="before")
    @classmethod
    def _known_section(cls, v: Any) -> str:
        # The field was free text before the design came in: an unknown name falls back to the default.
        name = _normalise_sheet_pile(v if isinstance(v, str) else None)
        return name if name in SHEET_PILE_SECTIONS else DEFAULT_SHEET_PILE

    @model_validator(mode="after")
    def _zones_descend(self) -> SheetPileInput:
        levels = [z.bottom_level for z in self.corrosion_zones]
        if any(b >= a for a, b in zip(levels, levels[1:], strict=False)):
            raise ValueError("Sheet pile corrosion zones must go down, top zone first.")
        return self


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
    vx: float = Field(0.0, title="Vx", json_schema_extra={"unit": "kN/m"})
    vy: float = Field(0.0, title="Vy", json_schema_extra={"unit": "kN/m"})
    nx: float = Field(0.0, title="Nx (compression +)", json_schema_extra={"unit": "kN/m"})
    ny: float = Field(0.0, title="Ny (compression +)", json_schema_extra={"unit": "kN/m"})

    @model_validator(mode="before")
    @classmethod
    def _no_twisting(cls, data: Any) -> Any:
        # The SAP output gives Mx, My, Vx, Vy, Nx and Ny only; older projects may carry an Mxy.
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "mxy"}
        return data


class SlabVoids(_Model):
    """Circular voids cast in the slab (e.g. PVC pipes), running across the quay between the beams."""

    diameter: float = _mm("Void diameter", 500.0, gt=0)
    spacing: float = _mm(
        "Spacing",
        700.0,
        gt=0,
        description="Centre to centre, across the voids (along the berth when they run across the quay).",
    )
    centre_depth: float | None = _mm(
        "Centre below the top",
        None,
        gt=0,
        description="Depth of the voids' centre below the top of the slab. Empty: mid-depth.",
    )
    direction: Literal["X", "Y"] = Field(
        "X", title="Voids run along", description="The global axis the voids run along (X: across the quay)."
    )
    start_offset: float = _m(
        "Start from the front beam face",
        1.0,
        ge=0,
        description="Solid slab between the front beam and the voids.",
    )
    end_offset: float = _m(
        "End before the rear beam face",
        1.0,
        ge=0,
        description="Solid slab between the voids and the rear beam.",
    )
    first_at: float | None = _m(
        "First void at",
        None,
        description="Global coordinate across the voids (Y when they run along X) of one void's centre; "
        "the others "
        "follow at the spacing. Empty: half a spacing in from the slab's edge.",
    )
    positions: list[float] = Field(
        default_factory=list,
        title="Void positions",
        description="Global coordinates across the voids of every void's centre, when they are not at a "
        "regular "
        "spacing. Empty: from the spacing.",
        json_schema_extra={"unit": "m"},
    )
    at_piles: Literal["stop", "leave_out"] = Field(
        "stop",
        title="Where a pile passes",
        description="Stop: the voids stop short of every pile and start again beyond it, so the slab is "
        "solid over the piles and punching is checked on the solid slab. Leave out: a void that would "
        "come near a pile is left out along its whole length.",
    )
    clear_to_piles: float = _mm(
        "Clear to the pile faces",
        150.0,
        ge=0,
        description="Solid concrete kept between a pile's face and the nearest void.",
    )


class SlabInput(_ConcreteSection):
    kind: Literal["slab"] = "slab"
    thickness: float = _mm("Slab thickness", 700.0, gt=0)
    cover_top: float | None = _mm("Top cover", None, gt=0, description=_PROJECT_VALUE)
    cover_bottom: float | None = _mm("Bottom cover", None, gt=0, description=_PROJECT_VALUE)
    strips: Literal["uniform", "column_and_field"] = Field(
        "column_and_field",
        title="Reinforcement layout",
        description="Column and field strips: strips from the front beam to the rear beam, each designed "
        "for its moments averaged across its width, every column strip together and every field strip "
        "together, station by station. Uniform: bars per 1 m cell, zoned.",
    )
    strip_direction: Literal["X", "Y"] = Field(
        "X",
        title="Strips run along",
        description="The global axis the strips run along, from the wall to the rear beam (across the quay).",
    )
    column_strip_width: float = _m(
        "Column strip width",
        2.2,
        gt=0,
        description="Centred on each line of piles along the strip.",
    )
    field_strip_width: float = _m(
        "Field strip width",
        2.0,
        gt=0,
        description="Centred between two lines of piles; it passes through no pile.",
    )
    stations: list[float] = Field(
        default_factory=list,
        title="Station boundaries",
        description="Distances (m) from the sea side: from the front wall line (the front beam's centre), "
        "increasing towards the rear beam. Empty: a station 2 m each side of every row of piles, and the "
        "spans between them. Stations can also be set on the diagram on the Design tab.",
        json_schema_extra={"unit": "m"},
    )
    twisting: Literal["ignore", "wood_armer"] = Field(
        "ignore",
        title="Twisting moment Mxy",
        description="Ignore (the office's method and the AdSec files): M11 with N1 only, M22 with N2 only, "
        "nothing counted twice. Wood–Armer: Mxy added to both M11 and M22 as design moments, which is safer "
        "where the slab twists, round the piles.",
    )
    punching_face_beta: Literal["ec2", "office"] = Field(
        "ec2",
        title="Punching β at the pile face",
        description="EC2 6.4.5(3): the same β as at the basic control perimeter u1 (1 + 0.6πe/(D + 4d)). "
        "Office sheets: β0 = 1 + 0.6πe/D, from the pile diameter itself, which is larger.",
    )
    shear_links: Literal["office", "ec2"] = Field(
        "office",
        title="Shear links carry",
        description="Office: V = Asw/s · 0.8d · 0.8fyk (θ = 45°), as in the office slab sheets. "
        "EC2: 6.2.3 with cot θ = 2.5, z = 0.9d and fyk/γs (about 3 times more per link).",
    )
    zone_size: float = _m(
        "Zone grid",
        1.0,
        gt=0.2,
        le=5,
        description="Cells of this size carry either the basic mesh or heavier bars in a zone.",
    )
    peaks: Literal["peak", "face_mean", "ring_mean", "envelope_face_mean"] = Field(
        "face_mean",
        title="Moments at the pile faces",
        description="Peak: as they are. Face mean: each face on its own, from the face out to one slab "
        "thickness over the pile diameter plus the slab thickness each side, per combination. Ring mean: "
        "all round the pile over one diameter (mixes opposite faces). Envelope then face mean: each node's "
        "worst value over the combinations, then the face mean. The Method tab explains each.",
    )

    @field_validator("peaks", mode="before")
    @classmethod
    def _old_peaks(cls, v: object) -> object:
        # Saved before the methods were named: "design" was the peak, "average" the default averaging.
        return {"design": "peak", "average": "face_mean"}.get(v, v) if isinstance(v, str) else v

    min_zone_length: float = _m(
        "Shortest additional bars", 2.5, gt=0, description="Shortest length of a zone of additional bars."
    )
    layout_bottom_x: Literal["mesh_and_additional", "mesh_only"] = Field(
        "mesh_and_additional",
        title="Bottom along X",
        description="A mesh with additional bars, or a mesh only.",
    )
    layout_bottom_y: Literal["mesh_and_additional", "mesh_only"] = Field(
        "mesh_and_additional",
        title="Bottom along Y",
        description="A mesh with additional bars, or a mesh only.",
    )
    layout_top_x: Literal["mesh_and_additional", "mesh_only"] = Field(
        "mesh_and_additional", title="Top along X", description="A mesh with additional bars, or a mesh only."
    )
    layout_top_y: Literal["mesh_and_additional", "mesh_only"] = Field(
        "mesh_and_additional", title="Top along Y", description="A mesh with additional bars, or a mesh only."
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
        58.0,
        gt=0,
        description=(
            "For the temperature and shrinkage restraint check (restraint from length / thickness); "
            "58 m is the office's joint spacing."
        ),
    )
    restraint_factor: float | None = Field(
        0.5,
        title="Restraint factor R",
        ge=0,
        le=1,
        description="Empty: from the length between joints and the thickness (ACI 207.2R, a wall on its "
        "base), which gives about 1 for a slab.",
    )
    restraint_check: Literal["off", "report", "design"] = Field(
        "off",
        title="Restraint cracking (temperature and shrinkage)",
        description="Off: no check; temperature and shrinkage come in as axial tension in the combinations, "
        "as the office's slab design. Report only: the restraint crack width of the bars along the quay is "
        "shown but does not choose the bars. Design: every mesh along the quay must also control it.",
    )
    crack_width_limit: float = _mm("Crack width limit wk (QP), top face", 0.2, gt=0, le=0.5)
    crack_width_limit_bottom: float = _mm(
        "Crack width limit wk (QP), bottom face",
        0.2,
        gt=0,
        le=0.5,
        description="e.g. tighter where the soffit is in the splash zone",
    )
    shear_in_tension: Literal["none", "ec2"] = Field(
        "none",
        title="Concrete shear resistance in tension",
        description="None: where the slab is in tension in the direction of the shear, the links carry it "
        "all, as the office's slab sheets. EC2 6.2.2(1): VRd,c reduced by 0.15·σcp for the tension.",
    )
    voids: SlabVoids | None = Field(
        None,
        title="Circular voids (PVC pipes)",
        description="Voids cast in the slab between the beams: bending on the voided section, shear on the "
        "webs between the voids with links in the webs only; the voids stop short of the piles, so the slab "
        "is solid over them.",
    )


class TieBars(_Model):
    """A group of straight tie bars from a bollard back into the deck, at a plan angle."""

    count: int = Field(3, title="Bars", ge=1)
    diameter: int = Field(32, title="Bar diameter", json_schema_extra={"unit": "mm"})
    angle: float = Field(
        0.0,
        title="Plan angle",
        ge=-90,
        le=90,
        description="From the line straight back from the quay face (0°), positive one way along the quay.",
        json_schema_extra={"unit": "°"},
    )


def _office_ties() -> list[TieBars]:
    # As the office drawing SC-502: 2Ø32 straight back, and a fan of 3Ø32 at 45° each side.
    return [TieBars(count=2, angle=0.0), TieBars(count=3, angle=45.0), TieBars(count=3, angle=-45.0)]


class Bollard(_Model):
    """A bollard on the front beam, tied back into the deck by straight bars (the fender loads are
    carried by the beam's own bars)."""

    capacity: float = Field(150.0, title="Bollard capacity", gt=0, json_schema_extra={"unit": "t"})
    load_factor: float = Field(
        0.75,
        title="Load factor",
        gt=0,
        description="On the rated capacity, taken as the characteristic mooring load. 0.75 is the office "
        "report's mooring factor (Table 3-6, ULS 01: 1.5 × ψ0 0.5); 1.5 treats mooring as the leading "
        "action.",
    )
    ties: list[TieBars] = Field(default_factory=_office_ties, title="Tie bars")
    tie_slope: float = Field(
        8.11,
        title="Tie bar slope in elevation",
        ge=0,
        lt=60,
        description="The ties run down from the bollard into the slab's bottom layer (8.11° on SC-502).",
        json_schema_extra={"unit": "°"},
    )
    lap_length: float = _mm(
        "Lap with the slab bottom bars", 1600.0, gt=0, description="SC-502: at least 1600 mm."
    )


class FrontBeamTruss(_Model):
    """The office's strut-and-tie check of the front beam between king piles (service loads)."""

    pile_spacing: float | None = _m(
        "King pile spacing",
        None,
        gt=0,
        description="Empty: from the king piles inside the beam in the workbook (3.2 m on Section 01a).",
    )
    crane_load: float = Field(
        900.0,
        title="Crane load on the beam",
        ge=0,
        description="Per metre along the beam (the office's 90 t/m).",
        json_schema_extra={"unit": "kN/m"},
    )
    surcharge: float = Field(
        35.0, title="Surcharge", ge=0, description="The office's 3.5 t/m².", json_schema_extra={"unit": "kPa"}
    )
    unit_weight: float = Field(
        25.0, title="Reinforced concrete weight", gt=0, json_schema_extra={"unit": "kN/m³"}
    )
    slab_width: float = _m(
        "Slab width carried by the beam",
        3.0,
        ge=0,
        description="Tributary width of deck slab behind the beam.",
    )
    slab_thickness: float = _mm("Slab thickness", 700.0, ge=0)
    bollard_slab_thickness: float | None = _mm(
        "Slab thickness at bollards",
        1400.0,
        gt=0,
        description="The slab is thickened at the bollards; checked as a second case. Empty: no such case.",
    )
    working_stress: float = Field(
        100.0,
        title="Allowable tie stress",
        gt=0,
        description="Service stress in the bottom bars; the office's 1 t/cm² for a 0.1 mm crack width.",
        json_schema_extra={"unit": "MPa"},
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
        58.0,
        gt=0,
        description=(
            "For the temperature and shrinkage restraint check (restraint from length / depth); "
            "58 m is the office's joint spacing."
        ),
    )
    restraint_factor: float | None = Field(
        None,
        title="Restraint factor R",
        ge=0,
        le=1,
        description="Empty: from the length between joints and the depth (edge restraint, ACI 207.2R).",
    )
    crack_width_limit: float = _mm("Crack width limit wk (QP), top face", 0.2, gt=0, le=0.5)
    crack_width_limit_bottom: float = _mm(
        "Crack width limit wk (QP), bottom face",
        0.2,
        gt=0,
        le=0.5,
        description="e.g. tighter where the soffit is in the splash zone",
    )
    bollard: Bollard | None = Field(
        None, title="Bollard", description="Front beam: a bollard and its tie bars. Empty: no bollard check."
    )
    truss: FrontBeamTruss | None = Field(
        None,
        title="Truss model between king piles",
        description="Front beam: struts from the loads down to the king pile heads, tied by the bottom bars. "
        "Empty: no truss check.",
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
        # The office's current sizes (width x depth): front beam 2.0 x 1.6 m, rear beam 2.0 x 2.0 m.
        kind = parsed.spec.type.value
        if kind == "front_beam":
            return BeamInput(kind=kind, width=2000.0, depth=1600.0, truss=FrontBeamTruss())
        if kind == "rear_beam":
            return BeamInput(kind=kind, width=2000.0, depth=2000.0)
        return BeamInput(kind=kind, depth=2000.0)
    return cls()


# --- Project -------------------------------------------------------------------


def _now() -> str:
    return clock.stamp()


# --- Costing -------------------------------------------------------------------


class SteelPrice(_Model):
    """A priced steel element: a sheet pile section, a tube, a casing and so on."""

    name: str = Field("AZ 26-700", title="Steel element", description="e.g. AZ 26-700, tube 1626 × 18")
    unit: Literal["t", "m²", "m"] = Field("t", title="Priced per")
    price: float | None = Field(None, title="Price", ge=0)
    mass: float | None = Field(
        None,
        title="Mass per m² or per m",
        ge=0,
        description="For the tonnage when priced per m² (kg/m²) or per m (kg/m).",
        json_schema_extra={"unit": "kg"},
    )


class PilePrice(_Model):
    """Bored piles are priced per linear metre with an allowance of reinforcement included."""

    diameter: float = _mm("Pile diameter", 1200.0, gt=0)
    price_per_m: float | None = Field(None, title="Price per linear metre", ge=0)
    rebar_included: float = Field(
        150.0,
        title="Reinforcement included",
        ge=0,
        description="What the price per metre allows for; designed reinforcement above it is added at the "
        "reinforcement price.",
        json_schema_extra={"unit": "kg/m"},
    )


def _office_steel_prices() -> list[SteelPrice]:
    return [SteelPrice(name="AZ 26-700", unit="t", mass=155.2), SteelPrice(name="King pile tube", unit="t")]


class Prices(_Model):
    """Unit prices for the Costing tab. They do not change any design."""

    @model_validator(mode="before")
    @classmethod
    def _usd_default(cls, data: Any) -> Any:
        # Saved before USD became the default (2026-09-24): "EGP" was the old default, not a choice.
        if isinstance(data, dict) and "currency_default" not in data and data.get("currency") == "EGP":
            data = {**data, "currency": "USD"}
        return data

    currency: str = Field("USD", title="Currency")
    currency_default: int = Field(
        2, json_schema_extra=_HIDDEN, description="Set by Triton: which default the currency was saved with."
    )
    concrete_slab: float | None = Field(
        None, title="Concrete, slab", ge=0, json_schema_extra={"unit": "per m³"}
    )
    concrete_beams: float | None = Field(
        None, title="Concrete, beams", ge=0, json_schema_extra={"unit": "per m³"}
    )
    concrete_infill: float | None = Field(
        None,
        title="Concrete, combi wall infill",
        ge=0,
        description="Empty: the beam concrete price.",
        json_schema_extra={"unit": "per m³"},
    )
    rebar: float | None = Field(
        None,
        title="Reinforcement",
        ge=0,
        description="Slab, beams, combi infill, and pile reinforcement above what the pile price includes.",
        json_schema_extra={"unit": "per t"},
    )
    steel: float | None = Field(
        None,
        title="Structural steel",
        ge=0,
        description="For steel elements with no price of their own below.",
        json_schema_extra={"unit": "per t"},
    )
    steel_elements: list[SteelPrice] = Field(
        default_factory=_office_steel_prices, title="Steel elements (AZ sheet piles, tubes, …)"
    )
    piles: list[PilePrice] = Field(
        default_factory=lambda: [PilePrice()], title="Bored piles, per linear metre with reinforcement"
    )


class BarLayer(_Model):
    """How one bar size is drawn: its AutoCAD layer and its Revit line style or detail family."""

    diameter: int = Field(16, title="Bar", json_schema_extra={"unit": "mm"})
    cad_layer: str = Field("REBAR-16", title="AutoCAD layer")
    revit_line_style: str = Field(
        "REBAR-16",
        title="Revit line style",
        description="Bars along the view; made if the template lacks it.",
    )
    revit_section_type: str = Field(
        "",
        title="Revit family type, cut bar",
        description="Detail component placed at each cut bar, as 'Family: Type'. Empty: a filled dot.",
    )
    revit_line_type: str = Field(
        "",
        title="Revit family type, bar line",
        description="Line-based detail component for bars along the view, as 'Family: Type'. Empty: a "
        "detail line in the line style.",
    )


def _placeholder_bar_layers() -> list[BarLayer]:
    return [
        BarLayer(diameter=d, cad_layer=f"REBAR-{d}", revit_line_style=f"REBAR-{d}") for d in BAR_DIAMETERS
    ]


class DrawingSettings(_Model):
    """Names used by the AutoCAD and Revit drawing exports. They do not change any design."""

    bars: list[BarLayer] = Field(
        default_factory=_placeholder_bar_layers,
        title="Bars: layer, line style and family type by diameter",
        description="Placeholders until the office names are set. A size not listed is drawn on REBAR-<Ø>.",
    )
    concrete_cad_layer: str = Field("TRITON-CONCRETE", title="AutoCAD layer, concrete outline")
    concrete_revit_line_style: str = Field("TRITON-CONCRETE", title="Revit line style, concrete outline")
    zones_cad_layer: str = Field("TRITON-ZONES", title="AutoCAD layer, zones and level marks")
    zones_revit_line_style: str = Field("TRITON-ZONES", title="Revit line style, zones and level marks")
    text_cad_layer: str = Field("TRITON-TEXT", title="AutoCAD layer, text")
    revit_text_type: str = Field(
        "", title="Revit text type", description="Empty: the project's default text type."
    )
    revit_view_prefix: str = Field("Triton", title="Revit drafting view names start with")


class ElementCosting(_Model):
    """How many of an element the berth needs, when not as in the design model."""

    spacing: float | None = _m(
        "Spacing along the berth", None, gt=0, description="Empty: as in the model (its length / count)."
    )
    count: int | None = Field(
        None, title="Number along the berth", ge=0, description="Overrides the spacing."
    )
    length: float | None = _m(
        "Length",
        None,
        gt=0,
        description="Pile or tube length, sheet pile length, or slab width across the quay. Empty: from "
        "the design.",
    )
    steel_element: str = Field("", title="Steel element price", description="A name from the price list.")
    intermediate_element: str = Field(
        "", title="Combi wall intermediate sheets", description="A name from the price list, e.g. AZ 26-700."
    )
    intermediate_length: float | None = _m("Intermediate sheet length", None, gt=0)


class OtherItem(_Model):
    """Something the berth needs besides the designed elements: fenders, bollards, crane rails, ..."""

    name: str = Field("", title="Item")
    unit: Literal["each", "m", "lump"] = Field(
        "each", title="Priced", description="each: per item at a spacing; m: per metre of berth; lump: once."
    )
    price: float | None = Field(None, title="Unit price", ge=0)
    spacing: float | None = _m(
        "Spacing along the berth",
        None,
        gt=0,
        description="For items priced each: one at each end and at this spacing.",
    )
    count: int | None = Field(None, title="Number", ge=0, description="Overrides the spacing.")
    runs: float = Field(
        1.0,
        title="Lines",
        gt=0,
        description="For items priced per metre: how many run along the berth (2 rails).",
    )
    length: float | None = _m(
        "Length",
        None,
        gt=0,
        description="For items priced per metre: the length of each line. Empty: the berth.",
    )


def _other_items() -> list[OtherItem]:
    # Spacings are common for a container berth, not from a drawing: change them to the project's.
    return [
        OtherItem(name="Fenders", unit="each", spacing=20.0),
        OtherItem(name="Bollards", unit="each", spacing=30.0),
        OtherItem(name="Crane rails", unit="m", runs=2.0),
    ]


class SectionCosting(_Model):
    berth_length: float | None = _m(
        "Berth length of this section",
        None,
        gt=0,
        description="The real berth length (e.g. 500 m), never the model's. Needed for costing.",
    )
    model_length: float | None = _m(
        "Length of berth the model covers",
        None,
        gt=0,
        description="Empty: the front or rear beam's length, else the slab's extent along the berth.",
    )
    elements: dict[str, ElementCosting] = Field(default_factory=dict)
    items: list[OtherItem] = Field(
        default_factory=_other_items, title="Other items", description="Fenders, bollards, crane rails, ..."
    )


class LoadFactor(_Model):
    factor: float = Field(1.35, title="Multiplier", gt=0)
    sheets: list[str] = Field(
        default_factory=list,
        title="Sheets",
        description="Workbook sheets whose straining actions are multiplied. X, Y and Z are not changed.",
    )
    note: str = Field("", title="Note", description="e.g. Set B actions to design values")
    applied_at: str | None = Field(
        None, title="Applied", description="When this multiplier last changed (set by Triton)."
    )


class CageRow(_Model):
    """One row of bars of a cage set by the user."""

    count: int = Field(26, title="Bars", ge=1)
    diameter: int = Field(32, title="Bar", json_schema_extra={"unit": "mm"})


class UserCage(_Model):
    """A pile (or combi wall infill) cage set by the user instead of the one Triton chooses.

    ``row_bars`` sets every row from the outside in, each with its own bar count and size (e.g.
    26Ø32 + 26Ø25 + 13Ø20). Without it the cage is ``rows`` of ``count`` bars (a half row has half
    as many), the outer row ``diameter`` and the inner rows ``inner_diameter``.
    """

    rows: float = Field(1, title="Rows", ge=1, le=4)
    count: int = Field(26, title="Bars in the outer row", ge=6)
    diameter: int = Field(32, title="Outer row bar", json_schema_extra={"unit": "mm"})
    inner_diameter: int | None = Field(
        None,
        title="Inner rows bar",
        description="Empty: the outer row's bar.",
        json_schema_extra={"unit": "mm"},
    )
    row_bars: list[CageRow] | None = Field(
        None, title="Rows, outer row first", description="Each row with its own bar count and size."
    )
    over_limit_with_couplers: bool = Field(
        False,
        title="Proceed over the steel limit with couplers",
        description="Steel over the limit (4%) is accepted for this cage, spliced with couplers at its "
        "joint (EN 1992-1-1 9.5.2(3)), up to 8%. The zones below are designed as usual.",
    )

    @model_validator(mode="before")
    @classmethod
    def _from_rows(cls, data: Any) -> Any:
        # The summary fields follow the rows: a row with fewer bars than the outer row counts as that
        # fraction of a row (26 + 26 + 13 bars is 2.5 rows).
        rows = data.get("row_bars") if isinstance(data, dict) else None
        if not rows:
            return data
        rows = [r.model_dump() if isinstance(r, CageRow) else dict(r) for r in rows]
        try:
            n0 = int(rows[0]["count"])
            share = sum(min(1.0, int(r["count"]) / n0) for r in rows) if n0 > 0 else 1
        except (KeyError, TypeError, ValueError):
            return data  # the field checks say what is wrong
        return {
            **data,
            "count": n0,
            "diameter": rows[0]["diameter"],
            "inner_diameter": rows[1]["diameter"] if len(rows) > 1 else None,
            "rows": max(1.0, min(4.0, round(2 * share) / 2)),
        }

    @model_validator(mode="after")
    def _rows(self) -> UserCage:
        if self.row_bars:
            if len(self.row_bars) > 4:
                raise ValueError("At most 4 rows of bars.")
            return self
        if self.rows not in (1, 1.5, 2, 2.5, 3):
            raise ValueError("Rows: 1, 1.5, 2, 2.5 or 3.")
        if self.rows in (1.5, 2.5) and self.count % 2:
            raise ValueError("A half row sits behind every second bar: use an even number of bars.")
        return self

    def row_list(self) -> list[tuple[int, int]]:
        """(bars, bar size) of each row from the outside in."""
        if self.row_bars:
            return [(r.count, r.diameter) for r in self.row_bars]
        inner = self.inner_diameter or self.diameter
        full, half = int(self.rows), self.rows % 1 > 0
        out = [(self.count, self.diameter)] + [(self.count, inner)] * (full - 1)
        return out + [(self.count // 2, inner)] if half else out


class BeamFace(_Model):
    """Bars along one face of a beam set by the user."""

    count: int = Field(10, title="Bars per layer", ge=0)
    diameter: int = Field(25, title="Bar", json_schema_extra={"unit": "mm"})
    layers: int = Field(1, title="Layers", ge=1, le=4)


class BeamCage(_Model):
    """A beam's longitudinal bars set by the user instead of the ones Triton chooses."""

    top: BeamFace = Field(default_factory=BeamFace, title="Top")
    bottom: BeamFace = Field(default_factory=BeamFace, title="Bottom")
    side: BeamFace = Field(
        default_factory=lambda: BeamFace(count=4, diameter=20), title="Each side", description="One layer."
    )


class SlabStrips(_Model):
    """Stations and bars for a slab's column and field strips, set on the Design tab."""

    stations: list[float] | None = Field(
        None,
        title="Station boundaries",
        description="Distances (m) from the sea side. Empty: the slab's own stations.",
        json_schema_extra={"unit": "m"},
    )
    bars: dict[str, str] = Field(
        default_factory=dict,
        title="Bars set by the user",
        description="Additional bars per 'layer|from|to|strip', by label ('mesh only' for none).",
    )
    spacing: float | None = Field(
        None,
        title="Mesh spacing picked",
        description="The slab is designed with each mesh spacing in Design settings; this one drives the "
        "results, drawings, AdSec files and report. Empty: the lighter one.",
        json_schema_extra={"unit": "mm"},
    )


def _short_id() -> str:
    return uuid.uuid4().hex[:8]


class SheetMapping(_Model):
    """A workbook sheet assigned by hand, when its name does not follow '<Element>-<Combination>'."""

    element: str = Field(
        "", title="Element", description="As Triton names it: Pile(3), Deck, Front Beam, SPW, ..."
    )
    combination: str = Field("", title="Combination", description="e.g. PT-B-Apron or QP")
    ignore: bool = Field(False, title="Leave this sheet out")

    @model_validator(mode="after")
    def _known(self) -> SheetMapping:
        from .elements import element_spec

        if not self.ignore:
            if element_spec(self.element) is None:
                raise ValueError(
                    f"'{self.element}' is not an element Triton knows: use Pile(n), Combi Wall, SPW, Deck, "
                    "Front Beam, Rear Beam or Transverse Beam."
                )
            if not self.combination.strip():
                raise ValueError("Give the sheet's combination, e.g. PT-B-Apron or QP.")
        return self


DEFAULT_COMBINATIONS = [
    "QP",
    "PT-B-Apron",
    "PT-B-Yard",
    "PT-C-Apron",
    "PT-C-Yard",
    "Accidental-Apron",
    "Accidental-Yard",
    "Seismic 1",
    "Seismic 2",
    "Seismic 3",
    "Seismic 4",
]


class Alignment(_Model):
    """The quay's line in plan, for berths that are not one straight line (a corner)."""

    mode: Literal["auto", "straight", "manual"] = Field(
        "auto",
        title="Berth alignment",
        description="Automatic: the straight and inclined parts are found from the front beam's nodes. "
        "Straight: the whole section is one straight berth. By hand: the corner points below.",
    )
    points: list[list[float]] = Field(
        default_factory=list,
        title="Alignment points (X, Y)",
        description="By hand: the start, every corner and the end of the quay's line (the front beam's "
        "centre line), in plan, m. Each run between two points is a part.",
    )
    min_angle: float = Field(
        2.0,
        title="Least turn for a corner",
        gt=0,
        le=45,
        json_schema_extra={"unit": "°"},
        description="Parts that turn by less than this are designed as they are (straight).",
    )
    own_axes: list[int] = Field(
        default_factory=list,
        title="Parts with results in their own axes",
        description="Part numbers whose plate results Plaxis gives in the part's own axes (a plate drawn "
        "along the inclined part): they are turned in plan only. Others are in global X/Y and are "
        "transformed (M11, M22, M12 together, likewise N and Q).",
    )

    @field_validator("points")
    @classmethod
    def _points(cls, v: list[list[float]]) -> list[list[float]]:
        for q in v:
            if len(q) != 2:
                raise ValueError("Each alignment point is X, Y.")
        return v


class WhatIf(_Model):
    """Bars taken out at one pile connection to see what it does (e.g. the contractor cannot place them).
    It never changes the design."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    group: str = Field(..., title="Pile and element", description="e.g. 'Pile(1)|Deck'")
    head: int = Field(0, title="Pile number in its element (from 0)", ge=0)
    bars: list[str] = Field(
        default_factory=list, title="Slab or beam bars taken out", description="Bar ids from the Clashes tab."
    )
    pile_bars: list[str] = Field(
        default_factory=list, title="Pile bars taken out", description="'row:bar' from the Clashes tab."
    )
    note: str = Field("", title="Note", description="e.g. why the bars cannot be placed")


class ClashSettings(_Model):
    """How the Clashes tab finds clashes between the pile bars and the slab and beam bars."""

    rule: Literal["touch", "ec2"] = Field(
        "touch",
        title="A clash is",
        description="Touch: bars that would overlap (less than the fixing tolerance apart). "
        "EC2: bars closer than EN 1992-1-1 8.2(2) allows, max(Ø, dg + 5, 20 mm), even if they do not touch.",
    )
    fixing_tolerance: float = _mm("Fixing tolerance", 10.0, ge=0, le=50)
    plate_level: Literal["mid", "top"] = Field(
        "mid",
        title="Plaxis plates are at the element's",
        description="Mid-depth (Plaxis) or top of concrete.",
    )
    top_levels: dict[str, float] = Field(
        default_factory=dict, title="Top of concrete by element (m)", description="Overrides the plate level."
    )
    mesh_start: dict[str, float] = Field(
        default_factory=dict,
        title="Slab mesh shifted by (mm)",
        description="By 'slab|X' (bars along X) or 'slab|Y': moves where the mesh starts from "
        "the slab's edge.",
    )
    choices: dict[str, str] = Field(
        default_factory=dict, title="Solution chosen", description="By 'pile|element': the solution used."
    )
    whatifs: list[WhatIf] = Field(default_factory=list, title="Bars taken out (what if)")


class ElementCheck(_Model):
    """The checker's word on one element's design."""

    status: Literal["designed", "comments", "checked", "approved"] = Field("designed", title="Status")
    by: str = Field("", title="By")
    comment: str = Field("", title="Comment")
    at: str | None = Field(None, title="When")
    design_run_at: str | None = Field(
        None, description="The design run it was given on: a later design makes it 'on an earlier design'."
    )


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
    sheet_map: dict[str, SheetMapping] = Field(
        default_factory=dict, title="Sheet mapping", description="Sheets assigned by hand, by sheet name."
    )
    costing: SectionCosting = Field(default_factory=SectionCosting, title="Costing")
    alignment: Alignment = Field(default_factory=Alignment, title="Berth alignment")
    user_cages: dict[str, UserCage] = Field(
        default_factory=dict,
        title="Cages set by the user",
        description="Pile or combi wall infill cages set on the Design tab, by element; Check designs "
        "the element with its cage.",
    )
    beam_cages: dict[str, BeamCage] = Field(
        default_factory=dict,
        title="Beam bars set by the user",
        description="By beam: top, bottom and side bars set on the Design tab; Check designs the beam with "
        "them.",
    )
    slab_strips: dict[str, SlabStrips] = Field(
        default_factory=dict,
        title="Slab stations and bars set by the user",
        description="By slab: stations and additional bars set on the Design tab; Re-check designs the "
        "slab with them.",
    )
    checks: dict[str, ElementCheck] = Field(
        default_factory=dict,
        title="Checking",
        description="By element: designed, returned with comments, checked or approved, by whom and when.",
    )
    clashes: ClashSettings = Field(
        default_factory=ClashSettings,
        title="Reinforcement clashes",
        description="Clashes tab settings, solutions chosen and bars taken out. It never changes the design.",
    )
    combinations: list[str] = Field(
        default_factory=lambda: list(DEFAULT_COMBINATIONS),
        title="Load combinations",
        description="The combinations this section's workbook should have. An upload is checked against "
        "them first, and sheets are mapped only to them.",
    )
    review: dict[str, Literal["accept", "reject"]] = Field(
        default_factory=dict,
        title="Reviewed warnings",
        description="The user's decision on each workbook warning, by its id.",
    )
    combination_map: dict[str, str] = Field(
        default_factory=dict,
        title="Workbook combinations read as",
        description="A combination as spelled in the workbook, and the defined one it is (empty: left out).",
    )

    @field_validator("combinations")
    @classmethod
    def _combinations(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for c in (x.strip() for x in v):
            if c and c.lower() not in {o.lower() for o in out}:
                out.append(c)
        return out

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


class Revision(_Model):
    """An issued revision of the calculations, with a copy of the project as it was issued."""

    rev: str = Field(title="Revision")
    description: str = Field("", title="Description")
    issued_at: str = Field(default_factory=_now, title="Issued")
    prepared: str = Field("", title="Prepared by")
    checked: str = Field("", title="Checked by")
    approved: str = Field("", title="Approved by")
    snapshot: str | None = Field(None, description="The copy's file name in the project's revisions folder.")


class Project(_Model):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    info: ProjectInfo = Field(default_factory=ProjectInfo, title="Project")
    design: DesignSettings = Field(default_factory=DesignSettings, title="Design settings")
    prices: Prices = Field(default_factory=Prices, title="Prices")
    drawings: DrawingSettings = Field(default_factory=DrawingSettings, title="Drawings (AutoCAD and Revit)")
    sections: list[Section] = Field(default_factory=lambda: [Section()], title="Sections", min_length=1)
    revisions: list[Revision] = Field(default_factory=list, title="Issued revisions")
    locked: bool = Field(
        False,
        title="Locked",
        description="Set when the model is designed: its inputs cannot change until it is unlocked to edit.",
    )

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
