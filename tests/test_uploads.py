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
    assert r.json()["suggestions"] == {}  # every sheet named as expected
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


def test_reading_reports_how_far_it_has_got(tmp_path):
    from triton.validation import import_workbook

    path = tmp_path / "w.xlsx"
    path.write_bytes(workbook_bytes())
    seen = []
    import_workbook(path, lambda fraction, step: seen.append((fraction, step)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 0.85
    assert seen[0][1].startswith("Reading SPW-") and seen[-1][1] == "Checking the sheets"
    assert abs(seen[-2][0] - 0.85) < 1e-9  # every sheet read


def test_progress_is_served_while_a_step_runs_and_gone_after(monkeypatch):
    from triton import api

    assert client.get("/api/progress/nothing-here").status_code == 404
    assert client.get("/api/progress/..%2Fetc").status_code == 404
    with api._Progress("abc") as tell:
        monkeypatch.setattr(api.time, "time", lambda real=time.time: real() + 10)
        tell(0.5, "Reading Pile(1)-QP")
        state = client.get("/api/progress/abc").json()
        assert state["fraction"] == 0.5 and state["step"] == "Reading Pile(1)-QP"
        assert 9 <= state["remaining_s"] <= 11  # as long again as it has taken
    assert client.get("/api/progress/abc").status_code == 404


def test_stop_ends_a_read_at_the_next_sheet_and_keeps_nothing(data_dir, monkeypatch):
    from triton import api

    upload_id = send(workbook_bytes())

    def read(path, progress):
        progress(0.1, "Reading SPW-QP")
        assert client.post(f"/api/progress/{upload_id}/stop").status_code == 202
        progress(0.2, "Reading SPW-PT-B-Apron")
        raise AssertionError("should have stopped")

    monkeypatch.setattr(api, "import_workbook", read)
    r = client.post(f"/api/workbooks/check/{upload_id}")
    assert r.status_code == 409 and r.json()["detail"] == "Stopped."
    assert not any((data_dir / "uploads").iterdir())
    assert not any((data_dir / "progress").iterdir())


def test_stop_needs_a_running_step_and_a_new_run_is_not_stopped_by_an_old_press():
    from triton import api

    assert client.post("/api/progress/idle/stop").status_code == 404
    with api._Progress("again"):
        client.post("/api/progress/again/stop")
    with api._Progress("again") as tell:  # the old press is gone
        tell(1.0, "Done")


def test_an_upload_can_be_dropped(data_dir):
    upload_id = client.post("/api/uploads", json={"filename": "w.xlsx"}).json()["id"]
    assert client.delete(f"/api/uploads/{upload_id}").status_code == 204
    assert not (data_dir / "uploads" / upload_id).exists()


def test_read_a_few_sheets_per_request(data_dir, monkeypatch):
    """A host cuts off a long request, so the page asks for the reading in steps."""
    from triton import api

    monkeypatch.setattr(api, "READ_STEP_S", 0)  # one sheet per step
    data = workbook_bytes()
    whole = client.post(f"/api/workbooks/check/{send(data)}").json()

    upload_id = send(data)
    steps = []
    while not (steps and steps[-1]["done"]):
        r = client.post(f"/api/uploads/{upload_id}/read")
        assert r.status_code == 200, r.text
        steps.append(r.json())
    assert [(s["sheets"], s["of"]) for s in steps] == [(1, 2), (2, 2)]
    assert 0 < steps[0]["fraction"] < 1 and steps[-1]["fraction"] == 1

    # Asked again after it finished (a retried request): nothing more to read.
    assert client.post(f"/api/uploads/{upload_id}/read").json()["done"]

    project = client.post("/api/projects", json={"info": {"name": "Steps"}}).json()
    url = f"/api/projects/{project['id']}/sections/{project['sections'][0]['id']}/workbook"
    kept = client.post(f"{url}/{upload_id}")
    assert kept.status_code == 200, kept.text
    for key in ("coverage", "sheets", "issues"):
        assert kept.json()[key] == whole[key], key
    assert not any((data_dir / "uploads").iterdir())

    # The rows as read were kept with the section, for the warnings review.
    sheet = client.get(f"{url}/sheet", params={"name": "SPW-QP"})
    assert sheet.status_code == 200, sheet.text
    assert sheet.json()["rows"] and sheet.json()["editable"]


def test_a_second_upload_read_in_steps_merges(data_dir, monkeypatch):
    from triton import api

    monkeypatch.setattr(api, "READ_STEP_S", 0)
    project = client.post("/api/projects", json={"info": {"name": "Steps"}}).json()
    url = f"/api/projects/{project['id']}/sections/{project['sections'][0]['id']}/workbook"
    for mode in ("replace", "update"):
        upload_id = send(workbook_bytes())
        while not client.post(f"/api/uploads/{upload_id}/read").json()["done"]:
            pass
        r = client.post(f"{url}/{upload_id}", params={"mode": mode})
        assert r.status_code == 200, r.text
    assert sorted(r.json()["merged"]["replaced"]) == ["SPW-PT-B-Apron", "SPW-QP"]
    assert client.get(f"{url}/sheet", params={"name": "SPW-PT-B-Apron"}).status_code == 200


def test_a_bad_workbook_read_in_steps_is_reported():
    upload_id = send(b"not a workbook at all")
    r = client.post(f"/api/uploads/{upload_id}/read")
    assert r.status_code == 400 and "Could not read" in r.json()["detail"]
