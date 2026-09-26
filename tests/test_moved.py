"""Moved piles: the deck grillage, the change a move makes, the bars as designed, and the tab's API."""

import math

import numpy as np
import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from test_slabs import deck_rows

from triton import moved
from triton.alignment import combine_parts
from triton.api import app, store
from triton.design.grillage import Grid, Solver, Spring, actions
from triton.design.runner import run_section
from triton.project import DesignSettings, PileInput, Project, Section, SlabInput
from triton.validation import import_sheets


def test_grillage_strip_gives_the_beam_moment_and_shear():
    # A 1 m wide strip, 10 m span on rigid supports, 100 kN at mid-span: M = PL/4 per metre.
    nx, ny = 21, 3
    g = Grid(0.0, 0.0, 0.5, nx, ny, np.full((nx, ny), 0.5), 30e6)
    s = Solver(g, [Spring(x, y, 1e10) for x in (0.0, 10.0) for y in (0.0, 0.5, 1.0)])
    f = np.zeros((nx, ny, 3, 1))
    f[10, :, 0, 0] = [25.0, 50.0, 25.0]
    u = s.solve(f)
    assert s.reactions(u).sum() == pytest.approx(100.0)
    a = actions(g, u)
    assert a["Mx"][10, :, 0] == pytest.approx([250.0] * 3, rel=1e-3)
    assert a["Qx"][5, :, 0] == pytest.approx([50.0] * 3, rel=1e-3)


def test_grillage_plate_carries_its_load_to_the_springs():
    # A square plate on four corner piles under 10 kPa: equilibrium, and div Q = −q inside.
    n, h = 41, 0.25
    g = Grid(0.0, 0.0, h, n, n, np.full((n, n), 0.3), 30e6)
    s = Solver(g, [Spring(x, y, 1e8) for x in (0.0, 10.0) for y in (0.0, 10.0)])
    area = np.full((n, n), h * h)
    area[[0, -1], :] /= 2
    area[:, [0, -1]] /= 2
    f = np.zeros((n, n, 3, 1))
    f[:, :, 0, 0] = 10 * area
    u = s.solve(f)
    assert s.reactions(u)[0] == pytest.approx([250.0] * 4)
    a = actions(g, u)
    div = np.gradient(a["Qx"][..., 0], h, axis=0) + np.gradient(a["Qy"][..., 0], h, axis=1)
    assert np.median(div[5:-5, 5:-5]) == pytest.approx(-10.0, rel=0.02)
    assert a["Mx"][20, 20, 0] > 0  # sagging mid-span


def piles_rows(points, loads, z_top=2.7):
    """A pile element's sheet: two nodes per pile, head load N (compression −, as Plaxis)."""
    rows = pile_sheet()[:1]
    for i, ((x, y), n) in enumerate(zip(points, loads, strict=True)):
        for j, z in enumerate((z_top, z_top - 20)):
            row = ["EmbeddedBeam\\_1\\_1", 500 + 2 * i + j, 1, x, y, z]
            row += [-n, -n - 1, 0.0] + [0.0, 0.0, 0.0] * 3 + [100.0, 99.0, 101.0] + [0.0, 0.0, 0.0]
            rows.append(row + [1.0, 2.0, 3.0, "N/A"])
    return rows


def deck_section():
    """A 12 × 12 m deck on two rows of three piles each, hogging over them and sagging between."""

    def forces(x, y):
        near = min(math.hypot(x - px, y - py) for px in (-9.0, -3.0) for py in (-4.0, 0.0, 4.0)) < 1.2
        m = -300.0 if near else 150.0
        return [0.0, 0.0, 0.0, 10.0, 20.0, m, 0.6 * m, 0.0]

    row1 = [(-9.0, y) for y in (-4.0, 0.0, 4.0)]
    row2 = [(-3.0, y) for y in (-4.0, 0.0, 4.0)]
    raw = {
        "Deck-PT-B-Apron": deck_rows(forces, x_range=(-12.0, 0.0), y_range=(-6.0, 6.0)),
        "Deck-QP": deck_rows(
            lambda x, y: [0.0] * 5 + [100.0, 50.0, 0.0], x_range=(-12.0, 0.0), y_range=(-6.0, 6.0)
        ),
        "Pile(1)-PT-B-Apron": piles_rows(row1, [1500.0, 1600.0, 1500.0]),
        "Pile(1)-QP": piles_rows(row1, [1000.0] * 3),
        "Pile(2)-PT-B-Apron": piles_rows(row2, [1400.0, 1500.0, 1400.0]),
        "Pile(2)-QP": piles_rows(row2, [900.0] * 3),
    }
    els = {
        "Pile(1)": PileInput(head_level=2.35),
        "Pile(2)": PileInput(head_level=2.35),
        "Deck": SlabInput(thickness=700, crack_width_limit=0.3, crack_width_limit_bottom=0.3),
    }
    return Section(elements=els), import_sheets(raw)


