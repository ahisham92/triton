"""Comparisons: an element designed at several sizes, costed per metre of berth; Costing counts."""

import math

import pytest
from fastapi.testclient import TestClient
from test_costing import project, results

from triton import trials
from triton.api import app
from triton.costing import cost_section, slab_links
from triton.project import BeamInput, ElementCosting, PileInput, Project, SlabInput, SlabStrips


def test_count_follows_the_berth_unless_given():
    p = project()
    p.sections[0].costing.berth_length = 67.2
    c = p.sections[0].costing.elements.setdefault("Pile(1)", ElementCosting())
    c.spacing = 4.0
    pile = next(r for r in cost_section(p, p.sections[0], results())["rows"] if r["element"] == "Pile(1)")
    assert pile["count"] == pile["count_auto"] == math.ceil(67.2 / 4.0)
    c.count = 10
    pile = next(r for r in cost_section(p, p.sections[0], results())["rows"] if r["element"] == "Pile(1)")
    assert pile["count"] == 10 and pile["count_auto"] == 17


def slab(**over):
    return {
        "element": "Deck",
        "thickness_mm": 700,
        "cover_top_mm": 50,
        "cover_bottom_mm": 50,
        "steel": {"kg_per_m2": 100.0, "area_m2": 100.0},
        "shear": {
            "links": [
                {"x": [0, 10], "y": [0, 5], "phi": 12, "asw_mm2_per_m2": 1000},
                {"x": [10, 20], "y": [0, 5], "phi": None},
            ]
        },
        "punching": [
            {"needs_reinforcement": True, "passed": True, "asw_mm2_per_perimeter": 2000, "perimeters": 3},
            {"needs_reinforcement": False, "passed": True},
        ],
        **over,
    }


def test_slab_links_are_weighed_and_costed():
    leg = (700 - 100 + 20 * 12) / 1000  # m
    w = slab_links(slab())
    assert w["shear_kg"] == pytest.approx(1000 * 50 * leg * 7850e-6)
    assert w["punching_kg"] == pytest.approx(6000 * leg * 7850e-6)
    p = project()
    res = {"slabs": [slab()]}
    row = cost_section(p, p.sections[0], res, length=10.0)["rows"][0]
    per_m2 = 100 + (w["shear_kg"] + w["punching_kg"]) / 100
    assert row["rebar_t"] == round(10.0 * 33.6 * per_m2 / 1000, 2)  # 10 m wide over the berth
    assert "links" in row["basis"]


def test_sizes_around_the_current_one():
    assert [s["thickness"] for s in trials.default_sizes(SlabInput(thickness=700))] == [
        600,
        650,
        700,
        750,
        800,
    ]
    assert [s["diameter"] for s in trials.default_sizes(PileInput(diameter=1200))] == [1000, 1200, 1400]
    beams = trials.default_sizes(BeamInput(width=2000, depth=1600))
    assert beams[0] == {"width": 2000, "depth": 1400} and len(beams) == 5
    assert trials.default_sizes(BeamInput(depth=1600), {"width_mm": 1800})[2] == {
        "width": 1800,
        "depth": 1600,
    }
    with pytest.raises(ValueError):
        trials.clean_size(SlabInput(), {"thickness": 5})
    assert trials.clean_size(BeamInput(), {"width": None, "depth": 1500}) == {"depth": 1500.0}


def test_a_trial_picks_its_own_bars():
    p = Project()
    s = p.sections[0]
    s.elements = {"Deck": SlabInput(thickness=700)}
    s.slab_strips = {"Deck": SlabStrips(stations=[2.0], bars={"k": "Ø25 @ 150"}, spacing=200)}
    t = trials.trial_section(s, "Deck", {"thickness": 750})
    assert t.elements["Deck"].thickness == 750 and s.elements["Deck"].thickness == 700
    assert t.slab_strips["Deck"].bars == {} and t.slab_strips["Deck"].spacing is None
    assert t.slab_strips["Deck"].stations == [2.0]


