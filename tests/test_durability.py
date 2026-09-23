import pytest
from fastapi.testclient import TestClient

from triton.api import app
from triton.durability import defaults, en1992_covers, en1993_5_corrosion
from triton.project import (
    BeamInput,
    Casing,
    CombiWallInput,
    DesignSettings,
    PileInput,
    SheetPileInput,
    SlabInput,
    with_project_grades,
)


def test_en1992_covers_by_design_life():
    # c_min,dur (Table 4.4N) + 10 mm; S4 at 50 years, S6 at 100; piles at least 75 mm.
    assert en1992_covers(50) == {
        "piles": 75.0,
        "combi_infill": 50.0,
        "slab_top": 45.0,
        "slab_bottom": 55.0,
        "beams": 55.0,
    }
    c100 = en1992_covers(100)
    assert (c100["slab_bottom"], c100["slab_top"], c100["combi_infill"]) == (65.0, 55.0, 60.0)
    assert en1992_covers(75)["beams"] == 60.0


def test_en1993_5_corrosion_by_design_life():
    assert en1993_5_corrosion(50) == {"casing": 3.75, "combi_tube": 3.75, "sheet_pile_per_face": 1.75}
    assert en1993_5_corrosion(100)["casing"] == 7.5
    assert en1993_5_corrosion(60)["casing"] == pytest.approx(3.75 + (5.6 - 3.75) * 10 / 25, abs=0.01)
    assert en1993_5_corrosion(125)["casing"] == pytest.approx(9.4, abs=0.01)


def test_bs6349_values_from_the_design_report():
    d = defaults("bs6349", "bs6349", 50)
    assert d["covers"] == {"piles": 75, "combi_infill": 75, "slab_top": 50, "slab_bottom": 50, "beams": 50}
    assert d["corrosion"] == {"casing": 4.5, "combi_tube": 4.5, "sheet_pile_per_face": 2.5}
    assert defaults("bs6349", "bs6349", 100)["corrosion"]["casing"] == 9.0


def test_project_defaults_follow_bs6349_and_adsec():
    s = DesignSettings()
    assert (s.durability.cover_code, s.durability.corrosion_code) == ("bs6349", "bs6349")
    assert s.partial_factors.alpha_cc == 1.0 and not s.partial_factors.deduct_bar_area


def test_elements_take_the_project_values_unless_set():
    s = DesignSettings()
    s.durability.covers.piles = 90.0
    s.durability.covers.slab_top = 40.0
    s.durability.corrosion.casing = 4.0
    m, d = s.materials, s.durability
    pile = with_project_grades(PileInput(casing=Casing()), m, d)
    assert pile.cover == 90.0 and pile.casing.corrosion_loss == 4.0
    assert with_project_grades(PileInput(cover=60.0), m, d).cover == 60.0
    slab = with_project_grades(SlabInput(cover_bottom=70.0), m, d)
    assert (slab.cover_top, slab.cover_bottom) == (40.0, 70.0)
    assert with_project_grades(BeamInput(), m, d).cover == d.covers.beams
    wall = with_project_grades(CombiWallInput(), m, d)
    assert (wall.cover, wall.corrosion_loss) == (d.covers.combi_infill, d.corrosion.combi_tube)
    assert with_project_grades(SheetPileInput(), m, d).corrosion_loss_per_face == 2.5


def test_defaults_endpoint():
    client = TestClient(app)
    r = client.get("/api/durability-defaults", params={"life": 100}).json()
    assert r["covers"]["beams"] == 65.0 and r["corrosion"]["combi_tube"] == 7.5
    assert client.get("/api/durability-defaults", params={"life": 0}).status_code == 422
