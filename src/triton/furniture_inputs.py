"""Inputs of the quay furniture: fenders, bollards, ladders, storm pins, crane rails and stoppers, and
tie rods. They are typical for the whole project (Project.furniture); each section only says where
its berth differs (Section.furniture): its length, its joints, where the cranes are stowed.

Loads are the supplier's catalogue values for the item chosen. The values filled in are common ones
for a container berth, not from a catalogue or drawing of this project: replace them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .protrusion_inputs import FenderProtrusion, StsCrane

BOLT_SIZES = [16, 20, 24, 30, 36, 42, 48, 56, 64, 72, 80, 90, 100]
BoltSize = Literal[tuple(BOLT_SIZES)]  # type: ignore[valid-type]
BoltGrade = Literal["4.6", "5.6", "8.8", "10.9", "A4-70", "A4-80"]
RailType = Literal["A100", "A120", "A150", "MRS 87A", "MRS 125", "175 lb CR"]
TieGrade = Literal["S355J2", "ASDO 500", "ASDO 700"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _mm(title: str, default: float | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "mm"}, **kw)


def _m(title: str, default: float | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "m"}, **kw)


def _kn(title: str, default: float, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "kN"}, **kw)


ASSUMED = "Assumed (common value), replace with the supplier's."


CIRCLE = {"show_when": {"pattern": ["circle"]}}
GRID = {"show_when": {"pattern": ["grid"]}}


class Anchors(_Model):
    """Bolts cast into the beam, with a nut and anchor plate at the bottom (headed)."""

    pattern: Literal["circle", "grid"] = Field("circle", title="Bolt pattern")
    count: int = Field(6, title="Bolts", ge=2, le=24, json_schema_extra=CIRCLE)
    circle_diameter: float = Field(
        1100.0, title="Bolt circle diameter", gt=0, json_schema_extra={"unit": "mm", **CIRCLE}
    )
    rows: int = Field(2, title="Rows (across)", ge=1, le=8, json_schema_extra=GRID)
    columns: int = Field(2, title="Columns (along the beam)", ge=1, le=12, json_schema_extra=GRID)
    spacing_along: float = Field(
        300.0, title="Spacing along the beam", gt=0, json_schema_extra={"unit": "mm", **GRID}
    )
    spacing_across: float = Field(
        300.0, title="Spacing across", gt=0, json_schema_extra={"unit": "mm", **GRID}
    )
    diameter: BoltSize = Field(30, title="Bolt size M", json_schema_extra={"unit": "mm"})
    grade: BoltGrade = Field("8.8", title="Bolt grade", description="8.8 galvanised, or A4 stainless.")
    embedment: float = _mm("Embedment hef", 500.0, gt=0, description="To the underside of the head or plate.")
    head_diameter: float | None = _mm(
        "Head or washer plate diameter", None, gt=0, description="Empty: 3 × the bolt."
    )


class Fenders(_Model):
    """Fender units on the front face of the front beam."""

    name: str = Field("Cone fender (assumed, 1000 mm high)", title="Fender")
    reaction: float = _kn("Rated reaction R", 1100.0, gt=0, description=ASSUMED)
    friction: float = Field(
        0.3, title="Friction on the panel μ", ge=0, le=1, description="UHMW-PE pads 0.2 to 0.3."
    )
    panel_weight: float = _kn("Panel weight on the flange", 0.0, ge=0, description="0 where chains carry it.")
    height: float = _m("Fender height (face to panel)", 1.0, gt=0)
    load_factor: float = Field(
        1.5,
        title="Load factor",
        gt=0,
        description="On the rated reaction: 1.5 with berthing as the leading action; 1.0 where the "
        "reaction already includes the abnormal berthing factor (accidental).",
    )
    flange: float = _mm("Base flange diameter", 1400.0, gt=0)
    centre_below_cope: float = _m("Centre below the cope", 0.8, gt=0)
    anchors: Anchors = Field(
        default_factory=lambda: Anchors(
            pattern="circle", count=6, circle_diameter=1100, diameter=36, embedment=500
        ),
        title="Anchor bolts",
    )
    spacing: float = _m(
        "Spacing along the berth",
        20.0,
        gt=0,
        description="Common value; BS 6349-4: ≤ 0.15 × the smallest ship's length.",
    )
    end_distance: float = _m("First fender from the berth end", 5.0, ge=0)
    smallest_ship: float | None = _m(
        "Smallest ship length (LOA)", 150.0, gt=0, description="For the spacing check. Empty: no check."
    )


class Bollards(_Model):
    """Bollards on top of the front beam."""

    capacity: float = Field(150.0, title="Rated capacity", gt=0, json_schema_extra={"unit": "t"})
    load_factor: float = Field(
        1.5,
        title="Load factor",
        gt=0,
        description="On the rated capacity for the bollard's own fixing (mooring as the leading "
        "action). The front beam's tie bars keep their own factor (0.75, the report's combination).",
    )
    max_vertical_angle: float = Field(
        60.0, title="Line angle above horizontal up to", ge=0, le=90, json_schema_extra={"unit": "°"}
    )
    line_height: float = _m("Line pull height above the base", 0.35, gt=0)
    base: float = _mm("Base plate size", 1000.0, gt=0)
    centre_from_face: float = _m("Centre from the quay face", 0.75, gt=0)
    anchors: Anchors = Field(
        default_factory=lambda: Anchors(
            pattern="circle", count=8, circle_diameter=800, diameter=48, embedment=1000, head_diameter=160
        ),
        title="Anchor bolts",
    )
    spacing: float = _m(
        "Spacing along the berth", 30.0, gt=0, description="Common value for container ships."
    )
    end_distance: float = _m("First bollard from the berth end", 10.0, ge=0)


class Ladders(_Model):
    """Steel ladders recessed in the front face, from the cope down below the lowest water."""

    spacing: float = _m(
        "Spacing along the berth, not more than",
        30.0,
        gt=0,
        description="Common specification value; they sit midway between fenders.",
    )
    bottom_level: float = _m("Bottom level", -1.0, description="About 1 m below LAT. Assumed LAT 0.0.")
    clear_width: float = _mm("Clear width", 450.0, gt=0)
    rung_diameter: float = _mm("Rung bar diameter", 25.0, gt=0)
    rung_pitch: float = _mm("Rung spacing", 300.0, gt=0)
    stringer_width: float = _mm("Stringer flat width", 75.0, gt=0)
    stringer_thickness: float = _mm("Stringer flat thickness", 16.0, gt=0)
    fy: float = Field(355.0, title="Steel yield strength", gt=0, json_schema_extra={"unit": "MPa"})
    corrosion: float = _mm(
        "Loss per face over the life", 1.0, ge=0, description="Galvanised and replaceable; assumed."
    )
    fixing_spacing: float = _m("Brackets every", 2.0, gt=0)
    standoff: float = _mm("Rung centre from the fixing face", 150.0, gt=0)
    users: int = Field(2, title="People on one bracket span", ge=1)
    recess: float = _mm("Recess width in the beam", 700.0, gt=0)
    anchors: Anchors = Field(
        default_factory=lambda: Anchors(
            pattern="grid", rows=1, columns=2, spacing_along=550, diameter=16, grade="A4-70", embedment=125
        ),
        title="Bracket bolts",
    )


class StormPins(_Model):
    """Storm (stowage) pins: a crane's sill beam pin dropped into a socket in the rail beam."""

    force: float = _kn("Horizontal force per pin", 800.0, gt=0, description=ASSUMED)
    load_factor: float = Field(1.5, title="Load factor", gt=0)
    socket_width: float = _mm("Socket width (loaded face)", 300.0, gt=0)
    socket_length: float = _mm("Socket length", 400.0, gt=0)
    socket_depth: float = _mm("Socket depth", 500.0, gt=0)
    offset_from_rail: float = _m(
        "Socket centre landward of the rail", 0.9, description="Negative: seaward of the rail."
    )
    bar: int = Field(20, title="Loop bar", json_schema_extra={"unit": "mm"})
    cranes: int = Field(2, title="Cranes to stow", ge=0)
    crane_width: float = _m("Crane width along the berth (buffer to buffer)", 27.0, gt=0)


