"""Duplicate a whole project, and edit its name and people while the model is locked."""

from __future__ import annotations

from conftest import pile_sheet
from test_design import xlsx_bytes


def test_duplicate_a_project_with_all_its_data(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from triton.api import app

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    p = client.post("/api/projects", json={"info": {"name": "Berth 1"}, "element_names": ["Pile(1)"]}).json()
    sid = p["sections"][0]["id"]
    url = f"/api/projects/{p['id']}/sections/{sid}"
    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet()})
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)})
    assert client.post(f"{url}/design").status_code == 200
    before = client.get(f"{url}/design").json()

    copy = client.post(f"/api/projects/{p['id']}/duplicate", json={}).json()
    assert copy["id"] != p["id"] and copy["info"]["name"] == "Berth 1 copy" and copy["locked"]
    again = client.post(f"/api/projects/{p['id']}/duplicate", json={"name": "Berth 1 copy"}).json()
    assert again["info"]["name"] == "Berth 1 copy (2)"
    new_url = f"/api/projects/{copy['id']}/sections/{sid}"
    assert client.get(f"{new_url}/workbook").json()["file"] == "s.xlsx"
    after = client.get(f"{new_url}/design").json()
    assert not after.get("changed") and after["piles"][0]["arrangement"] == before["piles"][0]["arrangement"]

    # The copy is its own project: deleting it leaves the original whole.
    assert client.delete(f"/api/projects/{again['id']}").status_code == 204
    assert client.get(f"{url}/design").json()["piles"]

    # Names, numbers and people change while the model is locked; design inputs do not.
    project = client.get(f"/api/projects/{copy['id']}").json()
    project["info"].update(name="Berth 1 option B", number="P-102", client="Port", approver="AM")
    r = client.put(f"/api/projects/{copy['id']}", json=project)
    assert r.status_code == 200 and r.json()["info"]["name"] == "Berth 1 option B"
    assert not client.get(f"{new_url}/design").json().get("changed")
    project = r.json()
    project["design"]["partial_factors"]["gamma_c"] = 1.4
    assert client.put(f"/api/projects/{copy['id']}", json=project).status_code == 409


def test_a_project_saved_with_issued_revisions_still_opens():
    from triton.project import Project

    p = Project.model_validate({"info": {"name": "Old"}, "revisions": [{"rev": "P01"}]})
    assert "revisions" not in p.model_dump()
