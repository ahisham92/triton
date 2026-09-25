"""The fender protrusion on the front beam and the STS crane's stand-off check."""

from __future__ import annotations

import io
import zipfile

import pytest
from test_furniture import api, geometry, project  # noqa: F401  (api is a fixture)

from triton import furniture as F
from triton import furniture_report
from triton.design import protrusion as P
from triton.furniture_inputs import Fenders
from triton.protrusion_inputs import FenderProtrusion, StsCrane

# The hand checks below were worked for a 1000 mm fender of 1100 kN (the old defaults).
HAND = Fenders(
    reaction=1100.0,
    height=1.0,
    flange=1400.0,
    anchors=dict(pattern="circle", count=6, circle_diameter=1100, diameter=36, embedment=500),
)


def block(**kw):
    return P.block(FenderProtrusion(**kw), HAND, 4500, 2000, "C40/50", 50)


def test_the_loads_on_the_block_by_hand():
    r = block()
    w = 25 * 1.5 * 2.5 * 3.0
    assert r["loads"]["block_weight_kN"] == pytest.approx(w, abs=0.1)
    down = next(c for c in r["load_cases"] if c["case"] == "Friction down")
    # 1.35 W at a/2, 0.3 × 1.5 × 1100 at a, and the reaction 0.25 m below the joint's centre.
    assert down["V_kN"] == pytest.approx(1.35 * w + 495, abs=0.1)
    assert down["Mv_kNm"] == pytest.approx(-(1.35 * w * 0.75 + 495 * 1.5) - 1650 * 0.25, abs=0.1)
    along = next(c for c in r["load_cases"] if c["case"] == "Friction along the quay")
    assert along["Mh_kNm"] == pytest.approx(495 * 1.5, abs=0.1)
    # The beam's torque: weight at B/2 + a/2, friction at B/2 + a, the reaction 0.25 m below its centre.
    assert r["beam"]["T_Ed_kNm"] == pytest.approx(1.35 * w * 3.0 + 495 * 3.75 + 1650 * 0.25, abs=0.1)
    assert r["passed"] and r["utilisation"] < 1


def test_the_short_cantilever_tie_and_the_bars_carry_it():
    r = block()
    s = r["section"]
    # Ftd = V·ac / z0 for the worst downward case.
    assert s["tie_Ftd_kN"] == pytest.approx((1.35 * 281.25 + 495) * s["ac_m"] / (s["z0_mm"] / 1000), rel=2e-3)
    assert r["bars"]["top_As_mm2"] >= s["tie_As_mm2"]
    assert "U-bars" in r["bars"]["top_ties"]


def test_the_joint_and_how_it_is_cast():
    rough = block()
    assert rough["joint"]["surface"] == "rough" and rough["joint"]["passed"]
    assert rough["joint"]["c_used"] == pytest.approx(0.225)
    smooth = block(joint="smooth")
    assert smooth["joint"]["v_Rdi_MPa"] < rough["joint"]["v_Rdi_MPa"]
    assert block(joint="monolithic")["joint"] is None


def test_a_low_fender_loads_the_downstand():
    none = block()
    assert none["downstand"]["F_kN"] == 0
    low = block(fender_centre_below_cope=1.9)
    assert 0 < low["downstand"]["reaction_share"] < 0.5 and low["downstand"]["M_kNm"] > 0
    assert block(depth=2000)["downstand"] is None


def test_a_heavier_fender_needs_more_ties():
    small = block()
    big = P.block(FenderProtrusion(), Fenders(reaction=6000), 4500, 2000, "C40/50", 50)
    assert big["bars"]["top_As_mm2"] > small["bars"]["top_As_mm2"]


def test_the_crane_stand_off_by_hand():
    c = P.clearance(StsCrane(), HAND, FenderProtrusion(), 2.25)
    assert c["standoff"]["free_m"] == pytest.approx(1.5 + 1.0 + 0.25)
    assert c["standoff"]["compressed_m"] == pytest.approx(1.5 + 0.28 + 0.25)
    assert c["reach"]["needed_m"] == pytest.approx(2.25 + 2.75 + 43.2 - 1.5)
    assert c["legs"]["clearance_m"] == pytest.approx(2.25 - 1.0 + 2.03 - 2.0)
    assert c["passed"]
    lo, hi = c["projection_range_m"]
    assert lo == pytest.approx(3.0 + 1.0 - 2.25 - 0.28 - 0.25) and hi == pytest.approx(
        70 - 2.25 - 43.2 + 1.5 - 1.25
    )
    # Without the block the ship's flare reaches the crane's legs: the reason for the protrusion.
    bare = P.clearance(StsCrane(), HAND, None, 2.25)
    assert not bare["passed"] and bare["parts"][1]["passed"] is False
    # Too far out and the crane cannot reach the far row.
    far = P.clearance(StsCrane(), HAND, FenderProtrusion(projection=30000), 2.25)
    assert not far["parts"][0]["passed"] and any("outside the range" in n for n in far["notes"])


