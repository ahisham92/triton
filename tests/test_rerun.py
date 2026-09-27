"""Designing a section again redesigns only what changed: the elements whose inputs changed, and the
beams and slabs whose piles changed under them; the others keep their results."""

import io

import pytest
from conftest import pile_sheet, plate_sheet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton import api, fresh, runner


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


@pytest.fixture
def designed(client, monkeypatch):
    """A section with two piles, a front beam and a deck, designed once; ``asked`` lists what each
    later design ran (None: everything)."""
    names = ["Pile(1)", "Pile(2)", "Front Beam", "Deck"]
    p = client.post("/api/projects", json={"element_names": names}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    sheets = {f"Pile({i})-{c}": pile_sheet() for i in (1, 2) for c in ("PT-B-Apron", "QP")}
    sheets |= {f"{e}-{c}": plate_sheet() for e in ("Front Beam", "Deck") for c in ("PT-B-Apron", "QP")}
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", xlsx_bytes(sheets))}).status_code == 200
    assert client.post(f"{url}/design").status_code == 200
    asked = []
    real = api.run_section

    def spy(*args, only=None, **kwargs):
        asked.append(None if only is None else sorted(only))
        return real(*args, only=only, **kwargs)

    monkeypatch.setattr(api, "run_section", spy)
    return p["id"], url, asked


def unlocked(client, pid):
    """The project, unlocked to edit."""
    project = client.get(f"/api/projects/{pid}").json()
    project["locked"] = False
    for s in project["sections"]:
        s["locked"] = False
    return project


def edit(client, pid, name, **change):
    project = client.get(f"/api/projects/{pid}").json()
    project["locked"] = False
    for s in project["sections"]:
        s["locked"] = False
        s["elements"][name].update(change)
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200


def test_nothing_changed_runs_nothing_and_keeps_the_results(client, designed):
    _, url, asked = designed
    before = client.get(f"{url}/design").json()
    r = client.post(f"{url}/design", json={"changed_only": True}).json()
    assert r["unchanged"] and r["designed"] == [] and r["left"] == [] and asked == []
    assert r["stale"] == [] and r["changed"] == []
    assert [p["element"] for p in r["piles"]] == [p["element"] for p in before["piles"]]


def test_a_pile_detail_redesigns_only_that_pile(client, designed):
    pid, url, asked = designed
    edit(client, pid, "Pile(2)", cover=90)
    assert client.get(f"{url}/design").status_code == 200  # results kept while unlocked
    r = client.post(f"{url}/design", json={"changed_only": True}).json()
    assert asked == [["Pile(2)"]] and r["designed"] == ["Pile(2)"]
    assert r["stale"] == [] and len(r["piles"]) == 2 and r["beams"] and r["slabs"]


def test_a_slab_change_redesigns_only_the_slab(client, designed):
    pid, url, asked = designed
    edit(client, pid, "Deck", thickness=900)
    r = client.post(f"{url}/design", json={"changed_only": True}).json()
    assert asked == [["Deck"]] and r["stale"] == []


def test_a_pile_size_redesigns_the_beams_and_slabs_on_it(client, designed):
    pid, url, asked = designed
    edit(client, pid, "Pile(1)", diameter=1400)
    stale = client.get(f"{url}/design").json()["stale"]
    assert sorted(stale) == ["Deck", "Front Beam", "Pile(1)"]
    r = client.post(f"{url}/design", json={"changed_only": True}).json()
    assert asked == [["Deck", "Front Beam", "Pile(1)"]] and r["stale"] == []


def test_everything_again_still_designs_everything(client, designed):
    _, url, asked = designed
    assert client.post(f"{url}/design").status_code == 200
    assert asked == [None]


def test_results_kept_before_supports_were_recorded_stay_up_to_date(client, designed, tmp_path):
    pid, url, _ = designed
    sid = url.rsplit("/", 1)[1]
    s = api.store()
    results = s.load_results(pid, sid)
    for inputs in results["element_inputs"].values():
        inputs.pop(fresh.SUPPORTS, None)
    s.save_results(pid, sid, results)
    assert client.get(f"{url}/design").json()["stale"] == []


def test_design_all_takes_only_what_changed(client, designed, monkeypatch):
    pid, url, asked = designed
    monkeypatch.setattr(runner, "alive", lambda data=None: True)
    q = client.post(f"/api/projects/{pid}/design-all", json={"changed_only": True}).json()
    assert q["changed_only"] is True
    runner.work(pid)
    assert asked == []
    assert runner.load(pid)["sections"][0]["note"] == "Nothing changed: results kept"


def test_unlocked_with_no_change_locks_again_at_once(client, designed):
    pid, url, asked = designed
    edit(client, pid, "Pile(1)")  # unlocked, nothing changed
    assert not client.get(f"/api/projects/{pid}").json()["sections"][0]["locked"]
    r = client.post(f"{url}/design", json={"changed_only": True}).json()
    assert r["unchanged"] and r["locked"] and asked == []
    assert client.get(f"/api/projects/{pid}").json()["sections"][0]["locked"]


def test_a_new_workbook_redesigns_everything(client, designed):
    pid, url, asked = designed
    sid = url.rsplit("/", 1)[1]
    s = api.store()
    results = s.load_results(pid, sid)
    for inputs in results["element_inputs"].values():
        inputs["workbook"] = "an earlier upload"
    s.save_results(pid, sid, results)
    client.post(f"{url}/design", json={"changed_only": True})
    assert asked == [["Deck", "Front Beam", "Pile(1)", "Pile(2)"]]


def test_a_berth_length_puts_nothing_out_of_date(client, designed):
    """The berth length on the Costing tab is for costing only: the expansion joints are given on the
    Sections tab, so it no longer sets the beams' and slabs' restraint length (Ahmed, 2026-09-27) and
    no result goes out of date."""
    pid, url, asked = designed
    project = client.get(f"/api/projects/{pid}").json()
    project["sections"][0]["costing"]["berth_length"] = 300
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200
    r = client.get(f"{url}/design").json()
    assert r["changed"] == [] and r["stale"] == []


def test_the_runs_put_only_the_beams_and_slabs_out_of_date(client, designed):
    """The runs on the Sections tab lay the joints, which set only the beams' and slabs' restraint
    length: the piles keep their results and are not redesigned."""
    pid, url, asked = designed
    project = unlocked(client, pid)
    project["sections"][0]["joints"]["runs"] = [300]
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200
    r = client.post(f"{url}/design", json={"changed_only": True}).json()
    assert asked == [["Deck", "Front Beam"]] and r["stale"] == []


def test_a_multiplier_note_puts_nothing_out_of_date(client, designed):
    """Editing a load multiplier's note does not change the results, so they stay up to date and the
    multiplier keeps when it was applied (Ahmed, 2026-09-27). Changing the multiplier does not."""
    pid, url, asked = designed
    project = unlocked(client, pid)
    project["sections"][0]["load_factors"] = [{"factor": 1.35, "sheets": ["Pile(1)-QP"], "note": ""}]
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200
    assert client.post(f"{url}/design").status_code == 200
    project = unlocked(client, pid)
    applied = project["sections"][0]["load_factors"][0]["applied_at"]
    project["sections"][0]["load_factors"][0]["note"] = "Set B actions to design values"
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200
    project = client.get(f"/api/projects/{pid}").json()
    assert project["sections"][0]["load_factors"][0]["applied_at"] == applied
    r = client.get(f"{url}/design").json()
    assert r["changed"] == [] and r["stale"] == [] and len(r["piles"]) == 2  # nothing dropped
    r = client.post(f"{url}/design", json={"changed_only": True}).json()
    assert r["unchanged"] and asked == [None]
    project = unlocked(client, pid)
    project["sections"][0]["load_factors"][0]["factor"] = 1.5
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200
    client.post(f"{url}/design", json={"changed_only": True})
    assert asked == [None, None]  # a shared input: every result was out of date and dropped


def test_using_a_trial_with_an_approach_slab_leaves_the_element_up_to_date(client, designed):
    """The trial's element inputs include the project's approach slab as an element of its own, as a
    design does; it once went in as a shared part, so the element read out of date at once."""
    pid, url, _ = designed
    project = client.get(f"/api/projects/{pid}").json()
    project["approach"] = {}
    for s in project["sections"]:
        s["locked"] = False
        s["elements"]["Rear Beam"] = {"kind": "rear_beam"}
    assert client.put(f"/api/projects/{pid}", json=project).status_code == 200
    assert client.post(f"{url}/design").status_code == 200
    assert (
        client.post(f"{url}/trials", json={"element": "Pile(1)", "sizes": [{"diameter": 1500}]}).status_code
        == 200
    )
    assert (
        client.post(f"{url}/trials/use", json={"element": "Pile(1)", "size": {"diameter": 1500}}).status_code
        == 200
    )
    r = client.get(f"{url}/design").json()
    assert "Pile(1)" not in r["stale"] and not any("Approach" in c for c in r["changed"])
