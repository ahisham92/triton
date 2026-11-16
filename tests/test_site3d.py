"""The site round the structure in the 3D views: seabed, water, soil, furniture and the STS crane.
Never a design input."""

import json

import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from test_deflection import SAVED, WORKBOOK
from test_design import xlsx_bytes
from test_furniture import geometry, project

from triton import fresh
from triton.api import app, store
from triton.project import Project, SiteView


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    p = project()
    p.locked = True
    store().save(p)
    s = p.sections[0]
    d = store()._dir(p.id, s.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "furniture_geometry.json").write_text(json.dumps({"key": [None, None], "geometry": geometry()}))
    return TestClient(app), p.id, f"/api/projects/{p.id}/sections/{s.id}"


def test_the_site_round_section_01a(api):
    client, pid, base = api
    out = client.get(base + "/site").json()
    lv = out["levels"]
    # Defaults: seabed -16.12, the tidal bar, soil up to the deck's underside (700 mm slab at 2.7).
    assert (
        lv["seabed"] == -16.12
        and lv["water"] == [0.945, 0.701, 0.38, 0.213, 0.091, 0.0]
        and lv["ground"] == pytest.approx(2.35)
    )
    assert lv["bottom"] == -42.0  # 3 m below the combi wall's toe
    assert out["wall"] == {"element": "Combi Wall", "d": 1.0}
    sea, land = out["soil"]
    # The quay face is at X 1, inland towards -X: the sea block runs 20 m out from the wall at X 0.
    xs = sorted({c[0] for c in sea["corners"]})
    assert xs == [0.0, 20.0] and land["top"] == pytest.approx(2.35)
    assert [w["name"] for w in out["water"]] == ["MHWS", "MHWN", "MSL", "MLWN", "MLWS", "LAT"]
    kinds = {i["kind"] for i in out["furniture"]}
    assert {"fenders", "bollards"} <= kinds
    # Fenders stand out of the face (X > 1) inside the model's 33.6 m.
    for f in (i for i in out["furniture"] if i["kind"] == "fenders"):
        assert f["box"]["X"][0] >= 1.0 - 1e-9 and -16.8 <= f["box"]["Y"][0] <= 16.8
    crane = out["crane"]
    assert crane and max(p[0] for ln in crane["lines"] for p in ln) > 60  # the boom reaches out to sea
    # Drawn about 35 m high overall above the cope, for show (Ahmed: 30 to 40 m).
    top = max(p[2] for ln in crane["lines"] for p in ln)
    assert top - lv["cope"] == pytest.approx(35.0)
    assert any("assumed" in n for n in out["notes"])
    # Sizes to draw the elements extruded (on by default): piles round, plates their depth.
    assert out["site"]["extrude"] is True
    sizes = out["sizes"]
    assert sizes["Combi Wall"]["round"] == pytest.approx(1.626)
    beam = sizes["Front Beam"]
    assert beam["t"] == pytest.approx(1.6) and beam["at"] == "mid"


def test_site_settings_are_open_while_locked_and_never_stale_a_design(api):
    client, pid, base = api
    page = client.get(f"/api/projects/{pid}").json()
    page["sections"][0]["site"] = {"seabed_level": -18.5, "water_level": 1.2, "soil": "full", "crane": False}
    r = client.put(f"/api/projects/{pid}", json=page)
    assert r.status_code == 200, r.text
    out = client.get(base + "/site").json()
    assert out["levels"]["seabed"] == -18.5 and out["site"]["soil"] == "full"
    # Saved with one water level (the first version): it becomes the list.
    assert out["site"]["water_levels"] == [{"name": "Water level", "level": 1.2}]
    # Several named levels, highest first; one below the seabed has nothing to draw.
    page = client.get(f"/api/projects/{pid}").json()
    page["sections"][0]["site"]["water_levels"] = [
        {"name": "LAT", "level": 0.0},
        {"name": "HAT", "level": 1.9},
        {"name": "Dry dock", "level": -30.0},
    ]
    page["sections"][0]["site"]["water_hidden"] = ["LAT"]
    assert client.put(f"/api/projects/{pid}", json=page).status_code == 200
    out = client.get(base + "/site").json()
    assert [w["name"] for w in out["water"]] == ["HAT", "LAT"] and out["site"]["water_hidden"] == ["LAT"]


