"""An unexpected server error answers with a code, and its traceback is kept under the code."""

import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from test_parallel import xlsx_bytes

from triton import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    return TestClient(api.app, raise_server_exceptions=False)


def test_a_design_error_names_its_code_element_and_place(client, monkeypatch):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    data = xlsx_bytes({f"Pile(1)-{c}": pile_sheet() for c in ("PT-B-Apron", "QP")})
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)}).status_code == 200

    def broken(*args, **kwargs):
        raise ZeroDivisionError("float division by zero")

    monkeypatch.setattr("triton.design.runner.design_pile", broken)
    r = client.post(f"{url}/design")
    assert r.status_code == 500
    body = r.json()
    assert body["code"].startswith("E") and body["element"] == "Pile(1)"
    assert "ZeroDivisionError" in body["detail"] and "while designing Pile(1)" in body["detail"]
    assert body["where"].startswith("test_errors.py") or "in broken" in body["where"]
    kept = client.get(f"/api/errors/{body['code']}")
    assert kept.status_code == 200 and "Traceback" in kept.text and "Element: Pile(1)" in kept.text
    assert client.get("/api/errors/Enope").status_code == 404
    assert client.get("/api/errors/..%2Fx").status_code == 404


def test_a_section_failing_in_the_runner_gives_the_code(client, monkeypatch):
    from triton import runner

    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    pid, sid = p["id"], p["sections"][0]["id"]
    data = xlsx_bytes({f"Pile(1)-{c}": pile_sheet() for c in ("PT-B-Apron", "QP")})
    client.post(f"/api/projects/{pid}/sections/{sid}/workbook", files={"file": ("s.xlsx", data)})
    monkeypatch.setattr("triton.design.runner.design_pile", lambda *a, **k: 1 / 0)
    state, note = runner.design_one(pid, sid, "detailed")
    code = note.split()[2].rstrip(":")
    assert state == "failed" and note.startswith("Server error E") and "Pile(1)" in note
    assert client.get(f"/api/errors/{code}").status_code == 200
