"""Two things at once: PythonAnywhere runs several web workers, so two requests can touch the same
section's files at the same time (two windows, a design and an upload)."""

import io
import json
import threading
import time

import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton import api, atomic
from triton.store import ProjectStore


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


def two_piles(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)", "Pile(2)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    data = xlsx_bytes({f"Pile({i})-{c}": pile_sheet() for i in (1, 2) for c in ("PT-B-Apron", "QP")})
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)}).status_code == 200
    return p, url


def test_writers_never_share_a_temporary_file(tmp_path):
    path = tmp_path / "results.json"
    assert atomic.tmp_for(path) != atomic.tmp_for(path)
    assert atomic.tmp_for(path).name.endswith(".tmp")

    errors = []

    def write(n):
        try:
            for i in range(40):
                ProjectStore._write_json(path, {"writer": n, "i": i, "pad": "x" * 20000})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert json.loads(path.read_text())["i"] == 39
    assert not list(tmp_path.glob("*.tmp"))


def test_two_designs_of_one_section_at_once_keep_both(client, monkeypatch):
    """Two windows each design one pile of the same section at the same moment: both piles end up in
    the results (before, the one saved last dropped the other's)."""
    _, url = two_piles(client)
    real = api.run_section
    both_read = threading.Barrier(2)

    def slow(*args, **kwargs):
        out = real(*args, **kwargs)
        both_read.wait(timeout=10)  # both designs are done before either saves
        return out

    monkeypatch.setattr(api, "run_section", slow)
    answers = {}

    def design(name, run):
        answers[name] = client.post(f"{url}/design", json={"elements": [name]})

    threads = [threading.Thread(target=design, args=(n, None)) for n in ("Pile(1)", "Pile(2)")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.status_code == 200 for r in answers.values()), answers
    kept = client.get(f"{url}/design").json()
    assert sorted(p["element"] for p in kept["piles"]) == ["Pile(1)", "Pile(2)"]
    assert kept["stale"] == [] and kept["changed"] == []


def test_a_section_designing_in_one_window_is_busy_in_another(client, tmp_path):
    p, url = two_piles(client)
    key = f"design-{p['id']}-{p['sections'][0]['id']}"
    owner = tmp_path / "progress" / f"{key}.owner"

    # Window A is between two steps of its design (elements still left).
    owner.parent.mkdir(exist_ok=True)
    owner.write_text(json.dumps({"run": "windowA", "at": time.time()}))
    r = client.post(f"{url}/design", json={"run": "windowB"})
    assert r.status_code == 409 and "another window" in r.json()["detail"]
    assert json.loads(owner.read_text())["run"] == "windowA"

    # Its own next step goes ahead, and the last step frees the section.
    r = client.post(f"{url}/design", json={"run": "windowA"})
    assert r.status_code == 200 and r.json()["left"] == []
    assert not owner.exists()
    assert client.post(f"{url}/design", json={"run": "windowB"}).status_code == 200

    # A window closed mid-design holds the section only for a while.
    owner.write_text(json.dumps({"run": "windowA", "at": time.time() - api.DESIGN_HOLD_S - 1}))
    assert client.post(f"{url}/design", json={"run": "windowB"}).status_code == 200


def test_a_step_with_elements_left_keeps_the_section(client, tmp_path, monkeypatch):
    p, url = two_piles(client)
    key = f"design-{p['id']}-{p['sections'][0]['id']}"
    owner = tmp_path / "progress" / f"{key}.owner"
    r = client.post(f"{url}/design", json={"run": "windowA", "budget_s": 1e-9})
    assert r.status_code == 200 and r.json()["left"] == ["Pile(2)"]
    assert json.loads(owner.read_text())["run"] == "windowA"
    assert client.post(f"{url}/design", json={"run": "windowB"}).status_code == 409
    # Stop from window A: the section is free at once.
    client.post(f"/api/progress/{key}/stop")  # nothing running between steps: 404, harmless
    monkeypatch.setattr(api, "run_section", lambda *a, **k: (_ for _ in ()).throw(api.Stopped()))
    assert client.post(f"{url}/design", json={"run": "windowA"}).status_code == 409  # stopped
    assert not owner.exists()


def test_the_design_keeps_edits_saved_while_it_ran(client, monkeypatch):
    """The model locks on the project as saved now, not on the copy the design read at its start."""
    p, url = two_piles(client)
    real = api.run_section

    def edit_meanwhile(*args, **kwargs):
        project = client.get(f"/api/projects/{p['id']}").json()
        project["info"]["name"] = "Renamed meanwhile"
        assert client.put(f"/api/projects/{p['id']}", json=project).status_code == 200
        return real(*args, **kwargs)

    monkeypatch.setattr(api, "run_section", edit_meanwhile)
    assert client.post(f"{url}/design").status_code == 200
    project = client.get(f"/api/projects/{p['id']}").json()
    assert project["locked"] and project["info"]["name"] == "Renamed meanwhile"


def test_a_step_lists_a_beam_or_slab_as_designed_only_once_it_is(client):
    """A step that runs out of time before the deck used to list it as designed and still to come:
    the page showed "Nothing to design" until a later step designed it."""
    from conftest import plate_sheet

    p = client.post("/api/projects", json={"element_names": ["Pile(1)", "Front Beam", "Deck"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    sheets = {f"Pile(1)-{c}": pile_sheet() for c in ("PT-B-Apron", "QP")}
    sheets |= {f"{e}-{c}": plate_sheet() for e in ("Front Beam", "Deck") for c in ("PT-B-Apron", "QP")}
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", xlsx_bytes(sheets))}).status_code == 200
    ask, seen = None, []
    for _ in range(10):
        r = client.post(f"{url}/design", json={"elements": ask, "budget_s": 1e-9}).json()
        assert not set(r["designed"]) & set(r["left"]), r["designed"]
        assert len(r["designed"]) == len(set(r["designed"]))
        seen += r["designed"]
        if not r["left"]:
            break
        ask = r["left"]
    assert sorted(seen) == ["Deck", "Front Beam", "Pile(1)"]
