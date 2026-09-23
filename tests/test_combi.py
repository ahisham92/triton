import math

import numpy as np
import pytest
from test_design import pile_rows

from triton.design.combi import design_combi_wall
from triton.design.export import pile_cages
from triton.design.runner import run_section
from triton.design.tube import Tube, check_tube, plastic_utilisation
from triton.project import CombiWallInput, DesignSettings, Section
from triton.validation import import_sheets

TUBE = Tube(1626, 18, 3, "S355")


def test_corroded_tube_properties():
    d, t = 1620, 15
    assert TUBE.fy == 345  # t > 16 mm
    assert TUBE.area == pytest.approx(math.pi / 4 * (d**2 - (d - 2 * t) ** 2))
    assert TUBE.w_pl == pytest.approx((d**3 - (d - 2 * t) ** 3) / 6)
    r = TUBE.resistances()
    assert r["V_pl_kN"] == pytest.approx(2 * TUBE.area / math.pi * 345 / math.sqrt(3) / 1e3)
    # d/t = 108 > 90ε² = 61: class 4 where the tube is empty.
    assert TUBE.section_class == 4
    assert Tube(610, 25, 0, "S355").section_class == 1


def test_shell_buckling_to_en_1993_1_6():
    # Hand calculation, EN 1993-1-6 D.1.2 with class B (Q = 25), r = 802.5, t = 15.
    b = TUBE.buckling()
    dwk_t = math.sqrt(802.5 / 15) / 25
    alpha = 0.62 / (1 + 1.91 * dwk_t**1.44)
    lam = math.sqrt(345 / (0.605 * 210_000 * 15 / 802.5))
    chi = 1 - 0.6 * (lam - 0.2) / (math.sqrt(alpha / 0.4) - 0.2)
    assert b["alpha"] == pytest.approx(alpha, abs=1e-3)
    assert b["chi"] == pytest.approx(chi, abs=1e-3)
    assert b["sigma_Rd_MPa"] == pytest.approx(chi * 345 / 1.1, abs=0.1)
    chi_c, chi_a = (Tube(1626, 18, 3, "S355", q).buckling()["chi"] for q in "CA")
    assert chi_c < b["chi"] < chi_a


def test_plastic_tube_interaction():
    r = TUBE.resistances()
    npl, mpl = r["N_pl_kN"], r["M_pl_kNm"]
    n = np.array([npl / 2, npl / 2, 0.0, -npl / 2])
    m = np.array([0.0, mpl * math.cos(math.pi / 4), mpl / 2, mpl * math.cos(math.pi / 4)])
    u = plastic_utilisation(n, m, np.zeros(4), TUBE)
    assert u == pytest.approx([0.5, 1.0, 0.5, 1.0], abs=1e-6)
    # High shear reduces the resistance (EN 1993-1-1 6.2.8).
    assert plastic_utilisation(n[:1], m[:1], np.array([0.75 * r["V_pl_kN"]]), TUBE)[0] > 0.5


def loads_frame(rows):
    import pandas as pd

    return pd.DataFrame(rows, columns=["combination", "Node", "Z", "N", "V", "M", "filled"])


def test_en_1993_check_uses_full_section_where_filled():
    # Same forces: plastic where filled (EN 1993-5 5.5.4(9)), buckling where empty.
    m = 9000.0
    out = check_tube(TUBE, loads_frame([("A", 1, -10.0, -1000.0, 0.0, m, True)]), method="ec3")
    empty = check_tube(TUBE, loads_frame([("A", 1, -30.0, -1000.0, 0.0, m, False)]), method="ec3")
    assert out["governing"]["zone"] == "concrete filled"
    assert empty["utilisation"] > out["utilisation"]
    sigma = 1000e3 / TUBE.area + m * 1e6 / TUBE.w_el
    assert empty["utilisation"] == pytest.approx(sigma / TUBE.buckling()["sigma_Rd_MPa"], abs=2e-3)


# Moment fading with depth; the tube runs on below the infill at -25 m.
LOADS = [(i + 1, 1.0 - i, -2000.0 - 50.0 * i, 12000.0 * math.exp(-i / 8), 0.0) for i in range(36)]


def combi_sheets():
    raw = {"Combi Wall-PT-B-Apron": pile_rows(LOADS), "Combi Wall-QP": pile_rows(LOADS)}
    return import_sheets(raw)


