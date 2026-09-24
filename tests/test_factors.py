import pytest
from pydantic import ValidationError
from test_design import pile_rows

from triton.design.runner import factored_elements, run_section
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
    out = run_section(DesignSettings(), section, workbook())
    (pile,) = out["piles"]
    assert pile["governing"]["M_kNm"] == pytest.approx(1500 * 1.35)
    assert "PT-B-Apron ×1.35" in pile["notes"][0]
    assert out["skipped"] == ["Load multiplier sheets not in the workbook: Nope-QP."]


def test_a_sheet_is_multiplied_once():
    with pytest.raises(ValidationError, match="more than one"):
        Section(load_factors=[LoadFactor(sheets=["A-QP"]), LoadFactor(factor=1.5, sheets=["A-QP"])])


def test_old_section_soffit_becomes_the_pile_top_level():
    section = Section.model_validate({"slab_soffit_level": -1.0, "elements": {"Pile(1)": {"kind": "pile"}}})
    assert section.elements["Pile(1)"].head_level == -1.0
    assert "slab_soffit_level" not in section.model_dump()


def test_results_10_cm_into_the_slab_are_used():
    loads = [(1, 1.85, -2000.0, 9000.0, 0.0), (2, 1.78, -2000.0, 2000.0, 0.0), (3, 1.0, -2000.0, 500.0, 0.0)]
    sheets = import_sheets({"Pile(1)-PT-B-Apron": pile_rows(loads), "Pile(1)-QP": pile_rows(loads)})
    section = Section()
    section.add_elements(["Pile(1)"])
    section.elements["Pile(1)"].head_level = 1.7
    (pile,) = run_section(DesignSettings(), section, sheets)["piles"]
    # 1.85 m is 15 cm into the slab (ignored); 1.78 m is kept and taken at the top level.
    assert pile["governing"]["M_kNm"] == pytest.approx(2000.0)
    assert pile["governing"]["z"] == 1.7
    assert any("10 cm into the slab" in n for n in pile["notes"])
    settings = DesignSettings(results_into_connection=0)
    (pile,) = run_section(settings, section, sheets)["piles"]
    assert pile["governing"]["M_kNm"] == pytest.approx(500.0)


def test_sheet_pile_wall_results_above_its_top_level_are_ignored():
    from conftest import plate_sheet

    from triton.project import SheetPileInput

    wb = import_sheets({"SPW-QP": plate_sheet(), "SPW-PT-B-Apron": plate_sheet(scale=2.0)})
    section = Section(elements={"SPW": SheetPileInput()})
    everything = factored_elements(section, wb)["SPW"]["QP"].frame["Z"]
    top = float(everything.max()) - 2.0
    section.elements["SPW"].top_level = top
    kept = factored_elements(section, wb)["SPW"]
    assert all(float(s.frame["Z"].max()) <= top for s in kept.values())
    assert len(kept["QP"].frame) < len(everything)
    spw = run_section(DesignSettings(), section, wb)["sheet_pile_walls"][0]
    assert spw["length_m"] == round(top - float(everything.min()), 2)
