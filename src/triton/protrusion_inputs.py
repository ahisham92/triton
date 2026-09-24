"""Inputs of the fender protrusion (a block cast on the front beam's sea face at each fender) and of
the STS crane that the berth must still serve. Both are the project's (QuayFurniture): typical for
every section's berth.

The block's sizes are Ahmed's (1.5 m out from a 4.5 m beam, 2.5 m deep to suit the fender, 2.5 m
long). The STS crane and ship values are common ones for a large container berth, not from this
project's crane specification: replace them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ASSUMED = "Assumed (common value), replace with the project's."


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
    length: float = _mm("Length along the berth", 2500.0, gt=0)
    fender_centre_below_cope: float | None = _m(
        "Fender centre below the cope", None, gt=0, description="Empty: the middle of the block's depth."
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

    outreach: float = _m(
        "Outreach from the seaside rail",
        70.0,
        gt=0,
        description="To the centre of the outermost row the crane serves. " + ASSUMED,
    )
    ship_beam: float = _m(
        "Design ship beam",
        61.5,
        gt=0,
        description="The widest ship the crane must serve (24,000 TEU class). " + ASSUMED,
    )
    far_row_inside: float = _m(
        "Outermost row centre inside the ship's far side",
        1.5,
        ge=0,
        description="Half a container plus the hull. " + ASSUMED,
    )
    leg_seaward_of_rail: float = _m(
        "Crane's seaside legs and sill beam, seaward of the rail",
        1.0,
        ge=0,
        description="Their furthest part towards the sea. " + ASSUMED,
    )
    flare_overhang: float = _m(
        "Ship's flare and list beyond the fender contact line",
        2.0,
        ge=0,
        description="How far the hull leans towards the quay at the height of the crane's legs. " + ASSUMED,
    )
    min_clearance: float = _m(
        "Clearance kept between the ship and the crane's legs",
        1.0,
        ge=0,
        description="No code value; a common specification value. " + ASSUMED,
    )
    panel_thickness: float = _m(
        "Fender panel and pads thickness", 0.3, ge=0, description="Added to the fender height. " + ASSUMED
    )
    rated_deflection: float = Field(
        0.72,
        title="Fender deflection at the rated reaction",
        gt=0,
        lt=1,
        description="Share of the fender height (cone fenders about 0.72). " + ASSUMED,
    )
