"""Quay furniture: EN 1992-4 anchor groups, each item's design, the arrangement along the berth, the
routes and the numbers Costing takes."""

import io
import json
import math
import zipfile

import pytest
from fastapi.testclient import TestClient

from triton import furniture as F
from triton.api import app, store
from triton.design import anchors as A
from triton.design import furniture as D
from triton.furniture_inputs import Anchors, Bollards, CraneRails, Fenders, TieRods
from triton.project import Project


def geometry():
    """Section 01a's layout: front beam X -1..1, king piles every 3.2 m, pile rows behind, rear beam."""
    g = [
        {
            "element": "Front Beam",
            "type": "front_beam",
            "kind": "plate",
            "box": {"X": [-1, 1], "Y": [-16.8, 16.8], "Z": [2.7, 2.7]},
        },
        {
            "element": "Rear Beam",
            "type": "rear_beam",
            "kind": "plate",
            "box": {"X": [-24.8, -23.2], "Y": [-16.8, 16.8], "Z": [2.7, 2.7]},
        },
        {
            "element": "Deck",
            "type": "slab",
            "kind": "plate",
            "box": {"X": [-23.2, -1], "Y": [-16.8, 16.8], "Z": [2.7, 2.7]},
        },
        {
            "element": "Combi Wall",
            "type": "combi_wall",
            "kind": "beam",
            "lines": [[0.0, -16 + 3.2 * i, 2.7, -39] for i in range(11)],
        },
    ]
    for n, x in (("Pile(1)", -7.5), ("Pile(4)", -24.0)):
        g.append(
            {
                "element": n,
                "type": "pile",
                "kind": "beam",
                "lines": [[x, -16 + 6.4 * i, 1.7, -30] for i in range(6)],
            }
        )
    return g


def project() -> Project:
    p = Project()
    s = p.sections[0]
    s.add_elements(["Front Beam", "Rear Beam", "Deck", "Combi Wall", "Pile(1)", "Pile(4)"])
    s.costing.berth_length = 300.0
    return p


def group(**kw) -> A.Group:
    base = dict(
        bolts=A.grid(1, 1, 0, 0),
        diameter=30.0,
        grade="8.8",
        hef=300.0,
        head=90.0,
        c_pos=5000.0,
        c_neg=5000.0,
        thickness=2000.0,
        concrete_grade="C40/50",
    )
    return A.Group(**{**base, **kw})


def check(res, name):
    return next(c for c in res["checks"] if c["check"].startswith(name))


def test_a_single_bolt_far_from_edges_by_hand():
    res = A.check_group(group(), 100.0, 0.0, 0.0, 0.0, 0.0)
    # N0Rk,c = 8.9 √40 · 300^1.5 = 292.5 kN, no edge or group: NRd,c = 195.0 kN.
    assert check(res, "Concrete cone")["Rd_kN"] == pytest.approx(
        8.9 * math.sqrt(40) * 300**1.5 / 1e3 / 1.5, abs=0.2
    )
    # Steel: As 561 × 800 / 1.5 (γMs = 1.2 × 800/640 = 1.5).
    assert check(res, "Steel failure in tension")["Rd_kN"] == pytest.approx(561 * 800 / 1.5 / 1e3, abs=0.1)
    # Pull-out: 7.5 × π/4 (90² − 30²) × 40 / 1.5.
    assert check(res, "Pull-out")["Rd_kN"] == pytest.approx(
        7.5 * math.pi / 4 * (90**2 - 30**2) * 40 / 1.5 / 1e3, abs=0.2
    )
    assert res["passed"] and "tension" not in res["reinforcement"]


def test_an_edge_cuts_the_cone_and_shear_to_the_edge_is_checked():
    far = A.check_group(group(), 100.0, 0.0, 0.0, 0.0, 0.0)
    near = A.check_group(group(c_pos=150.0), 100.0, 0.0, 0.0, 0.0, 0.0)
    # Ac,N = 900 × (450 + 150) against 900²; ψs,N = 0.7 + 0.3 × 150/450.
    ratio = (600 / 900) * (0.7 + 0.3 * 150 / 450)
    assert check(near, "Concrete cone")["Rd_kN"] == pytest.approx(
        check(far, "Concrete cone")["Rd_kN"] * ratio, rel=1e-3
    )
    assert (
        not near["blow_out"] and A.check_group(group(c_pos=100.0), 100.0, 0, 0, 0, 0)["blow_out"]
    )  # c < 0.5 hef
    towards = A.check_group(group(c_pos=150.0), 0.0, 0.0, 20.0, 0.0, 0.0)
    along = A.check_group(group(c_pos=150.0), 0.0, 20.0, 0.0, 0.0, 0.0)
    e_to = check(towards, "Concrete edge (+v")
    e_along = check(along, "Concrete edge (+v")
    assert e_along["Rd_kN"] == pytest.approx(2 * e_to["Rd_kN"], rel=1e-3)  # ψα,V = 2 parallel to the edge
    assert not any(
        c["check"].startswith("Concrete edge (-v") for c in towards["checks"]
    )  # shear away from it


