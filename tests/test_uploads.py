"""Workbooks sent in pieces: hosts cap one request (PythonAnywhere at about 100 MB)."""

import io
import os
import time

import pytest
from conftest import plate_sheet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton.api import UPLOAD_MAX_AGE, app

client = TestClient(app)


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    return tmp_path


def workbook_bytes():
    wb = Workbook()
    wb.remove(wb.active)
    for name, scale in (("SPW-QP", 1.0), ("SPW-PT-B-Apron", 2.0)):
        ws = wb.create_sheet(name)
        for r in plate_sheet(scale=scale):
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def send(data, name="section.xlsx", piece=1000):
    r = client.post("/api/uploads", json={"filename": name, "size": len(data)})
    assert r.status_code == 201, r.text
    upload_id = r.json()["id"]
    for at in range(0, len(data), piece):
        r = client.put(f"/api/uploads/{upload_id}?offset={at}", content=data[at : at + piece])
        assert r.status_code == 200 and r.json()["received"] == min(at + piece, len(data))
    return upload_id


def test_pieces_are_joined_and_checked(data_dir):
    data = workbook_bytes()
    upload_id = send(data)
    r = client.post(f"/api/workbooks/check/{upload_id}")
    assert r.status_code == 200, r.text
    assert r.json()["file"] == "section.xlsx"
    assert r.json()["coverage"]["SPW"] == {"PT-B-Apron": "ok", "QP": "ok"}
    assert not any((data_dir / "uploads").iterdir())  # taken, then deleted


def test_a_piece_sent_again_replaces_itself():
    data = workbook_bytes()
    upload_id = client.post("/api/uploads", json={"filename": "w.xlsx"}).json()["id"]
    client.put(f"/api/uploads/{upload_id}?offset=0", content=data[:500])
    client.put(f"/api/uploads/{upload_id}?offset=0", content=data[:500])  # a retry
    r = client.put(f"/api/uploads/{upload_id}?offset=500", content=data[500:])
    assert r.json()["received"] == len(data)
    assert client.post(f"/api/workbooks/check/{upload_id}").status_code == 200


def test_a_gap_is_refused():
    upload_id = client.post("/api/uploads", json={"filename": "w.xlsx"}).json()["id"]
    assert client.put(f"/api/uploads/{upload_id}?offset=10", content=b"x").status_code == 409


def test_kept_with_the_section():
    project = client.post("/api/projects", json={"info": {"name": "Pieces"}}).json()
    section = project["sections"][0]["id"]
    upload_id = send(workbook_bytes())
    url = f"/api/projects/{project['id']}/sections/{section}/workbook"
    r = client.post(f"{url}/{upload_id}")
    assert r.status_code == 200, r.text
    assert client.get(url).json()["file"] == "section.xlsx"


def test_wrong_type_and_unknown_ids():
    assert client.post("/api/uploads", json={"filename": "notes.pdf"}).status_code == 400
    assert client.put("/api/uploads/../../etc?offset=0", content=b"x").status_code == 404
    assert client.post("/api/workbooks/check/" + "0" * 32).status_code == 404


def test_a_bad_workbook_is_reported_and_cleaned_up(data_dir):
    upload_id = send(b"not a workbook at all")
    r = client.post(f"/api/workbooks/check/{upload_id}")
    assert r.status_code == 400
    assert not any((data_dir / "uploads").iterdir())


def test_abandoned_uploads_are_cleared(data_dir):
    stale = client.post("/api/uploads", json={"filename": "w.xlsx"}).json()["id"]
    old = time.time() - UPLOAD_MAX_AGE - 60
    os.utime(data_dir / "uploads" / stale, (old, old))
    client.post("/api/uploads", json={"filename": "w.xlsx"})
    assert not (data_dir / "uploads" / stale).exists()
