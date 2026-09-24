"""A whole project as a .trt file: download it, open it as a new project, replace or keep both."""

from __future__ import annotations

import io
import json
import zipfile

from conftest import pile_sheet
from test_design import xlsx_bytes


def _upload(client, name: str, data: bytes) -> str:
    up = client.post("/api/uploads", json={"filename": name, "size": len(data)}).json()["id"]
    client.put(
        f"/api/uploads/{up}?offset=0", content=data, headers={"Content-Type": "application/octet-stream"}
    )
    return up


def test_download_and_open_a_project(tmp_path, monkeypatch):
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

    r = client.get(f"/api/projects/{p['id']}/project.trt")
    assert r.status_code == 200 and 'filename="Berth 1.trt"' in r.headers["content-disposition"]
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {
        "triton.json",
        "project.json",
        f"sections/{sid}/results.json",
        f"sections/{sid}/sheets/0.json",
    } <= names
    assert not any(n.endswith((".pkl", ".pkl.gz")) for n in names)  # data only, nothing that runs
    trt = r.content

    # A project already has the name: asked first, the upload waits.
    up = _upload(client, "Berth 1.trt", trt)
    ask = client.post(f"/api/projects/open/{up}", json={}).json()
    assert ask["exists"][0]["id"] == p["id"]
    kept = client.post(f"/api/projects/open/{up}", json={"if_exists": "keep"}).json()
    assert kept["name"] == "Berth 1 (2)" and kept["id"] != p["id"] and not kept["notes"]
    new_url = f"/api/projects/{kept['id']}/sections/{sid}"
    wb = client.get(f"{new_url}/workbook").json()
    assert wb["file"] == "s.xlsx" and len(wb["sheets"]) == 2
    after = client.get(f"{new_url}/design").json()
    assert not after.get("changed") and after["piles"][0]["arrangement"] == before["piles"][0]["arrangement"]
    sheet = client.get(f"{new_url}/workbook/sheet", params={"name": "Pile(1)-QP"}).json()
    assert sheet["editable"]

    # Replace: the old project goes, the opened one keeps the name.
    up = _upload(client, "Berth 1.trt", trt)
    rep = client.post(f"/api/projects/open/{up}", json={"if_exists": "replace", "replace_id": p["id"]}).json()
    assert rep["replaced"] and rep["name"] == "Berth 1"
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    assert sorted(x["name"] for x in client.get("/api/projects").json()) == ["Berth 1", "Berth 1 (2)"]


def test_a_file_that_is_not_a_project_is_refused(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from triton.api import app

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    up = _upload(client, "x.trt", b"not a zip")
    r = client.post(f"/api/projects/open/{up}", json={"if_exists": "keep"})
    assert r.status_code == 400 and "not a Triton project" in r.json()["detail"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("triton.json", json.dumps({"format": "triton.project/1"}))
        project = {"info": {"name": "Evil"}, "sections": [{"id": "../x", "name": "S"}]}
        z.writestr("project.json", json.dumps(project))
    up = _upload(client, "x.trt", buf.getvalue())
    r = client.post(f"/api/projects/open/{up}", json={"if_exists": "keep"})
    assert r.status_code == 400 and client.get("/api/projects").json() == []


def test_a_workbook_uploaded_before_its_rows_were_kept_comes_along(tmp_path, monkeypatch):
    import shutil

    from fastapi.testclient import TestClient

    from triton.api import app, store

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    p = client.post("/api/projects", json={"info": {"name": "Old"}, "element_names": ["Pile(1)"]}).json()
    sid = p["sections"][0]["id"]
    url = f"/api/projects/{p['id']}/sections/{sid}"
    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet()})
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)})
    shutil.rmtree(store().root / p["id"] / sid / "raw")
    up = _upload(client, "Old.trt", client.get(f"/api/projects/{p['id']}/project.trt").content)
    new = client.post(f"/api/projects/open/{up}", json={"if_exists": "keep"}).json()
    a, b = store().load_workbook(p["id"], sid), store().load_workbook(new["id"], sid)
    assert [s.name for s in a.sheets] == [s.name for s in b.sheets]
    assert all(x.frame.equals(y.frame) for x, y in zip(a.sheets, b.sheets, strict=True))
    assert sorted(i.id for i in a.all_issues()) == sorted(i.id for i in b.all_issues())
    assert client.post(f"/api/projects/{new['id']}/sections/{sid}/design").status_code == 200
