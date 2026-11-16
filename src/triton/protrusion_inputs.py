"""Inputs of the fender protrusion (a block cast on the front beam's sea face at each fender) and of
the STS crane that the berth must still serve. Their sizes are the project's (QuayFurniture); each
section ticks whether it has them (SectionFurniture).

The block's sizes are Ahmed's and drawing SC-502-1's (1.5 m out from a 4.5 m beam, 2.5 m deep to
suit the fender, 3.0 m long, a bollard on top). The ship and fender panel values are the design
report's (N25185-...-RPT-ST-01 Rev 2); the ship's flare is worked out from its depth and ballast
draft at high water with an assumed flare and list; the crane's outreach and legs and the clearance
are not in the report and stay assumed.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASSUMED = "Assumed (common value), replace with the project's."
REPORT = "Design report N25185-0100D-FD-TIN-00-RPT-ST-01 Rev 2"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _mm(title: str, default: float | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "mm"}, **kw)


def _m(title: str, default: float | None = None, **kw) -> Field:
    return Field(default, title=title, json_schema_extra={"unit": "m"}, **kw)


Joint = Literal["monolithic", "indented", "rough", "smooth"]


class FenderProtrusion(_Model):
    """A rectangular block on the front beam's sea face at each fender, flush with the cope. It keeps
    the ship's side further from the quay (and the STS crane's legs) and gives the fender the depth
    its manufacturer asks for where the beam is shallower."""

    projection: float = _mm("Projection from the beam's sea face", 1500.0, gt=0)
    depth: float = _mm(
        "Depth from the cope",
        2500.0,
        gt=0,
        description="The fender manufacturer's depth; may be more than the beam's.",
    )
    length: float = _mm(
        "Length along the berth",
        3000.0,
        gt=0,
        description="Drawing SC-502-1 (reinforcement at protruded area): 3000 along, 1500 out.",
    )
    fender_centre_below_cope: float | None = _m(
        "Fender centre below the cope", None, gt=0, description="Empty: the middle of the block's depth."
    )
    bollard_on_block: bool = Field(
        True,
        title="A bollard stands on the block",
        description="As on drawing SC-502-1 (150 t bollard on the protruded area): the Furniture tab's "
        "bollard pulls on the block (mooring, not with berthing).",
    )
    joint: Joint = Field(
        "rough",
        title="Joint to the beam",
        description="Monolithic: cast with the beam. Otherwise a construction joint (EN 1992-1-1 6.2.5); "
        "rough is assumed: the block is cast after the beam's front face.",
    )
    concrete: str | None = Field(None, title="Concrete grade", description="Empty: the front beam's.")
    cover: float | None = _mm("Cover to links", None, gt=0, description="Empty: the front beam's.")
    tie_bar: int = Field(32, title="Top tie bar (U-bars into the beam)", json_schema_extra={"unit": "mm"})
    side_bar: int = Field(20, title="Side and bottom bars", json_schema_extra={"unit": "mm"})
    link_bar: int = Field(16, title="Links", json_schema_extra={"unit": "mm"})
    face_bar: int = Field(16, title="Face mesh bars", json_schema_extra={"unit": "mm"})
    max_spacing: float = _mm("Largest bar spacing", 200.0, gt=0)
    density: float = Field(25.0, title="Concrete density", gt=0, json_schema_extra={"unit": "kN/m³"})


class StsCrane(_Model):
    """Ship-to-shore cranes on this quay. The ship must stay close enough for the crane's outreach to
    reach its far row, and far enough that the ship's flare clears the crane's seaside legs."""

    @model_validator(mode="before")
    @classmethod
    def _report_values(cls, data: Any) -> Any:
        """Projects saved with the first defaults (61.5 m ship, 0.3 m panel, assumed) take the design
        report's (43.2 m, 0.25 m); a value the user changed is kept."""
        if isinstance(data, dict) and data.get("ship_beam") == 61.5 and data.get("panel_thickness") == 0.3:
            data = {**data, "ship_beam": 43.2, "panel_thickness": 0.25}
        if isinstance(data, dict) and data.get("flare_overhang") == 2.0 and "flare_angle" not in data:
            # The first, assumed 2.0 m: now worked out from the ship.
            data = {**data, "flare_overhang": None}
        return data

    outreach: float = _m(
        "Outreach from the seaside rail",
        70.0,
        gt=0,
        description="To the centre of the outermost row the crane serves. Not in the design report (it gives "
        "only the wheel loads and the 30.48 m gauge). " + ASSUMED,
    )
    ship_beam: float = _m(
        "Design ship beam",
        43.2,
        gt=0,
        description="The widest ship the crane must serve. "
        + REPORT
        + ", 7 Input Data: Post-Panamax container ship, LOA 340 m, breadth 43.2 m (110,000 DWT).",
    )
    far_row_inside: float = _m(
        "Outermost row centre inside the ship's far side",
        1.5,
        ge=0,
        description="Half a container plus the hull. Not in the design report. " + ASSUMED,
    )
    leg_seaward_of_rail: float = _m(
        "Crane's seaside legs and sill beam, seaward of the rail",
        1.0,
        ge=0,
        description="Their furthest part towards the sea. Not in the design report. " + ASSUMED,
    )
    flare_overhang: float | None = _m(
        "Ship's flare and list beyond the fender contact line",
        None,
        ge=0,
        description="How far the hull leans towards the quay at its deck edge. Empty: worked out from the "
        "ship "
        "(depth, ballast draft, flare and list below) at high water.",
    )
    ship_depth: float = _m(
        "Design ship depth (keel to deck edge)",
        19.12,
        gt=0,
        description=REPORT + ", 7 Input Data: moulded depth 19.12 m.",
    )
    ballast_draft: float = _m(
        "Design ship ballast draft",
        12.75,
        gt=0,
        description="The lightest ship stands highest. " + REPORT + ", 7 Input Data: ballast draft 12.75 m.",
    )
    high_water: float = _m(
        "High water level",
        0.945,
        description="MHWS on the tidal bar of drawing SC-502-1 (m CD).",
    )
    flare_angle: float = Field(
        10.0,
        title="Hull flare at the fender contact",
        ge=0,
        lt=60,
        description="Outward lean of the hull side above the fender line (0 on the parallel body, more "
        "towards "
        "the bow). Not in the design report. " + ASSUMED,
        json_schema_extra={"unit": "°"},
    )
    list_angle: float = Field(
        3.0,
        title="List towards the quay",
        ge=0,
        lt=30,
        description="While loading and unloading. Not in the design report. " + ASSUMED,
        json_schema_extra={"unit": "°"},
    )
    min_clearance: float = _m(
        "Clearance kept between the ship and the crane's legs",
        1.0,
        ge=0,
        description="No code value and not in the design report (its BS 6349-4 250 mm is the hull to cope, "
        "not to the crane); a common specification value. " + ASSUMED,
    )
    panel_thickness: float = _m(
        "Fender panel and pads thickness",
        0.25,
        ge=0,
        description="Added to the fender height. " + REPORT + ", hull-cope clearance: frontal panel 250 mm "
        "(indicative, to be confirmed by the fender supplier).",
    )
    rated_deflection: float = Field(
        0.72,
        title="Fender deflection at the rated reaction",
        gt=0,
        lt=1,
        description="Share of the fender height. " + REPORT + ", hull-cope clearance: SCN 1600 at 72%.",
    )