def test_the_furniture_designs_the_blocks_and_checks_the_crane():
    p = project()
    p.furniture.protrusion = FenderProtrusion()
    res = F.design(p, p.sections[0], geometry())
    kinds = [i["item"] for i in res["items"]]
    assert "fender_blocks" in kinds and "sts_clearance" in kinds
    fender = next(i for i in res["items"] if i["item"] == "fenders")
    # The fender is bolted to the 2.5 m deep block face, centred on it.
    assert "on the protrusion" in fender["title"] and fender["edges"]["to_cope_mm"] > 600
    lay = res["layout"]
    assert lay["counts"]["fender_blocks"] == lay["counts"]["fenders"]
    for it in lay["items"]["fenders"]:
        assert it["status"] != "clash"  # the bolts clear the king piles; the block only keeps off the joints
        assert all(abs(it["s_m"] - j) >= 1.5 + 1.0 - 0.01 for j in lay["joints_m"])
    # The front rail from the arrangement (over the 2 m beam's centre).
    sts = next(i for i in res["items"] if i["item"] == "sts_clearance")
    assert sts["standoff"]["rail_from_face_m"] == pytest.approx(1.0)
    assert furniture_report.plan_png(res).startswith(b"\x89PNG")
    # No STS crane, no block: neither item.
    p.furniture.protrusion = None
    p.furniture.sts_crane = None
    kinds = [i["item"] for i in F.design(p, p.sections[0], geometry())["items"]]
    assert "fender_blocks" not in kinds and "sts_clearance" not in kinds


def test_the_calculation_carries_the_block(api):  # noqa: F811
    client, pid, base = api
    page = client.get(f"/api/projects/{pid}").json()
    assert page["furniture"]["protrusion"]["length"] == 3000 and page["furniture"]["sts_crane"]["outreach"] == 70
    page["furniture"]["protrusion"] = FenderProtrusion().model_dump(mode="json")
    assert client.put(f"/api/projects/{pid}", json=page).status_code == 200
    out = client.get(base + "/furniture").json()
    assert any(i["item"] == "fender_blocks" for i in out["items"]) and out["protrusion"]["projection"] == 1500
    r = client.get(base + "/furniture/calc.docx")
    assert r.status_code == 200
    xml = zipfile.ZipFile(io.BytesIO(r.content)).read("word/document.xml").decode()
    assert "Fender protrusion" in xml and "Joint to the beam" in xml and "STS crane" in xml
    assert client.get(base + "/furniture/calc.pdf").status_code == 200
    assert "STS crane" in str(client.get(f"/api/projects/{pid}/method").json())


def test_the_drawings_show_the_block():
    p = project()
    p.furniture.protrusion = FenderProtrusion()
    res = F.design(p, p.sections[0], geometry())
    views = furniture_report.views(res)
    assert views[1]["name"] == "Fender protrusion"
    plan = str(views[0])
    assert "1500" in plan  # the blocks stand 1.5 m out from the face


def test_the_ship_and_panel_come_from_the_design_report():
    c = StsCrane()
    assert c.ship_beam == 43.2 and c.panel_thickness == 0.25 and c.rated_deflection == 0.72
    assert "RPT-ST-01" in StsCrane.model_fields["ship_beam"].description
    # A project saved with the first, assumed defaults takes the report's; a changed value stays.
    old = StsCrane.model_validate({"ship_beam": 61.5, "panel_thickness": 0.3})
    assert old.ship_beam == 43.2 and old.panel_thickness == 0.25
    mine = StsCrane.model_validate({"ship_beam": 61.5, "panel_thickness": 0.4})
    assert mine.ship_beam == 61.5 and mine.panel_thickness == 0.4


def test_a_bollard_on_the_block_pulls_the_joint_open():
    from triton.furniture_inputs import Bollards

    plain = block()
    moored = P.block(FenderProtrusion(), Fenders(), 4500, 2000, "C40/50", 50, bollards=Bollards())
    f = 1.5 * 150 * 9.81
    off = next(c for c in moored["load_cases"] if c["case"] == "Bollard pull off the quay")
    assert off["N_kN"] == pytest.approx(-f, abs=0.1)
    # The pull 0.35 m above the cope, 1.35 m above the 2 m joint's centre, and the block's weight.
    assert off["Mv_kNm"] == pytest.approx(-(1.35 * 25 * 1.5 * 2.5 * 3.0 * 0.75) - f * 1.35, abs=0.5)
    assert moored["bars"]["top_As_mm2"] > plain["bars"]["top_As_mm2"]
    assert moored["passed"] and moored["joint"]["passed"]
    assert moored["loads"]["bollard_pull_kN"] == pytest.approx(f, abs=0.1)
    # Without the bollard on the block, the mooring cases go.
    off_block = P.block(
        FenderProtrusion(bollard_on_block=False), Fenders(), 4500, 2000, "C40/50", 50, bollards=Bollards()
    )
    assert not any("Bollard" in c["case"] for c in off_block["load_cases"])


def test_the_block_follows_drawing_sc502():
    p = FenderProtrusion()
    assert (p.projection, p.depth, p.length, p.bollard_on_block) == (1500, 2500, 3000, True)
