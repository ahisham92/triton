"""Construction sequence and the existing structure: stages, clashes with the new piles, the tie rods'
effect on the new wall's movement. Never a design input."""

import json

import pytest
from fastapi.testclient import TestClient
from test_furniture import geometry, project

from triton import existing, fresh, furniture, sequence, site3d
from triton.api import app, store


def _setup(system="combi_wall"):
    p = project()
    s = p.sections[0]
    s.existing.use = True
    s.existing.system = system
    g = geometry()
    return p, s, g, furniture.berth_frame(p, s, g)


def test_default_order_and_stages():
    p, s, g, _ = _setup()
    out = sequence.stages(p, s, g)
    works = [st["work"] for st in out["steps"]]
    assert works[:4] == ["steel_pipes", "combi_cages", "combi_infill", "demolition"]
    assert works[-2:] == ["furniture", "dredging"] and "sheet_piles" not in works  # no sheet pile wall here
    # Front and rear beams at the same time: one stage.
    beams = next(st for st in out["stages"] if any(x["work"] == "front_beam" for x in st["steps"]))
    assert {x["work"] for x in beams["steps"]} == {"front_beam", "rear_beam"}
    first = out["stages"][0]
    assert (
        first["elements"] == {"Combi Wall": "pipe"} and first["seabed"] == -14.0 and not first["demolished"]
    )
    cast = next(st for st in out["stages"] if st["steps"][0]["work"] == "pile_concrete")
    assert cast["elements"]["Pile(1)"] == "cast_high" and cast["demolished"]
    assert out["stages"][-1]["seabed"] == -16.12
    # No existing structure: no demolition step.
    s.existing.use = False
    assert "demolition" not in [st.work for st in sequence.default_steps(p, s)]


def test_combi_existing_layout_and_clashes():
    p, s, g, fr = _setup()
    lay = existing.layout(p, s, g, fr)
    # The existing edge is the land side of the new Ø1626 pipes at X 0 (face at X 1, inland -X).
    assert lay["edge_d"] == pytest.approx(1.0 + 0.813)
    # Ahmed's existing quay: tie rods 0.5 m below the cope, three rows of Ø600 piles from 5 m behind
    # the edge every 4.2 m, and the anchor row at the tie rods' end.
    assert lay["tie_level"] == pytest.approx(3.5 - 0.5)
    assert lay["rows_d"] == pytest.approx([lay["edge_d"] + 5.0 + 4.2 * i for i in range(3)], abs=1e-3)
    assert lay["anchor_d"] == pytest.approx(lay["wall_d"] + 36.0)
    assert s.existing.pile_diameter == 600
    assert lay["tie_rods_s"][1] - lay["tie_rods_s"][0] == pytest.approx(1.4)
    out = existing.clashes(p, s, g, fr)
    whats = [f["what"] for f in out["found"]]
    assert any("tie rod" in w for w in whats)
    # An existing row on the new Pile(1) row (8.5 m in from the face): clashes with its piles.
    s.existing.first_row = 8.5 - lay["edge_d"]
    s.existing.pile_offset = 0.8  # the first new pile is at 0.8 m along
    assert any("existing pile" in f["what"] for f in existing.clashes(p, s, g, fr)["found"])
    assert any("warehouse" in w for w in whats)
    assert any(f["element"] == "Tie rods" and "only piles up to" in f["what"] for f in out["found"])
    # Rods far apart and no piles or warehouse: nothing.
    s.existing.tie_rod_spacing = 50.0
    s.existing.piles = False
    s.existing.warehouse = False
    assert existing.clashes(p, s, g, fr)["counts"]["clash"] == 0


def test_gravity_wall_quarry_run():
    p, s, g, fr = _setup("gravity_wall")
    s.existing.block_base_width = 4.0
    s.existing.block_top_width = 2.0
    s.existing.warehouse = False
    out = existing.clashes(p, s, g, fr)
    quarry = [f for f in out["found"] if "quarry run" in f["what"]]
    # Pile(1) at 8.5 m inland is inside the 1:1 quarry run behind the blocks; Pile(4) at 25 m is not.
    assert quarry and {f["element"] for f in quarry} == {"Pile(1)"}
    assert all(f["level"] == "warning" for f in quarry)


def test_tie_rods_reduce_the_movement_only_short_term():
    p, s, g, fr = _setup()
    shape = {
        "combination": "QP",
        "members": [
            {
                "element": "Combi Wall",
                "kind": "combi_wall",
                "x": 0.0,
                "y": 0.0,
                "points": [[-39, 0, 0], [2.7, 10.0, 0]],
            }
        ],
    }
    t = existing.tie_rods(p, s, g, fr, shape)
    assert 0 < t["factor"] < 1 and t["u_with_mm"] == pytest.approx(t["u_mm"] * t["factor"], abs=0.1)
    assert any("10 years" in n and "long-term" in n for n in t["notes"])
    s.existing.tie_rod_share = 1.0
    assert existing.tie_rods(p, s, g, fr, shape)["factor"] < t["factor"]


def test_site_scene_draws_the_existing_structure():
    p, s, g, fr = _setup()
    out = site3d.scene(p, s, g, fr, None)
    parts = {o["part"] for o in out["existing"]["objects"]}
    assert {"capping_beam", "tie_rods", "piles", "slab", "warehouse"} <= parts
    assert out["site"]["existing"] == "see_through"
    s.existing.use = False
    assert "existing" not in site3d.scene(p, s, g, fr, None)


def test_existing_and_sequence_never_stale_a_design():
    p, s, _, _ = _setup()
    before = fresh.fingerprint(p, s, None)
    s.existing.tie_rod_spacing = 2.0
    s.sequence.cast_above = 1.5
    assert fresh.fingerprint(p, s, None) == before


def test_sequence_endpoint_open_while_locked(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    p = project()
    p.locked = True
    store().save(p)
    s = p.sections[0]
    d = store()._dir(p.id, s.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "furniture_geometry.json").write_text(json.dumps({"key": [None, None], "geometry": geometry()}))
    client = TestClient(app)
    page = client.get(f"/api/projects/{p.id}").json()
    page["sections"][0]["existing"]["use"] = True
    page["sections"][0]["sequence"]["steps"] = [
        {"work": "pile_cages"},
        {"work": "dredging", "with_previous": True},
    ]
    assert client.put(f"/api/projects/{p.id}", json=page).status_code == 200
    out = client.get(f"/api/projects/{p.id}/sections/{s.id}/sequence").json()
    assert out["default"] is False and len(out["stages"]) == 1
    assert out["existing"]["counts"]["clash"] > 0 and out["existing"]["tie_rods"]["use"]