def test_combi_wall_design():
    wall = CombiWallInput(top_level_to_ignore=0.0)
    w = design_combi_wall("Combi Wall", wall, DesignSettings(), combi_sheets().elements()["Combi Wall"])
    share = w["steel_share"]
    assert 0.3 < share < 0.36  # the 67% concrete check from the design office
    infill, tube = w["infill"], w["tube"]
    assert infill["section"]["diameter_mm"] == 1590
    assert infill["section"]["head_level_m"] == 0.0 and infill["section"]["toe_level_m"] == -25.0
    # The infill gets its share, with the concrete sign (compression +).
    g = infill["governing"]
    z = g["z"]
    src = next(x for x in LOADS if abs(x[1] - z) < 1e-6)
    assert g["M_kNm"] == pytest.approx(src[3] * (1 - share), rel=1e-3)
    assert g["N_kN"] == pytest.approx(-src[2] * (1 - share), rel=1e-3)
    # The tube carries everything below the infill.
    below = [p for p in tube["profile"] if p["z"] < -25]
    assert below and min(p["z"] for p in tube["profile"]) == -34.0
    assert max(p["M_kNm"] for p in tube["profile"] if p["z"] == -26.0) == pytest.approx(
        12000 * math.exp(-27 / 8), rel=1e-3
    )
    assert w["utilisation"] == max(infill["utilisation"], tube["utilisation"])
    assert w["count"] == 1 and infill["count"] == 1
    # The tube is a casing: the infill's QP sets are 1s.
    assert all(r["N_kN"] == 1.0 for st in infill["governing_sets"] for r in st["qp"])


def test_section_run_and_export_include_the_combi_wall():
    section = Section()
    section.add_elements(["Combi Wall"])
    out = run_section(DesignSettings(), section, combi_sheets())
    (w,) = out["combi_walls"]
    assert out["piles"] == []
    cages = pile_cages("Berth", out)
    (c,) = cages["piles"]
    assert c["part"] == "infill" and c["diameter_mm"] == 1590


def test_class_4_effective_properties_as_the_office_sheets():
    tube = Tube(1626, 18, 4.5, "S355")  # 50-year splash, d/t = 120
    eps2, ratio = 235 / 345, 1617 / 13.5  # fy 345 for the 18 mm plate
    assert tube.a_eff / tube.area == pytest.approx(math.sqrt(90 * eps2 / ratio))  # about 0.72
    assert tube.w_eff / tube.w_el == pytest.approx((140 * eps2 / ratio) ** 0.25)  # about 0.94
    thick = Tube(610, 25, 0, "S355")
    assert (thick.a_eff, thick.w_eff) == (thick.area, thick.w_el)


def test_tube_share_and_check_options_and_column_buckling():
    els = combi_sheets().elements()["Combi Wall"]
    split = design_combi_wall("Combi Wall", CombiWallInput(top_level_to_ignore=0.0), DesignSettings(), els)
    assert split["tube"]["method"] == "office"
    wall = CombiWallInput(top_level_to_ignore=0.0, tube_share="all")
    alone = design_combi_wall("Combi Wall", wall, DesignSettings(), els)
    assert alone["tube"]["utilisation"] > split["tube"]["utilisation"]
    ec3 = CombiWallInput(top_level_to_ignore=0.0, tube_check="ec3")
    plastic = design_combi_wall("Combi Wall", ec3, DesignSettings(), els)["tube"]
    assert (
        plastic["method"] == "ec3"
        and plastic["governing"]["M_Rd_kNm"] > split["tube"]["governing"]["M_Rd_kNm"]
    )
    col = alone["tube"]["column"]
    assert col["buckling_length_m"] == pytest.approx(0.7 * col["length_m"], abs=0.02)
    assert 0 < col["chi"] < 1 and col["curve"] == "c" and col["N_b_Rd_kN"] > 0
    assert col["length_m"] == pytest.approx(34.0)  # from the ignored top at 0 to the toe


def test_corrosion_zones_set_the_tube_per_level():
    zones = [
        {"bottom_level": -5.0, "outside": 4.5, "inside": 0.0},
        {"bottom_level": -40.0, "outside": 1.75, "inside": 1.75},
    ]
    wall = CombiWallInput(top_level_to_ignore=0.0, corrosion_zones=zones)
    w = design_combi_wall("Combi Wall", wall, DesignSettings(), combi_sheets().elements()["Combi Wall"])
    top, low = w["tube"]["zones"]
    assert (top["outside_mm"], low["outside_mm"], low["inside_mm"]) == (4.5, 1.75, 1.75)
    assert top["t_mm"] == pytest.approx(18 - 4.5) and low["t_mm"] == pytest.approx(18 - 3.5)
    assert low["top"] == -5.0 and low["bottom"] == -40.0
    with pytest.raises(ValueError):
        CombiWallInput(corrosion_zones=[{"bottom_level": -5.0, "outside": 10.0, "inside": 9.0}])


def test_infill_has_no_crack_check_inside_the_tube():
    wall = CombiWallInput(top_level_to_ignore=0.0)
    w = design_combi_wall("Combi Wall", wall, DesignSettings(), combi_sheets().elements()["Combi Wall"])
    cracks = w["infill"]["cracks"]
    assert cracks["wk_mm"] is None and cracks["passed"] and "permanent casing" in cracks["casing"]
