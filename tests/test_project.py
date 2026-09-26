import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from triton.api import app
from triton.materials import concrete, structural_steel_fy
from triton.project import Casing, CombiWallInput, PileInput, Project, Section, default_element


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_concrete_properties_follow_ec2_table_3_1():
    c = concrete("C32/40")
    assert (c.fck, c.fcm) == (32, 40)
    assert c.ecm == pytest.approx(33_300, abs=100)
    assert c.fctm == pytest.approx(3.0, abs=0.05)
    with pytest.raises(ValueError):
        concrete("B25")


def test_structural_steel_yield_drops_above_16mm():
    assert structural_steel_fy("S355", 16) == 355
    assert structural_steel_fy("S355", 18) == 345


def test_default_elements_by_name():
    assert isinstance(default_element("Pile(3)"), PileInput)
    assert default_element("Rear Beam").kind == "rear_beam"
    assert default_element("Transverse Beam(2)").kind == "transverse_beam"
    assert default_element("Trans Beam").kind == "transverse_beam"
    assert default_element("Portal Frame") is None
    assert default_element("Notes") is None


def test_new_elements_start_from_the_office_sizes_and_zones():
    # Front beam 2.0 x 1.6, rear 2.0 x 2.0, slab 0.7 m; the king pile zones of the office's steel sheets.
    assert (default_element("Front Beam").width, default_element("Front Beam").depth) == (2000, 1600)
    assert (default_element("Rear Beam").width, default_element("Rear Beam").depth) == (2000, 2000)
    assert default_element("Deck").thickness == 700
    zones = [(z.bottom_level, z.outside, z.inside) for z in CombiWallInput().corrosion_zones]
    assert zones == [(-0.5, 4.5, 0), (-14.5, 2.5, 0), (-16.12, 2.5, 0), (-25, 1.75, 0), (-39, 1.75, 1.75)]


def test_add_elements_skips_known_and_unknown():
    p = Section()
    assert p.add_elements(["Pile(1)", "Deck", "Portal Frame"]) == ["Pile(1)", "Deck"]
    assert p.add_elements(["Pile(1)", "Combi Wall"]) == ["Combi Wall"]


def test_pile_is_plain_concrete_unless_casing_given():
    pile = PileInput()
    assert pile.casing is None
    cased = PileInput(casing=Casing(role="structural", top_level=2.7, bottom_level=-1.3))
    assert cased.casing.role == "structural"
    with pytest.raises(ValidationError):
        Casing(top_level=-2.0, bottom_level=0.0)
    with pytest.raises(ValidationError):
        Casing(thickness=10, corrosion_loss=10)


def test_combi_wall_checks_tube():
    with pytest.raises(ValidationError):
        CombiWallInput(tube_diameter=30, tube_thickness=18)
    with pytest.raises(ValidationError):
        CombiWallInput(corrosion_loss=20)


def test_element_name_must_match_kind():
    with pytest.raises(ValidationError):
        Section(elements={"Deck": PileInput()})
    with pytest.raises(ValidationError):
        Section(elements={"Wharf": PileInput()})


def test_project_crud(client):
    r = client.post("/api/projects", json={"info": {"name": "Berth 3"}, "element_names": ["Pile(1)", "Deck"]})
    assert r.status_code == 201
    p = r.json()
    [section] = p["sections"]
    assert set(section["elements"]) == {"Pile(1)", "Deck"}

    section["elements"]["Pile(1)"]["diameter"] = 1500
    section["elements"]["Pile(1)"]["casing"] = {"role": "crack_only", "top_level": 2.7, "bottom_level": -1.3}
    r = client.put(f"/api/projects/{p['id']}", json=p)
    assert r.status_code == 200, r.text
    got = client.get(f"/api/projects/{p['id']}").json()
    pile = got["sections"][0]["elements"]["Pile(1)"]
    assert pile["diameter"] == 1500 and pile["casing"]["thickness"] == 16

    [summary] = client.get("/api/projects").json()
    assert (summary["name"], summary["sections"], summary["elements"]) == ("Berth 3", 1, 2)

    url = f"/api/projects/{p['id']}/sections/{section['id']}"
    r = client.post(f"{url}/elements", json={"names": ["Combi Wall", "Deck", "Portal Frame"]})
    assert r.json()["added"] == ["Combi Wall"]

    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_sections(client):
    p = client.post("/api/projects", json={"section_name": "Section 01a"}).json()
    first = p["sections"][0]["id"]
    r = client.post(f"/api/projects/{p['id']}/sections", json={"name": "Section 02"})
    assert r.status_code == 201
    names = [s["name"] for s in r.json()["sections"]]
    assert names == ["Section 01a", "Section 02"]
    assert client.post(f"/api/projects/{p['id']}/sections", json={"name": "section 02"}).status_code == 400

    r = client.delete(f"/api/projects/{p['id']}/sections/{first}")
    assert [s["name"] for s in r.json()["sections"]] == ["Section 02"]
    last = r.json()["sections"][0]["id"]
    assert client.delete(f"/api/projects/{p['id']}/sections/{last}").status_code == 400
    assert client.delete(f"/api/projects/{p['id']}/sections/abcdef12").status_code == 404


