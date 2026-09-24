"""Reinforcement clashes at the pile heads: found, solved and checked again, what-ifs and the calc."""

import io
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from test_slabs import deck_workbook

from triton import clashes as C
from triton.api import app, store
from triton.design.runner import run_section
from triton.project import ClashSettings, DesignSettings, PileInput, Project, Section, SlabInput, WhatIf


@pytest.fixture(scope="module")
def designed():
    section = Section(
        name="Section 01",
        elements={"Deck": SlabInput(thickness=800), "Pile(1)": PileInput(head_level=2.7)},
    )
    project = Project(sections=[section])
    results = run_section(DesignSettings(), section, deck_workbook())
    results["run_at"] = "2026-09-24T21:00"
    return project, section, results


def _head(**kw) -> C.Head:
    host = C.Host("Deck", "slab", 0.0, 0.8, {}, {})
    h = C.Head(
        "Pile(1)",
        "pile",
        0,
        0.0,
        0.0,
        1200.0,
        [{"count": 8, "diameter_mm": 32.0, "radius_mm": 500.0}],
        host,
        0,
    )
    h.z1, h.legs_m = [0.7], [0.0]
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def test_a_bar_through_a_pile_bar_clashes_and_turning_the_cage_clears_it():
    # One Ø25 along X right over the pile bar at (500, 0) mm.
    bar = C.HBar("s|x|1|mesh|0", "X", 0.0, 0.4, 25.0, "bottom bars along X", "slab|bottom_x|1", "mesh")
    head = _head(hbars=[bar])
    rule = ClashSettings()
    hits = C.pile_hits(C.conflicts(head, rule, 20.0, C.pile_bars(head)))
    assert {(x.i, x.kind) for x in hits} == {(0, "clash"), (4, "clash")}
    assert hits[0].gap == pytest.approx(-28.5)
    ctx = type("Ctx", (), {"rule": rule, "dg": 20.0})()
    turn, left = C.best_turn(ctx, head)
    assert not left and 0 < abs(turn) <= 45 / 2
    # Clear gap after the turn at least the fixing tolerance: bar centre 500 sin(turn) from the bar.
    assert 500 * abs(np.sin(np.radians(turn))) - (32 + 25) / 2 >= rule.fixing_tolerance


def test_the_ec2_rule_flags_bars_that_do_not_touch():
    bar = C.HBar("s|x|1|mesh|0", "X", 0.060, 0.4, 25.0, "bottom", "slab|bottom_x|1", "mesh")
    head = _head(hbars=[bar])
    touch = C.pile_hits(C.conflicts(head, ClashSettings(), 20.0, C.pile_bars(head)))
    ec2 = C.pile_hits(C.conflicts(head, ClashSettings(rule="ec2"), 20.0, C.pile_bars(head)))
    assert not touch  # 60 - 28.5 = 31.5 mm clear
    assert [x.kind for x in ec2] == ["tight", "tight"]  # needs max(32, 25, 20) = 32 mm


def test_pile_bars_turn_into_an_l_under_the_slab_top_bars(designed):
    project, section, results = designed
    out = C.find_clashes(project, section, results)
    assert out["heads_checked"] == 1
    g = out["groups"][0]
    assert g["key"] == "Pile(1)|Deck"
    assert g["connection"]["shape"] == "L bars"
    assert "L leg" in g["connection"]["text"]


def test_solutions_are_checked_again_and_the_cheapest_passing_one_recommended(designed):
    project, section, results = designed
    c = C.Clashes(project, section, results)
    e = c.entry(c.heads[0])
    sols = {s["id"]: s for s in e["solutions"]}
    assert {"rotate", "shift", "crank", "cut_trim", "cut"} <= set(sols)
    rec = [s for s in e["solutions"] if s["recommended"]]
    assert len(rec) == 1 and rec[0]["passes"]
    passing = [s for s in e["solutions"] if s["passes"]]
    assert max(rec[0]["delta_kg"], 0) == min(max(s["delta_kg"], 0) for s in passing)
    # Cutting bars is designed again: the slab strip at the pile with less steel.
    cut = sols["cut"]
    slab = [x for x in cut["checks"] if x["element"] == "Deck" and "MRd_kNm_per_m" in x["before"]]
    assert slab and all(x["after"]["as_mm2_per_m"] < x["before"]["as_mm2_per_m"] for x in slab)
    assert sols["cut_trim"]["delta_kg"] > 0 > cut["delta_kg"]
    # The crank designs the pile head again with the smaller circle.
    crank = next(x for x in sols["crank"]["checks"] if x["element"] == "Pile(1)")
    assert crank["after"]["utilisation"] >= crank["before"]["utilisation"]


