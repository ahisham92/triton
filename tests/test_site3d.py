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
