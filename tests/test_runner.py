"""Design all sections on the server: the page queues, the runner designs with no page open."""

import io
import json
import time

import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton import api, runner


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    return TestClient(api.app)


def xlsx_bytes(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def three_sections(client):
    """Two sections with a workbook, the third without."""
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    pid, first = p["id"], p["sections"][0]["id"]
    for name in ("B", "C"):
        client.post(f"/api/projects/{pid}/sections", json={"name": name, "copy_from": first})
    ids = [s["id"] for s in client.get(f"/api/projects/{pid}").json()["sections"]]
    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet()})
    for sid in ids[:2]:
        r = client.post(f"/api/projects/{pid}/sections/{sid}/workbook", files={"file": ("s.xlsx", data)})
        assert r.status_code == 200
    return pid, ids


def test_queued_only_with_the_runner_on(client):
    pid, ids = three_sections(client)
    assert client.get("/api/runner").json() == {"on": False}
    assert client.post(f"/api/projects/{pid}/design-all").status_code == 503
    runner._beat_file().touch()
    assert client.get("/api/runner").json() == {"on": True}
    q = client.post(f"/api/projects/{pid}/design-all", json={"mode": "standard"}).json()
    assert [s["id"] for s in q["sections"]] == ids and q["mode"] == "standard" and not q["finished"]
    assert client.post(f"/api/projects/{pid}/design-all").status_code == 409  # already queued


def test_the_runner_designs_every_section_with_no_page_open(client):
    pid, ids = three_sections(client)
    runner._beat_file().touch()
    client.post(f"/api/projects/{pid}/design-all")
    assert runner._next() == pid
    runner.work(pid)
    q = client.get(f"/api/projects/{pid}/design-all").json()
    assert [(s["state"], s["note"]) for s in q["sections"]][:2] == [("done", "All safe")] * 2
    assert q["sections"][2]["state"] == "failed" and "workbook" in q["sections"][2]["note"]
    assert q["finished"] and runner._next() is None
    for sid in ids[:2]:
        assert client.get(f"/api/projects/{pid}/sections/{sid}/design").status_code == 200
    assert client.get(f"/api/projects/{pid}").json()["locked"]
    # Finished: a new one can be queued.
    assert client.post(f"/api/projects/{pid}/design-all").status_code == 200


def test_a_section_a_window_is_designing_is_waited_for(client, tmp_path):
    pid, ids = three_sections(client)
    owner = tmp_path / "progress" / f"design-{pid}-{ids[0]}.owner"
    owner.parent.mkdir(exist_ok=True)
    owner.write_text(json.dumps({"run": "windowA", "at": time.time()}))
    waits = []

    def sleep(s):
        waits.append(s)
        owner.unlink(missing_ok=True)  # the window finishes meanwhile

    assert runner.design_one(pid, ids[0], "detailed", sleep) == ("done", "All safe")
    assert waits == [runner.BUSY_WAIT_S]


def test_stop_leaves_the_rest_unstarted(client):
    pid, ids = three_sections(client)
    runner._beat_file().touch()
    client.post(f"/api/projects/{pid}/design-all")
    q = client.post(f"/api/projects/{pid}/design-all/stop").json()
    assert q["finished"] and q["stop"] and {s["state"] for s in q["sections"]} == {"stopped"}
    runner.work(pid)  # nothing left to do
    assert client.get(f"/api/projects/{pid}/sections/{ids[0]}/design").status_code == 404
    assert client.post(f"/api/projects/{pid}/design-all/stop").status_code == 404


def test_a_restarted_runner_starts_again_the_section_it_was_on(client):
    pid, ids = three_sections(client)
    runner._beat_file().touch()
    client.post(f"/api/projects/{pid}/design-all")
    runner._update(pid, lambda q: q["sections"][0].update(state="running"))
    runner._update(pid, runner._requeue)
    assert runner.load(pid)["sections"][0]["state"] == "waiting"