def test_a_move_keeps_the_total_load_and_takes_load_where_the_pile_went():
    section, wb = deck_section()
    project = Project(sections=[section])
    sc = moved.Scenario(moves=[moved.PileMove(element="Pile(1)", x=-9.0, y=4.0, dx=0.0, dy=1.5)])
    ch = moved.changes(project, section, wb, sc)
    assert not ch["problems"]
    piles = {(p["element"], p["y"]): p for p in ch["piles"]}
    for c in ch["combinations"]:
        # The loads on the deck do not change, so the pile loads only move between piles.
        assert sum(p["dN"][c] for p in ch["piles"]) == pytest.approx(0.0, abs=1e-6 * 1500)
    edge = piles[("Pile(1)", 4.0)]
    assert edge["moved"] and (edge["new_x"], edge["new_y"]) == (-9.0, 5.5)
    # Moved towards the deck's edge it takes less, and its neighbour in the row more.
    assert edge["dN"]["PT-B-Apron"] < 0 < piles[("Pile(1)", 0.0)]["dN"]["PT-B-Apron"]

    new_wb, notes = moved.moved_workbook(project, section, wb, ch)
    f = new_wb.elements()["Pile(1)"]["PT-B-Apron"].frame
    at = f[(f["X"] == -9.0) & (f["Y"] == 5.5)]
    assert len(at) == 2 and at["N"].iloc[0] == pytest.approx(-1500.0 - edge["dN"]["PT-B-Apron"])
    deck = new_wb.elements()["Deck"]["PT-B-Apron"].frame
    assert not np.allclose(deck["M_11"], wb.elements()["Deck"]["PT-B-Apron"].frame["M_11"])
    assert any("shears" in n for n in notes)


def test_moves_off_the_deck_or_of_unknown_piles_are_refused():
    section, wb = deck_section()
    project = Project(sections=[section])
    off = moved.Scenario(moves=[moved.PileMove(element="Pile(1)", dx=-8.0)])
    assert "off the deck" in moved.changes(project, section, wb, off)["problems"][0]
    none = moved.Scenario(moves=[moved.PileMove(element="Pile(1)", x=1.0, y=1.0, dy=1.0)])
    assert "no pile at" in moved.changes(project, section, wb, none)["problems"][0]


def test_the_bars_as_designed_give_back_the_same_design():
    section, wb = deck_section()
    settings = DesignSettings()
    res = run_section(settings, section, wb)
    fixed, loose = moved.as_designed(section, res)
    assert not loose
    assert set(fixed.user_cages) == {"Pile(1)", "Pile(2)"} and "Deck" in fixed.slab_strips
    again = combine_parts(run_section(settings, fixed, wb))
    for kind in ("piles", "slabs"):
        for a, b in zip(res[kind], again[kind], strict=True):
            assert b["utilisation"] == pytest.approx(a["utilisation"], abs=1e-3), a["element"]
    # The strips the bars lie on are kept; they are never saved with the section.
    assert fixed.slab_strips["Deck"].lines and "lines" not in fixed.slab_strips["Deck"].model_dump()


def test_the_tab_checks_a_scenario_in_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    section, wb = deck_section()
    c = TestClient(app)
    p = c.post("/api/projects", json={"info": {"name": "Moved"}}).json()
    p["sections"][0]["elements"] = {k: v.model_dump(mode="json") for k, v in section.elements.items()}
    assert c.put(f"/api/projects/{p['id']}", json=p).status_code == 200
    sid = p["sections"][0]["id"]
    url = f"/api/projects/{p['id']}/sections/{sid}/moved-piles"
    store().save_workbook(p["id"], sid, "deck.xlsx", wb)
    sc = {"id": "s1", "name": "Row 2 by 1.4 m", "moves": [{"element": "Pile(2)", "dy": 1.4}]}
    assert c.put(url + "/s1", json=sc).status_code == 200
    # Nothing designed yet: the scenario says so.
    view = c.post(url + "/s1/run", json={}).json()
    assert view["problems"] == [moved.NEEDS_DESIGN]
    assert c.post(f"/api/projects/{p['id']}/sections/{sid}/design", json={}).status_code == 200
    bad = {**sc, "moves": [{"element": "Deck", "dy": 1.0}]}
    assert c.put(url + "/s1", json=bad).status_code == 422
    for _ in range(10):
        view = c.post(url + "/s1/run", json={"budget_s": 0.01}).json()
        if not view["left"]:
            break
    (entry,) = view["scenarios"]
    assert entry["state"] == "done"
    assert {r["element"] for r in entry["elements"]} == {"Pile(1)", "Pile(2)", "Deck"}
    assert all(r["check"] is not None for r in entry["elements"])
    assert len([p for p in entry["change"]["piles"] if p["moved"]]) == 3
    deck = next(r for r in entry["elements"] if r["element"] == "Deck")
    assert any(q["moved"] for q in deck["punching"])
    assert view["piles"]["Pile(2)"] == [[-3.0, -4.0], [-3.0, 0.0], [-3.0, 4.0]]
    assert c.delete(url + "/s1").json()["scenarios"] == []
