"""Slabs with circular voids (PVC pipes): layout, voided section, shear in the webs, punching."""

from __future__ import annotations

import math

import numpy as np
import pytest
from test_slabs import design_deck

from triton.design import slabs
from triton.design import voids as vd
from triton.materials import concrete
from triton.project import SlabVoids


def test_voided_section_is_the_solid_one_while_the_block_stays_in_the_flange():
    h, d = 700, 620
    m = np.array([100.0, 300.0, 600.0, 900.0])
    n = np.array([0.0, 50.0, 100.0, -200.0])
    solid = slabs.required_as(m, n, h, d, 40, 435, 80)[0]
    for kind in ("circles", "through"):
        sec = vd.VoidSection(h, 500, 350, 700, kind)
        assert vd.required_as(m, n, h, d, 40, 435, 80, sec, "bottom")[0] == pytest.approx(solid, rel=2e-3)
    c = concrete("C40/50")
    sec = vd.VoidSection(h, 300, 450, 700, "circles")  # 300 mm solid above the voids
    x, z = vd.cracked_axis(2000, d, 200_000 / (c.ecm / 3), sec, "bottom")
    ae, rho = 200_000 / (c.ecm / 3), 2000 / (1000 * d)
    k = math.sqrt(2 * ae * rho + (ae * rho) ** 2) - ae * rho
    assert x == pytest.approx(k * d, rel=1e-3) and z == pytest.approx(d - k * d / 3, rel=1e-3)
    assert vd.mrd(3000, d, h, 0, 26.7, 435, sec, "bottom") == pytest.approx(
        slabs.strip_mrd(3000, d, h, 0, 26.7, 435), rel=1e-3
    )


def test_block_past_the_flange_needs_more_steel_or_compression_bars():
    h, d = 700, 620
    m, n = np.array([2000.0]), np.array([0.0])
    solid, _, s2 = slabs.required_as(m, n, h, d, 40, 435, 80)
    assert s2[0] == 0
    # Along a void only 100 mm of concrete is left above it: compression bars take the rest.
    thr = vd.VoidSection(h, 500, 350, 700, "through")
    a_s, _, a_s2 = vd.required_as(m, n, h, d, 40, 435, 80, thr, "bottom")
    assert a_s2[0] > 1000 and a_s[0] > 0
    # Across the voids the webs help: a little more tension steel than solid, no compression bars.
    cir = vd.VoidSection(h, 500, 350, 700, "circles")
    a_c, _, c2 = vd.required_as(m, n, h, d, 40, 435, 80, cir, "bottom")
    assert a_c[0] > solid[0] and c2[0] == 0
    assert vd.mrd(8000, d, h, 0, 26.7, 435, thr, "bottom") < slabs.strip_mrd(8000, d, h, 0, 26.7, 435)


def test_layout_between_the_beam_faces_and_clear_of_the_piles():
    v = SlabVoids(diameter=500, spacing=700, first_at=0.0, at_piles="leave_out")
    beams = [
        {"type": "front_beam", "box": {"X": [-1.0, 1.0], "Y": [-5, 5]}},
        {"type": "rear_beam", "box": {"X": [-24.8, -23.2], "Y": [-5, 5]}},
    ]
    box = {"X": [-23.2, -1.0], "Y": [-5.0, 5.0]}
    lay = vd.layout(v, 700, box, [(-12.0, 0.0, 0.6)], beams)
    assert lay["run"] == [-22.2, -2.0]
    # The void on the pile line and its neighbours within 0.6 + 0.25 + 0.15 m are left out.
    assert lay["left_out"] == [-0.7, 0.0, 0.7]
    assert -1.4 in lay["positions"] and 1.4 in lay["positions"]
    assert all(-5 + 0.25 <= p <= 5 - 0.25 for p in lay["positions"])
    m = vd.mask(lay, np.array([-10.0, -10.0, -1.5, -10.0]), np.array([1.4, 0.0, 1.4, 1.7]))
    assert list(m) == [True, False, False, True]


