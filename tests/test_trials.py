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
