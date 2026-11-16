"""Project sites and section locations: shown on the projects map, never design inputs."""

from fastapi.testclient import TestClient

from triton import fresh
from triton.api import app
from triton.project import Project, Section, Site

client = TestClient(app)


def _project_with_two_sites():
    p = client.post(
        "/api/projects",
        json={"info": {"name": "Two sites", "client": "Port"}, "element_names": ["Pile(1)"]},
    ).json()
    p = client.post(f"/api/projects/{p['id']}/sections", json={"name": "Section 02"}).json()
    p["info"]["sites"] = [
        {"id": "north001", "name": "North", "lat": 29.63, "lon": 32.35},
        {"id": "south001", "name": "South", "lat": 29.58, "lon": 32.36},
    ]
    p["sections"][0]["location"] = {"site": "north001"}
    p["sections"][1]["location"] = {"site": "south001", "lat": 29.581, "lon": 32.362}
    r = client.put(f"/api/projects/{p['id']}", json=p)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_projects_list_carries_sites_and_section_pins():
    p = _project_with_two_sites()
    row = next(x for x in client.get("/api/projects").json() if x["id"] == p["id"])
    assert row["client"] == "Port"
    assert [s["name"] for s in row["sites"]] == ["North", "South"]
    pins = {s["name"]: s for s in row["section_pins"]}
    assert pins["Section 1"]["site"] == "north001" and pins["Section 1"]["lat"] is None
    assert pins["Section 02"]["lat"] == 29.581 and pins["Section 02"]["site"] == "south001"
    assert pins["Section 1"]["kinds"] == ["pile"]


def test_a_locked_section_can_still_be_moved_on_the_map():
    p = _project_with_two_sites()
    p["sections"][0]["locked"] = True
    p = client.put(f"/api/projects/{p['id']}", json=p).json()
    p["sections"][0]["location"] = {"site": "south001", "lat": 29.5, "lon": 32.3}
    p["info"]["sites"].append({"id": "third001", "name": "Third", "lat": None, "lon": None})
    r = client.put(f"/api/projects/{p['id']}", json=p)
    assert r.status_code == 200, r.text
    p = r.json()
    p["sections"][0]["end_trim"] = 5.0  # a design input stays locked
    assert client.put(f"/api/projects/{p['id']}", json=p).status_code == 409


def test_sites_and_locations_leave_the_fingerprint_alone():
    section = Section(name="S")
    section.add_elements(["Pile(1)"])
    project = Project(sections=[section])
    before = fresh.fingerprint(project, section, None)
    project.info.sites = [Site(name="A", lat=30.0, lon=31.0)]
    section.location.site = project.info.sites[0].id
    section.location.lat, section.location.lon = 30.001, 31.002
    assert fresh.fingerprint(project, section, None) == before


def test_old_projects_load_with_no_sites():
    p = Project.model_validate({"info": {"name": "Old"}, "sections": [{"name": "S"}]})
    assert p.info.sites == [] and p.sections[0].location.site == ""
    assert "location" not in Section().model_dump(exclude_defaults=True)