def test_the_first_assumed_seabed_becomes_this_projects_dredge_level():
    assert SiteView.model_validate({"seabed_level": -16.0}).seabed_level == -16.12
    assert SiteView.model_validate({"seabed_level": -17.0}).seabed_level == -17.0
    assert [w.name for w in SiteView().water_levels][0] == "MHWS"
    # The level assumed before the tidal bar came (0.0 m, saved either way) becomes the tidal bar.
    for saved in ({"water_level": 0.0}, {"water_levels": [{"name": "Water level", "level": 0.0}]}):
        assert len(SiteView.model_validate(saved).water_levels) == 6


def test_fingerprint_unchanged_by_the_site():
    p = Project.model_validate(SAVED)
    # The 2 m left out at each end (the default since 2026-09-25) asks for one redesign; without it the
    # hash is the one the code gave before.
    trimmed = fresh.fingerprint(p, p.sections[0], WORKBOOK)["working zone and peaks"]
    p.sections[0].end_trim = 0.0
    before = fresh.fingerprint(p, p.sections[0], WORKBOOK)
    assert before["working zone and peaks"] == "9bb977fc731c" != trimmed
    p.sections[0].site = SiteView(seabed_level=-20, water_level=2, soil="hidden", water=False)
    assert fresh.fingerprint(p, p.sections[0], WORKBOOK) == before


def test_the_deformed_shape_of_every_pile_by_combination(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    p = client.post("/api/projects", json={"info": {"name": "D"}, "element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"

    def two(scale):  # a second pile 6 m away with half the moments
        rows = pile_sheet(scale=scale)
        return rows + [
            [r[0], r[1] + 100, r[2], 6.0, *r[4:6], *[v / 2 for v in r[6:24]], *r[24:]] for r in rows[1:]
        ]

    data = xlsx_bytes({"Pile(1)-PT-B-Apron": two(3000.0), "Pile(1)-QP": two(1000.0)})
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)}).status_code == 200
    r = client.get(f"{url}/deformed")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["combination"] == "QP" and set(out["combinations"]) == {"QP", "PT-B-Apron"}
    assert out["estimate"] is True and "not a Plaxis displacement result" in out["note"]
    a, b = sorted(out["members"], key=lambda m: m["x"])
    assert (a["x"], b["x"]) == (0.0, 6.0) and a["points"][0][0] == -5.0 and a["points"][-1][0] == -1.0
    # Tied at the deck (the default): both heads move with the deck, as the Design tab's estimate says,
    # by the mean of the two heads each fixed at its toe (the second has half the moments).
    est = client.get(f"{url}/deflections").json()
    move = abs(est["deck"]["move_mm"]["across"])
    heads = [max(abs(v) for v in m["points"][-1][1:]) for m in (a, b)]
    assert heads == pytest.approx([move, move], abs=0.02)
    assert abs(est["elements"][0]["head_mm"]) == pytest.approx(move, abs=0.02)
    assert max(abs(v) for v in out["deck_shift_mm"]) == pytest.approx(move, abs=0.02)
    head = out["max_mm"]
    uls = client.get(f"{url}/deformed", params={"combination": "PT-B-Apron"}).json()
    assert uls["combination"] == "PT-B-Apron" and uls["max_mm"] == pytest.approx(3 * head, rel=1e-3)

    # Each pile on its own (fixed at the toe): the second moves half as much as the first.
    page = client.get(f"/api/projects/{p['id']}").json()
    page["sections"][0]["deflection"]["toe"] = "fixed"
    assert client.put(f"/api/projects/{p['id']}", json=page).status_code == 200
    own = client.get(f"{url}/deformed").json()
    a, b = sorted(own["members"], key=lambda m: m["x"])
    fa, fb = (max(abs(v) for v in m["points"][-1][1:]) for m in (a, b))
    assert fb == pytest.approx(fa / 2, abs=0.02) and (fa + fb) / 2 == pytest.approx(move, abs=0.02)
    # Movement from a phase: its moments come off first, and it is not offered as a shape itself.
    page["sections"][0]["deflection"]["baseline"] = "PT-B-Apron"
    assert client.put(f"/api/projects/{p['id']}", json=page).status_code == 200
    after = client.get(f"{url}/deformed").json()
    assert after["combinations"] == ["QP"] and any("movement after PT-B-Apron" in n for n in after["notes"])
    assert after["max_mm"] == pytest.approx(2 * own["max_mm"], rel=1e-3)  # QP − 3 × QP


