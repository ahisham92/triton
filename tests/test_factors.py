import pytest
from pydantic import ValidationError
from test_design import pile_rows

from triton.design.runner import factored_elements, run_piles
from triton.forces import scale_forces
from triton.project import DesignSettings, LoadFactor, Section
from triton.validation import import_sheets

LOADS = [(1, 0.0, -2000.0, 1500.0, 0.0), (2, -5.0, -2500.0, 800.0, 0.0)]


def workbook():
    return import_sheets({"Pile(1)-PT-B-Apron": pile_rows(LOADS), "Pile(1)-QP": pile_rows(LOADS)})


def test_scale_forces_keeps_locations():
    frame = workbook().elements()["Pile(1)"]["PT-B-Apron"].frame
    out = scale_forces(frame, 1.35)
    for col in ("Node", "X", "Y", "Z"):
        assert out[col].tolist() == frame[col].tolist()
    for col in ("N", "N_min", "M_2", "M_2_max", "T_skin"):
        assert out[col].tolist() == pytest.approx((frame[col] * 1.35).tolist())


def test_factors_apply_to_chosen_sheets_only():
    section = Section(load_factors=[LoadFactor(factor=1.35, sheets=["Pile(1)-PT-B-Apron"])])
    sheets = factored_elements(section, workbook())["Pile(1)"]
    raw = workbook().elements()["Pile(1)"]
    assert sheets["PT-B-Apron"].frame["M_2"].tolist() == pytest.approx(
        (raw["PT-B-Apron"].frame["M_2"] * 1.35).tolist()
    )
    assert sheets["QP"].frame["M_2"].tolist() == raw["QP"].frame["M_2"].tolist()


def test_design_reports_the_multiplier():
    section = Section(load_factors=[LoadFactor(factor=1.35, sheets=["Pile(1)-PT-B-Apron", "Nope-QP"])])
    section.add_elements(["Pile(1)"])
    section.elements["Pile(1)"].head_level = 1.0
    out = run_piles(DesignSettings(), section, workbook())
    (pile,) = out["piles"]
    assert pile["governing"]["M_kNm"] == pytest.approx(1500 * 1.35)
    assert "PT-B-Apron ×1.35" in pile["notes"][0]
    assert out["skipped"] == ["Load multiplier sheets not in the workbook: Nope-QP."]


def test_a_sheet_is_multiplied_once():
    with pytest.raises(ValidationError, match="more than one"):
        Section(load_factors=[LoadFactor(sheets=["A-QP"]), LoadFactor(factor=1.5, sheets=["A-QP"])])


def test_slab_soffit_is_the_default_head_level():
    section = Section(slab_soffit_level=-1.0)
    section.add_elements(["Pile(1)"])
    (pile,) = run_piles(DesignSettings(), section, workbook())["piles"]
    assert pile["section"]["head_level_m"] == -1.0
    assert any("slab soffit" in n for n in pile["notes"])
    assert pile["count"] == 1
