from test_curtailment import LOADS
from test_design import pile_sheets

from triton.design.piles import design_pile
from triton.project import Casing, DesignSettings, PileInput


def design(**casing):
    pile = PileInput(head_level=0.0, casing=Casing(role="structural", **casing))
    return design_pile("Pile(1)", pile, DesignSettings(), pile_sheets(LOADS)).to_dict()


def test_connection_zone_above_the_casing_top():
    d = design(top_level=-2.0, bottom_level=-10.0, connection_bar_count=24, connection_bar_diameter=32)
    c = d["connection"]
    assert (c["top"], c["bottom"], c["welded"]) == (-1.5, -2.0, "24Ø32")
    # Welded bars at cover 0 add to the head cage, so the zone is stronger than the cage alone.
    alone = design(top_level=-2.0, bottom_level=-10.0)["connection"]
    assert alone["welded"] is None and "cage alone" in alone["notes"][0]
    assert c["utilisation"] < alone["utilisation"] and c["passed"]
    assert -2.0 <= c["governing"]["z"] <= -1.5


def test_connection_zone_at_the_pile_top_when_the_casing_reaches_it():
    c = design(top_level=1.0, bottom_level=-10.0, connection_bar_count=24, connection_length=0.5)[
        "connection"
    ]
    assert (c["top"], c["bottom"]) == (0.0, -0.5)


def test_no_connection_check_for_crack_only_casing():
    pile = PileInput(head_level=0.0, casing=Casing(top_level=0.0, bottom_level=-4.0))
    assert design_pile("Pile(1)", pile, DesignSettings(), pile_sheets(LOADS)).to_dict()["connection"] is None


def test_a_structural_casing_takes_its_share_and_is_checked():
    """Between the casing levels the actions are shared by E·I; the concrete gets the rest."""
    plain = design_pile("Pile(1)", PileInput(head_level=0.0), DesignSettings(), pile_sheets(LOADS)).to_dict()
    d = design(top_level=0.0, bottom_level=-10.0, thickness=16.0)
    c = d["casing"]
    assert 0.3 < c["steel_share"] < 0.8
    assert (c["top"], c["bottom"]) == (0.0, -10.0)
    assert c["tube"]["utilisation"] is not None and c["tube"]["method"] == "ec3"
    assert all(-10.0 - 1e-9 <= p["z"] <= 0.0 for p in c["tube"]["profile"])  # only inside the casing
    # The concrete carries less where the casing helps, so it needs less steel.
    assert d["arrangement"]["area_mm2"] < plain["arrangement"]["area_mm2"]
    assert any("to the casing" in n for n in d["notes"])


def test_a_crack_only_casing_carries_nothing():
    pile = PileInput(head_level=0.0, casing=Casing(top_level=0.0, bottom_level=-10.0))
    d = design_pile("Pile(1)", pile, DesignSettings(), pile_sheets(LOADS)).to_dict()
    assert d["casing"] is None
