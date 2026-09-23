import math

import pytest
from conftest import EMBEDDED_HEADER

from triton.design.piles import design_pile
from triton.project import DesignSettings, PileInput
from triton.validation import import_sheets


def rows(loads):
    """Embedded beam rows from (node, z, N, Q_12, M_2) in Plaxis signs."""
    out = [EMBEDDED_HEADER]
    for node, z, n, q, m in loads:
        row = ["EmbeddedBeam\\_1\\_1", node, 1, 0.0, 0.0, z]
        for v in (n, q, 0.0, 0.0, m, 0.0):
            row += [v, v - 1.0, v + 1.0]
        out.append(row + [1.0, 2.0, 3.0, "N/A"])
    return out


def design(loads, **pile):
    sheets = import_sheets({"Pile(1)-PT-B-Apron": rows(loads), "Pile(1)-QP": rows(loads)}).elements()[
        "Pile(1)"
    ]
    settings = DesignSettings()
    settings.piles.curtail = False
    return design_pile("Pile(1)", PileInput(head_level=0.0, **pile), settings, sheets).to_dict()


def column(n=0.0, q=100.0, m=500.0):
    return [(i + 1, -0.5 * i, n, q, m) for i in range(41)]


def test_concrete_shear_resistance_by_hand():
    d = design(column(q=100.0))
    sh = d["shear"]
    a = d["arrangement"]
    rs = a["rings"][0]["radius"]
    dd = 600 + 2 * rs / math.pi
    k = 1 + math.sqrt(200 / dd)
    rho = a["area_mm2"] / 2 / (1200 * dd)
    v = max(0.18 / 1.5 * k * (100 * rho * 40) ** (1 / 3), 0.035 * k**1.5 * math.sqrt(40))
    assert sh["governing"]["VRd_c_kN"] == pytest.approx(v * 1200 * dd / 1e3, rel=1e-3)
    assert sh["passed"] and {z["reason"] for z in sh["zones"]} <= {"near slab", "minimum"}


def test_detailing_zones():
    sh = design(column())["shear"]
    top = sh["zones"][0]
    assert top["reason"] == "near slab" and top["top"] == 0.0 and top["bottom"] == pytest.approx(-1.2)
    s_max = sh["max_spacing_mm"]
    assert top["spacing_mm"] <= 0.6 * s_max and sh["zones"][-1]["spacing_mm"] <= s_max
    assert all(z["top"] - z["bottom"] >= 1.0 - 1e-9 for z in sh["zones"])
    assert sh["zones"][-1]["bottom"] == -20.0


def test_no_concrete_shear_in_tension():
    # Plaxis N > 0 is tension; it stays tension in the design sign (negative).
    sh = design(column(n=500.0, q=1000.0))["shear"]
    assert sh["governing"]["VRd_c_kN"] == 0.0
    assert any(z["reason"] == "shear" for z in sh["zones"])
    assert sh["passed"] and sh["utilisation"] <= 1.0


def test_high_shear_needs_closer_links_and_crushing_is_reported():
    near = design(column(q=1500.0))["shear"]
    assert near["passed"] and min(z["spacing_mm"] for z in near["zones"]) < 300
    crush = design(column(q=20000.0))["shear"]
    assert not crush["passed"] and any("crushes" in n for n in crush["notes"])


def test_link_size_rule():
    sh = design(column(), link_diameter=6.0)["shear"]
    assert sh["min_link_diameter_mm"] >= 6
    d = design(column(m=6000.0), link_diameter=6.0)
    if max(r["diameter"] for r in d["arrangement"]["rings"]) > 24:
        assert any("9.5.3" in n for n in d["shear"]["notes"])


def test_steel_totals_include_links():
    d = design(column())
    st = d["steel"]
    assert st["total_kg"] == pytest.approx(st["longitudinal_kg"] + st["links_kg"], abs=0.2)
    assert st["kg_per_m3"] == pytest.approx(st["total_kg"] / (math.pi * 0.36 * 20), rel=1e-3)
