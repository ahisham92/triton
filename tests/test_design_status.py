"""The Design tab's sections board: each section designed or not, and what is out of date."""

from fastapi.testclient import TestClient

from triton.api import app


def test_design_status_lists_every_section_undesigned():
    client = TestClient(app)
    body = {"info": {"name": "Board"}, "section_name": "S1", "element_names": ["Pile(1)"]}
    pid = client.post("/api/projects", json=body).json()["id"]
    rows = client.get(f"/api/projects/{pid}/design-status").json()
    assert [r["name"] for r in rows] == ["S1"]
    assert rows[0]["designed"] is False
    assert rows[0]["elements"] == 1
    client.delete(f"/api/projects/{pid}")