class CraneRails(_Model):
    """Crane rails on the front and rear beams, continuously supported on a resilient pad."""

    rail: RailType = Field("A150", title="Rail")
    wheel_load: float = _kn("Wheel load (with dynamic factor)", 700.0, gt=0, description=ASSUMED)
    load_factor: float = Field(1.35, title="Load factor", gt=0)
    wheels: int = Field(8, title="Wheels per corner", ge=1, le=16)
    wheel_spacing: float = _mm("Wheel spacing", 1100.0, gt=0)
    lateral: float = Field(
        0.1, title="Lateral force / wheel load", ge=0, le=0.3, description="EN 1991-3 skewing: up to 0.2."
    )
    pad_stiffness: float = Field(
        1500.0,
        title="Pad foundation modulus",
        gt=0,
        description="Force per mm of rail per mm of settlement (≈ E·b/t of the pad).",
        json_schema_extra={"unit": "N/mm²"},
    )
    rail_fy: float = Field(500.0, title="Rail steel yield", gt=0, json_schema_extra={"unit": "MPa"})
    clip_spacing: float = _mm("Clip pairs every", 600.0, gt=0)
    clip_capacity: float = _kn("Clip lateral capacity", 60.0, gt=0, description=ASSUMED)
    clip_anchors: Anchors = Field(
        default_factory=lambda: Anchors(
            pattern="grid", rows=1, columns=1, diameter=24, grade="8.8", embedment=250
        ),
        title="Clip bolt (one per clip)",
    )
    front_rail_from_face: float | None = _m(
        "Front rail from the quay face", None, gt=0, description="Empty: over the front beam's centre."
    )
    gauge: float | None = _m(
        "Rail gauge", None, gt=0, description="Empty: front beam to rear beam centres in the model."
    )