def test_a_moment_puts_one_side_in_tension():
    g = group(bolts=A.grid(2, 1, 0, 400.0))
    res = A.check_group(g, 0.0, 0.0, 0.0, 40.0, 0.0)
    assert res["bolt_tension_kN"] == [0.0, 100.0]  # 40 kNm over ± 200 mm: 100 kN at +v, none at −v


def test_anchor_reinforcement_takes_what_the_cone_cannot():
    res = A.check_group(
        group(bolts=A.circle(8, 800), diameter=48.0, hef=1000.0, head=160.0), 2500.0, 0, 0, 0, 0
    )
    assert check(res, "Concrete cone")["utilisation"] > 1
    t = res["reinforcement"]["tension"]
    assert t["utilisation"] <= 1 and t["NRd_re_kN"] >= 2500 and t["legs"] >= 16
    assert res["passed"]


def test_the_items_with_their_filled_in_values_pass():
    beam = D.Beam(2000, 1600, "C40/50")
    for r in (
        D.fender(Fenders(), beam),
        D.bollard(Bollards(), beam),
        D.rail(CraneRails(), beam, 1.0),
        D.tie_rod(TieRods(), beam),
    ):
        assert r["passed"], (r["title"], r["parts"])


def test_the_bollard_is_checked_at_every_line_angle():
    r = D.bollard(Bollards(max_vertical_angle=45), D.Beam(2000, 1600, "C40/50"))
    assert r["angles_checked"] == 4 * 13  # 0, 15, 30, 45° up × 13 plan angles
    # A bigger pull needs more: utilisation grows with the capacity.
    assert D.bollard(Bollards(capacity=200), D.Beam(2000, 1600, "C40/50"))["utilisation"] > r["utilisation"]


def test_the_fender_bolts_do_not_fit_a_shallow_beam():
    r = D.fender(
        Fenders(anchors=Anchors(pattern="circle", count=6, circle_diameter=1400, diameter=36)),
        D.Beam(2000, 1200, "C40/50"),
    )
    assert r["notes"] and "do not fit" in r["notes"][0]


def test_tie_rod_by_hand():
    t = TieRods(force=250, spacing=3.2, shaft=85, thread=100, corrosion=1.75, grade="ASDO 500")
    r = D.tie_rod(t, D.Beam(2000, 1600, "C40/50"))
    ag = math.pi / 4 * (85 - 3.5) ** 2
    assert r["Ftg_Rd_kN"] == pytest.approx(500 * ag / 1e3, abs=0.2)
    ds = math.sqrt(4 * 7000 / math.pi)
    assert r["Ftt_Rd_kN"] == pytest.approx(
        0.6 * 0.9 * 660 * math.pi / 4 * (ds - 3.5) ** 2 / 1.25 / 1e3, abs=0.2
    )
    assert r["loads"]["F_Ed_kN"] == 800.0


def test_the_rail_single_wheel_moment_matches_the_closed_form():
    r = CraneRails(wheels=1, load_factor=1.0, wheel_load=500)
    out = D.rail(r, D.Beam(2000, 1600, "C40/50"), 1.0)
    inertia = D.RAILS["A150"]["I"] * 1e4
    beta = (r.pad_stiffness / (4 * 210000 * inertia)) ** 0.25
    assert out["M_max_kNm"] == pytest.approx(500e3 / (4 * beta) / 1e6, abs=0.1)


# A 1.0 m cone fender: small enough to sit between the king piles on the bare 1.6 m deep face.
SMALL = dict(
    name="1.0 m cone",
    reaction=1100.0,
    height=1.0,
    flange=1400.0,
    spacing=20.0,
    anchors=dict(pattern="circle", count=6, circle_diameter=1100, diameter=36, embedment=500),
)


