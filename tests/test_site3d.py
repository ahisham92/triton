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
    # Defaults: seabed -16, water 0, soil behind the wall up to the deck's underside (700 mm slab at 2.7).
    assert lv["seabed"] == -16.0 and lv["water"] == 0.0 and lv["ground"] == pytest.approx(2.35)
    assert lv["bottom"] == -42.0  # 3 m below the combi wall's toe
    assert out["wall"] == {"element": "Combi Wall", "d": 1.0}
    sea, land = out["soil"]
    # The quay face is at X 1, inland towards -X: the sea block runs 20 m out from the wall at X 0.
    xs = sorted({c[0] for c in sea["corners"]})
    assert xs == [0.0, 20.0] and land["top"] == pytest.approx(2.35)
    assert out["water"]["level"] == 0.0
    kinds = {i["kind"] for i in out["furniture"]}
    assert {"fenders", "bollards"} <= kinds
    # Fenders stand out of the face (X > 1) inside the model's 33.6 m.
    for f in (i for i in out["furniture"] if i["kind"] == "fenders"):
        assert f["box"]["X"][0] >= 1.0 - 1e-9 and -16.8 <= f["box"]["Y"][0] <= 16.8
    crane = out["crane"]
    assert crane and max(p[0] for ln in crane["lines"] for p in ln) > 60  # the boom reaches out to sea
    assert any("assumed" in n for n in out["notes"])


def test_site_settings_are_open_while_locked_and_never_stale_a_design(api):
    client, pid, base = api
    page = client.get(f"/api/projects/{pid}").json()
    page["sections"][0]["site"] = {"seabed_level": -18.5, "water_level": 1.2, "soil": "full", "crane": False}
    r = client.put(f"/api/projects/{pid}", json=page)
    assert r.status_code == 200, r.text
    out = client.get(base + "/site").json()
    assert out["levels"]["seabed"] == -18.5 and out["site"]["soil"] == "full"


def test_fingerprint_unchanged_by_the_site():
    p = Project.model_validate(SAVED)
    before = fresh.fingerprint(p, p.sections[0], WORKBOOK)
    assert before["working zone and peaks"] == "9bb977fc731c"
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
    # The pile that moves most is the Design tab's estimate; the other moves half as much.
    est = client.get(f"{url}/deflections").json()["elements"][0]
    head = max(abs(v) for v in a["points"][-1][1:])
    assert head == pytest.approx(abs(est["head_mm"]), abs=0.02)
    assert max(abs(v) for v in b["points"][-1][1:]) == pytest.approx(head / 2, abs=0.02)
    assert out["deck_shift_mm"] and out["max_mm"] == pytest.approx(head, abs=0.02)
    uls = client.get(f"{url}/deformed", params={"combination": "PT-B-Apron"}).json()
    assert uls["combination"] == "PT-B-Apron" and uls["max_mm"] == pytest.approx(3 * head, rel=1e-3)
