"""Diaphragm wall as the front wall: designed per metre run from the Plaxis plate (EN 1992-1-1)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from triton.costing import cost_section
from triton.design.dwall_design import design_diaphragm_wall
from triton.design.runner import run_section
from triton.design.standard import summary
from triton.elements import ElementType, parse_sheet_name
from triton.fresh import KINDS
from triton.importer import SheetData
from triton.project import (
    DesignSettings,
    DiaphragmWallInput,
    Project,
    Section,
    default_element,
    with_project_grades,
)
from triton.report import RENDERERS, build_report


def _sheets(m=900.0, n=-600.0, v=350.0, top=2.0, toe=-26.0):
    out = {}
    for combo, k in (("PT-B-Apron", 1.0), ("PT-C-Apron", 1.1), ("QP", 0.6)):
        z = np.linspace(top, toe, 57)
        rows = [
            pd.DataFrame(
                {
                    "Node": np.arange(len(z)) + int(x) * 100,
                    "X": x,
                    "Y": 0.0,
                    "Z": z,
                    "N_1": n * k * (1 - z / 30),
                    "N_2": -50.0,
                    "M_11": m * k * np.sin(np.linspace(-0.6, 3.4, len(z))),
                    "M_22": 60 * k,
                    "Q_13": v * k * np.cos(np.linspace(0, 3, len(z))),
                    "Q_23": 5.0,
                    "M_12": 0.0,
                }
            )
            for x in (0.0, 10.0, 20.0)
        ]
        out[combo] = SheetData(f"D-Wall-{combo}", None, pd.concat(rows, ignore_index=True))
    return out


def _design(wall=None, settings=None, **kw):
    settings = settings or DesignSettings()
    wall = with_project_grades(
        wall or DiaphragmWallInput(top_level=2.0), settings.materials, settings.durability
    )
    return design_diaphragm_wall("D-Wall", wall, settings, _sheets(**kw))


@pytest.mark.parametrize("name", ["D-Wall-PT-B-Apron", "DWall-QP", "Diaphragm Wall-PT-C-Yard", "D Wall-QP"])
def test_sheet_names(name):
    parsed = parse_sheet_name(name)
    assert parsed is not None and parsed.spec.type is ElementType.DIAPHRAGM_WALL
    assert parsed.spec.concrete


def test_added_by_name_with_the_pile_cover():
    wall = default_element("D-Wall")
    assert isinstance(wall, DiaphragmWallInput) and wall.cover is None
    s = DesignSettings()
    assert with_project_grades(wall, s.materials, s.durability).cover == s.durability.covers.piles == 75


def test_design_per_metre_both_faces():
    d = _design()
    assert d["ok"] and 0 < d["uf"] <= 1
    assert d["front_sign"] == "positive"  # the largest M_11 is positive
    assert d["top"] == 2.0 and d["toe"] == -26.0 and d["count"] == 8  # 20 m / 2.8 m panels
    zones = d["zones"]
    assert zones[0]["top"] == 2.0 and zones[-1]["bottom"] == -26.0
    for a, b in zip(zones, zones[1:], strict=False):
        assert a["bottom"] == b["top"]
    for z in zones:
        for face in ("front", "back"):
            f = z[face]
            assert f["area_mm2_per_m"] >= f["least_mm2_per_m"]
            assert f["wk_mm"] <= f["wk_limit_mm"] + 1e-9
            assert f["per_cage"] % 2 == 0  # even bar counts, as every cage
        # 9.6.3: horizontal at least 25% of the vertical on each face.
        for face in ("front", "back"):
            assert z["horizontal"][face]["area_mm2_per_m"] >= 0.25 * z[face]["area_mm2_per_m"] - 1
    # The sagging span (front face in tension) takes more steel than the back face there.
    span = max(zones, key=lambda z: z["M_max"])
    assert span["front"]["area_mm2_per_m"] > span["back"]["area_mm2_per_m"]
    assert d["steel"]["kg_per_m"] > 0 and d["steel"]["kg_per_m3"] > 0
    assert d["concrete_m3_per_m"] == pytest.approx(28.0)


def test_front_face_can_be_set():
    d = _design(DiaphragmWallInput(top_level=2.0, front_face="negative"))
    assert d["front_sign"] == "negative"
    span = max(d["zones"], key=lambda z: -z["M_min"])
    assert span["back"]["area_mm2_per_m"] > span["front"]["area_mm2_per_m"]
    assert not any("taken in tension" in n for n in d["notes"])


def test_results_inside_the_capping_beam_are_left_out():
    d = _design(DiaphragmWallInput(top_level=0.0))
    assert d["top"] == 0.0 and d["top_level_set"]
    # 0.1 m into the connection is kept, as the other walls.
    assert d["points"] == len([z for z in np.linspace(2.0, -26.0, 57) if z <= 0.1 + 1e-6]) * 3 * 2


def test_high_shear_needs_links_and_a_thin_wall_fails():
    d = _design(v=900.0)
    lk = [z["shear"]["links"] for z in d["zones"] if z["shear"]["links"]]
    assert lk and lk[0]["found"] and lk[0]["asw_s_mm2_per_mm"] >= lk[0]["need_mm2_per_mm"]
    assert d["steel"]["links_kg_per_m"] > 0
    thin = _design(DiaphragmWallInput(top_level=2.0, thickness=400.0), m=2500.0)
    assert not thin["ok"] and thin["uf"] > 1


def test_runner_costing_standard_and_report(tmp_path):
    section = Section()
    section.elements = {"D-Wall": DiaphragmWallInput(top_level=2.0)}

    class Book:
        sheets = []
        axes = []

        def elements(self):
            return {"D-Wall": _sheets()}

    out = run_section(DesignSettings(), section, Book(), lambda f, step: None)
    assert "diaphragm_walls" in KINDS
    [w] = out["diaphragm_walls"]
    assert w["kind"] == "diaphragm_wall" and w["passed"] and w["utilisation"] == w["design"]["uf"]
    s = summary("diaphragm_walls", w)
    assert s["workable"] and s["kg_per_m3"] == w["steel"]["kg_per_m3"]
    assert {c["check"] for c in s["checks"]} >= {"Shear", "Bending with N (N–M)"}

    p = Project()
    p.sections[0] = section.model_copy(update={"id": p.sections[0].id})
    p.prices.concrete_diaphragm_wall, p.prices.rebar = 500.0, 1000.0
    p.sections[0].costing.berth_length = 100.0
    row = next(r for r in cost_section(p, p.sections[0], out)["rows"] if r["element"] == "D-Wall")
    assert row["kind"] == "diaphragm_wall"
    assert row["concrete_m3"] == pytest.approx(100 * 28.0, rel=1e-3)
    assert row["cost"] > 0

    rep = build_report(p, p.sections[0], out, "detailed")
    text = " ".join(getattr(b, "text", "") or "" for b in rep.blocks)
    assert "diaphragm wall" in text.lower()
    for fmt, (render, _) in RENDERERS.items():
        assert render(rep), fmt


# --- Drawings, AdSec, clashes and displacements ------------------------------------------------------


def _beam(extra_y=None):
    """A 2000 x 1600 front beam along X over the wall line (Y = 0), plate at +2.0 (mid-depth)."""
    zb = -800 + 50 + 12 + 12.5
    ys = [-925 + i * 1850 / 9 for i in range(10)]
    bars = [{"y_mm": y, "z_mm": zb, "diameter_mm": 25} for y in ys + ([extra_y] if extra_y else [])]
    bars += [{"y_mm": y, "z_mm": -zb, "diameter_mm": 25} for y in ys]
    return {
        "element": "Front Beam",
        "kind": "front_beam",
        "along": "X",
        "start_m": -5.0,
        "end_m": 25.0,
        "centre_m": 0.0,
        "level_m": 2.0,
        "width_mm": 2000.0,
        "depth_mm": 1600.0,
        "cover_mm": 50.0,
        "bars": bars,
        "links": {"diameter_mm": 12, "legs": 4, "spacing_mm": 150},
        "transverse": {},
    }


def test_drawings_and_revit_views():
    from triton.design.export import pile_cages
    from triton.drawings import from_cages
    from triton.dxf import to_dxf
    from triton.project import DrawingSettings

    d = _design()
    assert d["line"] == [[0.0, 0.0], [20.0, 0.0]]
    cages = pile_cages("P", {"diaphragm_walls": [{"element": "D-Wall", "design": d}]}, "S")
    (w,) = cages["diaphragm_walls"]
    assert w["thickness_mm"] == 1000 and w["panel_width_mm"] == 2800 and len(w["zones"]) == len(d["zones"])
    out = from_cages(cages, DrawingSettings())
    names = [v["name"] for v in out["views"]]
    assert names == ["D-Wall - section"] + [f"D-Wall - zone {i + 1} plan" for i in range(len(d["zones"]))]
    # Each plan section shows every vertical bar of the cage on both faces.
    for z, v in zip(d["zones"], out["views"][1:], strict=True):
        bars = [it for it in v["items"] if it["type"] == "bar"]
        assert len(bars) == z["front"]["per_cage"] + z["back"]["per_cage"]
        assert any(
            it["type"] == "line" and it["layer"] == f"bar-{z['horizontal']['front']['phi']}"
            for it in v["items"]
        )
    assert "bar-16" in out["layers"]
    assert "D-WALL - WALL SECTION" in to_dxf(out)


def test_adsec_files_and_governing_sets_per_zone():
    import io

    import openpyxl

    from triton import adsec
    from triton.design.governing import workbook

    d = _design()
    res = {"diaphragm_walls": [{"element": "D-Wall", "design": d}]}
    files = adsec.section_files("J", "S", res, {}, "B500B")
    assert len(files) == len(d["zones"])
    z = d["zones"][0]
    data = files["D-Wall 1000mm - Zone 1.ads"]
    width = adsec.strip_width(z["front" if z["M_max"] >= -z["M_min"] else "back"]["spacing"])
    forces = adsec.read_forces(data)
    rows = z["sets"]["qp"] + z["sets"]["uls"]
    assert len(forces) == len(rows)
    for (n, my, mz), r in zip(forces, rows, strict=True):
        assert n == pytest.approx(r["N_kN_per_m"] * width / 1000, abs=0.01)
        assert my == pytest.approx(r["M_kNm_per_m"] * width / 1000, abs=0.01) and mz == 0
    # The sets are the zone's extremes.
    assert max(r["M_kNm_per_m"] for r in z["sets"]["uls"]) == z["M_max"]
    assert min(r["M_kNm_per_m"] for r in z["sets"]["uls"]) == z["M_min"]
    wb = openpyxl.load_workbook(io.BytesIO(workbook("P", "S", res)))
    assert "Diaphragm walls" in wb.sheetnames


def test_clashes_with_the_beam_over_the_wall():
    from triton.clashes import Ctx
    from triton.design.export import pile_cages
    from triton.dwall_clashes import wall_clashes

    d = _design()
    res = {"diaphragm_walls": [{"element": "D-Wall", "design": d}]}
    drawing = pile_cages("P", res, "S")
    drawing["diaphragm_walls"][0]["top_level_m"] = 1.2  # the beam's soffit
    p = Project()
    ctx = Ctx(p, p.sections[0], p.design, res, p.sections[0].clashes)
    # A bottom bar of the beam right on the back face's bar line (405 mm from the wall's centre line).
    drawing["beams"] = [_beam(extra_y=405.0)]
    (w,) = wall_clashes(ctx, drawing, res)
    assert w["host"] == "Front Beam" and w["count"]["clash"] > 0
    assert any("bottom bars" in g["beam_bars"] for g in w["groups"])
    shift = next(x for x in w["ways"] if x["id"] == "shift")
    assert shift["passes"] and shift["moves"][0]["at_m"] == 0.405
    # The links cross the wall at a pitch that is not the wall bars': laid between them instead.
    between = next(x for x in w["ways"] if x["id"] == "between")
    links = next(s for s in between["sets"] if "links" in s["bars"])
    assert links["passes"] and links["per_panel"] >= links["designed_per_panel"]
    # Bars into the beam: from the soffit (1.2) to under the top bars, more than 45Ø of a Ø16.
    a = w["anchorage"]
    assert a["wall_top_m"] == 1.2 and a["into_beam_m"] > a["needed_m"] and a["short_m"] == 0
    # With no beam over it there is nothing to check.
    drawing["beams"] = []
    (w,) = wall_clashes(ctx, drawing, res)
    assert w["host"] is None and "no beam" in w["text"]


def test_displacement_estimate():
    from triton.design.deflection import estimate, estimate_shapes

    class Book:
        sheets = []
        axes = []

        def elements(self):
            return {"D-Wall": _sheets(m=2000.0)}

    section = Section()
    section.elements = {"D-Wall": DiaphragmWallInput(top_level=2.0)}
    s = DesignSettings()
    out = run_section(s, section, Book(), lambda f, step: None)
    gross = estimate(s, section, Book(), out)["elements"][0]
    assert gross["kind"] == "diaphragm_wall" and gross["max_mm"] > 0
    assert gross["head_level"] == 2.0 and gross["toe_level"] == -26.0
    cracked = section.model_copy(
        update={"deflection": section.deflection.model_copy(update={"stiffness": "cracked"})}
    )
    more = estimate(s, cracked, Book(), out)["elements"][0]
    assert more["max_mm"] > gross["max_mm"] and "designed bars" in more["stiffness"]
    shapes = estimate_shapes(s, section, Book(), "", out)
    assert shapes["members"] and {m["kind"] for m in shapes["members"]} == {"diaphragm_wall"}


def test_a_combi_wall_project_has_no_diaphragm_wall_anywhere():
    """The diaphragm wall is an option: without one defined, the combi wall's files are as before."""
    from test_combi import combi_sheets

    from triton import adsec
    from triton.clashes import Ctx
    from triton.design.export import pile_cages
    from triton.drawings import from_cages
    from triton.dwall_clashes import wall_clashes
    from triton.project import DrawingSettings

    section = Section()
    section.add_elements(["Combi Wall"])
    assert not any(isinstance(e, DiaphragmWallInput) for e in section.elements.values())
    out = run_section(DesignSettings(), section, combi_sheets())
    assert out["diaphragm_walls"] == []
    cages = pile_cages("P", out, "S")
    assert "diaphragm_walls" not in cages
    assert set(cages) == {"format", "project", "section", "run_at", "piles", "beams", "slabs", "approach"}
    assert all(v["element"] == "Combi Wall" for v in from_cages(cages, DrawingSettings())["views"])
    assert all(
        "infill" in n
        for n in adsec.section_files(
            "J",
            "S",
            out,
            {"Combi Wall infill": {"diameter": 1590, "concrete": "C40/50", "cover": 75}},
            "B500B",
        )
    )
    p = Project()
    assert wall_clashes(Ctx(p, section, p.design, out, section.clashes), cages, out) == []


