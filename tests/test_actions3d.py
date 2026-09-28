"""Straining actions of one element for its 3D view on the Design tab: display only."""

import pytest
from conftest import plate_sheet, pile_sheet
from fastapi.testclient import TestClient
from test_design import xlsx_bytes

from triton.api import app


@pytest.fixture()
def section(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    p = client.post("/api/projects", json={"info": {"name": "A"}, "element_names": ["Pile(1)", "Deck"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    data = xlsx_bytes(
        {
            "Pile(1)-PT-B-Apron": pile_sheet(scale=1.0),
            "Pile(1)-PT-B-Yard": pile_sheet(scale=-2.0),
            "Pile(1)-QP": pile_sheet(scale=10.0),
            "Deck-PT-B-Apron": plate_sheet(scale=1.0),
            "Deck-QP": plate_sheet(scale=1.0),
        }
    )
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)}).status_code == 200
    return client, url


def test_a_pile_by_case_and_envelope(section):
    client, url = section
    r = client.get(f"{url}/actions", params={"element": "Pile(1)", "case": "PT-B-Apron", "action": "M_3"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["kind"] == "frame" and [a["label"] for a in out["actions"]][:5] == ["N", "Q12", "Q13", "M2", "M3"]
    assert set(out["combinations"]) == {"PT-B-Apron", "PT-B-Yard", "QP"} and "QP" not in out["design"]
    # Nodes at z -1..-5, one per 0.5 m step: M_3 is node + 5 (Plaxis value, multipliers of 1).
    by_z = {p[2]: v for p, v in zip(out["points"], out["values"], strict=True)}
    assert by_z[-0.75] == 6.0 and by_z[-4.75] == 10.0
    # Envelopes over the design combinations only (QP's ten times larger values left out).
    mx = client.get(f"{url}/actions", params={"element": "Pile(1)", "case": "env_max", "action": "M_3"}).json()
    mn = client.get(f"{url}/actions", params={"element": "Pile(1)", "case": "env_min", "action": "M_3"}).json()
    ab = client.get(f"{url}/actions", params={"element": "Pile(1)", "case": "env_abs", "action": "M_3"}).json()
    i = [p[2] for p in mx["points"]].index(-0.75)
    assert mx["values"][i] == 6.0 and mx["source"][i] == "PT-B-Apron"
    assert mn["values"][i] == -12.0 and mn["source"][i] == "PT-B-Yard"
    assert ab["values"][i] == -12.0  # the larger in size, with its sign


def test_the_deck_on_its_grid_and_no_stale_design(section):
    client, url = section
    out = client.get(f"{url}/actions", params={"element": "Deck", "case": "PT-B-Apron", "action": "M_11"}).json()
    assert out["kind"] == "plate" and out["size"] == 1.0
    assert "M11" in [a["label"] for a in out["actions"]]
    assert len(out["points"]) == len(out["values"]) > 0
    assert client.get(f"{url}/actions", params={"element": "Nothing"}).status_code == 404
