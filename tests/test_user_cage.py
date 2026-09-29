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
    assert any("clear spacing" in n and "below" in n for n in tight.failure)
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


def test_inner_link_rings_count_in_the_steel_not_the_shear_check():
    one = check(UserCage(rows=1, count=26, diameter=32))
    two = check(UserCage(rows=2, count=26, diameter=32))
    three = check(UserCage(rows=3, count=22, diameter=32))
    assert one.shear["inner_rings"] == 0 and one.shear["inner_links_kg"] == 0
    assert two.shear["inner_rings"] == 1 and three.shear["inner_rings"] == 2
    assert 0 < two.shear["inner_links_kg"] < three.shear["inner_links_kg"]
    assert (
        two.steel["links_kg"] == pytest.approx(two.shear["links_kg"])
        and two.shear["links_kg"] > two.shear["inner_links_kg"]
    )
    assert check(UserCage(rows=1.5, count=26, diameter=32)).shear["inner_rings"] == 1
    assert check(UserCage(rows=2.5, count=26, diameter=32, inner_diameter=25)).shear["inner_rings"] == 2


def test_every_row_can_have_its_own_bars():
    cage = UserCage(
        row_bars=[{"count": 26, "diameter": 32}, {"count": 26, "diameter": 25}, {"count": 13, "diameter": 20}]
    )
    assert cage.rows == 2.5 and cage.count == 26 and cage.row_list() == [(26, 32), (26, 25), (13, 20)]
    d = check(cage)
    assert d.arrangement.label == "26Ø32 + 26Ø25 + 13Ø20"
    radii = [r.radius for r in d.arrangement.rings]
    assert radii == sorted(radii, reverse=True)
    # The older form still reads as before.
    assert UserCage(rows=2.5, count=26, diameter=32, inner_diameter=25).row_list() == [
        (26, 32),
        (26, 25),
        (13, 25),
    ]


def test_zones_below_a_failing_user_cage_are_still_designed():
    # Over the 4% limit: it fails on that alone, says so, and offers couplers; the pile is still curtailed.
    heavy = UserCage(row_bars=[{"count": 26, "diameter": 32}] * 2 + [{"count": 13, "diameter": 32}])
    d = check(heavy, diameter=1100)
    assert not d.passed and any("over the 4% limit" in f for f in d.failure)
    assert d.with_couplers["passes"] and d.with_couplers["allow_pct"] > 4
    runs = d.curtailment["runs"]
    assert runs[0]["cage"]["label"] == "26Ø32 + 26Ø32 + 13Ø32" and len(runs) >= 2
    assert all(r["cage"]["rings"][0]["count"] == 26 for r in runs)
    assert all(r["utilisation"] <= 1.0 for r in runs[1:])
    assert runs[-1]["cage"]["area_mm2"] < runs[0]["cage"]["area_mm2"]
    # Proceeding with couplers: the steel rule no longer fails it, and its joint is a coupler.
    ok = check(heavy.model_copy(update={"over_limit_with_couplers": True}), diameter=1100)
    assert not any("% limit" in f for f in ok.failure)
    assert (
        ok.curtailment["runs"][0]["joint"] == "coupler" and ok.curtailment["runs"][0]["lap_below_m"][0] == 0
    )
    assert any("couplers" in n for n in ok.notes)


def test_a_failure_from_the_steel_limit_says_what_passes_with_couplers():
    settings = DesignSettings()
    settings.piles.max_steel_ratio = 1.0
    d = design_pile("Pile(1)", PileInput(head_level=0.0), settings, pile_sheets(LOADS))
    assert not d.passed and d.failure and d.failure[0].startswith("No cage within the 1% steel limit")
    c = d.with_couplers
    assert c["passes"] and c["ratio_pct"] > 1 and c["allow_pct"] >= c["ratio_pct"]
    assert c["couplers"] == (c["ratio_pct"] > 4) and "steel limit" in c["note"]
    # Below the strongest cage, the rest of the pile is still curtailed.
    assert d.curtailment and len(d.curtailment["runs"]) >= 2
