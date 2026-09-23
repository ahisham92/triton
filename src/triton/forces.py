"""Prepare imported Plaxis forces for design.

Design uses the phase value of each action (N, Q, M), not the Plaxis min/max
envelope columns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .elements import ElementSpec

AXIAL = ("N", "N_1", "N_2")
# Columns that locate a result rather than being one: never multiplied.
LOCATION_COLUMNS = frozenset({"plaxis_label", "Node", "local_number", "X", "Y", "Z"})


def scale_forces(frame: pd.DataFrame, factor: float) -> pd.DataFrame:
    """Multiply every straining action (phase, min and max) by ``factor``; X, Y, Z are kept."""
    out = frame.copy()
    cols = [c for c in out.columns if c not in LOCATION_COLUMNS and pd.api.types.is_numeric_dtype(out[c])]
    out[cols] = out[cols] * factor
    return out


def phase_values(frame: pd.DataFrame, actions: list[str]) -> pd.DataFrame:
    """Node, coordinates and phase-value actions only; min/max columns dropped."""
    keep = [c for c in ("Node", "X", "Y", "Z", *actions) if c in frame.columns]
    return frame[keep].copy()


def to_concrete_sign(frame: pd.DataFrame) -> pd.DataFrame:
    """Flip axial forces for concrete design.

    AdSec's sign convention for compression and tension is the opposite of
    Plaxis, so the team multiplies N by -1 for concrete elements.
    """
    out = frame.copy()
    for col in AXIAL:
        if col in out.columns:
            out[col] = -out[col]
    return out


def design_forces(frame: pd.DataFrame, spec: ElementSpec, actions: list[str]) -> pd.DataFrame:
    out = phase_values(frame, actions)
    return to_concrete_sign(out) if spec.concrete else out


@dataclass(frozen=True)
class CombiSection:
    """Concrete-filled steel tube of the combi wall. Lengths in m, moduli in kPa."""

    outer_diameter: float
    wall_thickness: float
    corrosion_loss: float  # loss of wall thickness from the outside over the design life
    e_steel: float = 210e6
    e_concrete: float = 33e6
    concrete_bottom_level: float = -25.0  # concrete fill stops here; steel only below

    def __post_init__(self) -> None:
        if self.wall_thickness <= 0 or self.outer_diameter <= 2 * self.wall_thickness:
            raise ValueError("Wall thickness must be positive and smaller than the radius.")
        if not 0 <= self.corrosion_loss < self.wall_thickness:
            raise ValueError("Corrosion loss must be between 0 and the wall thickness.")

    @property
    def inner_diameter(self) -> float:
        return self.outer_diameter - 2 * self.wall_thickness

    @property
    def i_steel(self) -> float:
        d_out = self.outer_diameter - 2 * self.corrosion_loss
        return math.pi / 64 * (d_out**4 - self.inner_diameter**4)

    @property
    def i_concrete(self) -> float:
        return math.pi / 64 * self.inner_diameter**4

    @property
    def steel_share(self) -> float:
        """Share of the actions carried by the steel tube where it is concrete filled."""
        es_is = self.e_steel * self.i_steel
        ec_ic = self.e_concrete * self.i_concrete
        return es_is / (es_is + ec_ic)


def split_combi_wall(
    frame: pd.DataFrame, section: CombiSection, actions: list[str]
) -> dict[str, pd.DataFrame]:
    """Split combi wall forces into the steel tube and the concrete core.

    Above ``concrete_bottom_level`` the actions are shared between steel and
    concrete in proportion to their flexural stiffness (E x I, with the
    corroded steel section). The axial force uses the same ratio as the
    moments and shears. Below it the steel carries everything. The concrete
    part gets the concrete sign convention for N.
    """
    base = phase_values(frame, actions)
    filled = base["Z"] >= section.concrete_bottom_level
    share = section.steel_share

    steel = base.copy()
    steel.loc[filled, actions] = base.loc[filled, actions] * share
    steel["zone"] = filled.map({True: "composite", False: "steel_only"})

    concrete = base.loc[filled].copy()
    concrete[actions] = concrete[actions] * (1 - share)
    concrete = to_concrete_sign(concrete)
    return {"steel": steel.reset_index(drop=True), "concrete": concrete.reset_index(drop=True)}
