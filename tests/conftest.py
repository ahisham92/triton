"""Synthetic Plaxis sheets that reproduce the quirks of the real workbooks."""

from __future__ import annotations

import pytest

PLATE_HEADER = [
    "     Structural element",
    " Node",
    "Local number",
    "                X [m]",
    "                 Y [m]",
    "                 Z [m]",
]
for _a, _u in [
    ("N_1", "kN/m"),
    ("N_2", "kN/m"),
    ("Q_12", "kN/m"),
    ("Q_23", "kN/m"),
    ("Q_13", "kN/m"),
    ("M_11", "kN m/m"),
    ("M_22", "kN m/m"),
    ("M_12", "kN m/m"),
]:
    PLATE_HEADER += [f"   {_a} [{_u}]", f"   {_a},min [{_u}]", f"   {_a},max [{_u}]"]

BEAM_HEADER = PLATE_HEADER[:6]
for _a, _u in [
    ("N", "kN"),
    ("Q_12", "kN"),
    ("Q_13", "kN"),
    ("M_1", "kN m"),
    ("M_2", "kN m"),
    ("M_3", "kN m"),
]:
    sep = "_" if _a == "N" else ","
    BEAM_HEADER += [f"   {_a} [{_u}]", f"   {_a}{sep}min [{_u}]", f"   {_a}{sep}max [{_u}]"]
EMBEDDED_HEADER = BEAM_HEADER + ["T_skin [kN/m]", "T_lat [kN/m]", "T_lat2 [kN/m]", "F_foot [kN]"]


def triplets(values):
    out = []
    for v in values:
        out += [v, v - 1.0, v + 1.0]
    return out


def plate_row(node, label="Plate\\_6\\_1", local=1, x=0.0, y=0.0, z=0.0, scale=1.0):
    return [label, node, local, x, y, z] + triplets([scale * (node + k) for k in range(8)])


def beam_row(node, label="Beam\\_1\\_1", z=0.0, scale=1.0, extras=None):
    row = [label, node, 1, 0.0, 0.0, z] + triplets([scale * (node + k) for k in range(6)])
    return row + (extras or [])


DEFAULT_NODES = range(1, 6)


def plate_sheet(nodes=DEFAULT_NODES, scale=1.0):
    return [PLATE_HEADER] + [plate_row(n, scale=scale, z=-float(n)) for n in nodes]


def pile_sheet(nodes=DEFAULT_NODES, scale=1.0):
    return [EMBEDDED_HEADER] + [
        beam_row(n, "EmbeddedBeam\\_1\\_1", z=-float(n), scale=scale, extras=[1.0, 2.0, 3.0, "N/A"])
        for n in nodes
    ]


@pytest.fixture
def workbook():
    """A small workbook with all element kinds and a clean set of combinations."""
    wb = {"Portal Frame": []}
    for i, combo in enumerate(["PT-B-Apron", "PT-B-Yard", "QP"]):
        s = 1.0 + i
        wb[f"SPW-{combo}"] = plate_sheet(scale=s)
        wb[f"Deck-{combo}"] = plate_sheet(scale=s)
        wb[f"Pile(1)-{combo}"] = pile_sheet(scale=s)
        wb[f"Pile(2)-{combo}"] = pile_sheet(scale=s)
    return wb
