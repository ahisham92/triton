import math

import pandas as pd
import pytest

from triton.elements import BEAM_ACTIONS, parse_sheet_name
from triton.forces import CombiSection, design_forces, split_combi_wall

ACTIONS = list(BEAM_ACTIONS)


def frame(zs):
    rows = []
    for i, z in enumerate(zs):
        row = {"Node": i + 1, "X": 0.0, "Y": 0.0, "Z": z}
        row.update({a: 100.0 for a in ACTIONS})
        row.update({f"{a}_min": 0.0 for a in ACTIONS})
        rows.append(row)
    return pd.DataFrame(rows)


def test_concrete_elements_flip_axial_force_only():
    spec = parse_sheet_name("Pile(2)-QP").spec
    out = design_forces(frame([0.0]), spec, ACTIONS)
    assert out.loc[0, "N"] == -100.0
    assert out.loc[0, "M_3"] == 100.0
    assert "N_min" not in out.columns  # design uses phase values only


def test_steel_elements_keep_plaxis_sign():
    spec = parse_sheet_name("SPW-QP").spec
    f = frame([0.0]).rename(columns={"N": "N_1"})
    assert design_forces(f, spec, ["N_1"]).loc[0, "N_1"] == 100.0


def test_combi_section_stiffness_share():
    sec = CombiSection(1.0, 0.02, 0.0, e_steel=2.0, e_concrete=1.0)
    i_s = math.pi / 64 * (1.0**4 - 0.96**4)
    i_c = math.pi / 64 * 0.96**4
    assert sec.steel_share == pytest.approx(2 * i_s / (2 * i_s + i_c))
    corroded = CombiSection(1.0, 0.02, 0.003, e_steel=2.0, e_concrete=1.0)
    assert corroded.steel_share < sec.steel_share


def test_combi_split_by_level():
    sec = CombiSection(1.0, 0.02, 0.002)
    parts = split_combi_wall(frame([2.7, -25.0, -30.0]), sec, ACTIONS)
    steel, concrete = parts["steel"], parts["concrete"]
    share = sec.steel_share
    assert list(steel["zone"]) == ["composite", "composite", "steel_only"]
    assert steel.loc[0, "M_3"] == pytest.approx(100 * share)
    assert steel.loc[2, "M_3"] == 100.0
    assert list(concrete["Z"]) == [2.7, -25.0]
    assert concrete.loc[0, "M_3"] == pytest.approx(100 * (1 - share))
    assert concrete.loc[0, "N"] == pytest.approx(-100 * (1 - share))  # concrete sign convention


def test_combi_section_rejects_bad_input():
    with pytest.raises(ValueError):
        CombiSection(1.0, 0.6, 0.0)
    with pytest.raises(ValueError):
        CombiSection(1.0, 0.02, 0.02)
