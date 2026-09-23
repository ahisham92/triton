import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from triton.api import app
from triton.materials import concrete, structural_steel_fy
from triton.project import Casing, CombiWallInput, PileInput, Project, default_element


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
    assert default_element("Portal Frame") is None
    assert default_element("Notes") is None


def test_add_elements_skips_known_and_unknown():
    p = Project()
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
        Project(elements={"Deck": PileInput()})
    with pytest.raises(ValidationError):
        Project(elements={"Wharf": PileInput()})


def test_project_crud(client):
    r = client.post("/api/projects", json={"info": {"name": "Berth 3"}, "element_names": ["Pile(1)", "Deck"]})
    assert r.status_code == 201
    p = r.json()
    assert set(p["elements"]) == {"Pile(1)", "Deck"}

    p["elements"]["Pile(1)"]["diameter"] = 1500
    p["elements"]["Pile(1)"]["casing"] = {"role": "crack_only", "top_level": 2.7, "bottom_level": -1.3}
    r = client.put(f"/api/projects/{p['id']}", json=p)
    assert r.status_code == 200, r.text
    got = client.get(f"/api/projects/{p['id']}").json()
    assert got["elements"]["Pile(1)"]["diameter"] == 1500
    assert got["elements"]["Pile(1)"]["casing"]["thickness"] == 16

    [summary] = client.get("/api/projects").json()
    assert (summary["name"], summary["elements"]) == ("Berth 3", 2)

    r = client.post(
        f"/api/projects/{p['id']}/elements", json={"names": ["Combi Wall", "Deck", "Portal Frame"]}
    )
    assert r.json()["added"] == ["Combi Wall"]

    assert client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert client.get(f"/api/projects/{p['id']}").status_code == 404


def test_invalid_update_is_rejected_with_field_path(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    p["elements"]["Pile(1)"]["diameter"] = -1
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