def test_a_section_without_sts_cranes_draws_none(api):
    client, pid, base = api
    page = client.get(f"/api/projects/{pid}").json()
    page["sections"][0]["furniture"]["sts_crane"] = False
    page["sections"][0]["furniture"]["protrusion"] = True
    r = client.put(f"/api/projects/{pid}", json=page)  # open while locked: never a design input
    assert r.status_code == 200, r.text
    out = client.get(base + "/site").json()
    assert out["crane"] is None
    assert any(i["kind"] == "fender_blocks" for i in out["furniture"])


def test_a_corner_berths_furniture_follows_the_front_beams_face():
    """Ahmed 09-26: on a corner berth the fenders, bollards and rails follow the front face of the
    front beam round the corner, turned with it."""
    import math

    from triton import furniture as F
    from triton import site3d

    p = project()
    s = p.sections[0]
    g = geometry()
    fr = F.berth_frame(p, s, g)
    furn = F.design(p, s, g, None)
    # The front beam (2 m wide, centre at X 0) runs up Y to 0, then turns 30° inland (towards -X).
    turn = [-math.sin(math.radians(30)), math.cos(math.radians(30))]
    line = [[0.0, -16.8], [0.0, 0.0], [16.8 * turn[0], 16.8 * turn[1]]]
    out = site3d.scene(p, s, g, fr, furn, line)
    fenders = [i for i in out["furniture"] if i["kind"] == "fenders" and not i["label"].endswith("panel")]
    assert fenders
    for f in fenders:
        plan = f["box"]["plan"]
        cx = sum(q[0] for q in plan) / 4
        cy = sum(q[1] for q in plan) / 4
        along = [plan[1][0] - plan[0][0], plan[1][1] - plan[0][1]]
        n = math.hypot(*along)
        if cy < -0.5:
            # The straight part: square to X, out to sea of the face at X 1.
            assert abs(along[0]) < 1e-6 and cx > 1.0
        elif cy > 2.0:
            # The turned part: along the turned face, out to sea of it.
            assert along[0] / n == pytest.approx(turn[0], abs=2e-3) and along[1] / n == pytest.approx(
                turn[1], abs=2e-3
            )
            face = [1.0 * turn[1], -1.0 * turn[0]]  # the face point beside the corner, 1 m to sea
            sea = (cx - face[0]) * turn[1] - (cy - face[1]) * turn[0]
            assert sea > 0
    # Rails come in one piece per straight, meeting at the corner.
    rails = out["rails"]
    assert len(rails) == 2 * len({r["label"] for r in rails})
    a, b = rails[0]["line"][1], rails[1]["line"][0]
    assert a == b
    # The straight berth draws as before: squares with the model's axes.
    plain = site3d.scene(p, s, g, fr, furn)
    for f in plain["furniture"]:
        if "box" in f:
            plan = f["box"]["plan"]
            assert {round(q[0], 6) for q in plan} == {round(v, 6) for v in f["box"]["X"]}
