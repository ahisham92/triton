from fastapi.testclient import TestClient

from triton import method
from triton.project import BeamInput, PileInput, Project, Section, SlabInput


def test_method_lists_kinds_topics_and_options_in_use():
    p = Project(
        sections=[
            Section(
                elements={
                    "Deck": SlabInput(peaks="ring_mean"),
                    "Pile(1)": PileInput(),
                    "Front Beam": BeamInput(),
                }
            )
        ]
    )
    v = method.view(p)
    assert [k["kind"] for k in v["kinds"]] == ["Piles", "Slabs", "Beams"]
    slabs = v["kinds"][1]
    assert all(t["text"] for t in slabs["topics"])
    faces = slabs["pile_faces"]
    assert [f["value"] for f in faces["methods"]] == ["peak", "face_mean", "ring_mean", "envelope_face_mean"]
    assert faces["chosen"] == [{"section": "Section 1", "element": "Deck", "value": "ring_mean"}]
    titles = {o["title"] for o in v["project_options"]}
    assert "Positive plate moments (M11, M22) in the workbook" in titles
    assert not any("grade" in t.lower() for t in titles)
    peaks = next(o for o in slabs["options"] if o["field"] == "peaks")
    assert peaks["chosen"] == "ring_mean" and "face_mean" in peaks["choices"]


def test_method_route(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    from triton import api

    c = TestClient(api.app)
    pid = c.post("/api/projects", json={"name": "M"}).json()["id"]
    r = c.get(f"/api/projects/{pid}/method")
    assert r.status_code == 200 and r.json()["empty"] is True
