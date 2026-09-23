import math

import numpy as np
import pytest
from conftest import PLATE_HEADER, pile_sheet

from triton.design.beams import Support, design_beam, layout, shear_stations, station_forces
from triton.design.circular import ConcreteLaw, SteelLaw
from triton.design.crack import crack_width, restraint_factor
from triton.design.governing import workbook
from triton.design.rect import Bars, RectSection
from triton.design.runner import run_section
from triton.geometry import section_geometry
from triton.project import BeamInput, DesignSettings, PileInput, Section
from triton.validation import import_sheets

LAWS = (ConcreteLaw(40), SteelLaw(500))


def test_rectangular_capacity_and_cracked_stress_match_hand_calcs():
    # 1000 x 1000, 5Ø25 at d = 940, 2Ø12 on top.
    bars = Bars.join(Bars.row(5, 25, -440, 400), Bars.row(2, 12, 440, 400))
    sec = RectSection(1000, 1000, bars, *LAWS)
    a_s = 5 * math.pi * 25**2 / 4
    fyd, fcd = 500 / 1.15, 0.85 * 40 / 1.5
    x = a_s * fyd / (0.8 * 1000 * fcd)
    assert sec.m_rd("v", np.array([1]), np.array([0.0]))[0] == pytest.approx(
        a_s * fyd * (940 - 0.4 * x) / 1e6, rel=0.01
    )
    # Hogging is weaker: only the two top bars.
    assert (
        sec.m_rd("v", np.array([-1]), np.array([0.0]))[0]
        < 0.2 * sec.m_rd("v", np.array([1]), np.array([0.0]))[0]
    )
    # Cracked elastic section, αe = 200 / (35 / 3): x = k·d, σs = M / (As (d − x/3)).
    e_c = 35_000 / 3
    ae, rho = 200_000 / e_c, a_s / (1000 * 940)
    k = math.sqrt(2 * ae * rho + (ae * rho) ** 2) - ae * rho
    r = sec.cracked(0.0, 500.0, e_c)
    assert r["x"] == pytest.approx(k * 940, rel=0.02)
    assert -r["stress"][:5].min() == pytest.approx(500e6 / (a_s * (940 - k * 940 / 3)), rel=0.01)


def test_biaxial_interaction_to_5_8_9():
    bars = Bars.join(Bars.row(4, 25, -440, 440), Bars.row(4, 25, 440, 440))
    sec = RectSection(1000, 1000, bars, *LAWS)
    mv = sec.m_rd("v", np.array([1]), np.array([0.0]))[0]
    mh = sec.m_rd("h", np.array([1]), np.array([0.0]))[0]
    # At N = 0, a = 1: the two ratios add.
    u = sec.utilisation(np.array([0.0]), np.array([mv / 2]), np.array([mh / 2]))
    assert u[0] == pytest.approx(1.0, abs=0.01)
    assert sec.utilisation(np.array([0.0]), np.array([mv]))[0] == pytest.approx(1.0, abs=0.01)
    # Pure tension beyond the bars' capacity fails.
    assert sec.utilisation(np.array([-1.1 * bars.total * 500 / 1.15 / 1e3]), np.array([0.0]))[0] > 1


def test_crack_width_to_7_3_4():
    # σs = 250 MPa, Ø20 @ 150 at c = 50, h = 1000, d = 940, x = 250, fctm 3.5, Ecm 35 GPa.
    area = 1000 / 150 * math.pi * 100
    c = crack_width(
        250, h=1000, d=940, x=250, b=1000, area=area, phi=20, cover=50, spacing=150, fctm=3.5, ecm=35_000
    )
    hc = min(2.5 * 60, 750 / 3, 500)
    rho = area / (1000 * hc)
    strain = max((250 - 0.4 * 3.5 / rho * (1 + 200 / 35 * rho)) / 200_000, 0.6 * 250 / 200_000)
    sr = 3.4 * 50 + 0.8 * 0.5 * 0.425 * 20 / rho
    assert c["wk"] == pytest.approx(sr * strain, abs=0.002)
    assert (
        crack_width(
            -10, h=1000, d=940, x=250, b=1000, area=area, phi=20, cover=50, spacing=150, fctm=3.5, ecm=35_000
        )["wk"]
        == 0
    )


def test_restraint_factor_from_length_over_height():
    assert restraint_factor(30, 2) == pytest.approx((13 / 16) ** (1 / 15))
    assert restraint_factor(4, 2) == pytest.approx((1 / 12) ** 0.5)
    assert restraint_factor(1, 2) == 0
    assert restraint_factor(60, 2) > restraint_factor(10, 2)


def beam_rows(fn, width=2.0, length=12.0, z=2.7):
    """Plate rows of a beam along Y centred on X = 0; fn(x, y) -> the 8 plate actions."""
    rows = [PLATE_HEADER]
    node = 1
    for y in np.arange(0.0, length + 1e-9, 0.25):
        for x in np.linspace(-width / 2, width / 2, 5):
            vals = fn(x, y)
            row = ["Plate\\_1\\_1", node, 1, float(x), float(y), z]
            for v in vals:
                row += [v, min(v, 0.0), max(v, 0.0)]
            rows.append(row)
            node += 1
    return rows


def uniform(n2=0.0, m22=0.0, q23=0.0, m11=0.0, grad_n2=0.0):
    # N_1, N_2, Q_12, Q_23, Q_13, M_11, M_22, M_12
    return lambda x, y: [0.0, n2 + grad_n2 * x, 0.0, q23, 0.0, m11, m22, 0.0]