def test_new_section_copies_settings(client):
    p = client.post("/api/projects", json={"section_name": "Section 01a"}).json()
    src = p["sections"][0]
    url = f"/api/projects/{p['id']}/sections/{src['id']}"
    client.post(f"{url}/elements", json={"names": ["Pile(1)", "Slab"]})
    p = client.get(f"/api/projects/{p['id']}").json()
    s = p["sections"][0]
    s.update(x_min=10, x_max=90, peaks="average", combinations=["QP", "Seismic 1"])
    s["elements"]["Pile(1)"]["diameter"] = 1500
    s["sheet_map"] = {"Odd sheet": {"element": "Slab", "combination": "QP"}}
    s["combination_map"] = {"Seis 1": "Seismic 1"}
    s["review"] = {"abc": "accept"}
    s["excluded_peaks"] = ["Slab|QP|12"]
    assert client.put(f"/api/projects/{p['id']}", json=p).status_code == 200

    r = client.post(f"/api/projects/{p['id']}/sections", json={"name": "Section 02", "copy_from": src["id"]})
    assert r.status_code == 201
    old, new = r.json()["sections"]
    assert new["name"] == "Section 02" and new["id"] != old["id"]
    for key in ("x_min", "x_max", "peaks", "combinations", "elements", "load_factors", "costing"):
        assert new[key] == old[key], key
    assert new["elements"]["Pile(1)"]["diameter"] == 1500
    workbook_own = (new["sheet_map"], new["combination_map"], new["review"], new["excluded_peaks"])
    assert workbook_own == ({}, {}, {}, [])

    # Settings are the new section's own afterwards.
    p = r.json()
    p["sections"][1]["elements"]["Pile(1)"]["diameter"] = 1200
    p = client.put(f"/api/projects/{p['id']}", json=p).json()
    assert [s["elements"]["Pile(1)"]["diameter"] for s in p["sections"]] == [1500, 1200]

    bad = {"name": "Section 03", "copy_from": "abcdef12"}
    assert client.post(f"/api/projects/{p['id']}/sections", json=bad).status_code == 404


def test_old_projects_move_into_one_section():
    old = {"info": {"name": "Berth", "section": "Section 01a"}, "elements": {"Pile(1)": {"kind": "pile"}}}
    p = Project.model_validate(old)
    [s] = p.sections
    assert s.name == "Section 01a" and list(s.elements) == ["Pile(1)"]
    assert "section" not in p.info.model_dump()


def test_invalid_update_is_rejected_with_field_path(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    p["sections"][0]["elements"]["Pile(1)"]["diameter"] = -1
    r = client.put(f"/api/projects/{p['id']}", json=p)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][-1] == "diameter"


def test_bad_ids_are_not_found(client):
    assert client.get("/api/projects/..%2Fsecret").status_code == 404
    assert client.get("/api/projects/abcdef").status_code == 404


def test_reference_endpoints(client):
    assert "C40/50" in [c["grade"] for c in client.get("/api/materials").json()["concrete"]]
    schema = client.get("/api/schema/project").json()
    assert "PileInput" in schema["$defs"]


def test_elements_use_project_grades_unless_set():
    from triton.project import Materials, SheetPileInput, with_project_grades

    m = Materials(concrete="C50/60", infill_concrete="C35/45", structural_steel="S460")
    pile = with_project_grades(PileInput(casing=Casing()), m)
    assert pile.concrete == "C50/60" and pile.casing.steel == "S460"
    assert with_project_grades(PileInput(concrete="C30/37"), m).concrete == "C30/37"
    wall = with_project_grades(CombiWallInput(), m)
    assert (wall.concrete, wall.steel) == ("C35/45", "S460")
    assert with_project_grades(SheetPileInput(), m).steel == "S355GP"
    assert PileInput().concrete is None  # unset until designed
