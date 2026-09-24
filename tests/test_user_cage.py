"""A cage set by the user is checked, not chosen; the fallback cage carries the loads first."""

import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_curtailment import LOADS
from test_design import pile_sheets, xlsx_bytes

from triton.api import app
from triton.design.piles import design_pile
from triton.project import DesignSettings, PileInput, UserCage


def check(cage, **pile):
    return design_pile(
        "Pile(1)", PileInput(head_level=0.0, **pile), DesignSettings(), pile_sheets(LOADS), cage
    )


def test_a_cage_set_by_the_user_is_checked_as_it_is():
    auto = design_pile("Pile(1)", PileInput(head_level=0.0), DesignSettings(), pile_sheets(LOADS))
    light = check(UserCage(rows=1, count=20, diameter=20))
    assert light.user_set and light.arrangement.label == "20Ø20"
    assert light.utilisation > auto.utilisation and not light.passed
    assert any("set by you" in n for n in light.notes)
    heavy = check(UserCage(rows=2.5, count=26, diameter=32, inner_diameter=25))
    assert heavy.arrangement.label == "26Ø32 + 26Ø25 + 13Ø25" and heavy.arrangement.rows == 2.5
    assert heavy.utilisation < 1 and heavy.passed
    assert heavy.to_dict()["user_set"] and not auto.to_dict()["user_set"]


def test_a_user_cage_breaking_the_spacing_rules_fails_and_says_why():
    tight = check(UserCage(rows=1, count=60, diameter=32))
    assert not tight.passed
    assert any("clear spacing" in n and "below" in n for n in tight.notes)
    with pytest.raises(ValidationError, match="even number"):
        UserCage(rows=1.5, count=25, diameter=32)


def test_without_a_passing_cage_the_one_shown_carries_the_loads_first():
    # Crack widths cannot be met (limit far too small), but strength can: the cage shown carries the
    # loads, rather than the one with the smallest combined ratio.
    d = design_pile(
        "Pile(1)", PileInput(head_level=0.0, crack_width_limit=0.01), DesignSettings(), pile_sheets(LOADS)
    )
    assert not d.passed and d.utilisation <= 1.0
    assert any("carries the loads" in n for n in d.notes)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_setting_a_cage_while_locked_then_checking_it(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet()})
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)})
    client.post(f"{url}/design")
    project = client.get(f"/api/projects/{p['id']}").json()
    assert project["locked"]
    # The cage is set on the Design tab while the model stays locked; the pile shows as out of date.
    project["sections"][0]["user_cages"] = {"Pile(1)": {"rows": 2, "count": 20, "diameter": 25}}
    assert client.put(f"/api/projects/{p['id']}", json=project).status_code == 200
    assert "Pile(1)" in client.get(f"{url}/design").json()["stale"]
    (pile,) = client.post(f"{url}/design", json={"elements": ["Pile(1)"]}).json()["piles"]
    assert pile["user_set"] and pile["arrangement"]["label"] == "20Ø25 + 20Ø25"
    assert client.get(f"{url}/design").json()["stale"] == []
    # A new section copied from this one (once unlocked) does not take the cage.
    project = client.get(f"/api/projects/{p['id']}").json()
    project["locked"] = False
    client.put(f"/api/projects/{p['id']}", json=project)
    s2 = client.post(
        f"/api/projects/{p['id']}/sections", json={"name": "B", "copy_from": p["sections"][0]["id"]}
    )
    assert s2.status_code in (200, 201) and not s2.json().get("user_cages")