def test_the_export_routes(tmp_path, monkeypatch):
    import io
    import zipfile

    from fastapi.testclient import TestClient

    from triton.api import app, store

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))

    class Book:
        sheets = []
        axes = []

        def elements(self):
            return {"D-Wall": _sheets()}

    section = Section(name="Section 01", elements={"D-Wall": DiaphragmWallInput(top_level=2.0)})
    p = Project(sections=[section])
    results = run_section(DesignSettings(), section, Book(), lambda f, step: None)
    results["run_at"] = "2026-09-27T15:00"
    store().save(p)
    store().save_results(p.id, section.id, results)
    client, base = TestClient(app), f"/api/projects/{p.id}/sections/{section.id}"
    crm = client.get(base + "/design/drawings.crm").json()
    assert crm["views"][0]["name"] == "D-Wall - section"
    assert client.get(base + "/design/drawings.dxf").status_code == 200
    names = zipfile.ZipFile(io.BytesIO(client.get(base + "/design/adsec.zip").content)).namelist()
    assert names and all(n.startswith("D-Wall 1000mm - Zone") for n in names)
    assert client.get(base + "/design/governing.xlsx").status_code == 200
    cl = client.get(base + "/clashes").json()
    assert cl["walls"][0]["element"] == "D-Wall" and cl["walls"][0]["host"] is None
