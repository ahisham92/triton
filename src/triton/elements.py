"""Element types, combination types and the sheet naming convention.

Sheets in the geotechnical workbook are named ``<Element>-<Combination>``,
for example ``Pile(1)-PT-B-Apron`` or ``Deck-QP``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ResultKind(StrEnum):
    """Which Plaxis result table a sheet holds."""

    PLATE = "plate"  # forces per metre width: N_1, N_2, Q_12, Q_23, Q_13, M_11, M_22, M_12
    BEAM = "beam"  # member forces: N, Q_12, Q_13, M_1, M_2, M_3 (+ embedded-beam skin/foot forces)


class ElementType(StrEnum):
    SHEET_PILE_WALL = "sheet_pile_wall"
    COMBI_WALL = "combi_wall"
    PILE = "pile"
    SLAB = "slab"
    FRONT_BEAM = "front_beam"
    REAR_BEAM = "rear_beam"
    PORTAL_FRAME = "portal_frame"


class CombinationType(StrEnum):
    ULS = "uls"
    SLS_QP = "sls_qp"  # quasi-permanent, used for crack width only
    SEISMIC = "seismic"
    ACCIDENTAL = "accidental"


PLATE_ACTIONS = ("N_1", "N_2", "Q_12", "Q_23", "Q_13", "M_11", "M_22", "M_12")
BEAM_ACTIONS = ("N", "Q_12", "Q_13", "M_1", "M_2", "M_3")
EMBEDDED_BEAM_EXTRAS = ("T_skin", "T_lat", "T_lat2", "F_foot")
LOCATION_COLUMNS = ("Node", "X", "Y", "Z")


@dataclass(frozen=True)
class ElementSpec:
    type: ElementType
    kind: ResultKind
    family: str  # elements in one family are expected to share combinations
    concrete: bool  # concrete elements get N * -1 before design (AdSec sign convention)
    required_actions: tuple[str, ...]


_PATTERNS: list[tuple[re.Pattern[str], ElementSpec]] = [
    (
        re.compile(r"^SPW$", re.I),
        ElementSpec(ElementType.SHEET_PILE_WALL, ResultKind.PLATE, "wall", False, PLATE_ACTIONS),
    ),
    (
        re.compile(r"^Combi\s*Wall$", re.I),
        ElementSpec(ElementType.COMBI_WALL, ResultKind.BEAM, "wall", True, BEAM_ACTIONS),
    ),
    (
        re.compile(r"^Pile\s*\(\s*\d+\s*\)$", re.I),
        ElementSpec(ElementType.PILE, ResultKind.BEAM, "pile", True, BEAM_ACTIONS),
    ),
    (
        re.compile(r"^(Deck|Slab)$", re.I),
        ElementSpec(ElementType.SLAB, ResultKind.PLATE, "deck", True, PLATE_ACTIONS),
    ),
    (
        re.compile(r"^Front\s*Beam$", re.I),
        ElementSpec(ElementType.FRONT_BEAM, ResultKind.PLATE, "edge_beam", True, PLATE_ACTIONS),
    ),
    (
        re.compile(r"^Rear\s*Beam$", re.I),
        ElementSpec(ElementType.REAR_BEAM, ResultKind.PLATE, "edge_beam", True, PLATE_ACTIONS),
    ),
    (
        re.compile(r"^Portal\s*Frame$", re.I),
        ElementSpec(ElementType.PORTAL_FRAME, ResultKind.PLATE, "portal", True, PLATE_ACTIONS),
    ),
]

# Element names can themselves contain hyphens in future ("Pile(1)" does not,
# but be permissive): try every split point, longest element name first.
_COMBO_SPLIT = re.compile(r"-")


@dataclass(frozen=True)
class SheetName:
    raw: str
    element: str  # as written in the sheet name, e.g. "Pile(1)"
    combination: str  # e.g. "PT-B-Apron"
    spec: ElementSpec


def parse_sheet_name(name: str) -> SheetName | None:
    """Split a sheet name into element and combination, or None if unrecognised."""
    cleaned = name.strip()
    positions = [m.start() for m in _COMBO_SPLIT.finditer(cleaned)]
    for pos in reversed(positions):
        element, combination = cleaned[:pos].strip(), cleaned[pos + 1 :].strip()
        if not combination:
            continue
        for pattern, spec in _PATTERNS:
            if pattern.match(element):
                return SheetName(name, _normalise_element(element), combination, spec)
    return None


def _normalise_element(element: str) -> str:
    return re.sub(r"\s*\(\s*(\d+)\s*\)", r"(\1)", re.sub(r"\s+", " ", element))


def combination_type(combination: str) -> CombinationType:
    c = combination.upper()
    if c.startswith("QP") or c.endswith("QP"):
        return CombinationType.SLS_QP
    if "SEIS" in c or re.search(r"\bEQ", c):
        return CombinationType.SEISMIC
    if "ACC" in c:
        return CombinationType.ACCIDENTAL
    return CombinationType.ULS
