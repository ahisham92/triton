"""Sheet pile wall design (EN 1993-5, as ArcelorMittal Durability 4.2.1)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from triton.design.sheet_piles import (
    SECTIONS,
    Options,
    evaluate,
    idealised,
    normalise,
    reduced,
    rho_p,
)
from triton.design.spw_design import design_spw, loss_at
from triton.importer import SheetData
from triton.project import SheetPileInput, SheetPileZone


def _props(tf, tw, h, area, inertia, wel, wpl, b, alpha, c, av):
    a = np.array
    return {
        "tf": a(tf), "tw": a(tw), "h": a(h), "area": a(area), "inertia": a(inertia), "wel": a(wel),
        "wpl": a(wpl), "b": b, "alpha": alpha, "c": c, "av": a(av),
    }  # fmt: skip


# Durability Part C, example 1.3: AU 25 S 320 GP head wall after 2.35 mm, NAD UK factors, l = 8 m.
AU25 = _props(12.15, 7.85, 447.6, 156.0, 47020, 2095, 2400, 406, 59.6, 252.5, 45.6)
AU25_OPTS = Options(fy=320, gamma_m0=1.0, gamma_m1=1.0, buckling_length=8, kind="U", beta_b=0.8, beta_d=0.55)


def test_durability_au25_head_wall():
    levels = [(-191, 317, 68, 38), (389, -9, 140, 41)]  # z = -0.75 and -4.40 m
    M = [abs(m) + n * e / 1000 for m, _, n, e in levels]
    r = evaluate(AU25, 750, AU25_OPTS, M, [v for _, v, _, _ in levels], [n for _, _, n, _ in levels])
    assert r["class"].tolist() == [3, 3]
    assert r["Mc"][0] == pytest.approx(536, abs=1)
    assert r["Vpl"][0] == pytest.approx(842, abs=1)
    assert r["Npl"][0] == pytest.approx(4992, abs=1)
    assert r["Ncr"][0] == pytest.approx(8375, abs=1)
    assert r["c_tw_eps"][0] == pytest.approx(37.5, abs=0.1)
    assert np.isnan(r["web_buckling"]).all()  # 37.5 ≤ 72: not needed
    # Durability: Uf 0.38 and 0.74.
    assert np.round(r["uf"], 2).tolist() == [0.38, 0.74]


def test_durability_au14_anchor_wall_class_4():
    # AU 14 S 460 AP after 1.2 mm: class 4, taken as class 3 with a reduced fy. Durability shows
    # fy,red 416.3 MPa; the manual's formula 235 k² tf² / b² gives 408.1, so Triton is 2 % lower.
    p = _props(8.8, 7.1, 406.8, 117.3, 25260, 1240, 1240, 327.2, 47.8, 268.6, 37.7)
    o = Options(fy=460, gamma_m0=1.0, gamma_m1=1.0, buckling_length=7, kind="U", beta_b=0.8, beta_d=0.55)
    r = evaluate(p, 750, o, [362], [275], [125])
    assert r["class"][0] == 4
    assert r["fy_used"][0] == pytest.approx(408.1, abs=0.1)
    assert r["Ncr"][0] == pytest.approx(5877, rel=0.002)
    assert r["uf"][0] == pytest.approx(0.88, abs=0.02)


def test_catalogue_and_idealised_section():
    assert normalise("az14-770") == "AZ 14-770"
    assert normalise("") == "AZ 14-770"
    for name, s in SECTIONS.items():
        p = reduced(name, 0.0)
        assert float(p["area"]) == pytest.approx(s.area)
        assert float(p["inertia"]) == pytest.approx(s.inertia)
        assert float(p["wel"]) == pytest.approx(s.wel)
        assert float(p["wpl"]) == pytest.approx(s.wpl)
        # The idealised double pile fitted to A and I lands close to the catalogue Wpl and Wel.
        from triton.design.sheet_piles import _thin

        bf, ai = idealised(name)
        t = _thin(s, bf, ai, 0.0)
        assert float(t["wpl"]) == pytest.approx(s.wpl, rel=0.025), name
        assert float(t["inertia"]) * 0.1 / (s.h / 2) * 10 == pytest.approx(s.wel, rel=0.01), name


def test_corrosion_takes_thickness_off_every_plate():
    p = reduced("AZ 14-770", np.array([0.0, 2.35]))
    assert p["tf"].tolist() == [9.5, 7.15]
    assert p["h"].tolist() == [345.0, 342.65]
    # Thin plates: the moduli drop about in proportion to the thickness, as Durability's charts show.
    assert p["wel"][1] / p["wel"][0] == pytest.approx(7.15 / 9.5, abs=0.02)
    assert p["av"][0] == pytest.approx((345 - 9.5) * 9.5 / 770 * 10)


def test_rho_p():
    assert rho_p(4.0, 45) == 1.0
    assert rho_p(10.0, 40) == 0.95
    assert rho_p(20.0, 50) == 0.60
    assert rho_p(12.5, 35) == pytest.approx(0.95)


def _sheets(n=-400.0, v=150.0, m=120.0):
    rows = []
    for combo, k in (("PT-B-Apron", 1.0), ("PT-C-Apron", 1.2)):
        z = np.linspace(2.0, -19.0, 43)
        f = pd.DataFrame(
            {
                "Node": np.arange(len(z)),
                "X": 0.0,
                "Y": np.where(np.arange(len(z)) % 2, 1.0, 5.0),
                "Z": z,
                "N_1": n * k,
                "M_11": m * k * np.sin(np.linspace(0, 3, len(z))),
                "M_22": 5.0,
                "Q_13": v * k,
                "Q_23": 1.0,
            }
        )
        rows.append((combo, SheetData(f"SPW-{combo}", None, f)))
    return dict(rows)


def test_zones_top_down():
    wall = SheetPileInput(
        corrosion_zones=[
            SheetPileZone(bottom_level=0, front=2, back=0),
            SheetPileZone(bottom_level=-10, front=1, back=1),
        ]
    )
    front, back, zone = loss_at(np.array([3.0, 0.0, -0.1, -10.0, -12.0]), wall)
    assert zone.tolist() == [1, 1, 2, 2, 2]
    assert (front + back).tolist() == [2, 2, 2, 2, 2]


def test_design_with_n_left_out_shows_both():
    wall = SheetPileInput(buckling_length=12.0)
    d = design_spw("SPW", wall, 355.0, _sheets(n=-900.0))
    assert d["section"] == "AZ 14-770" and not d["adjusted"]
    assert d["designed"]["governing"]["governs"] in ("buckling", "bending_axial")
    wall = SheetPileInput(
        buckling_length=12.0, ignore=[{"combination": "All combinations", "ignore_n": True}]
    )
    d2 = design_spw("SPW", wall, 355.0, _sheets(n=-900.0))
    assert d2["adjusted"]
    assert d2["as_plaxis"]["uf"] == pytest.approx(d["uf"])  # the check with every action is kept
    assert d2["uf"] < d["uf"]
    assert d2["designed"]["governing"]["N"] == 0
    assert [c["ignore_n"] for c in d2["by_combination"]] == [True, True]
    # One combination only.
    wall = SheetPileInput(buckling_length=12.0, ignore=[{"combination": "PT-C-Apron", "ignore_n": True}])
    d3 = design_spw("SPW", wall, 355.0, _sheets(n=-900.0))
    assert {c["combination"]: c["ignore_n"] for c in d3["by_combination"]} == {
        "PT-B-Apron": False,
        "PT-C-Apron": True,
    }
    assert d3["designed"]["governing"]["combination"] == "PT-B-Apron"


def test_king_piles_left_out_and_summary():
    d = design_spw("SPW", SheetPileInput(buckling_length=10), 355.0, _sheets(), king_piles=[(0.0, 5.0, 0.8)])
    assert d["points"] == 2 * 21  # every other node sits in a king pile
    grades = d["uf_summary"]["grades"]
    assert "S460AP" in grades
    row = next(r for r in d["uf_summary"]["rows"] if r["section"] == "AZ 14-770")
    assert row["uf"]["S355GP"] == pytest.approx(d["uf"], abs=0.006)


def test_corrosion_through_the_section_is_an_error():
    wall = SheetPileInput(
        section_name="AZ 12-770", corrosion_zones=[SheetPileZone(bottom_level=-30, front=5, back=4)]
    )
    assert "eats the whole" in design_spw("SPW", wall, 355.0, _sheets())["error"]


def test_saved_walls_still_open():
    old = {"kind": "sheet_pile_wall", "section_name": "AZ 26-700", "section_class": 2, "area": 187.0}
    w = SheetPileInput.model_validate(old)
    assert w.section_name == "AZ 26-700" and w.class_from == "catalogue"
    assert SheetPileInput.model_validate({"section_name": "PU 22"}).section_name == "AZ 14-770"