def test_a_what_if_is_checked_again_without_changing_the_design(designed):
    project, section, results = designed
    before = repr(results)
    c = C.Clashes(project, section, results)
    head = c.heads[0]
    bottom = [h.id for h in head.hbars if "bottom" in h.layer][:6]
    w = WhatIf(group="Pile(1)|Deck", bars=bottom + ["nope"], pile_bars=["0:0", "0:1", "9:9"])
    out = c.whatif(w)
    assert out["bars_found"] == 6
    assert out["unknown"] == ["nope", "9:9"]
    assert out["delta_kg"] < 0
    whats = [x["what"] for x in out["checks"]]
    assert any("taken out" in s and "Deck" in s for s in whats)
    pile = next(x for x in out["checks"] if x["element"] == "Pile(1)")
    assert pile["after"]["utilisation"] > pile["before"]["utilisation"]
    assert repr(results) == before
    assert c.whatif(WhatIf(group="Pile(9)|Deck"))["error"]


def test_punching_layout_follows_9_4_3():
    head = _head(punch_info={"d_mm": 600, "phi": 12, "asw_mm2_per_perimeter": 1000})
    legs = []
    for k, r in enumerate((600 + 250, 600 + 250 + 450)):
        for i in range(12):
            a = 2 * np.pi * i / 12
            legs.append(C.Leg(f"p|{k}|{i}", r / 1e3 * np.cos(a), r / 1e3 * np.sin(a), 12.0, "links", k))
    lay = C.punching_layout(None, head, legs)
    assert lay["passes"]
    assert [r["from_face_mm"] for r in lay["rows"]] == [[250, 250], [700, 700]]
    far = [C.Leg(g.id, g.x * 1.2, g.y * 1.2, g.phi, g.group, g.link) if g.link else g for g in legs]
    lay = C.punching_layout(None, head, far)
    assert not lay["passes"] and lay["rows"][1]["radial_mm"] > 0.75 * 600


@pytest.fixture()
def api(designed, tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    project, section, results = designed
    p = Project(**project.model_dump())
    p.locked = True
    store().save(p)
    store().save_results(p.id, section.id, results)
    return TestClient(app), f"/api/projects/{p.id}/sections/{section.id}"


def test_the_clashes_routes(api):
    client, base = api
    out = client.get(base + "/clashes").json()
    assert out["groups"][0]["key"] == "Pile(1)|Deck" and "scene" not in out["groups"][0]["heads"][0]
    head = client.get(base + "/clashes/head", params={"group": "Pile(1)|Deck", "index": 0}).json()
    assert head["scene"]["pile_bars"] and head["scene"]["hbars"] and head["solutions"]
    assert client.get(base + "/clashes/head", params={"group": "Pile(7)|Deck"}).status_code == 404
    # Settings are saved while the project is locked: they never change the design.
    body = {"rule": "ec2", "choices": {"Pile(1)|Deck": "cut_trim"}}
    s = client.put(base + "/clashes/settings", json=body).json()
    assert s["rule"] == "ec2"
    again = client.get(base + "/clashes").json()
    assert again["settings"]["rule"] == "ec2" and again["groups"][0]["choice"] == "cut_trim"
    assert again["groups"][0]["count"]["pairs"] >= out["groups"][0]["count"]["pairs"]
    bar = head["scene"]["hbars"][0]["id"]
    w = {"group": "Pile(1)|Deck", "head": 0, "bars": [bar], "pile_bars": ["0:0"], "note": "cannot fix"}
    checked = client.post(base + "/clashes/whatif", json=w).json()
    assert checked["bars_found"] == 1 and checked["checks"]
    assert client.get(base + "/clashes").json()["whatifs"] == []
    kept = client.post(base + "/clashes/whatifs", json=w).json()
    assert client.get(base + "/clashes").json()["whatifs"][0]["id"] == kept["id"]
    for fmt in ("docx", "pdf"):
        r = client.get(base + f"/clashes/calc.{fmt}", params={"whatif": kept["id"]})
        assert r.status_code == 200 and "what-if" in r.headers["content-disposition"]
        if fmt == "pdf":
            assert r.content.startswith(b"%PDF")
        else:
            assert any(n.startswith("word/media/") for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    r = client.get(base + "/clashes/calc.xlsx", params={"group": "Pile(1)|Deck", "index": 0})
    assert r.status_code == 200
    assert client.delete(base + f"/clashes/whatifs/{kept['id']}").status_code == 204
    assert client.get(base + f"/clashes/calc.pdf?whatif={kept['id']}").status_code == 404


def test_saving_the_project_page_keeps_the_what_ifs(api):
    client, base = api
    pid = base.split("/")[3]
    page = client.get(f"/api/projects/{pid}").json()
    client.post(base + "/clashes/whatifs", json={"group": "Pile(1)|Deck", "bars": [], "pile_bars": ["0:0"]})
    page["prices"] = {**page.get("prices", {})}
    assert client.put(f"/api/projects/{pid}", json=page).status_code == 200
    assert len(client.get(base + "/clashes").json()["whatifs"]) == 1
