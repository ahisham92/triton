"""Saves that must not undo another window's work: a page loaded before a design, a section deleted
while it is designed, and two requests changing the project file at once."""

import io
import json
import threading
import time

import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton import api

OLD = "2026-01-01T00:00:00+02:00"  # when a page loaded long ago last saved


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


def one_pile(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    data = xlsx_bytes({f"Pile(1)-{c}": pile_sheet() for c in ("PT-B-Apron", "QP")})
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)}).status_code == 200
    return p, url


# --- A page from before a design ------------------------------------------------------------


def test_a_page_loaded_before_a_design_cannot_unlock_it(client):
    p, url = one_pile(client)
    stale = client.get(f"/api/projects/{p['id']}").json()
    stale["updated_at"] = OLD
    assert client.post(f"{url}/design").status_code == 200  # in another window: the section is locked

    # The stale page still shows the section open and saves an edit with it.
    stale["sections"][0]["elements"]["Pile(1)"]["diameter"] = 1500.0
    r = client.put(f"/api/projects/{p['id']}", json=stale)
    assert r.status_code == 409 and "reload" in r.json()["detail"].lower()
    now = client.get(f"/api/projects/{p['id']}").json()
    assert now["locked"] and now["sections"][0]["locked"]
    assert now["sections"][0]["elements"]["Pile(1)"]["diameter"] != 1500.0
    assert client.get(f"{url}/design").json()["piles"]  # the results are kept


def test_the_window_that_designed_still_saves_and_unlocks(client):
    p, url = one_pile(client)
    page = client.get(f"/api/projects/{p['id']}").json()
    page["updated_at"] = OLD
    assert client.post(f"{url}/design").status_code == 200
    # The page locks the section itself (it did not read the project again): its edits still save.
    page["sections"][0]["locked"] = page["locked"] = True
    page["info"]["name"] = "Renamed"
    r = client.put(f"/api/projects/{p['id']}", json=page)
    assert r.status_code == 200
    # A check saved meanwhile, then Unlock from the page's copy of the last save.
    page = r.json()
    el = f"{url}/checks/Pile(1)"
    assert client.put(el, json={"status": "checked"}).status_code == 200
    page["updated_at"] = OLD  # the check saved after the page's last save (stamps are to the second)
    page["sections"][0]["locked"] = page["locked"] = False
    page["sections"][0]["elements"]["Pile(1)"]["diameter"] = 1500.0
    r = client.put(f"/api/projects/{p['id']}", json=page)
    assert r.status_code == 409  # a server write since: read again first (the page does, on Unlock)
    page["updated_at"] = client.get(f"/api/projects/{p['id']}").json()["updated_at"]
    r = client.put(f"/api/projects/{p['id']}", json=page)
    assert r.status_code == 200 and not r.json()["locked"]
    # Quick saves one after another, each from the answer of the one before.
    for name in ("One", "Two", "Three"):
        page = r.json()
        page["info"]["name"] = name
        r = client.put(f"/api/projects/{p['id']}", json=page)
        assert r.status_code == 200


def test_a_save_with_no_time_keeps_working(client):
    """Older pages and scripts send no updated_at: saved as before."""
    p, url = one_pile(client)
    assert client.post(f"{url}/design").status_code == 200
    body = client.get(f"/api/projects/{p['id']}").json()
    body.pop("updated_at")
    body["locked"] = False
    body["sections"][0]["locked"] = False
    r = client.put(f"/api/projects/{p['id']}", json=body)
    assert r.status_code == 200 and not r.json()["sections"][0]["locked"]


# --- A section deleted while it is designed -------------------------------------------------------


def two_sections(client):
    p, url = one_pile(client)
    project = client.post(f"/api/projects/{p['id']}/sections", json={"name": "Section 02"}).json()
    return project, url


def test_a_section_being_designed_cannot_be_deleted(client, tmp_path):
    p, url = two_sections(client)
    sid = p["sections"][0]["id"]
    owner = tmp_path / "progress" / f"design-{p['id']}-{sid}.owner"
    owner.parent.mkdir(exist_ok=True)
    owner.write_text(json.dumps({"run": "windowA", "at": time.time()}))  # between two steps

    r = client.delete(url)
    assert r.status_code == 409 and r.json()["detail"] == "Section 1 is being designed: stop it first."
    # Nor by a save that leaves it out (the Sections tab's Remove).
    page = client.get(f"/api/projects/{p['id']}").json()
    page["sections"] = page["sections"][1:]
    r = client.put(f"/api/projects/{p['id']}", json=page)
    assert r.status_code == 409 and "being designed" in r.json()["detail"]
    assert len(client.get(f"/api/projects/{p['id']}").json()["sections"]) == 2

    # A window closed mid-design holds it only for a while.
    owner.write_text(json.dumps({"run": "windowA", "at": time.time() - api.DESIGN_HOLD_S - 1}))
    assert client.delete(url).status_code == 200


def test_a_design_keeps_no_results_for_a_section_deleted_meanwhile(client, tmp_path, monkeypatch):
    p, url = two_sections(client)
    sid = p["sections"][0]["id"]
    real = api.run_section

    def deleted_meanwhile(*args, **kwargs):
        out = real(*args, **kwargs)
        project = api.store().get(p["id"])
        project.sections = [s for s in project.sections if s.id != sid]
        api.store().save(project)
        api.store().delete_section_files(p["id"], sid)
        return out

    monkeypatch.setattr(api, "run_section", deleted_meanwhile)
    r = client.post(f"{url}/design")
    assert r.status_code == 404
    assert not (tmp_path / p["id"] / sid / "results.json").exists()


# --- One change to the project file at a time --------------------------------------------------


def waits_for_the_project(client, pid, call):
    """``call`` waits while another request holds the project file, then goes ahead."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", call()))
    with api.store().project_lock(pid):
        t.start()
        t.join(0.5)
        assert t.is_alive(), "changed the project while another request held it"
    t.join(10)
    return out["r"]


SAVES = {
    "add a section": lambda c, base, url, pid, sid, other: c.post(base, json={"name": "Section 03"}),
    "delete a section": lambda c, base, url, pid, sid, other: c.delete(f"{base}/{other}"),
    "add elements": lambda c, base, url, pid, sid, other: c.post(
        f"{url}/elements", json={"names": ["Pile(2)"]}
    ),
    "set a check": lambda c, base, url, pid, sid, other: c.put(
        f"{url}/checks/Pile(1)", json={"status": "checked"}
    ),
    "carry renamed tabs": lambda c, base, url, pid, sid, other: api._carry_renamed_sheets(
        pid, sid, ["A → B"]
    ),
}


@pytest.mark.parametrize("save", SAVES)
def test_saves_wait_for_the_project(client, save):
    p, url = two_sections(client)
    pid, sid, other = p["id"], p["sections"][0]["id"], p["sections"][1]["id"]
    base = f"/api/projects/{pid}/sections"
    r = waits_for_the_project(client, pid, lambda: SAVES[save](client, base, url, pid, sid, other))
    assert r == [] if save == "carry renamed tabs" else r.status_code in (200, 201)