class CraneStoppers(_Model):
    """End stops at both ends of each rail, bolted to the beam top."""

    force: float = _kn("Buffer force per stop", 600.0, gt=0, description=ASSUMED)
    load_factor: float = Field(1.5, title="Load factor", gt=0)
    buffer_height: float = _m("Buffer centre above the beam top", 1.2, gt=0)
    base_length: float = _mm("Base plate along the rail", 1200.0, gt=0)
    base_width: float = _mm("Base plate across", 700.0, gt=0)
    end_distance: float = _m("From the berth end", 1.0, ge=0)
    anchors: Anchors = Field(
        default_factory=lambda: Anchors(
            pattern="grid",
            rows=2,
            columns=4,
            spacing_along=330,
            spacing_across=500,
            diameter=42,
            embedment=700,
        ),
        title="Anchor bolts",
    )


class TieRods(_Model):
    """Tie rods from the wall back to an anchor, anchored in the beam with a plate."""

    force: float = Field(
        250.0,
        title="Design tie force per metre",
        gt=0,
        description="ULS from the Plaxis anchor results. " + ASSUMED,
        json_schema_extra={"unit": "kN/m"},
    )
    force_sls: float | None = Field(
        None,
        title="Service tie force per metre",
        gt=0,
        description="Empty: the design force / 1.35.",
        json_schema_extra={"unit": "kN/m"},
    )
    spacing: float = _m("Tie spacing", 3.2, gt=0, description="The king pile spacing.")
    grade: TieGrade = Field("ASDO 500", title="Steel")
    shaft: float = _mm("Shaft diameter", 85.0, gt=0)
    thread: int = Field(100, title="Upset thread M", json_schema_extra={"unit": "mm"})
    corrosion: float = _mm("Loss per face over the life", 1.75, ge=0, description="BS 6349-1-4: in soil.")
    kt: float = Field(
        0.6,
        title="Notch factor kt",
        gt=0,
        le=1,
        description="EN 1993-5 7.2.3: 0.6 allows for bending at the thread.",
    )
    plate: float = _mm("Anchor plate size (square)", 400.0, gt=0)
    bar: int = Field(20, title="Bursting bar", json_schema_extra={"unit": "mm"})


class FurnitureRules(_Model):
    """How the items are arranged along each berth."""

    joint_clearance: float = _m("Keep items clear of a joint by", 1.0, ge=0)
    item_clearance: float = _m("Clear distance between items", 0.5, ge=0)
    pile_clearance: float = _mm(
        "Anchors clear of pile heads by",
        100.0,
        ge=0,
        description="Where the anchors reach down to a pile head.",
    )
    max_shift: float = _m("Move an item from its spacing by at most", 2.0, ge=0)


class QuayFurniture(_Model):
    """Every section's berth gets these items; a section changes only its own berth details."""

    fenders: Fenders | None = Field(default_factory=Fenders, title="Fenders")
    bollards: Bollards | None = Field(default_factory=Bollards, title="Bollards")
    ladders: Ladders | None = Field(default_factory=Ladders, title="Ladders")
    storm_pins: StormPins | None = Field(default_factory=StormPins, title="Storm pins")
    crane_rails: CraneRails | None = Field(default_factory=CraneRails, title="Crane rails")
    crane_stoppers: CraneStoppers | None = Field(default_factory=CraneStoppers, title="Crane stoppers")
    tie_rods: TieRods | None = Field(
        default_factory=TieRods,
        title="Tie rods",
        description="Untick where the section has no tie rods.",
    )
    rules: FurnitureRules = Field(default_factory=FurnitureRules, title="Arrangement rules")
    protrusion: FenderProtrusion | None = Field(
        None,
        title="Front beam protrusion at each fender",
        description="A block on the beam's sea face at every fender, flush with the cope. Tick to add it.",
    )
    sts_crane: StsCrane | None = Field(
        default_factory=StsCrane,
        title="STS cranes on this quay",
        description="Checks the ship's stand-off from the quay face against the crane's outreach and legs. "
        "Untick where there is no ship-to-shore crane.",
    )


class SectionFurniture(_Model):
    """Where this section's berth differs from the project's furniture."""

    use: bool = Field(
        True, title="This section has quay furniture", description="Off for a section with no berth face."
    )
    berth_length: float | None = _m(
        "Berth length", None, gt=0, description="Empty: the Costing berth length, else the model's length."
    )
    stow_positions: list[float] = Field(
        default_factory=list,
        title="Crane stow positions at",
        description="Distances from the berth start. Empty: the cranes side by side from the start.",
        json_schema_extra={"unit": "m"},
    )
    no_tie_rods: bool = Field(False, title="No tie rods in this section")
