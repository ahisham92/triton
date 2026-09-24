"""Costing: quantities along the berth, prices, and the pile reinforcement allowance."""

import math

from triton.costing import cost_project, cost_section
from triton.project import ElementCosting, PileInput, Project, SteelPrice


def results():
    return {
        "run_at": "2026-09-24T09:00:00+03:00",
        "piles": [
            {
                "element": "Pile(1)",
                "count": 8,
                "section": {"diameter_mm": 1200.0, "head_level_m": 2.7, "toe_level_m": -20.0},
                "steel": {"total_kg": 4540.0, "concrete_m3": math.pi * 0.36 * 22.7},
            }
        ],
        "beams": [
            {
                "element": "Front Beam",
                "kind": "front_beam",
                "width_mm": 2000,
                "depth_mm": 1600,
                "steel": {"kg_per_m": 500.0, "length_m": 33.6},
            }
        ],
        "slabs": [
            {"element": "Deck", "thickness_mm": 700, "steel": {"kg_per_m2": 200.0, "area_m2": 33.6 * 20}}
        ],
    }


def project():
    p = Project()
    p.sections[0].elements = {"Pile(1)": PileInput()}
    pr = p.prices
    pr.concrete_slab, pr.concrete_beams, pr.rebar = 4000.0, 5000.0, 60000.0
    pr.piles[0].price_per_m = 30000.0
    pr.piles[0].rebar_included = 150.0
    return p


def test_quantities_scale_from_the_model_to_the_berth():
    p = project()
    p.sections[0].costing.berth_length = 67.2  # twice the model
    out = cost_section(p, p.sections[0], results())
    rows = {r["element"]: r for r in out["rows"]}
    assert out["model_length_m"] == 33.6
    pile = rows["Pile(1)"]
    assert pile["count"] == 16 and abs(pile["spacing_m"] - 4.2) < 1e-9 and abs(pile["length_m"] - 22.7) < 1e-6
    # 200 kg/m designed against 150 included: 50 kg/m × 22.7 m × 16 piles extra at the rebar price.
    extra_t = 50 * 22.7 * 16 / 1000
    assert pile["cost"] == round(16 * 22.7 * 30000 + extra_t * 60000)
    assert "above" in pile["flags"][0]
    beam = rows["Front Beam"]
    assert beam["concrete_m3"] == round(3.2 * 67.2, 1) and beam["rebar_t"] == round(0.5 * 67.2, 2)
    deck = rows["Deck"]
    assert deck["concrete_m3"] == round(20 * 67.2 * 0.7, 1)
    assert out["totals"]["complete"] and out["per_m"]["cost"] == round(out["totals"]["cost"] / 67.2)


def test_a_spacing_or_a_number_overrides_the_model():
    p = project()
    p.sections[0].costing.elements["Pile(1)"] = ElementCosting(spacing=3.0)
    rows = cost_section(p, p.sections[0], results())["rows"]
    assert rows[0]["count"] == 12  # 33.6 / 3.0 = 11.2, rounded up
    p.sections[0].costing.elements["Pile(1)"] = ElementCosting(spacing=3.0, count=5)
    assert cost_section(p, p.sections[0], results())["rows"][0]["count"] == 5


def test_pile_reinforcement_within_the_allowance_is_not_charged():
    p = project()
    p.prices.piles[0].rebar_included = 250.0
    pile = cost_section(p, p.sections[0], results())["rows"][0]
    assert pile["cost"] == round(8 * 22.7 * 30000) and "Within" in pile["flags"][0]


def test_a_missing_price_is_named_and_the_total_is_incomplete():
    p = project()
    p.prices.concrete_slab = None
    out = cost_section(p, p.sections[0], results())
    assert not out["totals"]["complete"]
    assert any("slab concrete price" in n for n in out["notes"])


def test_steel_priced_per_square_metre():
    p = project()
    p.prices.steel_elements = [SteelPrice(name="AZ 26-700", unit="m²", price=1000.0, mass=155.0)]
    res = {"sheet_pile_walls": [{"element": "SPW", "length_m": 20.0}], "beams": results()["beams"]}
    p.sections[0].costing.elements["SPW"] = ElementCosting(steel_element="AZ 26-700")
    row = next(r for r in cost_section(p, p.sections[0], res)["rows"] if r["element"] == "SPW")
    assert row["cost"] == round(33.6 * 20 * 1000) and row["steel_t"] == round(33.6 * 20 * 155 / 1000, 2)


def test_sections_side_by_side():
    p = project()
    p.sections.append(p.sections[0].model_copy(update={"id": "b2", "name": "Section 2"}))
    out = cost_project(p, {p.sections[0].id: results(), "b2": None})
    assert [s["section"] for s in out["sections"]] == ["Section 1", "Section 2"]
    assert out["sections"][1]["notes"] == ["Not designed yet."]
    assert out["total"]["cost"] == out["sections"][0]["totals"]["cost"]