def test_section_forces_are_integrated_across_the_width():
    raw = {
        "Front Beam-PT-B-Apron": beam_rows(uniform(n2=-50.0, m22=100.0, q23=30.0, grad_n2=20.0)),
        "Front Beam-QP": beam_rows(uniform(m22=60.0)),
    }
    sheets = import_sheets(raw).elements()["Front Beam"]
    lay = layout(sheets, {"1": "X", "2": "Y"})
    assert (lay.along, lay.span_local, lay.width) == ("Y", "2", 2.0)
    f = station_forces(sheets["PT-B-Apron"].frame, lay, 1.0, np.array([6.0]))
    r = f.iloc[0]
    assert r["N"] == pytest.approx(100.0)  # 50 kN/m tension × 2 m, concrete sign
    assert r["Mv"] == pytest.approx(200.0)
    assert r["V"] == pytest.approx(60.0)
    # N2 = 20·x kN/m across a 2 m width: Mh = 20·2³/12, compression on the −x side.
    assert abs(r["Mh"]) == pytest.approx(20 * 8 / 12)
    assert station_forces(sheets["PT-B-Apron"].frame, lay, -1.0, np.array([6.0])).iloc[0][
        "Mv"
    ] == pytest.approx(-200.0)


def test_shear_falls_back_to_midway_between_close_supports():
    s = np.arange(0, 6.01, 0.1)
    sup = [Support("P", 0.0, 0.0, 0.6), Support("P", 3.0, 0.0, 0.6), Support("P", 6.0, 0.0, 0.6)]
    keep, fallback = shear_stations(s, sup, 1.9)
    assert fallback
    assert sorted(np.round(s[keep], 2)) == [1.5, 4.5]
    keep, fallback = shear_stations(np.arange(0, 12.01, 0.1), sup[:1], 1.0)
    assert not fallback and keep.sum() == len(np.arange(1.6, 12.01, 0.1))


def pile_line(y_positions, z_top=2.7):
    rows = pile_sheet()[:1]
    for i, y in enumerate(y_positions):
        for j, z in enumerate((z_top, z_top - 5)):
            row = ["EmbeddedBeam\\_1\\_1", 100 + 2 * i + j, 1, 0.0, float(y), z]
            row += [-1000.0, -1001.0, 0.0] + [0.0, 0.0, 0.0] * 5 + [1.0, 2.0, 3.0, "N/A"]
            rows.append(row)
    return rows


def test_design_beam_with_supports_and_export():
    peak = uniform(m22=150.0, q23=80.0, m11=300.0)

    def with_peaks(x, y):
        v = peak(x, y)
        if math.hypot(x, y - 6.0) < 0.55:  # FE peak inside the pile at y = 6
            v[6] = 5000.0
        return v

    raw = {
        "Front Beam-PT-B-Apron": beam_rows(with_peaks),
        "Front Beam-QP": beam_rows(uniform(m22=100.0, m11=200.0)),
        "Pile(1)-PT-B-Apron": pile_line([0.0, 6.0, 12.0]),
        "Pile(1)-QP": pile_line([0.0, 6.0, 12.0]),
    }
    wb = import_sheets(raw)
    els = {"Front Beam": BeamInput(depth=1500), "Pile(1)": PileInput(head_level=2.7)}
    d = design_beam(
        "Front Beam",
        els["Front Beam"],
        DesignSettings(),
        wb.elements()["Front Beam"],
        section_geometry(wb),
        els,
        {"1": "X", "2": "Y"},
    )
    assert [q["s"] for q in d["supports"]] == [0.0, 6.0, 12.0]
    assert d["passed"] and d["utilisation"] <= 1
    # The 5000 kNm/m peak lies inside the pile at y = 6 and is left out.
    assert d["bending"]["extremes"]["Mv"]["max"] < 1000
    assert set(d["cracks"]) == {"bottom"} and d["cracks"]["bottom"]["limit"] == 0.2
    assert d["restraint"]["R"] == pytest.approx(restraint_factor(58, 1.5), abs=1e-3)
    assert d["shear"]["link"]["legs"] >= 2 and d["transverse"]["bottom"]["label"].startswith("Ø")
    assert d["steel"]["kg_per_m3"] > 0
    assert len(d["governing_sets"][0]["uls"]) == 7 and len(d["governing_sets"][0]["qp"]) == 7

    res = run_section(DesignSettings(), Section(elements=els), wb)
    assert [b["element"] for b in res["beams"]] == ["Front Beam"]
    from io import BytesIO

    from openpyxl import load_workbook

    ws = load_workbook(BytesIO(workbook("P", "S", res)))["Concrete"]
    titles = [r[0] for r in ws.iter_rows(values_only=True) if r[0] and str(r[0]).startswith("Front Beam")]
    assert titles and "M3 vertical" in titles[0]


def test_bollard_ties_as_the_office_drawing():
    from triton.design.bollard import check_bollard
    from triton.project import Bollard, TieBars

    d = check_bollard(Bollard(), "C40/50", DesignSettings())
    # 150 t × 1.5 × g square to the quay; 2Ø32 straight and 3Ø32 at ±45°, 8.11° down.
    assert d["F_Ed_kN"] == round(1.5 * 150 * 9.81)
    fyd = 500 / 1.15
    bar = math.pi * 32**2 / 4 * fyd * math.cos(math.radians(8.11)) / 1e3
    assert d["R_kN"] == pytest.approx(2 * bar + 6 * bar * math.cos(math.pi / 4), abs=2)
    assert d["tie_utilisation"] == pytest.approx(d["F_Ed_kN"] / d["R_kN"], abs=2e-3)
    more = check_bollard(
        Bollard(ties=[TieBars(count=2), TieBars(count=6, angle=45), TieBars(count=6, angle=-45)]),
        "C40/50",
        DesignSettings(),
    )
    assert more["passed"] and more["laps"][0]["l0_mm"] <= 1600
