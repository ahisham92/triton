"""Tab data kept once worked out (triton/views.py): the Clashes answer and heads, and the stamp."""

import time

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