def test_the_berth_frame_and_arrangement():
    p = project()
    p.furniture.fenders = Fenders(**SMALL)
    res = F.design(p, p.sections[0], geometry())
    fr = res["frame"]
    assert fr["along"] == "Y" and fr["inland"] == -1 and fr["face"] == 1 and fr["cope_m"] == 3.5
    assert res["berth_length_m"] == 300 and fr["rear_beam"]["centre_m"] == 25.0
    lay = res["layout"]
    assert lay["joints_m"] == [58.0, 116.0, 174.0, 232.0, 290.0]
    # Fenders at no more than 20 m: 290 m between the end ones -> 16 fenders.
    assert lay["counts"]["fenders"] == 16 and lay["counts"]["crane_stoppers"] == 4
    heads = F.pile_heads(F.berth_frame(p, p.sections[0], geometry()), 300.0)
    kings = [h for h in heads if h["element"] == "Combi Wall"]
    assert len(kings) == 94 and kings[1]["s"] - kings[0]["s"] == pytest.approx(3.2)
    for it in lay["items"]["fenders"]:
        assert it["status"] != "clash"
        assert all(abs(it["s_m"] - j) >= 1.0 for j in lay["joints_m"])
        # Its flange (to 0.55 m in from the face) clear every king pile head (radius 0.813 m + 0.1 m).
        half, depth = it["to_m"] - it["s_m"], it["across_m"][1]
        assert all(
            math.hypot(max(abs(it["s_m"] - k["s"]) - half, 0), k["d"] - depth) >= 0.913 - 0.01
            for k in kings  # positions are rounded to 10 mm
        )
    # With the rail over the 2 m front beam's centre there is no room for the bollards.
    assert all("crane rail" in " ".join(b["clashes"]) for b in lay["items"]["bollards"])
    p.furniture.crane_rails.front_rail_from_face = 2.0
    lay = F.design(p, p.sections[0], geometry())["layout"]
    assert not any("crane rail" in " ".join(b["clashes"]) for b in lay["items"]["bollards"])
    # Ladders sit between fenders.
    fs = [f["s_m"] for f in lay["items"]["fenders"]]
    for lad in lay["items"]["ladders"]:
        assert any(a < lad["s_m"] < b for a, b in zip(fs, fs[1:], strict=False))


def test_section_berth_details():
    p = project()
    s = p.sections[0]
    s.furniture.stow_positions = [50.0]
    s.furniture.no_tie_rods = True
    layout = {"segments": [1], "berth_length_m": 300.0, "joints": [{"chainage": 100.0}, {"chainage": 200.0}]}
    res = F.design(p, s, geometry(), layout)
    assert res["layout"]["joints_m"] == [100.0, 200.0]
    assert [x["tag"] for x in res["layout"]["items"]["storm_pins"]] == ["front rail", "rear rail"]
    assert "tie_rods" not in res["layout"]["counts"]
    s.furniture.use = False
    assert F.design(p, s, geometry()) == {"section": s.name, "section_id": s.id, "use": False}
    assert F.counts_for_costing({"use": False}) == {}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    p = project()
    p.locked = True
    store().save(p)
    s = p.sections[0]
    d = store()._dir(p.id, s.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "furniture_geometry.json").write_text(json.dumps({"key": [None, None], "geometry": geometry()}))
    return TestClient(app), p.id, f"/api/projects/{p.id}/sections/{s.id}"


