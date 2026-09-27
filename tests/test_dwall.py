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
