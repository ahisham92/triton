import math

import numpy as np
import pandas as pd
import pytest
from test_design import pile_sheets

from triton.design.crack import K1, K3, K4, KT
from triton.design.pile_cracks import cracked_stress, pile_crack_widths
from triton.design.piles import design_pile
from triton.project import Casing, DesignSettings, PileInput

D, RING = 1200, (26, 32, 1200 / 2 - 75 - 12 - 16)


def test_cracked_circle_is_in_equilibrium():
    count, phi, r = RING
    ang = 2 * math.pi * np.arange(count) / count
    depth, area = D / 2 - r * np.cos(ang), np.full(count, math.pi * phi**2 / 4)
    n, m = np.array([-2000.0, 0.0, 1500.0]), np.array([0.0, 1000.0, 1000.0])
    e_c = 35_000 / 3
    e0, k, x = cracked_stress(D, depth, area, n, m, e_c)
    y = np.linspace(0, D, 20001)
    yc = (y[:-1] + y[1:]) / 2
    w = 2 * np.sqrt(np.maximum((D / 2) ** 2 - (yc - D / 2) ** 2, 0)) * D / 20000
    for i in range(3):
        sc = e_c * np.maximum(e0[i] - k[i] * yc, 0)
        ss = 200_000 * (e0[i] - k[i] * depth)
        assert ((sc * w).sum() + (ss * area).sum()) / 1e3 == pytest.approx(n[i], abs=1)
        assert ((sc * w * (D / 2 - yc)).sum() + (ss * area * (D / 2 - depth)).sum()) / 1e6 == pytest.approx(
            m[i], abs=1
        )
    assert x[0] == 0 and 0 < x[1] < x[2] < D


def test_pile_crack_width_in_pure_tension():
    qp = pd.DataFrame({"N": [-2000.0, 3000.0], "M": [0.0, 50.0]})
    r = pile_crack_widths(D, [RING], qp, fctm=3.5, ecm=35_000, creep=2.0)
    count, phi, radius = RING
    a_s = count * math.pi * phi**2 / 4
    sigma = 2000e3 / a_s
    assert r["sigma_s"].iloc[0] == pytest.approx(sigma, rel=1e-3)
    # Ring of 2.5(h − d) round the perimeter, all bars in it, k2 = 1.
    c = D / 2 - radius - phi / 2
    hc = 2.5 * (D / 2 - radius)
    rho = a_s / (math.pi * ((D / 2) ** 2 - (D / 2 - hc) ** 2))
    sr = K3 * c + K1 * 1.0 * K4 * phi / rho
    strain = max((sigma - KT * 3.5 / rho * (1 + 200 / 35 * rho)) / 200_000, 0.6 * sigma / 200_000)
    assert r["wk"].iloc[0] == pytest.approx(sr * strain, rel=1e-3)
    assert r["wk"].iloc[1] == 0  # wholly compressed


def test_pile_design_checks_cracks_outside_the_casing():
    uls = [(i + 1, -1.0 * i, -3000.0, 2500.0 * math.exp(-i / 4), 0.0) for i in range(21)]
    plain = design_pile("Pile(1)", PileInput(head_level=0.0), DesignSettings(), pile_sheets(uls)).to_dict()
    c = plain["cracks"]
    assert c["passed"] and 0 < c["wk_mm"] <= c["limit_mm"] and c["governing"]["z"] == 0.0
    assert c["profile"][0]["z"] == 0.0 and c["governing"]["cage"]
    # A tight limit needs more steel than strength alone.
    tight = design_pile(
        "Pile(1)", PileInput(head_level=0.0, crack_width_limit=0.2), DesignSettings(), pile_sheets(uls)
    ).to_dict()
    assert tight["passed"] and tight["cracks"]["wk_mm"] <= 0.2 < plain["cracks"]["wk_mm"]
    assert tight["arrangement"]["area_mm2"] > plain["arrangement"]["area_mm2"]
    cased = design_pile(
        "Pile(1)",
        PileInput(head_level=0.0, casing=Casing(top_level=0.0, bottom_level=-4.0)),
        DesignSettings(),
        pile_sheets(uls),
    ).to_dict()
    assert cased["cracks"]["governing"]["z"] < -4.0 and "casing" in cased["cracks"]