def test_trials_run_once_and_are_costed(tmp_path, monkeypatch):
    p = project()
    s = p.sections[0]
    s.elements = {"Deck": SlabInput(thickness=700)}
    calls = []

    def fake(settings, section, workbook, progress=None, only=None, deadline=None):
        h = section.elements["Deck"].thickness
        calls.append(h)
        kg = 120000 / h  # thicker: less steel
        return {
            "run_at": "now",
            "skipped": [],
            "slabs": [
                slab(
                    thickness_mm=h,
                    steel={"kg_per_m2": kg, "area_m2": 100.0},
                    utilisation=700 / h,
                    passed=h >= 700,
                    box={"X": [-10, 0], "Y": [0, 10]},
                )
            ],
        }

    monkeypatch.setattr(trials, "run_section", fake)
    sizes = [{"thickness": 650}, {"thickness": 700}, {"thickness": 800}]
    out = trials.run(p, s, None, None, tmp_path, "Deck", sizes)
    assert calls == [650, 700, 800] and out["left"] == []
    trials.run(p, s, None, None, tmp_path, "Deck", sizes)
    assert len(calls) == 3  # nothing changed: not run again
    view = trials.view(p, s, None, {}, tmp_path)
    rows = view["elements"][0]["rows"]
    assert [r["state"] for r in rows] == ["done"] * 3
    assert rows[1]["current"] and rows[1]["saving_per_m"] == 0
    assert not rows[0]["passed"] and "over 1" in rows[0]["why"]
    best = [r for r in rows if r.get("best")]
    assert len(best) == 1 and best[0]["passed"]
    assert all(r["cost_per_m"] > 0 for r in rows)
    # A changed element input makes the trials out of date.
    s.elements["Deck"] = SlabInput(thickness=700, cover_top=60)
    assert {r["state"] for r in trials.view(p, s, None, {}, tmp_path)["elements"][0]["rows"]} == {
        "out of date"
    }