def test_the_furniture_routes(api):
    client, pid, base = api
    out = client.get(base + "/furniture").json()
    assert out["layout"]["counts"]["fenders"] == 18 and out["items"]  # SCN 1600 every 18 m
    for fmt in ("docx", "pdf", "xlsx"):
        r = client.get(base + f"/furniture/calc.{fmt}")
        assert r.status_code == 200, r.text
        if fmt == "docx":
            assert any(n.startswith("word/media/") for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    dxf = client.get(base + "/furniture/plan.dxf")
    assert dxf.status_code == 200 and "TRITON-FURNITURE" in dxf.text
    crm = client.get(base + "/furniture/plan.crm").json()
    assert crm["views"][0]["name"] == "Furniture plan" and len(crm["views"]) > 1
    assert client.get(base + "/furniture/plan.png").content.startswith(b"\x89PNG")
    # The furniture is open while the model is locked, and Costing takes its numbers.
    page = client.get(f"/api/projects/{pid}").json()
    page["furniture"]["fenders"]["spacing"] = 25.0
    page["sections"][0]["furniture"]["berth_length"] = 250.0
    assert client.put(f"/api/projects/{pid}", json=page).status_code == 200
    again = client.get(base + "/furniture").json()
    assert again["berth_length_m"] == 250.0 and again["layout"]["counts"]["fenders"] < 16
    method = client.get(f"/api/projects/{pid}/method").json()
    assert "EN 1992-4" in json.dumps(method)


def test_no_workbook_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    p = Project()
    store().save(p)
    r = TestClient(app).get(f"/api/projects/{p.id}/sections/{p.sections[0].id}/furniture")
    assert r.status_code == 409 and "workbook" in r.json()["detail"]


def test_the_joints_keep_clear_of_the_furniture_and_the_furniture_of_the_joints():
    from triton.joints import section_joints

    p = project()
    s = p.sections[0]
    at = F.positions_for(p, s)
    spots = at(300.0)
    assert {x["name"] for x in spots} >= {"Fender", "Bollard", "Ladder", "Crane stopper", "Storm pin"}
    layout = section_joints(p.design, s, {}, None, "Y", at)
    assert "Furniture tab" in layout["furniture_from"]
    res = F.design(p, s, geometry(), layout)
    assert res["layout"]["joints_m"] == [j["chainage"] for j in layout["joints"]]
    assert "expansion joint layout" in res["layout"]["joints_from"]
    for j in layout["joints"]:
        assert all(
            abs(j["chainage"] - x["chainage"]) >= p.design.joints.furniture_clearance - 1e-6 for x in spots
        )
    # The joints keep 1.5 m from the items' centres; a fender's flange is wider, so the odd one is still
    # listed where king piles stop it moving.
    near = [
        it
        for v in res["layout"]["items"].values()
        for it in v
        if any("expansion joint" in c for c in it["clashes"])
    ]
    assert len(near) <= 1


def test_bollard_extra_beam_bars_and_the_thickening_step():
    from triton.design.bollard import check_bollard
    from triton.furniture_report import bollard_views
    from triton.project import Bollard, DesignSettings

    b = Bollard()
    r = check_bollard(b, "C40/50", DesignSettings(), (2000.0, 1600.0, 50.0, 16.0), 3.2)
    f = 0.75 * 150 * 9.81
    bb = r["beam_bars"]
    assert bb["T_kNm"] == pytest.approx(f * (0.35 + 0.8), abs=0.1)
    # Pull along the quay: F / fyd.
    assert bb["rows"][2]["need"] == f"{f * 1e3 / (500 / 1.15):.0f} mm²"
    th = r["thickening"]
    assert th["N_kN"] == pytest.approx(f, abs=0.1) and th["passed"]
    assert th["bottom"]["ties_mm2"] == round(math.pi * 32**2 / 4 * (2 + 6 * math.cos(math.radians(45))))
    no = check_bollard(Bollard(thickening=None), "C40/50", DesignSettings())
    assert no["thickening"] is None and no["beam_bars"] is None
    views = bollard_views(
        {"beams": [{"element": "Front Beam", "width_mm": 2000, "depth_mm": 1600, "bollard": r}]}
    )
    assert views and any(it["type"] == "bar" for it in views[0]["items"])


def test_the_report_fender_and_the_tie_downs():
    p = project()
    f = p.furniture
    # The design report's fender and steel appendix loads (10 kN/t, as the report).
    assert (f.fenders.reaction, f.fenders.energy, f.fenders.height, f.fenders.spacing) == (
        2012,
        1867,
        1.6,
        18,
    )
    assert (f.crane_stoppers.force, f.storm_pins.force, f.tie_downs.force) == (1500, 1800, 1650)
    res = F.design(p, p.sections[0], geometry())
    lay = res["layout"]
    # On the bare 1.6 m deep face its 1365 mm bolt circle cannot miss the king piles at 3.2 m.
    assert all(it["status"] == "clash" for it in lay["items"]["fenders"])
    # A set at each leg of each stowed crane, on both rails.
    assert lay["counts"]["tie_downs"] == 4 * f.storm_pins.cranes
    td = next(i for i in res["items"] if i["item"] == "tie_downs")
    # One plate of a set: 1.5 x 1650 / 2 = 1237.5 kN (the report's 1240 kN), 225 mm off centre.
    assert td["loads"]["N_Ed_kN"] == pytest.approx(1237.5)
    # Rigid plate: 1237.5 / 6 + 1237.5 x 0.225 x 425 / (4 x 425^2) on the end bolts.
    assert td["anchors"]["N_max_kN"] == pytest.approx(1237.5 / 6 + 1237.5 * 0.225 / (4 * 0.425), rel=1e-3)
    assert td["passed"]
    assert F.counts_for_costing(res)["Crane tie-downs"] == lay["counts"]["tie_downs"]
    # On a protrusion block the fender bolts stay in the block, clear of the pile heads.
    from triton.protrusion_inputs import FenderProtrusion

    f.protrusion = FenderProtrusion()
    lay = F.design(p, p.sections[0], geometry())["layout"]
    assert not any("Combi Wall" in " ".join(it["clashes"]) for it in lay["items"]["fenders"])
