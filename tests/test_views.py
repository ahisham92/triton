"""Tab data kept once worked out (triton/views.py): the Clashes answer and heads, and the stamp."""

import time

import pytest
from test_clashes import api, designed  # noqa: F401 - fixtures

from triton import api as api_mod
from triton import views
from triton.api import store


def test_the_clashes_are_kept_for_any_worker_until_something_changes(api, monkeypatch):  # noqa: F811
    client, base = api
    pid, sid = base.split("/")[3], base.split("/")[5]
    first = client.get(base + "/clashes", params={"progress": "view-test1"}).json()
    folder = store()._dir(pid, sid) / views.FOLDER
    assert (folder / "clashes.json.gz").exists() and (folder / "clashes-heads.pkl.gz").exists()

    # Another worker: nothing in memory, and nothing worked out again.
    api_mod._CLASHES.clear()

    def boom(*_a, **_k):
        raise AssertionError("worked out again")

    with monkeypatch.context() as m:
        m.setattr(api_mod, "find_clashes", boom)
        m.setattr(api_mod, "Clashes", boom)
        assert client.get(base + "/clashes").json() == first
        # A pile head comes from the kept heads, with their solutions.
        head = client.get(base + "/clashes/head", params={"group": "Pile(1)|Deck", "index": 0})
        assert head.status_code == 200

    # New results: worked out again.
    stamp = client.get(base + "/stamp").json()["stamp"]
    time.sleep(0.01)
    store().save_results(pid, sid, {**store().load_results(pid, sid), "run_at": "later"})
    assert client.get(base + "/stamp").json()["stamp"] != stamp
    api_mod._CLASHES.clear()
    assert client.get(base + "/clashes").json()["heads_checked"] == first["heads_checked"]


def test_a_kept_view_is_used_only_with_its_key(tmp_path):
    assert views.get(tmp_path, "x", "a") is None
    views.put(tmp_path, "x", "a", {"n": 1})
    assert views.get(tmp_path, "x", "a") == {"n": 1}
    assert views.get(tmp_path, "x", "b") is None
    assert views.kept(tmp_path, "x", "b", lambda: {"n": 2}) == {"n": 2}
    assert views.get(tmp_path, "x", "b") == {"n": 2}


def test_a_worker_keeps_the_last_workbooks_it_read(tmp_path, monkeypatch):
    from test_slabs import deck_workbook

    from triton.project import PileInput, Project, Section, SlabInput

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    api_mod._LAST_VIEWS.clear()
    section = Section(
        name="Section 01",
        elements={"Deck": SlabInput(thickness=800), "Pile(1)": PileInput(head_level=2.7)},
    )
    p = Project(sections=[section])
    store().save(p)
    assert api_mod._workbook(p.id, section) is None
    store().save_workbook(p.id, section.id, "deck.xlsx", deck_workbook())
    first = api_mod._workbook(p.id, section)
    sheets = [s.name for s in first.sheets]

    # Read again without opening the workbook, and a caller changing its copy changes nothing.
    first.sheets.clear()
    first.issues.append("changed")
    with monkeypatch.context() as m:
        m.setattr(store().__class__, "load_workbook", lambda *_a, **_k: 1 / 0)
        again = api_mod._workbook(p.id, section)
    assert [s.name for s in again.sheets] == sheets and "changed" not in again.issues

    # A new upload, or the section reading it another way, reads it again.
    store().save_workbook(p.id, section.id, "deck.xlsx", deck_workbook())
    with monkeypatch.context() as m:
        m.setattr(store().__class__, "load_workbook", lambda *_a, **_k: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            api_mod._workbook(p.id, section)
    api_mod._workbook(p.id, section)
    with monkeypatch.context() as m:
        m.setattr(store().__class__, "load_workbook", lambda *_a, **_k: 1 / 0)
        api_mod._workbook(p.id, section)
        with pytest.raises(ZeroDivisionError):
            api_mod._workbook(p.id, section.model_copy(update={"review": {"x": "accept"}}))