def test_deck_with_voids_shear_in_the_webs_and_solid_over_the_piles():
    solid = design_deck()
    d = design_deck(voids=SlabVoids(diameter=500, spacing=700))
    v = d["voids"]
    assert v["positions"] and v["web_mm"] == 200 and v["flange_top_mm"] == 150
    # 0.85 of the concrete is left, so the same steel is more per m³.
    assert d["steel"]["concrete_share"] == pytest.approx(1 - v["void_share_pct"] / 100, abs=1e-3)
    assert d["steel"]["kg_per_m3"] > solid["steel"]["kg_per_m3"]
    g = d["shear"]["governing"]
    assert g["in_voided_slab"] and g["VRd_max_kN_per_m"] == pytest.approx(
        solid["shear"]["governing"]["VRd_max_kN_per_m"] * 200 / 700, rel=1e-3
    )
    (p,), (p0,) = d["punching"], solid["punching"]
    # The voids stop short of the pile: punching is the solid slab's.
    assert p["u1_mm"] == p0["u1_mm"] and p["utilisation"] == p0["utilisation"]
    assert not v["left_out"] and v["solid_round_piles_m"] == 0.15
    assert any(n.startswith("Voids:") for n in d["notes"])


def heavy_shear_deck(voids):
    from test_slabs import deck_rows, piles_at

    from triton.design.runner import run_section
    from triton.project import DesignSettings, PileInput, Section, SlabInput
    from triton.validation import import_sheets

    wb = import_sheets(
        {
            "Deck-PT-B-Apron": deck_rows(lambda x, y: [0.0, 0.0, 0.0, 0.0, 400.0, 100.0, 50.0, 0.0]),
            "Deck-QP": deck_rows(lambda x, y: [0.0] * 5 + [50.0, 20.0, 0.0]),
            "Pile(1)-PT-B-Apron": piles_at([(-4.0, 0.0)]),
            "Pile(1)-QP": piles_at([(-4.0, 0.0)], 1000.0),
        }
    )
    els = {
        "Deck": SlabInput(thickness=800, voids=voids, peaks="design"),
        "Pile(1)": PileInput(head_level=2.7),
    }
    return run_section(DesignSettings(), Section(elements=els), wb)["slabs"][0]


def test_links_in_the_webs_where_the_voided_slab_needs_them():
    # Heavy shear across the voided run: the links stand one per web at the void spacing.
    d = heavy_shear_deck(SlabVoids(diameter=500, spacing=650))
    webs = [q for q in d["shear"]["links"] if q.get("in_webs")]
    assert webs and all(q["sy_mm"] == 650 and "1 leg per web" in q["label"] for q in webs)


def test_voids_can_be_left_out_along_the_pile_lines_instead():
    stop = design_deck(voids=SlabVoids(diameter=500, spacing=700))
    out = design_deck(voids=SlabVoids(diameter=500, spacing=700, at_piles="leave_out"))
    assert out["voids"]["left_out"] and len(out["voids"]["positions"]) < len(stop["voids"]["positions"])
    lay = stop["voids"]
    # Stopping short: no void within 150 mm of the 1.2 m pile's face, voids beyond it.
    assert not vd.mask(lay, np.array([-4.0]), np.array([0.6]))[0]
    assert vd.mask(lay, np.array([-6.8]), np.array([3.0]))[0]
    assert out["punching"][0]["utilisation"] == stop["punching"][0]["utilisation"]


def test_link_quantities_count_the_voided_and_solid_parts_once():
    from triton.costing import slab_links

    d = heavy_shear_deck(SlabVoids(diameter=500, spacing=650))
    links = d["shear"]["links"]
    box = d["box"]
    whole = (box["X"][1] - box["X"][0]) * (box["Y"][1] - box["Y"][0])
    assert any(q.get("in_webs") for q in links) and all("area_m2" in q for q in links)
    assert sum(q["area_m2"] for q in links) <= whole + 1e-6
    assert slab_links(d)["shear_kg"] >= 0


def test_ductility_uses_the_voided_compression_zone():
    from triton.design import ductility

    thr = vd.VoidSection(700, 500, 350, 700, "through")
    a, area, _, _ = thr.cumulative("top")
    solid = ductility.strip(9000, 620, 0.0, 26.7, 435)
    voided = ductility.strip(9000, 620, 0.0, 26.7, 435, block=lambda b: float(np.interp(b, a, area)))
    # Only 100 mm of concrete above the void: the neutral axis goes much deeper.
    assert voided["x_d"] > solid["x_d"] * 1.5
    d = design_deck(voids=SlabVoids(diameter=500, spacing=700))
    rows = [r for r in d["strip_design"]["rows"] if r.get("voided")]
    assert rows and all("ductility" in r for r in rows)
