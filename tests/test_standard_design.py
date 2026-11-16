"""Standard design: the quick overview beside the Detailed one."""

import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from test_design import xlsx_bytes
from test_slabs import deck_workbook

from triton.api import app
from triton.design import standard
from triton.design.runner import run_section
from triton.project import DesignSettings, PileInput, Section, SlabInput
from triton.validation import import_sheets


def deck_section():
    limits = {"crack_width_limit": 0.3, "crack_width_limit_bottom": 0.3, "peaks": "design"}
    return Section(
        elements={"Deck": SlabInput(thickness=800, **limits), "Pile(1)": PileInput(head_level=2.7)}
    )


def test_standard_is_the_detailed_design_marked():
    wb = deck_workbook()
    full = run_section(DesignSettings(), deck_section(), wb)
    same = run_section(DesignSettings(), deck_section(), wb, mode="standard")
    assert full["mode"] == "detailed" and same["mode"] == "standard"
    for kind in ("piles", "slabs"):
        (d,), (q,) = full[kind], same[kind]
        assert standard.KEY not in d and q[standard.KEY] == "standard"
        assert q["notes"][0] == standard.NOTE and q["standard"]["checks"]
        # Everything else exactly as the Detailed design: bars, zones, alternatives, AdSec sets.
        drop = (standard.KEY, "standard", "notes")
        assert {k: v for k, v in q.items() if k not in drop} == {k: v for k, v in d.items() if k not in drop}
        assert q["notes"][1:] == d["notes"]
    with pytest.raises(ValueError):
        run_section(DesignSettings(), deck_section(), wb, mode="rough")


def test_summary_says_what_does_not_work():
    entry = {
        "element": "Pile(1)",
        "passed": False,
        "utilisation": 1.2,
        "shear": {"utilisation": 0.4, "passed": True},
        "cracks": {"wk_mm": 0.3, "limit_mm": 0.2, "passed": False},
        "steel": {"kg_per_m3": 150.0, "ratio_pct": 1.9},
        "failure": [],
    }
    o = standard.summary("piles", entry)
    assert not o["workable"] and o["kg_per_m3"] == 150 and o["ratio_pct"] == 1.9
    assert o["governs"].startswith("QP crack width")
    assert [c["check"].split(" (")[0] for c in o["checks"] if not c["passed"]] == [
        "Bending with axial force",
        "QP crack width",
    ]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_standard_results_have_no_bar_exports_until_designed_in_detail(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)", "Pile(2)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    sheets = {f"Pile({i})-{c}": pile_sheet() for i in (1, 2) for c in ("PT-B-Apron", "QP")}
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", xlsx_bytes(sheets))}).status_code == 200
    assert client.post(f"{url}/design", json={"mode": "rough"}).status_code == 422

    r = client.post(f"{url}/design", json={"mode": "standard"})
    assert r.status_code == 200, r.text
    assert {e["element"]: e[standard.KEY] for e in r.json()["piles"]} == {
        "Pile(1)": "standard",
        "Pile(2)": "standard",
    }
    assert r.json()["changed"] == [] and "mode" not in client.get(f"{url}/design").json()
    for path in ("drawings.dxf", "drawings.crm", "cages.json", "adsec.zip", "governing.xlsx"):
        got = client.get(f"{url}/design/{path}")
        assert got.status_code == 409 and "Detailed" in got.json()["detail"], path
    assert client.get(f"{url}/clashes").status_code == 409
    rep = client.get(f"{url}/design/report.xlsx?detail=detailed")
    assert rep.status_code == 200

    # One element designed again in Detailed: its bars export, the other stays Standard.
    r = client.post(f"{url}/design", json={"elements": ["Pile(1)"]})
    modes = {e["element"]: e.get(standard.KEY, "detailed") for e in r.json()["piles"]}
    assert modes == {"Pile(1)": "detailed", "Pile(2)": "standard"}
    cages = client.get(f"{url}/design/cages.json")
    assert cages.status_code == 200
    assert "Pile(2)" not in cages.text and "Pile(1)" in cages.text
    assert client.get(f"{url}/design/drawings.dxf").status_code == 200


def test_report_lists_standard_elements_in_an_overview():
    from triton.project import Project
    from triton.report import build_report

    wb = import_sheets({f"Pile(1)-{c}": pile_sheet() for c in ("PT-B-Apron", "QP")})
    section = Section(elements={"Pile(1)": PileInput()})
    res = run_section(DesignSettings(), section, wb, mode="standard")
    rep = build_report(Project(sections=[section]), section, res, "detailed")
    text = str(rep.blocks)
    assert "Standard design (overview)" in text and "Pile(1): pile" in text


def test_standard_finished_in_detailed_without_designing_again(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)", "Pile(2)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    sheets = {f"Pile({i})-{c}": pile_sheet() for i in (1, 2) for c in ("PT-B-Apron", "QP")}
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", xlsx_bytes(sheets))})
    before = client.post(f"{url}/design", json={"mode": "standard"}).json()
    assert client.get(f"{url}/design/drawings.dxf").status_code == 409

    # Pile(2) changed since (its results are dropped): it has to be designed again; Pile(1) is taken
    # as it is.
    project = client.get(f"/api/projects/{p['id']}").json()
    project["locked"] = False
    project["sections"][0]["elements"]["Pile(2)"]["diameter"] = 1500
    assert client.put(f"/api/projects/{p['id']}", json=project).status_code == 200
    r = client.post(f"{url}/design/finish").json()
    assert r["finished"] == ["Pile(1)"] and "Pile(2)" not in {e["element"] for e in r["piles"]}
    (p1,) = [e for e in r["piles"] if e["element"] == "Pile(1)"]
    (was,) = [e for e in before["piles"] if e["element"] == "Pile(1)"]
    assert standard.KEY not in p1 and "standard" not in p1 and standard.NOTE not in p1["notes"]
    drop = (standard.KEY, "standard", "notes")
    assert {k: v for k, v in p1.items() if k not in drop} == {k: v for k, v in was.items() if k not in drop}
    assert r["run_at"] == before["run_at"]  # not designed again
    assert client.get(f"{url}/design/drawings.dxf").status_code == 200
    assert client.post(f"{url}/design/finish").json()["finished"] == []