def test_trial_api_needs_a_workbook_and_a_known_element(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    c = TestClient(app)
    p = c.post("/api/projects", json={"element_names": ["Deck", "Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}/trials"
    v = c.get(url).json()
    assert {e["element"] for e in v["elements"]} == {"Deck", "Pile(1)"}
    assert c.post(url, json={"element": "Nope", "sizes": [{"thickness": 700}]}).status_code == 404
    assert c.post(url, json={"element": "Deck", "sizes": [{"thickness": 5}]}).status_code == 422
    assert c.post(url, json={"element": "Deck", "sizes": [{"thickness": 700}]}).status_code == 409
    assert c.post(url + "/use", json={"element": "Deck", "size": {"thickness": 750}}).status_code == 409


def test_voided_slab_trials_carry_the_voids():
    import triton.project as pr

    if not hasattr(pr, "SlabVoids"):
        pytest.skip("slab voids not in this build")
    deck = SlabInput(thickness=700, voids=pr.SlabVoids(diameter=500, spacing=700))
    assert trials.size_of(deck) == {"thickness": 700, "void_diameter": 500, "void_spacing": 700}
    assert all("void_diameter" in s for s in trials.default_sizes(deck))
    size = trials.clean_size(deck, {"thickness": 750, "void_diameter": 450, "void_spacing": 650})
    moved = trials.with_size(deck, size)
    assert moved.thickness == 750 and moved.voids.diameter == 450 and moved.voids.spacing == 650
    assert deck.voids.diameter == 500
    with pytest.raises(ValueError):
        trials.clean_size(deck, {"thickness": 550, "void_diameter": 500})
    with pytest.raises(ValueError):
        trials.clean_size(deck, {"thickness": 800, "void_diameter": 500, "void_spacing": 450})
    # A solid slab takes no void sizes.
    assert trials.clean_size(SlabInput(), {"thickness": 700, "void_diameter": 500}) == {"thickness": 700.0}


def test_all_elements_with_a_crack_limit(tmp_path, monkeypatch):
    p = project()
    s = p.sections[0]
    s.elements = {"Pile(1)": PileInput(), "Deck": SlabInput(thickness=700)}
    calls = []

    def fake(settings, section, workbook, progress=None, only=None, deadline=None):
        (name,) = only
        e = section.elements[name]
        calls.append((name, e.crack_width_limit))
        if name == "Deck":
            kg = 100 * 0.2 / e.crack_width_limit  # a looser limit: less steel
            box = {"X": [-20, 0], "Y": [0, 33.6]}
            return {
                "slabs": [
                    slab(steel={"kg_per_m2": kg, "area_m2": 672.0}, utilisation=0.9, passed=True, box=box)
                ]
            }
        return {"piles": [{**results()["piles"][0], "utilisation": 0.9, "passed": True}]}

    monkeypatch.setattr(trials, "run_section", fake)
    variants = [{"crack_width_limit": 0.3}]
    out = trials.run_scenarios(p, s, None, None, tmp_path, variants)
    assert out["left"] == 0 and len(calls) == 4
    assert trials.variant_section(s, variants[0]).elements["Deck"].crack_width_limit_bottom == 0.3
    trials.run_scenarios(p, s, None, None, tmp_path, variants)
    assert len(calls) == 4  # nothing changed: not run again
    v = trials.scenarios_view(p, s, None, {}, tmp_path)
    base, loose = v["variants"]
    assert base["base"] and base["label"].startswith("As set") and loose["label"] == "wk 0.3 mm"
    assert base["complete"] and loose["complete"]
    assert loose["saving_per_m"] > 0 and loose["rebar_t_per_m"] < base["rebar_t_per_m"]
    assert loose["elements"]["Deck"]["cost_per_m"] < base["elements"]["Deck"]["cost_per_m"]
    # Stepping: a deadline in the past designs one element per call.
    s.elements["Deck"] = SlabInput(thickness=750)
    out = trials.run_scenarios(p, s, None, None, tmp_path, variants, deadline=0.0)
    assert out["done"] == 1 and out["left"] == 1


def test_value_engineering_ideas_and_mixes(tmp_path, monkeypatch):
    p = project()
    s = p.sections[0]
    s.elements = {"Pile(1)": PileInput(diameter=1200), "Deck": SlabInput(thickness=700)}
    ideas = trials.ideas(s)
    labels = [i["label"] for i in ideas]
    assert "Crack width limit 0.3 mm on every element" in labels and "Deck 750 mm thick (+50)" in labels
    assert "Pile(1) Ø1400 (+200)" in labels
    assert any(i["what"] == "peaks" for i in ideas)
    deck = next(i for i in ideas if i["label"] == "Deck 750 mm thick (+50)")
    pile = next(i for i in ideas if i["label"] == "Pile(1) Ø1400 (+200)")
    mix = trials.clean_variant(
        {"crack_width_limit": 0.3, "elements": {**deck["change"]["elements"], **pile["change"]["elements"]}},
        s,
    )
    vs = trials.variant_section(s, mix)
    assert vs.elements["Deck"].thickness == 750 and vs.elements["Pile(1)"].diameter == 1400
    assert vs.elements["Deck"].crack_width_limit_bottom == 0.3 and s.elements["Deck"].thickness == 700
    with pytest.raises(ValueError):
        trials.clean_variant({"elements": {"Nope": {"thickness": 700}}}, s)
    with pytest.raises(ValueError):
        trials.clean_variant({"elements": {"Pile(1)": {"peaks": "ring_mean"}}}, s)
    # The deck is designed again when only the piles under it change.
    only_pile = trials.variant_section(s, {"elements": pile["change"]["elements"]})
    assert trials.scenario_key(p, only_pile, "Deck", None) != trials.scenario_key(p, s, "Deck", None)
    assert trials.run_key(p, only_pile, "Deck", None) == trials.run_key(p, s, "Deck", None)
    calls = []

    def fake(settings, section, workbook, progress=None, only=None, deadline=None):
        (name,) = only
        calls.append(name)
        if name == "Deck":
            h = section.elements["Deck"].thickness
            box = {"X": [-20, 0], "Y": [0, 33.6]}
            return {
                "slabs": [
                    slab(thickness_mm=h, steel={"kg_per_m2": 100.0, "area_m2": 672.0}, passed=True, box=box)
                ]
            }
        return {"piles": [{**results()["piles"][0], "utilisation": 0.9, "passed": True}]}

    monkeypatch.setattr(trials, "run_section", fake)
    out = trials.run_scenarios(p, s, None, None, tmp_path, [{**mix, "label": "Mix"}], which="ve")
    assert out["left"] == 0 and len(calls) == 4
    v = trials.scenarios_view(p, s, None, {}, tmp_path, "ve")
    assert [c["label"] for c in v["variants"]][1] == "Mix" and v["variants"][1]["complete"]
    assert (
        trials.scenarios_view(p, s, None, {}, tmp_path)["variants"][1]["label"] == "wk 0.3 mm"
    )  # its own list
